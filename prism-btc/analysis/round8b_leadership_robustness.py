# analysis/round8b_leadership_robustness.py — 라운드8b: L계층 강건성 실험
#
# 사전 등록: tasks/btc_round8b_leadership_robustness.md.
#   E-A 대조군 강건성 (ETH / ALT_BASKET / TOTAL_EX_BTC)
#   E-B RS 윈도우 강건성 ({30,45,60,90})
#   E-C 통계 유의성 (부트스트랩 CI + 순열검정)
#   E-D 단조성 (RS 분위수별 평균 R + Spearman)
#
# 관측 대상: 라운드7 메인 연속실행 + 스윙 시뮬 트레이드 (사이징 미개입, 라벨만).
# PIT: 진입 시각 이전 완결 1d 봉만 사용.
#
# 산출: backtest/results/round8b_robustness.json
# 실행: ../.venv-bt/bin/python -m analysis.round8b_leadership_robustness
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
BASKET = ("ETHUSDT", "BNBUSDT", "XRPUSDT", "ADAUSDT", "SOLUSDT", "DOGEUSDT", "LTCUSDT")
WINDOWS = (30, 45, 60, 90)
RNG = np.random.default_rng(20260725)
N_BOOT = 10_000


def load_closes() -> dict[str, pd.DataFrame]:
    conn = sqlite3.connect(SPOT_DB)
    out = {}
    syms = ("BTCUSDT",) + BASKET
    for s in syms:
        df = pd.read_sql_query(
            "SELECT open_time, close FROM spot_klines WHERE symbol=? AND "
            "timeframe='1d' AND confirmed=1 ORDER BY open_time", conn, params=(s,))
        df["close_time"] = df["open_time"] + 86_400_000
        out[s] = df
    conn.close()
    return out


def build_rs(closes: dict, benchmark: str, window: int) -> tuple[np.ndarray, np.ndarray]:
    """(close_time_ms[], rs[]) — BTC window수익 − 대조군 window수익. PIT.

    benchmark: 'ETHUSDT' | 'ALT_BASKET' | 'TOTAL_EX_BTC'.
    ALT_BASKET/TOTAL_EX_BTC 는 동일가중 로그수익 평균 (해당 시점 상장분만).
    """
    btc = closes["BTCUSDT"].set_index("close_time")["close"]
    btc_ret = btc / btc.shift(window) - 1.0

    if benchmark == "ETHUSDT":
        eth = closes["ETHUSDT"].set_index("close_time")["close"]
        bench_ret = eth / eth.shift(window) - 1.0
    else:
        # 동일가중: 각 코인 window 수익률의 평균 (그 시점 데이터 있는 것만)
        rets = []
        for s in BASKET:
            c = closes[s].set_index("close_time")["close"]
            rets.append((c / c.shift(window) - 1.0).rename(s))
        mat = pd.concat(rets, axis=1)
        bench_ret = mat.mean(axis=1, skipna=True)

    df = pd.concat([btc_ret.rename("b"), bench_ret.rename("m")], axis=1).dropna()
    rs = (df["b"] - df["m"]).values
    return df.index.values.astype("int64"), rs


