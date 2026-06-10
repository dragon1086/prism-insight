#!/usr/bin/env python3
"""
Regime classifier backtest (deterministic).
=================================================================
전체 매매로직(LLM 에이전트)은 백테스트 불가하지만, regime 분류기는 결정론적이라
과거 지수에 '그날 운영이 계산했을' 방식으로 재생(replay)할 수 있다.

방법: 각 거래일 t 에 대해 직전 ~1년 윈도우를 잘라 _compute_*_regime 에 넣고 라벨을 기록.
  - NEW  = 50/200(US)·60/120(KR) 추세템플릿 (윈도우 252일 → 200MA 사용)
  - LEGACY = 20MA-only (윈도우 30일 → 함수의 ma_200=None 레거시 분기 강제)
이 둘을 비교하면 이번 변경이 '약세장 반등을 strong_bull로 오판'하던 걸 얼마나 줄였는지
정량화된다.

데이터: yfinance (^GSPC, ^VIX, ^KS11). 외부 LLM/거래 무관.
실행: cd /root/prism-insight && python tools/regime_backtest.py [--years 6]
"""
import sys
import os
import argparse
from collections import Counter, defaultdict

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "prism-us"))  # US cores 우선
sys.path.insert(0, ROOT)

import pandas as pd  # noqa: E402

WIN_NEW = 252      # ~1y → 200/120 MA 가능
WIN_LEGACY = 30    # 짧게 → ma_200/ma_120 = None → 레거시 분기


def _yf_download(ticker, years):
    import yfinance as yf
    df = yf.download(ticker, period=f"{years}y", interval="1d",
                     auto_adjust=True, progress=False)
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    return df.dropna(how="all")


def _dist(labels):
    n = len(labels) or 1
    c = Counter(labels)
    return {k: round(100.0 * v / n, 1) for k, v in c.most_common()}


def _whipsaw(seq):
    return sum(1 for i in range(1, len(seq)) if seq[i] != seq[i - 1])


def _yearly(dates, labels):
    bull = {"parabolic", "strong_bull", "moderate_bull"}
    out = defaultdict(lambda: Counter())
    for d, lab in zip(dates, labels):
        y = d.year
        grp = "bull" if lab in bull else ("bear" if "bear" in lab else "sideways")
        out[y][grp] += 1
    rows = []
    for y in sorted(out):
        t = sum(out[y].values()) or 1
        rows.append((y, round(100 * out[y]["bull"] / t), round(100 * out[y]["sideways"] / t),
                     round(100 * out[y]["bear"] / t)))
    return rows


def _purge_cores():
    for m in [k for k in list(sys.modules) if k == "cores" or k.startswith("cores.")]:
        del sys.modules[m]


def backtest_us(years):
    # prism-us/cores 가 root/cores 를 shadow 하도록 정리 후 import
    _purge_cores()
    sys.path.insert(0, os.path.join(ROOT, "prism-us"))
    from cores.data_prefetch import _compute_us_regime
    gspc = _yf_download("^GSPC", years)
    vix = _yf_download("^VIX", years).reindex(gspc.index).ffill()
    dates, new_lab, leg_lab, dist = [], [], [], []
    for i in range(WIN_NEW, len(gspc)):
        w = gspc.iloc[: i + 1]
        vw = vix.iloc[: i + 1]
        res = _compute_us_regime(w.tail(WIN_NEW), None, vw.tail(WIN_NEW))
        leg = _compute_us_regime(w.tail(WIN_LEGACY), None, vw.tail(WIN_LEGACY))["market_regime"]
        dates.append(gspc.index[i])
        new_lab.append(res["market_regime"])
        leg_lab.append(leg)
        dist.append(res.get("index_summary", {}).get("distribution_days"))
    return dates, new_lab, leg_lab, dist


