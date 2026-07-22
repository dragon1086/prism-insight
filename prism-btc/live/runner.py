# live/runner.py — 섀도우 데몬 루프 (CLI)
#
#   python -m live.runner --once   : 한 틱만 실행하고 종료 (테스트/cron용)
#   python -m live.runner          : 상주 루프 (다음 30m 경계+10초까지 sleep → tick)
#
# tick 순서:
#   1. update_all()  — market.db 증분 갱신 (실패 시 이번 틱 스킵 + 이벤트 기록)
#   2. 새 확정 30m 봉이 있으면:
#        지표/스냅샷 빌드 (backtest 헬퍼 재사용) → exits 평가/집행 → 진입 평가
#        (4h 하드캡 + 쿨다운) → DB 기록
#   3. btc_events 에 하트비트 기록
#
# 모든 네트워크 호출 실패에 내성: update.py 가 TF별 재시도/예외 흡수, 여기서는
# 전체 update 실패 시 이번 틱을 스킵하고 에러 이벤트를 남긴다.
from __future__ import annotations

import argparse
import logging
import time

import pandas as pd

from collector.store import get_connection as market_connection
from collector.update import update_all
from backtest.engine import _load_tf_data, _get_tf_slice, ALL_TFS
from engine.indicators import add_indicators

from live import tracking
from live.shadow import ShadowAdapter, _load_funding

log = logging.getLogger("live.runner")

_30M_SEC = 30 * 60
_BOUNDARY_DELAY_SEC = 10  # 30m 경계 후 confirmed 캔들이 안정적으로 들어올 여유


def _last_processed_30m_ns(root_conn, mode: str):
    return tracking.get_meta(root_conn, "last_processed_30m_ns", mode)


def _confirmed_30m(tf_data) -> "pd.DataFrame":
    """confirmed 30m 봉만 (add_indicators 후 NaN 워밍업은 build_snapshot 가 처리)."""
    return tf_data["30m"]


