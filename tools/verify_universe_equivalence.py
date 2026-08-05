"""452종목 유니버스 등가성 검증 하네스 (설계서 §8 7단계).

Plan A 는 스크리닝 스냅샷을 전종목 2,686 에서 시총 5,000억 이상 452 로 줄인다.
"줄여도 트리거 산출이 같다"는 **아직 코드 분석 기반 추론**이고, 이 하네스가 그걸
실증한다.

**오케스트레이터를 돌리지 않는다.** KRX OPEN API 가 과거 전종목 완전판을 주므로
그 스냅샷을 고정해놓고, 트리거가 *실제로 소비하는 지점* 을 유니버스만 바꿔 두 번
계산해 비교한다. 재현 가능하고 지연·LLM·자동매매와 무관하다.

비교 대상은 두 부류로 나뉜다. 이 구분이 이 하네스의 핵심이다.

A. 시총 필터를 스스로 거는 트리거 6개
   (:677 :754 :892 :956 :1002 :1081 에서 ``시가총액 >= 5,000억``)
   → 실제 트리거 함수를 두 유니버스로 각각 호출해 산출을 통째로 대조한다.

B. 시총 필터가 없는 트리거 2개 — ``trigger_macro_sector_leader`` /
   ``trigger_contrarian_value``
   → 이 둘은 ``apply_absolute_filters`` 뒤에 곧바로 ``nlargest(N, "Amount")`` 로
   거래대금 상위만 취한다. 시총 컷이 없다. 따라서 유니버스를 줄이면 **소비 집합이
   바뀔 수 있다.** 두 함수는 각각 macro_context(LLM 산출) 와 krx_data_client
   (스크래핑) 를 요구해 통째 호출이 재현 불가하므로, 외부 의존이 시작되기 *직전*
   까지의 결정적 구간만 양쪽으로 계산해 대조한다. 그 지점이 유니버스 축소가
   영향을 주는 유일한 지점이다.

실행:

    PYTHONPATH=. .venv312/bin/python tools/verify_universe_equivalence.py --days 20

``--days`` 는 거슬러 올라갈 거래일 수. 경계 종목의 드나듦을 잡으려면 20 거래일
정도가 필요하다.
"""

from __future__ import annotations

import argparse
import datetime
import json
import logging
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from dotenv import load_dotenv

# load_dotenv() 는 stdin 스크립트에서 프레임 탐색에 실패한다. 경로를 명시한다.
load_dotenv(REPO_ROOT / ".env")

import trigger_batch  # noqa: E402
from cores.krx_openapi_snapshot import (  # noqa: E402
    KrxOpenApiError,
    fetch_krx_openapi_bundle,
)
from trigger_batch import (  # noqa: E402
    SCREENING_MIN_TRADE_VALUE,
    apply_absolute_filters,
    trigger_afternoon_closing_strength,
    trigger_afternoon_daily_rise_top,
    trigger_afternoon_volume_surge_flat,
    trigger_morning_gap_up_momentum,
    trigger_morning_value_to_cap_ratio,
    trigger_morning_volume_surge,
)

logger = logging.getLogger("universe_equivalence")

# 트리거 6개가 공통으로 거는 임계값 (trigger_batch.py:677 등).
MARKET_CAP_FLOOR = 500_000_000_000

def _suppress_krx_ticker_names() -> None:
    """``enhance_dataframe`` 이 종목명을 붙이려고 KRX 를 긁는 것을 막는다.

    Plan A 가 걷어내려는 바로 그 경로이고, 선택 결과에는 영향이 없다(이름 컬럼만
    채운다). ``None`` 이 아니면 조회를 건너뛰므로 빈 dict 를 심는다.
    """
    trigger_batch._TICKER_NAME_CACHE = {}

# ``trigger_afternoon_volume_surge_flat`` 의 MA20 하락추세 게이트(:1126)는 종목별
# KRX OHLCV 를 긁는다. 유니버스와 무관한 값(종목·날짜만의 함수)인데 전종목/시총컷
# 두 번을 그냥 돌리면 같은 호출을 두 배로 하게 되고, 20거래일이면 KRX 가 db-server
# 에 했던 것처럼 이 IP 도 차단할 수 있다. (ticker, date) 로 메모하고 디스크에 남겨
# 양쪽 실행이 공유하게 한다 — 재실행은 호출이 0 이 된다.
MA20_CACHE_PATH = REPO_ROOT / "tasks" / "bench" / "universe_equivalence_ma20_cache.json"
_MA20_CACHE: dict[str, float] = {}
_MA20_STATS = {"hit": 0, "miss": 0}


