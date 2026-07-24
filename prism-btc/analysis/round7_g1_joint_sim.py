# analysis/round7_g1_joint_sim.py — 라운드7 G-1 본실험: 1.5x 공동 시뮬 + intra-trade MDD 실측
#
# Rocky 결정 (2026-07-24): 위험 배수 1.5x — 메인 3% / 스윙 1.5%.
# 프론티어(round7_g1_exposure_frontier.py)는 closed-trade 근사였다. 본실험은:
#   - 메인: 실제 엔진 연속 실행(수수료·펀딩·청산버퍼·쿨다운 전부 실집행)을
#     바 단위 mark-to-market equity 로 계측 (engine instr_mtm_curve).
#   - 스윙: Lane B 미러를 4h 바 단위 MTM 으로 시뮬 (5x 명목 캡 실적용 —
#     리스크를 올리면 캡 바인딩으로 스윙은 선형보다 덜 커지는 것까지 반영).
#   - 결합: 두 MTM 곡선을 4h 그리드에 정렬해 r_p = r_main + r_swing 오버레이
#     합성 (공유 계좌 1차 근사 — 두 레인이 같은 자본 위에서 동일 성장률 가정).
#
# 수용 기준 (사전 등록): 1.5x 결합 intra-trade MDD ≤ ~20%(Rocky 각오치),
# 메인 liq_approach_count == 0 유지 (리스크 배수는 수량만 바꾸고 레버리지/청산
# 거리는 불변이므로 0이어야 정상).
#
# 산출: backtest/results/round7_g1_joint_sim.json
# 실행: ../.venv-bt/bin/python -m analysis.round7_g1_joint_sim
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np
import pandas as pd

import backtest.engine as be
import engine.sizing as _sizing
from collector.store import get_connection
from analysis.round7_portfolio import _load, COST
from engine.config import SWING_STOP_ATR_MULT, SWING_MAX_LEVERAGE

START = "2022-01-01"
END = "2026-07-22"
INITIAL = 10_000.0

# (라벨, 메인 리스크, 스윙 리스크)
SCENARIOS = [
    ("1.0x", 0.02, 0.010),
    ("1.5x", 0.03, 0.015),
]


def run_main_mtm(risk: float) -> tuple[np.ndarray, np.ndarray, dict]:
    """메인 엔진 연속 실행 → (ts_ns[], mtm[]) + 요약 metrics."""
    default = _sizing.RISK_PER_TRADE
    _sizing.RISK_PER_TRADE = risk
    try:
        conn = get_connection(None)
        state = be.run_backtest(
            conn, pd.Timestamp(START, tz="UTC"), pd.Timestamp(END, tz="UTC"),
            initial_equity=INITIAL,
        )
        conn.close()
        m = be.compute_metrics(state, INITIAL)
    finally:
        _sizing.RISK_PER_TRADE = default
    curve = np.array(state.instr_mtm_curve, dtype="float64")
    ts = curve[:, 0].astype("int64")
    mtm = curve[:, 1] / INITIAL  # normalize to 1.0
    summary = {k: m[k] for k in (
        "total_return_pct", "mdd_pct", "trade_count", "liq_approach_count",
        "profit_factor", "total_fees", "total_funding") if k in m}
    return ts, mtm, summary