def tick(mode: str = "shadow", market_db_path=None, root_db_path=None) -> dict:
    """단일 틱 실행. 처리한 새 확정 30m 봉 수 + 하트비트 결과 dict 반환."""
    result = {"updated": False, "new_bars": 0, "error": None, "ts": None}
    root_conn = tracking.get_connection(root_db_path)
    tracking.ensure_schema(root_conn)

    # --- 0a. 코드 버전 추적 (감사용: "이 트레이드는 어느 커밋의 코드였나") ---
    try:
        import subprocess
        from pathlib import Path as _P
        rev = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"], capture_output=True,
            text=True, timeout=5,
            cwd=str(_P(__file__).resolve().parent.parent)).stdout.strip()
        if rev and tracking.get_meta(root_conn, "code_version", mode) != rev:
            tracking.set_meta(root_conn, "code_version", rev, mode)
            tracking.log_event(root_conn, "version", f"code version: {rev}", mode=mode)
    except Exception:  # noqa: BLE001 — 버전 추적 실패는 무해
        pass

    # --- 0. 챔피언 오버라이드 적용 (자가개선 루프의 라이브 반영 지점) ---
    # 연구공장이 train+OOS 게이트로 검증·활성화한 파라미터만 여기서 적용된다.
    # 실패해도 동결 기본값으로 트레이딩 계속 (보수적 폴백).
    try:
        from research import overrides as _overrides
        applied = _overrides.apply_active(root_conn, mode)
        if applied:
            result["overrides"] = applied
            log.info("champion overrides applied: %s", applied)
    except Exception as exc:  # noqa: BLE001
        tracking.log_event(root_conn, "error", f"overrides apply: {exc}",
                           level="error", mode=mode)

    # --- 1. market.db 증분 갱신 (실패 내성) ---
    try:
        upserts = update_all(market_db_path)
        result["updated"] = True
        tracking.log_event(root_conn, "update",
            f"update_all ok: {sum(upserts.values())} rows upserted", mode=mode)
    except Exception as exc:  # noqa: BLE001 — 네트워크 등 모든 실패 흡수
        result["error"] = f"update_all failed: {exc}"
        tracking.log_event(root_conn, "error", result["error"], level="error", mode=mode)
        tracking.log_event(root_conn, "heartbeat", "tick skipped (update failed)", mode=mode)
        root_conn.close()
        return result

    # --- 2. 지표/스냅샷용 tf_data 빌드 (backtest 헬퍼 재사용 — confirmed=1 만) ---
    market_conn = market_connection(market_db_path)
    try:
        tf_data = {tf: add_indicators(_load_tf_data(market_conn, tf)) for tf in ALL_TFS}
        funding_times, funding_rates = _load_funding(market_conn)
    finally:
        market_conn.close()

    bars_30m = _confirmed_30m(tf_data)
    if bars_30m.empty:
        tracking.log_event(root_conn, "heartbeat", "no confirmed 30m bars yet", mode=mode)
        root_conn.close()
        return result

    last_ns = _last_processed_30m_ns(root_conn, mode)
    # 처리 대상: 아직 처리 안 한 확정 30m 봉들 (오름차순).
    if last_ns is None:
        # 콜드 스타트: 마지막 확정봉 1개만 처리 (과거 전체 재시뮬 방지).
        new_bars = bars_30m.iloc[[-1]]
    else:
        new_bars = bars_30m[bars_30m.index.map(lambda t: int(t.value) > int(last_ns))]

    # --- 어댑터 선택 (mode 분기 — 결정로직 동일, "집행"만 다름) ---
    # demo: 거래소 실주문 집행 (live/demo.py). 다른 모든 모드: 가상 체결 (shadow).
    # demo 어댑터 import/생성 실패(pybit 없음·키 없음 등)는 흡수 → 에러 이벤트 +
    # 이번 틱 스킵. 섀도우/다른 모드는 영향 0 (지연 임포트로 shadow 경로 안 깨짐).
    if mode == "demo":
        try:
            from live.demo import DemoAdapter
            adapter = DemoAdapter(root_conn, tf_data, funding_times,
                                  funding_rates, mode=mode)
        except Exception as exc:  # noqa: BLE001 — 어댑터 생성 실패 흡수
            result["error"] = f"demo adapter init failed: {exc}"
            tracking.log_event(root_conn, "error", result["error"],
                               level="error", mode=mode)
            tracking.log_event(root_conn, "heartbeat",
                               "tick skipped (demo adapter init failed)", mode=mode)
            root_conn.close()
            return result
    else:
        adapter = ShadowAdapter(root_conn, tf_data, funding_times, funding_rates, mode=mode)

    # 4h 확정 추적 (backtest cadence gate 미러).
    last_confirmed_4h_ns = tracking.get_meta(root_conn, "last_confirmed_4h_ns", mode)
    processed = 0
    last_bar_ns = last_ns
    for bar_time, bar in new_bars.iterrows():
        slice_4h = _get_tf_slice(tf_data, bar_time, "4h")
        new_4h_confirmed = False
        cur_4h_ns = None
        if not slice_4h.empty:
            cur_4h_ns = int(slice_4h.index[-1].value)
            if last_confirmed_4h_ns is None or cur_4h_ns != int(last_confirmed_4h_ns):
                new_4h_confirmed = True
                last_confirmed_4h_ns = cur_4h_ns

        adapter.process_bar(bar_time, bar, new_4h_confirmed, cur_4h_ns)
        processed += 1
        last_bar_ns = int(bar_time.value)
        result["ts"] = str(bar_time)

    if last_bar_ns is not None:
        tracking.set_meta(root_conn, "last_processed_30m_ns", int(last_bar_ns), mode)
    if last_confirmed_4h_ns is not None:
        tracking.set_meta(root_conn, "last_confirmed_4h_ns", int(last_confirmed_4h_ns), mode)

    result["new_bars"] = processed

    # --- 2b. 스윙 레인 (라운드6 Lane B — mode='swing' 자체 원장) ---
    # SWING_RUN_MODES 틱에서만 구동 (기본 demo 전용 — shadow/demo 크론 병행 시
    # 커서 경합 방지). 집행 백엔드는 swing 모듈이 자동 선택: 스윙 전용 키
    # (BYBIT_SWING_API_KEY/SECRET, 메인과 별도 계정) 있으면 실주문, 없으면
    # 가상 체결. 메인 결정로직/상태와 완전 독립 (자체 메타 커서). 어떤 예외도
    # 메인 트레이딩을 멈출 수 없다. 검증: tasks/btc_round6_swing_lane.md.
    try:
        from engine.config import SWING_RUN_MODES
        if mode in SWING_RUN_MODES:
            from live import swing as swing_lane
            sres = swing_lane.process(root_conn, tf_data, main_mode=mode)
            if sres.get("events"):
                result["swing"] = sres
    except Exception as exc:  # noqa: BLE001 — 스윙 레인 실패는 메인과 무관
        tracking.log_event(root_conn, "error", f"swing lane: {exc}",
                           level="error", mode="swing")

    # --- 3. 매매일지/부검 (학습 기어 — 트레이딩 처리 완료 후에만, 실패 절대 비전파) ---
    # LLM 은 주문 경로 밖: 종결 트레이드의 facts 추출 + 부검만 수행한다.
    # 어떤 예외도 데몬을 멈출 수 없다 (이벤트 로그로 흡수, 다음 틱 재시도).
    try:
        from live import journal
        jres = journal.process_pending(root_conn, tf_data, mode=mode, limit=1)
        if jres["facts_created"] or jres["analyzed"] or jres["failed"]:
            result["journal"] = jres
    except Exception as exc:  # noqa: BLE001 — 학습 기어 실패는 트레이딩과 무관
        tracking.log_event(root_conn, "error", f"journal pipeline: {exc}",
                           level="error", mode=mode)

    # --- 3b. 실시간 매매 이벤트 알림 (데모/실모드만 — shadow 는 가상 시뮬이라 미발송) ---
    # 새 진입/비중 추가/포지션 정리를 즉시 텔레그램으로 알린다 (멱등). 어떤 예외도
    # 데몬을 멈출 수 없다 (notify_new_events 가 내부 흡수하지만 여기서도 한 겹 더 방어).
    if mode in ("demo", "live"):
        try:
            from live import notifier
            notifier.notify_new_events(root_conn, mode)
        except Exception as exc:  # noqa: BLE001 — 알림 실패는 트레이딩과 무관
            tracking.log_event(root_conn, "error", f"notifier: {exc}",
                               level="error", mode=mode)

    tracking.log_event(root_conn, "heartbeat",
        f"tick ok: processed {processed} new 30m bar(s); "
        f"last={result['ts']}", mode=mode)
    root_conn.close()
    return result


