# analysis/round9_funding_si.py — 라운드9: S/I 펀딩 검정
#
# 사전 등록: tasks/btc_round9_funding_si.md.
#   E-F1 통계 유의성 (부트스트랩 CI + 순열검정)
#   E-F2 평활 윈도우 강건성 ({1,3,9,21} 펀딩주기)
#   E-F3 단조성 (펀딩 5분위 롱 평균 R, Spearman < 0 기대)
#   E-F4 임계값 강건성 (부호 vs 3분위)
#
# 가설(역발상): 고펀딩(롱 쏠림) → 롱 진입 불리. PIT: 진입 이전 펀딩만, stale(>1d) 제외.
# 데이터: btc_market.db funding (funding_time ms, rate). 관측 대상: 라운드7 트레이드.
#
# 산출: backtest/results/round9_funding_si.json
# 실행: ../.venv-bt/bin/python -m analysis.round9_funding_si
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

MARKET_DB = Path(__file__).resolve().parents[1] / "state" / "btc_market.db"
WINDOWS = (1, 3, 9, 21)          # 펀딩주기 (8h 단위)
STALE_MS = 86_400_000            # 진입−펀딩 간격 1일 초과면 제외
RNG = np.random.default_rng(20260725)
N_BOOT = 10_000


def load_funding() -> tuple[np.ndarray, np.ndarray]:
    conn = sqlite3.connect(MARKET_DB)
    df = pd.read_sql_query("SELECT funding_time, rate FROM funding ORDER BY funding_time", conn)
    conn.close()
    return df["funding_time"].values.astype("int64"), df["rate"].values.astype("float64")


def trailing_funding(ft: np.ndarray, fr: np.ndarray, entry_ms: int, n: int):
    """진입 이전 마지막 n 개 펀딩의 평균 (PIT). stale/부족 시 None."""
    k = int(np.searchsorted(ft, entry_ms, side="right")) - 1
    if k < 0 or entry_ms - ft[k] > STALE_MS or k + 1 < n:
        return None
    return float(fr[k - n + 1:k + 1].mean())


