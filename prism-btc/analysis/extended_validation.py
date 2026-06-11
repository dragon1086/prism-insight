# analysis/extended_validation.py — 표본 확장 검증 (Rocky 요청)
# ① 2020.7~2026.6 풀스팬 단일런 (진짜 6년 복리 equity curve + MDD)
# ② 분기별 워크포워드 (전수 분포 — 좋은 분기/나쁜 분기 비율)
# ③ 몬테카를로 부트스트랩 (트레이드 R 재추출 → CAGR/MDD 신뢰구간, risk별)
# 주의: 1w TF는 2021-03 이전 워밍업 부족(MIN_ROWS=50) → 2020~21Q1은 게이트가
# 6TF 중 5TF로 동작(보수적). 파라미터 변경 없음 — 검증 전용.
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import logging

logging.disable(logging.WARNING)

import numpy as np
import pandas as pd

import engine.sizing as sz
from backtest.engine import run_backtest, compute_metrics
from collector.store import get_connection


def run_span(conn, s, e, risk=0.02):
    sz.RISK_PER_TRADE = risk
    st = run_backtest(conn, pd.Timestamp(s, tz="UTC"), pd.Timestamp(e, tz="UTC"),
                      initial_equity=10_000.0)
    return st, compute_metrics(st, 10_000.0)


def main():
    conn = get_connection(None)

    # ① 풀스팬 (risk 2%)
    st, m = run_span(conn, "2020-07-01", "2026-06-09", 0.02)
    rs = [t.r_multiple for t in st.trade_logs]
    yrs = 5.94
    print(f"[풀스팬 2020.7~2026.6, risk 2%] n={m['trade_count']} "
          f"ret={m['total_return_pct']:+.1f}% CAGR={100*((1+m['total_return_pct']/100)**(1/yrs)-1):.1f}% "
          f"MDD={m['mdd_pct']:.1f}% PF={m['profit_factor']:.2f} win={m['win_rate_pct']:.0f}% "
          f"liq={m['liq_approach_count']}")

    # ② 분기별 워크포워드
    qs = pd.date_range("2020-07-01", "2026-04-01", freq="QS")
    rows = []
    for q0 in qs:
        q1 = min(q0 + pd.offsets.QuarterBegin(1), pd.Timestamp("2026-06-09"))
        _, qm = run_span(conn, str(q0.date()), str(q1.date()), 0.02)
        rows.append((f"{q0.year}Q{q0.quarter}", qm["total_return_pct"], qm["trade_count"]))
    rets = np.array([r[1] for r in rows])
    print(f"\n[분기 워크포워드 {len(rows)}개, risk 2%]")
    print("  " + " ".join(f"{n}:{r:+.1f}" for n, r, _ in rows))
    print(f"  양수 분기: {sum(rets>0)}/{len(rets)} ({100*np.mean(rets>0):.0f}%) "
          f"평균 {rets.mean():+.2f}% 중앙값 {np.median(rets):+.2f}% "
          f"최악 {rets.min():+.2f}% 최고 {rets.max():+.2f}%")

    # ③ 몬테카를로 부트스트랩 (R 시퀀스 재추출 — 5000회)
    print(f"\n[몬테카를로: n={len(rs)} trades, 5000 resamples, 1년 단위]")
    rng = np.random.default_rng(42)
    n_per_year = int(round(len(rs) / yrs))
    R = np.array(rs)
    for risk in (0.02, 0.04, 0.06):
        cagr_s, mdd_s = [], []
        for _ in range(5000):
            seq = rng.choice(R, size=n_per_year, replace=True)
            eq = np.cumprod(1 + risk * seq)
            peak = np.maximum.accumulate(eq)
            mdd_s.append(((eq / peak) - 1).min() * 100)
            cagr_s.append((eq[-1] - 1) * 100)
        c, d = np.array(cagr_s), np.array(mdd_s)
        print(f"  risk {risk:.0%}: 연수익 5%/50%/95%={np.percentile(c,5):+.1f}/{np.percentile(c,50):+.1f}/{np.percentile(c,95):+.1f}% "
              f"| MDD중앙값={np.median(d):.1f}% P(MDD>20%)={100*np.mean(d<-20):.1f}% "
              f"P(연손실)={100*np.mean(c<0):.1f}%")
    conn.close()


if __name__ == "__main__":
    main()
