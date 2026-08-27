# analysis/round8_l_exposure_ab.py — Phase B B-2: L 계층 노출 배수 A/B
#
# 사전 등록 (tasks/btc_phase_b_design.md §9): 단일 매핑, 스윕 금지.
#   btc_leading(RS60>0):  롱 1.25x / 숏 0.75x
#   lagging(RS60<=0):     롱 0.75x / 숏 1.25x
# 기준선: 1.5x 통합 포트폴리오 (라운드7 G-1 방법론 — closed-trade 합성).
# 기각 조건: 3분리 구간(전체/2022-23/2024-26) 중 1곳이라도 Calmar 악화.
# (거래수는 배수 방식이라 불변 — 무매매 회귀 없음. intra-MDD 는 통과 시
#  라이브 전 확인 항목으로 기록.)
#
# 산출: backtest/results/round8_l_exposure_ab.json
# 실행: ../.venv-bt/bin/python -m analysis.round8_l_exposure_ab
from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np
import pandas as pd
from analysis.round7_portfolio import run_main_lane, run_swing_lane

from collector.store import get_connection

SPOT_DB = Path(__file__).resolve().parents[1] / "state" / "btc_spot.db"
RS_WINDOW = 60
BASE_SCALE = 1.5   # 라운드7 G-1 반영치 (메인 3%/스윙 1.5%)
MULT = {("long", True): 1.25, ("short", True): 0.75,
        ("long", False): 0.75, ("short", False): 1.25}

WINDOWS = [("full", "2022-01-01", "2026-07-22"),
           ("2022_2023", "2022-01-01", "2024-01-01"),
           ("2024_2026", "2024-01-01", "2026-07-22")]


def leading_series() -> tuple[np.ndarray, np.ndarray]:
    conn = sqlite3.connect(SPOT_DB)
    q = ("SELECT open_time, close FROM spot_klines WHERE symbol=? AND "
         "timeframe='1d' AND confirmed=1 ORDER BY open_time")
    b = pd.read_sql_query(q, conn, params=("BTCUSDT",))
    e = pd.read_sql_query(q, conn, params=("ETHUSDT",))
    conn.close()
    m = b.merge(e, on="open_time", suffixes=("_b", "_e"))
    rs = (m["close_b"] / m["close_b"].shift(RS_WINDOW)
          - m["close_e"] / m["close_e"].shift(RS_WINDOW))
    close_time = (m["open_time"] + 86_400_000).values.astype("int64")
    return close_time, (rs > 0).values


def curve_stats(trs: list[dict], lo: str, hi: str, use_mult: bool,
                ct: np.ndarray, lead: np.ndarray) -> dict:
    lo_ts = pd.Timestamp(lo, tz="UTC"); hi_ts = pd.Timestamp(hi, tz="UTC")

    def _tz(t):
        return t.tz_localize("UTC") if t.tzinfo is None else t

    sel = sorted((t for t in trs if lo_ts <= _tz(t["entry_dt"]) < hi_ts),
                 key=lambda t: _tz(t["exit_dt"]))
    eq = 1.0; peak = 1.0; mdd = 0.0
    for t in sel:
        f = t["f"] * BASE_SCALE
        if use_mult:
            ms = int(_tz(t["entry_dt"]).value // 1_000_000)
            k = int(np.searchsorted(ct, ms, side="right")) - 1
            flag = bool(lead[k]) if k >= 0 and not np.isnan(lead[k]) else None
            if flag is not None:
                f *= MULT[(t["side"], flag)]
        eq *= (1.0 + f)
        peak = max(peak, eq)
        mdd = min(mdd, eq / peak - 1.0)
    yrs = (_tz(sel[-1]["exit_dt"]) - _tz(sel[0]["entry_dt"])).days / 365.25
    cagr = eq ** (1 / max(yrs, 0.1)) - 1
    return {"n": len(sel), "cum_ret_pct": round((eq - 1) * 100, 1),
            "cagr_pct": round(cagr * 100, 2),
            "mdd_closed_pct": round(mdd * 100, 1),
            "calmar": round(cagr / abs(mdd), 2) if mdd < 0 else None}


def main() -> None:
    ct, lead = leading_series()
    print("[B-2] 메인 연속 실행 + 스윙 시뮬 ...")
    main_tr = run_main_lane()
    conn = get_connection(None)
    swing_tr = run_swing_lane(conn)
    conn.close()
    trades = main_tr + swing_tr

    out = {"mapping": {f"{k[0]}|{'leading' if k[1] else 'lagging'}": v
                       for k, v in MULT.items()},
           "base_scale": BASE_SCALE, "windows": {}}
    fails = []
    for name, lo, hi in WINDOWS:
        base = curve_stats(trades, lo, hi, False, ct, lead)
        lvar = curve_stats(trades, lo, hi, True, ct, lead)
        out["windows"][name] = {"base_1.5x": base, "L_mult": lvar}
        worse = (lvar["calmar"] or 0) < (base["calmar"] or 0)
        if worse:
            fails.append(name)
        print(f"[{name}] base: {base}")
        print(f"[{name}] L   : {lvar}  calmar {'WORSE' if worse else 'better/eq'}")

    out["failed_windows"] = fails
    out["B2_PASS"] = len(fails) == 0
    res = Path(__file__).resolve().parents[1] / "backtest" / "results" / "round8_l_exposure_ab.json"
    with open(res, "w") as f:
        json.dump(out, f, indent=2)
    print(f"\nB-2 판정: {'PASS' if out['B2_PASS'] else 'FAIL (' + ','.join(fails) + ')'}")
    print(f"saved → {res}")


if __name__ == "__main__":
    main()
