# analysis/round6_swing_lane.py — 라운드6: 스윙 레인 후보 백테스트
#
# 배경 (2026-07-21, Rocky): "너무 보수적이다. 횡보에선 스윙도 먹고, 7월 같은
# 추세 초입에서도 먹을 수 있는 전략을 테스트해라."
# 현행 시스템(|score|>=70 & ts_4h>=2.0)은 성숙 추세 전용 — 횡보 스윙과
# 추세 초입은 구조적으로 미커버. 별도 소형 레인 3후보를 고정 파라미터로 검증.
#
#   Lane A: 횡보 평균회귀 (ts_4h<2 & ts_1d<2 에서 z=±1.5 진입, MA35 회귀 청산)
#   Lane B: 추세 초입 (4h MA10/35 크로스 + 완결 1d봉 방향 일치, MA35 이탈 청산)
#   Lane C: 돈치안 20/10 벤치마크
#
# 원칙: 파라미터 스윕 금지(각 레인 1세트 고정), 수수료+슬리피지 왕복 0.15%,
# 신호=확정봉 / 체결=다음 봉 시가, 스탑은 봉내 촉발 시 스탑가 체결.
# 기간 분리: 2020-21 / 2022-23 / 2024-26.
#
# 결과 (2020-03~2026-07, tasks/btc_round6_swing_lane.md):
#   A: n=671, cum -88% — 전 기간 손실. 기각.
#   B: n=186, cum +293%, maxDD -35%, 3기간 모두 양수. 채택 후보.
#   C: n=363, cum +76%, 2024-26 음수. 기각 (B 열위).
#
# 실행 (prism-btc 패키지 루트에서):
#   python -m analysis.round6_swing_lane [--db state/btc_market.db]
from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np
import pandas as pd

from engine.indicators import add_indicators

COST = 0.0015  # 왕복 수수료+슬리피지 (Bybit taker 0.055%x2 + slippage)


