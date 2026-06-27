# analysis/autoloop_walkforward.py — 자가개선 루프 워크포워드 메타 백테스트
#
# 질문 (Rocky): "자가개선 루프가 과거에 돌았다면 성적이 어떻게 변했을까?"
#
# 방법 (룩어헤드 금지):
#   - 반기마다 '연구 이벤트': 시점 T 에서 T 까지의 데이터만으로
#     train(시작~T-6mo) + OOS(최근 6mo) 게이트 판정 (research.factory 와 동일 게이트)
#   - 합격 → 챔피언 채택 (슬롯 2, 동일 param 교체) / 활성안은 매 이벤트 재검증, 실패 시 은퇴
#   - 채택된 챔피언으로 다음 반기를 트레이딩 (자본 이월)
#   - 동결 전략도 같은 반기 경계로 잘라 동일 조건 비교 (경계에서 강제 청산 — 양쪽 동일)
#
# 시뮬레이션 전용 보정 (실제 공장과의 차이, 보고서에 명시):
#   1. 표본 부족(min trades 미달)만으로는 영구 기각하지 않고 다음 이벤트로 보류
#      (실제로는 가설이 시간에 걸쳐 도착하므로)
#   2. train/OOS 메트릭은 전구간 1회 실행 후 트레이드/곡선 슬라이스로 산출 (속도)
#   3. OOS 최소 트레이드 5건 (6mo 창 — 실공장 8건은 18mo 창 기준)
#
# 후보 메뉴: 그리드 스윕이 아니라 LLM 이 낼 법한 경계 인접값 10개 고정
# (이 실험의 목적은 더 좋은 파라미터 찾기가 아니라 루프 동역학 측정이다).
#
# 실행: cd prism-btc && ../.venv/bin/python -m analysis.autoloop_walkforward
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import pandas as pd

from collector.store import get_connection
from backtest.engine import run_backtest
from research import overrides
from research.factory import evaluate_gate
import research.factory as factory

DATA_START = "2020-04-01"
SIM_END = "2026-06-12"
INITIAL_EQUITY = 10_000.0
OOS_DAYS = 183

EVENTS = ["2022-01-01", "2022-07-01", "2023-01-01", "2023-07-01",
          "2024-01-01", "2024-07-01", "2025-01-01", "2025-07-01", "2026-01-01"]

CANDIDATE_MENU: list[tuple[str, object]] = [
    ("ENTRY_SCORE_MIN", 60.0), ("ENTRY_SCORE_MIN", 75.0), ("ENTRY_SCORE_MIN", 80.0),
    ("TS_MIN", 1.5), ("TS_MIN", 2.5), ("TS_MIN", 3.0),
    ("BE_TRAIL_ACTIVATE_R", 1.0), ("BE_TRAIL_ACTIVATE_R", 2.0),
    ("TRAILING_TF", "4h"), ("TRAILING_TF", "1d"),
]

MIN_OOS_TRADES_SIM = 5  # 6mo OOS 창 보정

OUT_JSON = Path(__file__).parent / "results_autoloop_wf.json"
OUT_MD = Path(__file__).resolve().parent.parent.parent / "tasks" / "btc_autoloop_walkforward.md"

_run_cache: dict = {}


def _cfg_key(cfg: dict) -> tuple:
    return tuple(sorted(cfg.items()))


def _full_run(cfg: dict, start: str, end: str):
    """(cfg, 구간) 백테스트 1회 — 캐시. (trade_logs, equity_curve) 반환."""
    key = (_cfg_key(cfg), start, end)
    if key in _run_cache:
        return _run_cache[key]
    conn = get_connection(None)
    try:
        with overrides.apply(cfg):
            state = run_backtest(conn, pd.Timestamp(start, tz="UTC"),
                                 pd.Timestamp(end, tz="UTC"),
                                 initial_equity=INITIAL_EQUITY)
    finally:
        conn.close()
    trades = [{"exit_time": t.exit_time, "net_pnl": t.net_pnl,
               "r_multiple": t.r_multiple} for t in state.trade_logs]
    curve = [(ts, v) for ts, v in state.equity_curve]
    liq = sum(1 for t in state.trade_logs
              if getattr(t, "exit_reason", "") == "liq_forced_reduce")
    _run_cache[key] = (trades, curve, liq)
    print(f"  run cfg={dict(cfg) or 'frozen'} {start}~{end}: "
          f"{len(trades)} trades", flush=True)
    return _run_cache[key]