def run_swing_mtm(risk: float) -> tuple[np.ndarray, np.ndarray, dict]:
    """스윙 Lane B 미러 → 4h 바 단위 MTM 곡선 (정규화 1.0 시작)."""
    conn = get_connection(None)
    d4 = _load(conn, "4h")
    d1 = _load(conn, "1d")
    conn.close()

    lo = pd.Timestamp(START, tz="UTC")
    hi = pd.Timestamp(END, tz="UTC")

    d1_close_time = d1["open_time"].values + 86_400_000
    t_close = (d4["open_time"] + 4 * 3_600_000).values
    i1 = np.searchsorted(d1_close_time, t_close, side="right") - 1
    d1_up = (d1["ma10"] > d1["ma35"]).values

    o = d4["open"].values; h = d4["high"].values; l = d4["low"].values
    c = d4["close"].values; atr = d4["atr14"].values
    ma10 = d4["ma10"].values; ma35 = d4["ma35"].values
    xup = (ma10 > ma35) & (np.roll(ma10, 1) <= np.roll(ma35, 1))
    xdn = (ma10 < ma35) & (np.roll(ma10, 1) >= np.roll(ma35, 1))
    xup[0] = xdn[0] = False
    ts_ns = d4["dt"].values.astype("int64")
    n = len(d4)

    eq = 1.0
    pos = 0; ep = st = lev = 0.0; ei = -1
    cap_hits = 0; trades = 0
    curve_ts = []; curve_v = []

    for i in range(40, n - 1):
        t = pd.Timestamp(ts_ns[i], tz="UTC")
        in_window = lo <= t < hi
        if pos == 0:
            k = i1[i]
            sig = 0
            if in_window and k >= 35:
                if xup[i] and d1_up[k] and c[i] > ma35[i]:
                    sig = 1
                elif xdn[i] and (not d1_up[k]) and c[i] < ma35[i]:
                    sig = -1
            if sig != 0:
                pos = sig
                ep = o[i + 1]
                st = ep - sig * SWING_STOP_ATR_MULT * atr[i]
                sl_dist_pct = abs(ep - st) / ep
                lev = min(risk / sl_dist_pct, SWING_MAX_LEVERAGE)
                if lev >= SWING_MAX_LEVERAGE:
                    cap_hits += 1
                ei = i + 1
                trades += 1
        else:
            j = i
            if j >= ei:
                exited = False
                if pos == 1 and l[j] <= st:
                    x = st; exited = True
                elif pos == -1 and h[j] >= st:
                    x = st; exited = True
                elif ((pos == 1 and c[j] < ma35[j])
                      or (pos == -1 and c[j] > ma35[j])) and j + 1 < n:
                    x = o[j + 1]; exited = True
                if exited:
                    eq *= (1.0 + lev * (pos * (x - ep) / ep - COST))
                    pos = 0
        # 4h 바 종가 기준 MTM 기록
        if in_window:
            if pos != 0 and i >= ei:
                mtm = eq * (1.0 + lev * pos * (c[i] - ep) / ep)
            else:
                mtm = eq
            curve_ts.append(ts_ns[i])
            curve_v.append(mtm)

    return (np.array(curve_ts, dtype="int64"), np.array(curve_v),
            {"trades": trades, "lev_cap_hits": cap_hits})


def _stats(ts: np.ndarray, v: np.ndarray) -> dict:
    peak = np.maximum.accumulate(v)
    dd = v / peak - 1.0
    imin = int(np.argmin(dd))
    ipeak = int(np.argmax(v[: imin + 1])) if imin > 0 else 0
    yrs = (ts[-1] - ts[0]) / 1e9 / 86400 / 365.25
    cagr = (v[-1] / v[0]) ** (1 / max(yrs, 0.1)) - 1
    mdd = float(dd[imin])
    return {
        "cum_ret_pct": round((v[-1] / v[0] - 1) * 100, 1),
        "cagr_pct": round(cagr * 100, 2),
        "mdd_intra_pct": round(mdd * 100, 1),
        "calmar": round(cagr / abs(mdd), 2) if mdd < 0 else None,
        "worst_dd_peak": str(pd.Timestamp(int(ts[ipeak]), tz="UTC").date()),
        "worst_dd_trough": str(pd.Timestamp(int(ts[imin]), tz="UTC").date()),
    }


def main() -> None:
    out: dict = {"window": f"{START} ~ {END}", "scenarios": {}}
    for label, mr, sr in SCENARIOS:
        print(f"[G-1 joint] {label}: main risk {mr:.3f}, swing risk {sr:.3f} ...")
        m_ts, m_v, m_sum = run_main_mtm(mr)
        s_ts, s_v, s_sum = run_swing_mtm(sr)

        # 4h 그리드(스윙 ts)에 메인 곡선 샘플링 후 수익률 오버레이 합성
        idx = np.searchsorted(m_ts, s_ts, side="right") - 1
        valid = idx >= 0
        g_ts = s_ts[valid]
        mv = m_v[idx[valid]]
        sv = s_v[valid]
        rm = np.diff(mv) / mv[:-1]
        rs = np.diff(sv) / sv[:-1]
        pv = np.concatenate([[1.0], np.cumprod(1.0 + rm + rs)])

        sc = {
            "main_only": {**_stats(m_ts, m_v), **m_sum},
            "swing_only": {**_stats(s_ts, s_v), **s_sum},
            "combined": _stats(g_ts, pv),
            "worst_case_concurrent_risk_pct": round((mr + sr) * 100, 2),
        }
        out["scenarios"][label] = sc
        for lane in ("main_only", "swing_only", "combined"):
            print(f"  {lane:10s}: {sc[lane]}")

    res = Path(__file__).resolve().parents[1] / "backtest" / "results" / "round7_g1_joint_sim.json"
    with open(res, "w") as f:
        json.dump(out, f, indent=2)
    print(f"\nsaved → {res}")


if __name__ == "__main__":
    main()
