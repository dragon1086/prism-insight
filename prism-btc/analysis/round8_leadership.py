# analysis/round8_leadership.py — Phase B B-3: L 계층(BTC/ETH 상대강도) 관측
#
# 정의 (tasks/btc_phase_b_design.md §5, 사전 등록):
#   RS(t) = BTC 60일 수익률 − ETH 60일 수익률 (spot 종가, 완결 1d 봉)
#   btc_leading(t) = RS(t) > 0
# 관측: 트레이드 진입 시점(직전 완결 1d 봉 기준, PIT)의 btc_leading 조건부 성과.
# 통과 기준 (B-1 동형): 롱 트레이드 leading vs not 평균 R 차 ≥ +0.3R,
# 2022-23 / 2024-26 두 반쪽 부호 일관, leading 표본 n ≥ 15.
#
# 산출: backtest/results/round8_leadership_observation.json
# 실행: ../.venv-bt/bin/python -m analysis.round8_leadership
from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np
import pandas as pd

from collector.store import get_connection
from analysis.round7_portfolio import run_main_lane, run_swing_lane

SPOT_DB = Path(__file__).resolve().parents[1] / "state" / "btc_spot.db"
RS_WINDOW = 60


def load_close(symbol: str) -> pd.DataFrame:
    conn = sqlite3.connect(SPOT_DB)
    df = pd.read_sql_query(
        "SELECT open_time, close FROM spot_klines "
        "WHERE symbol=? AND timeframe='1d' AND confirmed=1 ORDER BY open_time",
        conn, params=(symbol,))
    conn.close()
    df["close_time"] = df["open_time"] + 86_400_000
    return df


def main() -> None:
    btc = load_close("BTCUSDT")
    eth = load_close("ETHUSDT")
    m = btc.merge(eth, on="close_time", suffixes=("_btc", "_eth"))
    rb = m["close_btc"] / m["close_btc"].shift(RS_WINDOW) - 1
    re = m["close_eth"] / m["close_eth"].shift(RS_WINDOW) - 1
    m["rs"] = rb - re
    m["leading"] = m["rs"] > 0
    m = m.dropna(subset=["rs"]).reset_index(drop=True)

    print("[관측] 메인 연속 실행 + 스윙 시뮬 ...")
    main_tr = run_main_lane()
    conn = get_connection(None)
    swing_tr = run_swing_lane(conn)
    conn.close()

    close_times = m["close_time"].values
    leading = m["leading"].values
    rows = []
    for lane, trs in (("main", main_tr), ("swing", swing_tr)):
        for t in trs:
            e = t["entry_dt"]
            e = e.tz_localize("UTC") if e.tzinfo is None else e
            k = int(np.searchsorted(close_times, int(e.value // 1_000_000), side="right")) - 1
            if k < 0:
                continue
            rows.append({"lane": lane, "side": t["side"], "r": t["r"],
                         "leading": bool(leading[k]), "year": e.year})

    def agg(sub):
        out = {}
        for side in ("long", "short"):
            for flag in (True, False):
                sel = [r["r"] for r in sub if r["side"] == side and r["leading"] == flag]
                if sel:
                    out[f"{side}|{'leading' if flag else 'lagging'}"] = {
                        "n": len(sel), "avg_r": round(float(np.mean(sel)), 3),
                        "win_pct": round(100 * float(np.mean([x > 0 for x in sel])), 1),
                        "sum_r": round(float(np.sum(sel)), 1)}
        return out

    def spread(sub):
        a = [r["r"] for r in sub if r["side"] == "long" and r["leading"]]
        b = [r["r"] for r in sub if r["side"] == "long" and not r["leading"]]
        if a and b:
            return round(float(np.mean(a) - np.mean(b)), 3), len(a), len(b)
        return None, len(a), len(b)

    result = {"leading_day_share_pct": round(100 * float(m["leading"].mean()), 1),
              "all": agg(rows)}
    verdicts = {}
    for name, sub in (("all", rows),
                      ("2022_2023", [r for r in rows if r["year"] <= 2023]),
                      ("2024_2026", [r for r in rows if r["year"] >= 2024])):
        s, na, nb = spread(sub)
        verdicts[name] = {"spread": s, "n_leading": na, "n_lagging": nb}
    result["verdict_inputs"] = verdicts
    s_all = verdicts["all"]["spread"]
    h1, h2 = verdicts["2022_2023"]["spread"], verdicts["2024_2026"]["spread"]
    passed = (s_all is not None and s_all >= 0.3
              and h1 is not None and h2 is not None and h1 > 0 and h2 > 0
              and verdicts["all"]["n_leading"] >= 15)
    result["L_LAYER_PROCEED"] = bool(passed)

    res = Path(__file__).resolve().parents[1] / "backtest" / "results" / "round8_leadership_observation.json"
    with open(res, "w") as f:
        json.dump(result, f, indent=2)
    print(json.dumps(result, indent=2))
    print(f"\nL 계층 판정: {'PASS' if passed else 'FAIL'}")
    print(f"saved → {res}")


if __name__ == "__main__":
    main()