def label(trades, ft, fr, n):
    rows = []
    for t in trades:
        if t["side"] != "long":
            continue
        e = t["entry_dt"]
        e = e.tz_localize("UTC") if e.tzinfo is None else e
        f = trailing_funding(ft, fr, int(e.value // 1_000_000), n)
        if f is None:
            continue
        rows.append({"r": t["r"], "funding": f})
    return rows


def spread_sign(rows):
    """스프레드 = R(음펀딩) − R(양펀딩)."""
    neg = [r["r"] for r in rows if r["funding"] < 0]
    pos = [r["r"] for r in rows if r["funding"] >= 0]
    if not neg or not pos:
        return None, len(neg), len(pos)
    return float(np.mean(neg) - np.mean(pos)), len(neg), len(pos)


def spread_tercile(rows):
    """스프레드 = R(하위33% 펀딩) − R(상위33% 펀딩)."""
    if len(rows) < 6:
        return None
    f = np.array([r["funding"] for r in rows])
    r = np.array([r["r"] for r in rows])
    lo, hi = np.quantile(f, [1 / 3, 2 / 3])
    low = r[f <= lo]; high = r[f >= hi]
    if not len(low) or not len(high):
        return None
    return float(low.mean() - high.mean())


def _spearman(x, y):
    rx = pd.Series(x).rank().values
    ry = pd.Series(y).rank().values
    if rx.std() == 0 or ry.std() == 0:
        return 0.0
    return float(np.corrcoef(rx, ry)[0, 1])


def main() -> None:
    print("[9] 트레이드 생성 (메인+스윙) ...")
    main_tr = run_main_lane()
    conn = get_connection(None)
    swing_tr = run_swing_lane(conn)
    conn.close()
    trades = main_tr + swing_tr
    ft, fr = load_funding()
    result = {}

    # E-F2 윈도우 강건성 (부호 분할)
    ef2 = {}
    for n in WINDOWS:
        rows = label(trades, ft, fr, n)
        s, nn, np_ = spread_sign(rows)
        ef2[str(n)] = {"spread": None if s is None else round(s, 3),
                       "n_neg": nn, "n_pos": np_,
                       "pass": bool(s is not None and s > 0)}
    result["E_F2_window"] = ef2
    ef2_pass = sum(v["pass"] for v in ef2.values()) >= 3

    # 기준 윈도우 = 9 (3일) — E-F1/F3/F4
    rows = label(trades, ft, fr, 9)
    fv = np.array([r["funding"] for r in rows])
    rv = np.array([r["r"] for r in rows])

    # E-F1 유의성 (부호 분할)
    neg = rv[fv < 0]; pos = rv[fv >= 0]
    obs = float(neg.mean() - pos.mean()) if len(neg) and len(pos) else None
    if obs is not None:
        boot = np.array([RNG.choice(neg, len(neg)).mean() - RNG.choice(pos, len(pos)).mean()
                         for _ in range(N_BOOT)])
        ci = (round(float(np.percentile(boot, 2.5)), 3),
              round(float(np.percentile(boot, 97.5)), 3))
        allr = rv.copy(); nneg = len(neg)
        perm = np.empty(N_BOOT)
        for i in range(N_BOOT):
            idx = RNG.permutation(len(allr))
            perm[i] = allr[idx[:nneg]].mean() - allr[idx[nneg:]].mean()
        pval = float((np.abs(perm) >= abs(obs)).mean())
    else:
        ci, pval = (None, None), None
    result["E_F1_significance"] = {
        "observed_spread": None if obs is None else round(obs, 3),
        "n_neg": int(len(neg)), "n_pos": int(len(pos)),
        "boot_ci95": ci, "ci_lower_gt0": bool(ci[0] is not None and ci[0] > 0),
        "perm_pvalue": None if pval is None else round(pval, 4),
        "significant": bool(pval is not None and pval < 0.05)}
    ef1_pass = ci[0] is not None and ci[0] > 0 and pval is not None and pval < 0.05

    # E-F3 단조성 (펀딩↑ → R↓ 기대, ρ<0)
    q = np.quantile(fv, [0, .2, .4, .6, .8, 1.0])
    buckets = []
    for i in range(5):
        lo, hi = q[i], q[i + 1]
        m = (fv >= lo) & (fv <= hi if i == 4 else fv < hi)
        buckets.append({"q": i + 1, "funding_range": [round(lo, 6), round(hi, 6)],
                        "n": int(m.sum()),
                        "avg_r": round(float(rv[m].mean()), 3) if m.sum() else None})
    rho = _spearman(fv, rv)
    result["E_F3_monotonic"] = {
        "buckets": buckets, "spearman_rho": round(rho, 3),
        "low_funding_gt_high": bool(buckets[0]["avg_r"] is not None
                                    and buckets[-1]["avg_r"] is not None
                                    and buckets[0]["avg_r"] > buckets[-1]["avg_r"]),
        "pass": bool(rho < 0 and buckets[0]["avg_r"] and buckets[-1]["avg_r"]
                     and buckets[0]["avg_r"] > buckets[-1]["avg_r"])}
    ef3_pass = result["E_F3_monotonic"]["pass"]

    # E-F4 임계값 강건성 (부호 vs 3분위)
    st = spread_tercile(rows)
    result["E_F4_threshold"] = {
        "spread_by_sign": None if obs is None else round(obs, 3),
        "spread_by_tercile": None if st is None else round(st, 3),
        "pass": bool(obs is not None and st is not None and (obs > 0) == (st > 0))}
    ef4_pass = result["E_F4_threshold"]["pass"]

    # 종합
    if ef1_pass and ef2_pass and ef3_pass and ef4_pass:
        verdict = "STRONG"
    elif ef1_pass and (ef2_pass or ef4_pass) and rho < 0:
        verdict = "MODERATE"
    else:
        verdict = "WEAK"
    result["verdict"] = verdict
    result["gate_summary"] = {"E_F1": ef1_pass, "E_F2": ef2_pass,
                              "E_F3": ef3_pass, "E_F4": ef4_pass}

    res = Path(__file__).resolve().parents[1] / "backtest" / "results" / "round9_funding_si.json"
    with open(res, "w") as f:
        json.dump(result, f, indent=2)
    print(json.dumps(result, indent=2, ensure_ascii=False))
    print(f"\n종합 판정: {verdict}")
    print(f"saved → {res}")


if __name__ == "__main__":
    main()