def _window_metrics(trades: list, curve: list, liq: int,
                    w_start: str, w_end: str) -> dict:
    """전구간 실행 결과에서 윈도우 메트릭 슬라이스."""
    ws, we = pd.Timestamp(w_start, tz="UTC"), pd.Timestamp(w_end, tz="UTC")
    wt = [t for t in trades if ws <= pd.Timestamp(t["exit_time"]) < we]
    wins = sum(t["net_pnl"] for t in wt if t["net_pnl"] > 0)
    losses = -sum(t["net_pnl"] for t in wt if t["net_pnl"] < 0)
    pf = (wins / losses) if losses > 0 else (9999.0 if wins > 0 else 0.0)
    cv = [(pd.Timestamp(ts), v) for ts, v in curve
          if ws <= pd.Timestamp(ts) < we]
    if cv:
        peak = mdd = 0.0
        peak = cv[0][1]
        for _, v in cv:
            peak = max(peak, v)
            mdd = max(mdd, (peak - v) / peak * 100)
        ret_pct = (cv[-1][1] / cv[0][1] - 1) * 100
    else:
        mdd, ret_pct = 0.0, 0.0
    return {"profit_factor": round(pf, 3), "mdd_pct": round(mdd, 2),
            "total_return_pct": round(ret_pct, 2), "trade_count": len(wt),
            "liq_approach_count": liq}


def _judge(champion: dict, cand_cfg: dict, T: str) -> tuple[str, dict]:
    """시점 T 에서 후보 판정 (T 까지 데이터만). 반환: (verdict, checks)."""
    oos_start = (pd.Timestamp(T) - pd.Timedelta(days=OOS_DAYS)).strftime("%Y-%m-%d")
    b_tr, b_cv, b_liq = _full_run(champion, DATA_START, T)
    v_tr, v_cv, v_liq = _full_run(cand_cfg, DATA_START, T)
    bt = _window_metrics(b_tr, b_cv, b_liq, DATA_START, oos_start)
    vt = _window_metrics(v_tr, v_cv, v_liq, DATA_START, oos_start)
    bo = _window_metrics(b_tr, b_cv, b_liq, oos_start, T)
    vo = _window_metrics(v_tr, v_cv, v_liq, oos_start, T)
    passed, checks = evaluate_gate(bt, vt, bo, vo)
    # 표본 부족이 하나라도 있으면 '보류' (시뮬 보정 #1):
    # 표본 미달 상태의 실질 비교는 노이즈 — 영구 기각 메모리에 박제하면 안 된다.
    sample_keys = {"train_min_trades", "oos_min_trades"}
    failed = {k for k, ok in checks.items() if not ok}
    if failed & sample_keys:
        return "deferred", checks
    return ("validated" if passed else "rejected"), checks


def simulate_loop() -> tuple[dict, list]:
    """이벤트 진행 — 채택/은퇴 타임라인과 이벤트별 챔피언 반환."""
    factory.MIN_OOS_TRADES = MIN_OOS_TRADES_SIM  # 시뮬 보정 #3
    champion: dict = {}
    judged: dict = {}            # (param, value) -> verdict (영구 기각 메모리)
    active_log: list = []        # 타임라인
    champions_at: dict = {}      # event T -> champion snapshot
    for T in EVENTS:
        print(f"[event {T}] champion={champion or 'frozen'}", flush=True)
        # 1. 활성 재검증
        for param in list(champion):
            without = {k: v for k, v in champion.items() if k != param}
            verdict, checks = _judge(without, champion, T)
            if verdict != "validated":
                active_log.append({"event": T, "action": "retire",
                                   "param": param, "value": champion[param],
                                   "failed": [k for k, ok in checks.items() if not ok]})
                champion = without
        # 2. 후보 판정
        for param, value in CANDIDATE_MENU:
            if judged.get((param, value)) in ("validated", "rejected"):
                continue
            if champion.get(param) == value:
                continue
            cand_cfg = {**champion, param: value}
            verdict, checks = _judge(champion, cand_cfg, T)
            if verdict == "deferred":
                continue
            judged[(param, value)] = verdict
            if verdict == "validated":
                if param not in champion and len(champion) >= overrides.MAX_ACTIVE:
                    active_log.append({"event": T, "action": "slot_full",
                                       "param": param, "value": value})
                    continue
                champion = {**champion, param: value}
                active_log.append({"event": T, "action": "adopt",
                                   "param": param, "value": value})
            else:
                active_log.append({"event": T, "action": "reject",
                                   "param": param, "value": value,
                                   "failed": [k for k, ok in checks.items() if not ok]})
        champions_at[T] = dict(champion)
    return champions_at, active_log


