"""KRX 가 죽은 상태에서 KR 배치가 **끝까지 도는지** 로컬에서 확인한다.

2026-08-05 오후 배치는 리포트 0건으로 끝났다. `run_batch` 의 첫 데이터 호출이
KRX 를 직접 부르는데 폴백이 없었기 때문이다. 그걸 고쳤지만(PR #528), **고친
함수가 동작하는 것과 배치가 완주하는 것은 다른 문제다.** 이번 장애 자체가
"함수 하나가 죽어서 파이프라인 전체가 끝난" 사건이라 더욱 그렇다.

이 스크립트는 KRX 클라이언트를 **강제로 실패시킨 뒤** 실제 `run_batch()` 를
돌려서, 폴백 사슬이 실제로 이어받아 종목 선정까지 도달하는지 본다.

    거래일 해석   로컬 달력   (KRX 폴백 강등 — PR #528)
    스냅샷·시총   Naver       (load_market_snapshot_bundle 폴백)
    종목별 OHLCV  FDR         (get_multi_day_ohlcv 폴백)
    종목명        종목코드    (try/except 강등)

프로덕션에서만 재현되는 조건이지만 **주입은 로컬에서 가능하다.** 실제 KRX 를
긁지 않으므로 이 호스트가 차단될 위험도 없다.

실행:

    PYTHONPATH=. .venv312/bin/python tools/verify_batch_survives_krx_outage.py --trigger afternoon
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(REPO_ROOT / ".env")

import krx_data_client  # noqa: E402
import trigger_batch  # noqa: E402

logger = logging.getLogger("verify_batch")

# db-server 가 실제로 받는 예외 메시지 그대로.
_BLOCKED_MESSAGE = (
    "KRX 접근이 차단된 상태입니다. 198분 뒤(18:04)에 다시 시도하세요. "
    "차단 중 재시도는 차단을 연장시킵니다."
)


class KrxCallCounter:
    """KRX 를 부르려는 시도를 세고 전부 실패시킨다."""

    def __init__(self) -> None:
        self.attempts: list[str] = []

    def fail(self, name: str):
        def _raise(*args, **kwargs):
            self.attempts.append(name)
            raise RuntimeError(_BLOCKED_MESSAGE)

        return _raise


def force_krx_outage() -> KrxCallCounter:
    """KRX 로 나가는 모든 경로를 막는다.

    ``trigger_batch.stock_api`` 는 import 시점에 ``krx_data_client`` 함수를
    staticmethod 로 묶어둔 래퍼라 양쪽 다 막아야 한다.
    """
    counter = KrxCallCounter()

    for attr in (
        "get_market_ohlcv_by_ticker",
        "get_nearest_business_day_in_a_week",
        "get_market_cap_by_ticker",
        "get_market_ticker_name",
    ):
        setattr(trigger_batch.stock_api, attr, counter.fail(f"stock_api.{attr}"))

    for attr in (
        "get_market_ohlcv_by_date",
        "get_market_fundamental_by_date",
        "_get_client",
    ):
        if hasattr(krx_data_client, attr):
            setattr(krx_data_client, attr, counter.fail(f"krx_data_client.{attr}"))

    # 종목명 캐시는 조회 자체를 건너뛰게 둔다(빈 dict = 조회 완료).
    trigger_batch._TICKER_NAME_CACHE = {}

    return counter


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--trigger", default="afternoon", choices=["morning", "afternoon"])
    parser.add_argument("--log-level", default="INFO")
    parser.add_argument(
        "--with-macro",
        action="store_true",
        help="macro_sector_leader / contrarian_value 까지 태운다 (합성 macro_context)",
    )
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    print("=" * 72)
    print(f"KRX 차단 상태에서 {args.trigger} 배치 완주 확인")
    print("=" * 72)

    counter = force_krx_outage()

    macro_context = None
    if args.with_macro:
        # 실제 오케스트레이터는 LLM 이 채운다. 여기서는 경로를 태우는 것이 목적이라
        # 섹터 매칭이 반드시 성립하도록 넓게 잡는다.
        macro_context = {
            "market_regime": "sideways",
            "leading_sectors": [
                {"sector": "반도체", "confidence": 0.9},
                {"sector": "자동차", "confidence": 0.8},
                {"sector": "금융", "confidence": 0.7},
            ],
            "sector_map": {},
        }

    try:
        result = trigger_batch.run_batch(
            args.trigger, log_level=args.log_level, macro_context=macro_context
        )
    except Exception as exc:  # noqa: BLE001
        print()
        print("❌ 배치가 예외로 종료됐다 — 8/5 장애가 재현된 것이다.")
        print(f"   {type(exc).__name__}: {exc}")
        print(f"   KRX 시도 {len(counter.attempts)}회: {sorted(set(counter.attempts))}")
        return 1

    print()
    print("-" * 72)
    print(f"KRX 시도 {len(counter.attempts)}회 (전부 실패 처리됨)")
    for name in sorted(set(counter.attempts)):
        print(f"   {name}: {counter.attempts.count(name)}회")

    if not result:
        print()
        print("⚠️ 배치는 죽지 않았지만 최종 선정이 비었다.")
        print("   휴장일·데이터 부재일 수 있으니 위 로그의 트리거별 건수를 볼 것.")
        return 2

    total = 0
    bearish: list[tuple[str, str, float, float]] = []
    print()
    print("최종 선정:")
    for trigger_type, df in result.items():
        n = len(df) if hasattr(df, "__len__") else 0
        total += n
        print(f"   {trigger_type}: {n}종목")
        for ticker in list(getattr(df, "index", [])):
            row = df.loc[ticker]
            open_px = float(row.get("Open", 0) or 0)
            close_px = float(row.get("Close", 0) or 0)
            candle = "양봉" if close_px > open_px else "🔴음봉"
            print(f"      - {ticker}  시가 {open_px:,.0f} → 종가 {close_px:,.0f}  {candle}")
            if open_px > 0 and close_px <= open_px:
                bearish.append((trigger_type, str(ticker), open_px, close_px))

    print()
    if bearish:
        # PR #527 이 실데이터에서 안 먹었다는 뜻이다. 합성 테스트만으로는 못 잡는다.
        print("❌ 음봉이 선정됐다 — 매수 트리거 음봉 배제(PR #527)가 실데이터에서 안 먹었다.")
        for trigger_type, ticker, open_px, close_px in bearish:
            print(f"   {trigger_type}: {ticker} 시가 {open_px:,.0f} → 종가 {close_px:,.0f}")
        return 3

    print(f"✅ 배치 완주. 트리거 {len(result)}종, 총 {total}종목 선정. 전부 양봉.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