def load(conn: sqlite3.Connection, tf: str) -> pd.DataFrame:
    df = pd.read_sql_query(
        "SELECT open_time, open, high, low, close, volume, turnover FROM klines "
        "WHERE timeframe=? AND confirmed=1 ORDER BY open_time ASC",
        conn, params=(tf,))
    df = add_indicators(df)
    # 환경 가드 (라운드5와 동일): pandas rolling 버그 조기 실패
    if len(df) >= 35 and df["ma10"].isna().all():
        raise RuntimeError(
            f"indicator computation broken in this environment ({tf}) — "
            f"pandas rolling bug. Run on .venv-bt / db-server.")
    df["dt"] = pd.to_datetime(df["open_time"], unit="ms", utc=True)
    return df.dropna(subset=["ma10", "ma35", "atr14"]).reset_index(drop=True)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default="state/btc_market.db")
    parser.add_argument("--csv-dir", default=None, help="트레이드 CSV 저장 경로")
    args = parser.parse_args()

    conn = sqlite3.connect(args.db)
    d4 = load(conn, "4h")
    d1 = load(conn, "1d")
    conn.close()

    d4["ts4"] = (d4["ma10"] - d4["ma35"]).abs() / d4["atr14"]
    d4["z"] = (d4["close"] - d4["ma35"]) / d4["atr14"]
    d4["cross_up"] = (d4["ma10"] > d4["ma35"]) & (d4["ma10"].shift(1) <= d4["ma35"].shift(1))
    d4["cross_dn"] = (d4["ma10"] < d4["ma35"]) & (d4["ma10"].shift(1) >= d4["ma35"].shift(1))
    d4["hh20"] = d4["high"].rolling(20).max().shift(1)
    d4["ll20"] = d4["low"].rolling(20).min().shift(1)
    d4["hh10"] = d4["high"].rolling(10).max().shift(1)
    d4["ll10"] = d4["low"].rolling(10).min().shift(1)

    # 각 4h 확정봉 시점의 "완결된" 1d 봉 매핑 (미래정보 차단)
    d1_close_time = d1["open_time"].values + 86_400_000
    d4["t_close"] = d4["open_time"] + 4 * 3_600_000
    i1 = np.searchsorted(d1_close_time, d4["t_close"].values, side="right") - 1
    d1_up = (d1["ma10"] > d1["ma35"]).values
    d1_ts = ((d1["ma10"] - d1["ma35"]).abs() / d1["atr14"]).values

    o = d4["open"].values; h = d4["high"].values; l = d4["low"].values; c = d4["close"].values
    atr = d4["atr14"].values; ma35 = d4["ma35"].values
    ts4 = d4["ts4"].values; z = d4["z"].values
    xup = d4["cross_up"].values; xdn = d4["cross_dn"].values
    hh20 = d4["hh20"].values; ll20 = d4["ll20"].values
    hh10 = d4["hh10"].values; ll10 = d4["ll10"].values
    dts = d4["dt"].values
    n = len(d4)

    def run_lane(entry_fn, exit_fn, stop_mult, label, time_stop=None):
        trades = []
        pos = 0
        ep = st = 0.0
        ei = -1
        for i in range(40, n - 1):
            if pos == 0:
                sig = entry_fn(i)
                if sig != 0:
                    pos = sig
                    ep = o[i + 1]
                    st = ep - sig * stop_mult * atr[i]
                    ei = i + 1
            else:
                j = i
                if j >= ei:
                    if pos == 1 and l[j] <= st:
                        trades.append((ei, j, pos, ep, st)); pos = 0; continue
                    if pos == -1 and h[j] >= st:
                        trades.append((ei, j, pos, ep, st)); pos = 0; continue
                    if exit_fn(j, pos) or (time_stop and j - ei >= time_stop):
                        if j + 1 < n:
                            trades.append((ei, j + 1, pos, ep, o[j + 1])); pos = 0
        rows = []
        for (a, b, p, e, x) in trades:
            rows.append({"lane": label, "entry_dt": dts[a], "exit_dt": dts[b],
                         "side": "L" if p == 1 else "S", "entry": e, "exit": x,
                         "ret": p * (x - e) / e - COST, "bars": b - a})
        return pd.DataFrame(rows)

    # Lane A: 횡보 평균회귀
    def entryA(i):
        k = i1[i]
        if k < 35 or ts4[i] >= 2.0 or d1_ts[k] >= 2.0:
            return 0
        if z[i] <= -1.5:
            return 1
        if z[i] >= 1.5:
            return -1
        return 0

    def exitA(j, pos):
        return (pos == 1 and c[j] >= ma35[j]) or (pos == -1 and c[j] <= ma35[j])

    # Lane B: 추세 초입
    def entryB(i):
        k = i1[i]
        if k < 35:
            return 0
        if xup[i] and d1_up[k] and c[i] > ma35[i]:
            return 1
        if xdn[i] and (not d1_up[k]) and c[i] < ma35[i]:
            return -1
        return 0

    def exitB(j, pos):
        return (pos == 1 and c[j] < ma35[j]) or (pos == -1 and c[j] > ma35[j])

    # Lane C: 돈치안 20/10
    def entryC(i):
        if c[i] > hh20[i]:
            return 1
        if c[i] < ll20[i]:
            return -1
        return 0

    def exitC(j, pos):
        return (pos == 1 and c[j] < ll10[j]) or (pos == -1 and c[j] > hh10[j])

    lanes = [
        (run_lane(entryA, exitA, 1.5, "A_chop_swing", time_stop=30), "Lane A 횡보스윙"),
        (run_lane(entryB, exitB, 2.0, "B_early_trend"), "Lane B 추세초입"),
        (run_lane(entryC, exitC, 2.0, "C_donchian"), "Lane C 돈치안"),
    ]

    def stats(t, label):
        if len(t) == 0:
            print(f"{label:20s} n=0")
            return
        r = t["ret"]
        eq = (1 + r).cumprod()
        dd = (eq / eq.cummax() - 1).min()
        yrs = (t["exit_dt"].iloc[-1] - t["entry_dt"].iloc[0]).days / 365.25
        print(f"{label:20s} n={len(t):4d}  win={100 * (r > 0).mean():4.1f}%  "
              f"avg={100 * r.mean():+5.2f}%  sum={100 * r.sum():+7.1f}%  "
              f"cum={100 * (eq.iloc[-1] - 1):+8.1f}%  maxDD={100 * dd:5.1f}%  "
              f"trades/yr={len(t) / max(yrs, 0.1):.0f}")

    for t, lab in lanes:
        print(f"\n[{lab}]")
        stats(t, "전체")
        for lo, hi in [("2020", "2022"), ("2022", "2024"), ("2024", "2027")]:
            stats(t[(t["entry_dt"] >= lo) & (t["entry_dt"] < hi)], f"  {lo}~")
        for side in ["L", "S"]:
            stats(t[t["side"] == side], f"  side={side}")
        if args.csv_dir:
            t.to_csv(Path(args.csv_dir) / f"round6_{t['lane'].iloc[0]}.csv", index=False)


if __name__ == "__main__":
    main()