def backtest_kr(years):
    # root/cores 가 우선되도록 정리 후 import
    _purge_cores()
    sys.path.insert(0, ROOT)
    from cores.data_prefetch import _compute_kr_regime
    ks = _yf_download("^KS11", years)
    recs = {idx.strftime("%Y%m%d"): {"Open": float(r.Open), "High": float(r.High),
            "Low": float(r.Low), "Close": float(r.Close), "Volume": float(r.Volume)}
            for idx, r in ks.iterrows()}
    keys = list(recs.keys())
    dates, new_lab, leg_lab, dist = [], [], [], []
    for i in range(WIN_NEW, len(keys)):
        win = {k: recs[k] for k in keys[max(0, i + 1 - WIN_NEW): i + 1]}
        winL = {k: recs[k] for k in keys[max(0, i + 1 - WIN_LEGACY): i + 1]}
        res = _compute_kr_regime(win)
        new_lab.append(res["market_regime"])
        leg_lab.append(_compute_kr_regime(winL)["market_regime"])
        dist.append(res.get("index_summary", {}).get("distribution_days"))
        dates.append(ks.index[i])
    return dates, new_lab, leg_lab, dist


def _dist_yearly_max(dates, dist):
    """연도별 분산일 카운트 최대/평균 (None 제외)."""
    by = defaultdict(list)
    for d, c in zip(dates, dist or []):
        if c is not None:
            by[d.year].append(c)
    rows = []
    for y in sorted(by):
        vals = by[y]
        rows.append((y, max(vals), round(sum(vals) / len(vals), 1)))
    return rows


def report(market, dates, new_lab, leg_lab, dist=None):
    print(f"\n========== {market} (n={len(dates)} days, {dates[0].date()}~{dates[-1].date()}) ==========")
    print(f"NEW (50/200) distribution : {_dist(new_lab)}")
    print(f"LEGACY (20MA) distribution: {_dist(leg_lab)}")
    print(f"Whipsaw (regime changes)  : NEW {_whipsaw(new_lab)} | LEGACY {_whipsaw(leg_lab)}")
    # bear-rally 오판 정량화: legacy=strong_bull 인데 new!=strong_bull 인 일수
    flips = sum(1 for n, l in zip(new_lab, leg_lab) if l == "strong_bull" and n != "strong_bull")
    sb_new = sum(1 for x in new_lab if x == "strong_bull")
    sb_leg = sum(1 for x in leg_lab if x == "strong_bull")
    print(f"strong_bull days          : NEW {sb_new} | LEGACY {sb_leg}  "
          f"(legacy→non-strong reclassified: {flips})")
    print("Yearly bull/sideways/bear % (NEW):")
    for y, b, s, br in _yearly(dates, new_lab):
        print(f"  {y}: bull {b}% | sideways {s}% | bear {br}%")
    # 분산일 카운트 재생 (O'Neil): 천장/급락 직전 급증해야 정상
    if dist and any(c is not None for c in dist):
        thr = {"US": 6, "KR": 6}.get(market, 6)
        print(f"Distribution-day count (window=25, demote≥{thr}) — yearly max | avg:")
        for y, mx, av in _dist_yearly_max(dates, dist):
            flag = "  <== tops/distribution" if mx >= thr else ""
            print(f"  {y}: max {mx} | avg {av}{flag}")
        trig = sum(1 for c in dist if c is not None and c >= thr)
        print(f"Days at/over demote threshold: {trig} / {sum(1 for c in dist if c is not None)}")
    # spot checks
    spots = {"US": ["2020-03-23", "2022-06-16", "2024-02-15", "2025-04-07"],
             "KR": ["2020-03-19", "2022-09-30", "2024-07-11", "2025-04-09"]}.get(market, [])
    dmap = {d.strftime("%Y-%m-%d"): lab for d, lab in zip(dates, new_lab)}
    cmap = {d.strftime("%Y-%m-%d"): c for d, c in zip(dates, dist or [])}
    sc = []
    for s in spots:
        near = next((k for k in sorted(dmap) if k >= s), None)
        lab = dmap.get(near)
        dd = cmap.get(near)
        sc.append(f"{s}->{lab}(dd={dd})")
    print("Spot checks (NEW, regime+distribution_days):", " | ".join(sc))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--years", type=int, default=6)
    ap.add_argument("--market", choices=["us", "kr", "both"], default="both")
    a = ap.parse_args()
    if a.market in ("us", "both"):
        try:
            report("US", *backtest_us(a.years))
        except Exception as e:
            print(f"US backtest failed: {e}")
    if a.market in ("kr", "both"):
        try:
            report("KR", *backtest_kr(a.years))
        except Exception as e:
            print(f"KR backtest failed: {e}")


if __name__ == "__main__":
    main()