def label(trades: list, ct: np.ndarray, rs: np.ndarray) -> list[dict]:
    rows = []
    for t in trades:
        e = t["entry_dt"]
        e = e.tz_localize("UTC") if e.tzinfo is None else e
        ms = int(e.value // 1_000_000)
        k = int(np.searchsorted(ct, ms, side="right")) - 1
        if k < 0:
            continue
        rows.append({"side": t["side"], "r": t["r"], "rs": float(rs[k]),
                     "leading": bool(rs[k] > 0), "year": e.year})
    return rows


def long_spread(rows: list) -> tuple[float | None, int, int]:
    a = [r["r"] for r in rows if r["side"] == "long" and r["leading"]]
    b = [r["r"] for r in rows if r["side"] == "long" and not r["leading"]]
    if not a or not b:
        return None, len(a), len(b)
    return float(np.mean(a) - np.mean(b)), len(a), len(b)


def main() -> None:
    print("[8b] 트레이드 생성 (메인+스윙) ...")
    main_tr = run_main_lane()
    conn = get_connection(None)
    swing_tr = run_swing_lane(conn)
    conn.close()
    trades = main_tr + swing_tr
    closes = load_closes()
    result = {}

    # E-A 대조군 강건성 (윈도우 60 고정)
    ea = {}
    for bench in ("ETHUSDT", "ALT_BASKET", "TOTAL_EX_BTC"):
        ct, rs = build_rs(closes, bench, 60)
        rows = label(trades, ct, rs)
        s_all, na, nb = long_spread(rows)
        h1, _, _ = long_spread([r for r in rows if r["year"] <= 2023])
        h2, _, _ = long_spread([r for r in rows if r["year"] >= 2024])
        ea[bench] = {"spread": None if s_all is None else round(s_all, 3),
                     "n_lead": na, "n_lag": nb,
                     "half_2022_2023": None if h1 is None else round(h1, 3),
                     "half_2024_2026": None if h2 is None else round(h2, 3),
                     "pass": bool(s_all and s_all > 0 and h1 and h2 and h1 > 0 and h2 > 0)}
    result["E_A_benchmark"] = ea
    ea_pass = all(v["pass"] for v in ea.values())

    # E-B RS 윈도우 강건성 (ETH 고정)
    eb = {}
    for w in WINDOWS:
        ct, rs = build_rs(closes, "ETHUSDT", w)
        rows = label(trades, ct, rs)
        s, na, nb = long_spread(rows)
        eb[str(w)] = {"spread": None if s is None else round(s, 3),
                      "n_lead": na, "n_lag": nb,
                      "pass": bool(s is not None and s >= 0.3)}
    result["E_B_window"] = eb
    eb_pass = sum(v["pass"] for v in eb.values()) >= 3

    # E-C 통계 유의성 (ETH, 60)
    ct, rs = build_rs(closes, "ETHUSDT", 60)
    rows = label(trades, ct, rs)
    long_rows = [r for r in rows if r["side"] == "long"]
    a = np.array([r["r"] for r in long_rows if r["leading"]])
    b = np.array([r["r"] for r in long_rows if not r["leading"]])
    obs = float(a.mean() - b.mean())
    # 부트스트랩 CI
    boot = np.array([RNG.choice(a, len(a)).mean() - RNG.choice(b, len(b)).mean()
                     for _ in range(N_BOOT)])
    ci = (round(float(np.percentile(boot, 2.5)), 3),
          round(float(np.percentile(boot, 97.5)), 3))
    # 순열검정: leading 라벨 셔플
    allr = np.array([r["r"] for r in long_rows])
    nlead = len(a)
    perm = np.empty(N_BOOT)
    for i in range(N_BOOT):
        idx = RNG.permutation(len(allr))
        perm[i] = allr[idx[:nlead]].mean() - allr[idx[nlead:]].mean()
    pval = float((np.abs(perm) >= abs(obs)).mean())
    result["E_C_significance"] = {
        "observed_spread": round(obs, 3), "n_lead": len(a), "n_lag": len(b),
        "boot_ci95": ci, "ci_lower_gt0": bool(ci[0] > 0),
        "perm_pvalue": round(pval, 4), "significant": bool(pval < 0.05)}
    ec_pass = ci[0] > 0 and pval < 0.05

    # E-D 단조성 (ETH, 60): RS 분위수별 롱 평균 R + Spearman
    rsv = np.array([r["rs"] for r in long_rows])
    rv = np.array([r["r"] for r in long_rows])
    q = np.quantile(rsv, [0, .2, .4, .6, .8, 1.0])
    buckets = []
    for i in range(5):
        lo, hi = q[i], q[i + 1]
        m = (rsv >= lo) & (rsv <= hi if i == 4 else rsv < hi)
        buckets.append({"q": i + 1, "rs_range": [round(lo, 3), round(hi, 3)],
                        "n": int(m.sum()),
                        "avg_r": round(float(rv[m].mean()), 3) if m.sum() else None})
    # Spearman (순위상관) — 순수 numpy (rank 후 Pearson), scipy 불필요
    def _spearman(x, y):
        rx = pd.Series(x).rank().values
        ry = pd.Series(y).rank().values
        if rx.std() == 0 or ry.std() == 0:
            return 0.0
        return float(np.corrcoef(rx, ry)[0, 1])
    rho = _spearman(rsv, rv)
    result["E_D_monotonic"] = {
        "buckets": buckets, "spearman_rho": round(float(rho), 3),
        "top_gt_bottom": bool(buckets[-1]["avg_r"] is not None
                              and buckets[0]["avg_r"] is not None
                              and buckets[-1]["avg_r"] > buckets[0]["avg_r"]),
        "pass": bool(rho > 0 and buckets[-1]["avg_r"] and buckets[0]["avg_r"]
                     and buckets[-1]["avg_r"] > buckets[0]["avg_r"])}
    ed_pass = result["E_D_monotonic"]["pass"]

    # 종합 판정
    if ea_pass and eb_pass and ec_pass and ed_pass:
        verdict = "STRONG"
    elif ec_pass and (ea_pass or eb_pass) and result["E_D_monotonic"]["spearman_rho"] > 0:
        verdict = "MODERATE"
    else:
        verdict = "WEAK"
    result["verdict"] = verdict
    result["gate_summary"] = {"E_A": ea_pass, "E_B": eb_pass,
                              "E_C": ec_pass, "E_D": ed_pass}

    res = Path(__file__).resolve().parents[1] / "backtest" / "results" / "round8b_robustness.json"
    with open(res, "w") as f:
        json.dump(result, f, indent=2)
    print(json.dumps(result, indent=2, ensure_ascii=False))
    print(f"\n종합 판정: {verdict}")
    print(f"saved → {res}")


if __name__ == "__main__":
    main()