def _sleep_to_next_boundary() -> None:
    now = time.time()
    next_boundary = (int(now // _30M_SEC) + 1) * _30M_SEC + _BOUNDARY_DELAY_SEC
    delay = max(1.0, next_boundary - now)
    log.info("sleeping %.0fs to next 30m boundary", delay)
    time.sleep(delay)


def main() -> int:
    parser = argparse.ArgumentParser(description="prism-btc shadow paper daemon")
    parser.add_argument("--once", action="store_true",
                        help="run a single tick and exit (test/cron)")
    parser.add_argument("--mode", default="shadow", choices=["shadow", "demo", "live"])
    parser.add_argument("--market-db", default=None, help="market.db path override")
    parser.add_argument("--root-db", default=None, help="root tracking db path override")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    if args.once:
        res = tick(mode=args.mode, market_db_path=args.market_db, root_db_path=args.root_db)
        log.info("tick result: %s", res)
        return 0 if res["error"] is None else 1

    log.info("starting resident shadow loop (Ctrl-C to stop)")
    while True:
        try:
            res = tick(mode=args.mode, market_db_path=args.market_db, root_db_path=args.root_db)
            log.info("tick result: %s", res)
        except KeyboardInterrupt:
            log.info("interrupted — exiting")
            return 0
        except Exception as exc:  # noqa: BLE001
            log.error("tick raised (continuing): %s", exc)
        _sleep_to_next_boundary()


if __name__ == "__main__":
    raise SystemExit(main())