def _load_ma20_cache() -> None:
    if MA20_CACHE_PATH.exists():
        try:
            _MA20_CACHE.update(json.loads(MA20_CACHE_PATH.read_text(encoding="utf-8")))
        except Exception as exc:  # noqa: BLE001
            logger.warning("MA20 캐시 로드 실패(무시하고 새로 만든다): %s", exc)


def _save_ma20_cache() -> None:
    try:
        MA20_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        MA20_CACHE_PATH.write_text(
            json.dumps(_MA20_CACHE, ensure_ascii=False, indent=0), encoding="utf-8"
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("MA20 캐시 저장 실패: %s", exc)


_ORIGINAL_COMPUTE_MA20 = trigger_batch._compute_ma20


def _memoized_compute_ma20(ticker: str, trade_date: str, lookback_days: int = 20) -> float:
    key = f"{ticker}:{trade_date}:{lookback_days}"
    if key in _MA20_CACHE:
        _MA20_STATS["hit"] += 1
        return _MA20_CACHE[key]
    _MA20_STATS["miss"] += 1
    value = _ORIGINAL_COMPUTE_MA20(ticker, trade_date, lookback_days)
    _MA20_CACHE[key] = value
    return value


def _install_ma20_memo() -> None:
    """MA20 메모를 설치한다.

    import 부작용으로 두면 이 모듈을 import 하는 테스트까지 ``trigger_batch`` 전역을
    오염시킨다. 실행 경로(``main``)에서만 건다.
    """
    trigger_batch._compute_ma20 = _memoized_compute_ma20

# (이름, 함수) — 전부 (trade_date, snapshot, prev_snapshot, cap_df) 시그니처.
CAP_FILTERED_TRIGGERS = (
    ("morning_volume_surge", trigger_morning_volume_surge),
    ("morning_gap_up_momentum", trigger_morning_gap_up_momentum),
    ("morning_value_to_cap_ratio", trigger_morning_value_to_cap_ratio),
    ("afternoon_daily_rise_top", trigger_afternoon_daily_rise_top),
    ("afternoon_closing_strength", trigger_afternoon_closing_strength),
    ("afternoon_volume_surge_flat", trigger_afternoon_volume_surge_flat),
)


@dataclass
class TriggerDiff:
    """한 트리거·하루의 대조 결과."""

    name: str
    full_tickers: list[str]
    slim_tickers: list[str]
    only_in_full: list[str] = field(default_factory=list)
    only_in_slim: list[str] = field(default_factory=list)
    order_changed: bool = False
    score_max_delta: float | None = None
    error: str | None = None

    @property
    def identical(self) -> bool:
        if self.error is not None:
            return False
        return (
            not self.only_in_full
            and not self.only_in_slim
            and not self.order_changed
            and (self.score_max_delta is None or self.score_max_delta < 1e-9)
        )

    def as_dict(self) -> dict:
        return {
            "trigger": self.name,
            "identical": self.identical,
            "full_count": len(self.full_tickers),
            "slim_count": len(self.slim_tickers),
            "only_in_full": self.only_in_full,
            "only_in_slim": self.only_in_slim,
            "order_changed": self.order_changed,
            "score_max_delta": self.score_max_delta,
            "error": self.error,
        }


def slim_bundle(snapshot: pd.DataFrame, prev_snapshot: pd.DataFrame, cap_df: pd.DataFrame):
    """전종목 번들을 시총 5,000억 이상 유니버스로 줄인다.

    Plan A 가 장중에 하려는 것과 같다 — KIS 로 452 종목만 받는다는 것은 스냅샷
    자체가 처음부터 이 집합이라는 뜻이다. prev_snapshot 과 cap_df 도 같이 줄여야
    실제 전환 후 상태를 재현한다.
    """
    keep = cap_df.index[cap_df["시가총액"] >= MARKET_CAP_FLOOR]
    return (
        snapshot.loc[snapshot.index.intersection(keep)].copy(),
        prev_snapshot.loc[prev_snapshot.index.intersection(keep)].copy(),
        cap_df.loc[cap_df.index.intersection(keep)].copy(),
    )


def _score_column(df: pd.DataFrame) -> str | None:
    for col in ("composite_score", "CompositeScore"):
        if col in df.columns:
            return col
    return None


def compare_frames(name: str, full: pd.DataFrame, slim: pd.DataFrame) -> TriggerDiff:
    """두 산출 프레임을 종목 집합·순서·점수까지 대조한다.

    종목 집합만 보면 안 된다. 점수가 달라지면 ``select_final_tickers`` 의 상위
    선택이 바뀌므로, 같은 종목이 같은 순서로 같은 점수를 받아야 등가다.
    """
    full_tickers = [str(t) for t in full.index]
    slim_tickers = [str(t) for t in slim.index]
    diff = TriggerDiff(name=name, full_tickers=full_tickers, slim_tickers=slim_tickers)
    diff.only_in_full = sorted(set(full_tickers) - set(slim_tickers))
    diff.only_in_slim = sorted(set(slim_tickers) - set(full_tickers))
    diff.order_changed = full_tickers != slim_tickers

    col = _score_column(full)
    if col and col in slim.columns:
        shared = [t for t in full_tickers if t in set(slim_tickers)]
        if shared:
            delta = (full.loc[shared, col] - slim.loc[shared, col]).abs().max()
            diff.score_max_delta = float(delta)
    return diff


def run_cap_filtered(trade_date, full, slim) -> list[TriggerDiff]:
    """시총 필터를 스스로 거는 트리거 6개를 양쪽 유니버스로 실제 호출한다."""
    results = []
    for name, fn in CAP_FILTERED_TRIGGERS:
        try:
            out_full = fn(trade_date, full[0].copy(), full[1].copy(), full[2].copy())
            out_slim = fn(trade_date, slim[0].copy(), slim[1].copy(), slim[2].copy())
            results.append(compare_frames(name, out_full, out_slim))
        except Exception as exc:  # noqa: BLE001 - 하네스는 계속 돌아야 한다
            logger.warning("%s %s failed: %s", trade_date, name, exc)
            results.append(
                TriggerDiff(name=name, full_tickers=[], slim_tickers=[], error=repr(exc))
            )
    return results


def _consumption_set_macro(snapshot: pd.DataFrame, prev_snapshot: pd.DataFrame) -> pd.DataFrame:
    """``trigger_macro_sector_leader`` 가 섹터 매칭에 넘기는 집합 (:1172-1184).

    이 뒤로는 macro_context 의 leading_sectors/sector_map 에만 의존한다. 즉
    유니버스 축소가 결과를 바꿀 수 있는 지점은 여기까지다.
    """
    common = snapshot.index.intersection(prev_snapshot.index)
    snap = snapshot.loc[common].copy()
    snap = apply_absolute_filters(snap, min_value=SCREENING_MIN_TRADE_VALUE)
    if snap.empty:
        return snap
    return snap.nlargest(100, "Amount")


def _consumption_set_contrarian(
    snapshot: pd.DataFrame, prev_snapshot: pd.DataFrame
) -> pd.DataFrame:
    """``trigger_contrarian_value`` 가 52주 고가 조회에 넘기는 집합 (:1281-1297).

    이 뒤로는 krx_data_client 의 종목별 조회 결과에만 의존한다.
    """
    common = snapshot.index.intersection(prev_snapshot.index)
    snap = snapshot.loc[common].copy()
    prev = prev_snapshot.loc[common].copy()
    snap = apply_absolute_filters(snap, min_value=SCREENING_MIN_TRADE_VALUE)
    if snap.empty:
        return snap
    snap["DailyChange"] = ((snap["Close"] - prev["Close"]) / prev["Close"]) * 100
    snap = snap[snap["Close"] > snap["Open"]]
    if snap.empty:
        return snap
    return snap.nlargest(50, "Amount")


def run_uncapped(trade_date, full, slim, cap_df: pd.DataFrame) -> tuple[list[TriggerDiff], dict]:
    """시총 필터가 없는 트리거 2개의 소비 집합을 대조한다."""
    results = []
    detail = {}
    for name, fn in (
        ("macro_sector_leader[top100_by_amount]", _consumption_set_macro),
        ("contrarian_value[top50_by_amount]", _consumption_set_contrarian),
    ):
        try:
            out_full = fn(full[0], full[1])
            out_slim = fn(slim[0], slim[1])
            results.append(compare_frames(name, out_full, out_slim))

            # 왜 갈라지는지 숫자로 남긴다: 소비 집합 중 시총 5,000억 미만이 몇 개인가.
            below = [
                t
                for t in out_full.index
                if float(cap_df.loc[t, "시가총액"]) < MARKET_CAP_FLOOR
            ] if not out_full.empty else []
            detail[name] = {
                "full_set_size": int(len(out_full)),
                "below_cap_floor": int(len(below)),
                "below_cap_floor_pct": (
                    round(100.0 * len(below) / len(out_full), 1) if len(out_full) else 0.0
                ),
            }
        except Exception as exc:  # noqa: BLE001
            logger.warning("%s %s failed: %s", trade_date, name, exc)
            results.append(
                TriggerDiff(name=name, full_tickers=[], slim_tickers=[], error=repr(exc))
            )
    return results, detail


def sessions_backwards(end_date: str, days: int) -> list[str]:
    """``end_date`` 부터 거슬러 올라가며 실제 거래일만 모은다.

    번들이 자기 prev_date 를 알려주므로 그 체인을 따라간다. 휴장일 판단을 따로
    구현할 필요가 없다.
    """
    sessions: list[str] = []
    cursor = end_date
    while len(sessions) < days:
        try:
            bundle = fetch_krx_openapi_bundle(cursor)
        except (KrxOpenApiError, ValueError) as exc:
            logger.warning("세션 탐색 중단 %s: %s", cursor, exc)
            break
        sessions.append(cursor)
        cursor = bundle.prev_date
        if not cursor:
            break
    return sessions


def run_day(trade_date: str) -> dict:
    bundle = fetch_krx_openapi_bundle(trade_date)
    full = (bundle.snapshot, bundle.prev_snapshot, bundle.cap_df)
    slim = slim_bundle(bundle.snapshot, bundle.prev_snapshot, bundle.cap_df)

    capped = run_cap_filtered(trade_date, full, slim)
    uncapped, detail = run_uncapped(trade_date, full, slim, bundle.cap_df)

    return {
        "trade_date": trade_date,
        "prev_date": bundle.prev_date,
        "universe_full": int(len(bundle.snapshot)),
        "universe_slim": int(len(slim[0])),
        "cap_filtered": [d.as_dict() for d in capped],
        "uncapped": [d.as_dict() for d in uncapped],
        "uncapped_detail": detail,
        "all_identical": all(d.identical for d in capped + uncapped),
    }


def summarize(days: list[dict]) -> str:
    lines = []
    lines.append("=" * 72)
    lines.append("452종목 유니버스 등가성 검증 — 설계서 §8 7단계")
    lines.append("=" * 72)
    if not days:
        return "\n".join(lines + ["", "거래일 데이터를 하나도 받지 못했다."])

    uni_full = [d["universe_full"] for d in days]
    uni_slim = [d["universe_slim"] for d in days]
    lines.append(
        f"거래일 {len(days)}일 ({days[-1]['trade_date']} ~ {days[0]['trade_date']})"
    )
    lines.append(
        f"유니버스 전종목 {min(uni_full)}~{max(uni_full)} / "
        f"시총컷 후 {min(uni_slim)}~{max(uni_slim)}"
    )
    lines.append("")

    lines.append("── A. 시총 필터를 스스로 거는 트리거 6개 ──")
    a_mismatch = []
    for name, _ in CAP_FILTERED_TRIGGERS:
        rows = [r for d in days for r in d["cap_filtered"] if r["trigger"] == name]
        mismatched = [r for r in rows if not r["identical"]]
        errored = [r for r in rows if r["error"]]
        status = "✅ 일치" if not mismatched else f"❌ 불일치 {len(mismatched)}/{len(rows)}일"
        if errored:
            status += f" (오류 {len(errored)}일)"
        lines.append(f"  {name:<32} {status}")
        if mismatched:
            a_mismatch.append(name)

    if "morning_value_to_cap_ratio" in a_mismatch:
        lines.append("")
        lines.append("  ⚠️ morning_value_to_cap_ratio 는 **필터 순서가 다른 5개와 반대다.**")
        lines.append("     다른 5개: 시총컷(:677 등) → apply_absolute_filters")
        lines.append("     이 하나 : apply_absolute_filters(:862) → 시총컷(:892)")
        lines.append("     apply_absolute_filters 의 거래량 평균(:319)이 시총컷 *이전* 집합에서")
        lines.append("     계산되므로, 유니버스를 452 로 줄이면 평균이 올라가 임계값이 달라진다.")
        lines.append("     설계서 §2-4 가 등가의 근거로 든 ':319 는 시총 필터 이후' 전제가")
        lines.append("     이 트리거에서만 성립하지 않는다.")

    lines.append("")
    lines.append("── B. 시총 필터가 없는 트리거 2개 (소비 집합 대조) ──")
    for name in (
        "macro_sector_leader[top100_by_amount]",
        "contrarian_value[top50_by_amount]",
    ):
        rows = [r for d in days for r in d["uncapped"] if r["trigger"] == name]
        mismatched = [r for r in rows if not r["identical"]]
        status = "✅ 일치" if not mismatched else f"❌ 불일치 {len(mismatched)}/{len(rows)}일"
        lines.append(f"  {name:<38} {status}")
        if mismatched:
            dropped = [len(r["only_in_full"]) for r in mismatched]
            lines.append(
                f"      전종목에만 있던 종목 하루 평균 "
                f"{sum(dropped) / len(dropped):.1f}개 (최대 {max(dropped)}개)"
            )
        pcts = [
            d["uncapped_detail"][name]["below_cap_floor_pct"]
            for d in days
            if name in d.get("uncapped_detail", {})
        ]
        if pcts:
            lines.append(
                f"      소비 집합 중 시총 5,000억 미만 비중 "
                f"평균 {sum(pcts) / len(pcts):.1f}% (최대 {max(pcts):.1f}%)"
            )

    lines.append("")
    ok_days = sum(1 for d in days if d["all_identical"])
    lines.append(f"전체 일치 거래일: {ok_days}/{len(days)}")
    lines.append("")
    if ok_days == len(days):
        lines.append("판정: 452 유니버스로 줄여도 트리거 산출이 동일하다. 8단계 진행 가능.")
    else:
        lines.append(
            "판정: 불일치가 있다. 설계서 §9 대로 전환하지 말고 되돌린다.\n"
            "      A 가 불일치면 시총 필터 전제가 깨진 것이고,\n"
            "      B 만 불일치면 해당 트리거는 전종목 스냅샷이 실제로 필요하다는 뜻이다."
        )
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--days", type=int, default=20, help="거슬러 올라갈 거래일 수")
    parser.add_argument(
        "--end-date",
        default=None,
        help="시작 기준일 YYYYMMDD (기본: 어제. 당일 데이터는 OPEN API 에 없다)",
    )
    parser.add_argument("--output", default=None, help="JSON 결과 저장 경로")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    if not args.verbose:
        logging.getLogger("trigger_batch").setLevel(logging.ERROR)

    if not os.getenv("KRX_OPENAPI_AUTH_KEY"):
        print("KRX_OPENAPI_AUTH_KEY 가 없다. .env 를 확인하라.", file=sys.stderr)
        return 2

    end_date = args.end_date or (
        datetime.date.today() - datetime.timedelta(days=1)
    ).strftime("%Y%m%d")

    _suppress_krx_ticker_names()
    _install_ma20_memo()
    _load_ma20_cache()
    logger.info("MA20 캐시 %d건 로드", len(_MA20_CACHE))

    logger.info("거래일 탐색: %s 부터 %d일", end_date, args.days)
    sessions = sessions_backwards(end_date, args.days)
    if not sessions:
        print("거래일을 하나도 찾지 못했다.", file=sys.stderr)
        return 2
    logger.info("대상 거래일 %d일: %s ... %s", len(sessions), sessions[0], sessions[-1])

    days = []
    for i, session in enumerate(sessions, 1):
        logger.info("[%d/%d] %s 대조 중", i, len(sessions), session)
        try:
            days.append(run_day(session))
        except Exception as exc:  # noqa: BLE001
            logger.error("%s 실패: %s", session, exc)

    _save_ma20_cache()
    logger.info(
        "MA20 조회 hit=%d miss=%d (miss 만 KRX 를 실제로 긁었다)",
        _MA20_STATS["hit"],
        _MA20_STATS["miss"],
    )

    report = summarize(days)
    print()
    print(report)

    if args.output:
        Path(args.output).write_text(
            json.dumps({"days": days}, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        logger.info("JSON 저장: %s", args.output)

    return 0 if days and all(d["all_identical"] for d in days) else 1


if __name__ == "__main__":
    raise SystemExit(main())