def splice_forward(champions_at: dict, arm_frozen: bool) -> dict:
    """반기 에라를 자본 이월로 이어붙여 전진 성적 산출."""
    boundaries = EVENTS + [SIM_END]
    equity = INITIAL_EQUITY
    curve_all: list[tuple[pd.Timestamp, float]] = []
    trades_all: list[dict] = []
    for i in range(len(EVENTS)):
        t0, t1 = boundaries[i], boundaries[i + 1]
        cfg = {} if arm_frozen else champions_at[EVENTS[i]]
        conn = get_connection(None)
        try:
            with overrides.apply(cfg):
                state = run_backtest(conn, pd.Timestamp(t0, tz="UTC"),
                                     pd.Timestamp(t1, tz="UTC"),
                                     initial_equity=equity)
        finally:
            conn.close()
        for t in state.trade_logs:
            trades_all.append({"net_pnl": t.net_pnl, "r": t.r_multiple})
        curve_all.extend((pd.Timestamp(ts), v) for ts, v in state.equity_curve)
        equity = state.equity_curve[-1][1] if state.equity_curve else equity
        print(f"  era {t0}~{t1} cfg={cfg or 'frozen'}: equity -> {equity:.0f}", flush=True)
    # 메트릭
    vals = [v for _, v in curve_all]
    peak = vals[0]
    mdd = 0.0
    for v in vals:
        peak = max(peak, v)
        mdd = max(mdd, (peak - v) / peak * 100)
    days = (pd.Timestamp(SIM_END) - pd.Timestamp(EVENTS[0])).days
    cagr = ((equity / INITIAL_EQUITY) ** (365.0 / days) - 1) * 100
    wins = sum(t["net_pnl"] for t in trades_all if t["net_pnl"] > 0)
    losses = -sum(t["net_pnl"] for t in trades_all if t["net_pnl"] < 0)
    n_win = sum(1 for t in trades_all if t["net_pnl"] > 0)
    rr_w = [t["r"] for t in trades_all if t["net_pnl"] > 0]
    rr_l = [abs(t["r"]) for t in trades_all if t["net_pnl"] <= 0]
    return {
        "final_equity": round(equity, 0),
        "total_return_pct": round((equity / INITIAL_EQUITY - 1) * 100, 1),
        "cagr_pct": round(cagr, 2),
        "mdd_pct": round(mdd, 2),
        "profit_factor": round(wins / losses, 3) if losses else None,
        "win_rate_pct": round(n_win / len(trades_all) * 100, 1) if trades_all else None,
        "rr": (round((sum(rr_w) / len(rr_w)) / (sum(rr_l) / len(rr_l)), 2)
               if rr_w and rr_l else None),
        "trade_count": len(trades_all),
    }


def main() -> int:
    t0 = time.time()
    print(f"=== 자가개선 루프 워크포워드 ({EVENTS[0]} ~ {SIM_END}) ===", flush=True)
    champions_at, log = simulate_loop()
    print("\n=== 전진 성적: 자가개선 루프 암 ===", flush=True)
    loop_perf = splice_forward(champions_at, arm_frozen=False)
    print("\n=== 전진 성적: 동결 전략 암 (동일 에라 경계) ===", flush=True)
    frozen_perf = splice_forward(champions_at, arm_frozen=True)
    result = {
        "sim": {"start": EVENTS[0], "end": SIM_END, "events": EVENTS,
                "oos_days": OOS_DAYS, "menu": CANDIDATE_MENU,
                "risk_per_trade": "engine default 2%"},
        "timeline": log,
        "champions_at": champions_at,
        "loop": loop_perf,
        "frozen_spliced": frozen_perf,
        "elapsed_sec": round(time.time() - t0, 1),
    }
    OUT_JSON.write_text(json.dumps(result, ensure_ascii=False, indent=1, default=str))
    print(f"\nsaved -> {OUT_JSON}", flush=True)
    print(json.dumps({"loop": loop_perf, "frozen": frozen_perf},
                     ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
