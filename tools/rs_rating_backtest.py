#!/usr/bin/env python3
"""O'Neil RS Rating backtest — does the IBD-style multi-month RS Rating beat the
current 60-trading-day return proxy (#289)? Run KR and US separately.

This is a VALIDATION tool, not production code. It answers one question before we
touch screening/report/prompt: is a longer, weighted, percentile-ranked RS
materially better than the single-window 60d return we use today?

Strategies compared (monthly rebalance, equal-weight top-N, long-only):
  A  RS Rating top-N   raw = 2*R63 + R126 + R189 + R252 -> universe percentile 1..99
  B  60d-return top-N  current #289 proxy (return over ~63 trading days)
  C  index benchmark   KOSPI (KR) / SPY (US)

Data: yfinance for US; FinanceDataReader for KR (Naver-sourced, avoids the KRX
data.krx.co.kr endpoint that outaged 2026-07-13). Prices cached to /tmp/rsbt_cache.

Usage:
    python tools/rs_rating_backtest.py --market kr   [--years 4] [--top 20] [--universe 200]
    python tools/rs_rating_backtest.py --market us   [--years 4] [--top 20] [--universe 200]

Caveats (see report): survivorship bias — universe is today's constituents; a
delisted 2023 loser is absent, inflating both A and B equally (the A-vs-B delta is
the signal, absolute CAGR is not). No transaction costs/slippage.
"""
from __future__ import annotations
import argparse
import os
import sys
import time
import datetime as dt
from typing import Optional

import numpy as np
import pandas as pd

CACHE_DIR = "/tmp/rsbt_cache"
os.makedirs(CACHE_DIR, exist_ok=True)

# RS Rating lookback windows (trading days) and IBD weighting: most-recent quarter 2x.
W63, W126, W189, W252 = 63, 126, 189, 252
RS_MIN_HISTORY = W252 + 5  # need ~1y of data to rank a name


# --------------------------------------------------------------------------- #
# Data access
# --------------------------------------------------------------------------- #
def _cache_path(market: str) -> str:
    return os.path.join(CACHE_DIR, f"prices_{market}.csv")


def get_kr_universe(n: int) -> list[str]:
    """Top-n KOSPI names by market cap (today's snapshot -> survivorship caveat)."""
    import FinanceDataReader as fdr
    listing = fdr.StockListing("KOSPI")
    # Column names vary by FDR version; find market-cap + code columns defensively.
    cap_col = next((c for c in listing.columns if c.lower() in ("marcap", "marketcap", "시가총액")), None)
    code_col = next((c for c in listing.columns if c.lower() in ("code", "symbol", "종목코드")), None)
    if code_col is None:
        raise RuntimeError(f"KR listing: no code column in {list(listing.columns)}")
    if cap_col is not None:
        listing = listing.sort_values(cap_col, ascending=False)
    codes = [str(c).zfill(6) for c in listing[code_col].tolist() if str(c).strip()]
    return codes[:n]


def get_us_universe(n: int) -> list[str]:
    """S&P 500 current constituents. survivorship caveat (today's members)."""
    import io
    import urllib.request
    url = ("https://raw.githubusercontent.com/datasets/s-and-p-500-companies/"
           "main/data/constituents.csv")
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        raw = urllib.request.urlopen(req, timeout=20).read()
        df = pd.read_csv(io.BytesIO(raw))
        syms = df["Symbol"].astype(str).str.replace(".", "-", regex=False).tolist()
        if len(syms) >= 50:
            return syms[:n]
        raise RuntimeError(f"only {len(syms)} symbols")
    except Exception as e:
        print(f"[US universe] constituents CSV fetch failed ({e}); embedded fallback")
        return _US_FALLBACK[:n]


def fetch_prices(market: str, tickers: list[str], start: str, end: str,
                 refresh: bool = False) -> pd.DataFrame:
    """Return a (date x ticker) close-price frame. Cached to parquet."""
    cp = _cache_path(market)
    if os.path.exists(cp) and not refresh:
        df = pd.read_csv(cp, index_col=0, parse_dates=True)
        have = set(df.columns)
        if set(tickers).issubset(have) and df.index.min() <= pd.Timestamp(start):
            print(f"[{market}] cache hit ({df.shape[1]} tickers, {df.shape[0]} rows)")
            return df[[t for t in tickers if t in have]]

    if market == "kr":
        closes = _fetch_kr(tickers, start, end)
    else:
        closes = _fetch_us(tickers, start, end)

    closes = closes.dropna(axis=1, how="all")
    closes.to_csv(cp)
    print(f"[{market}] fetched {closes.shape[1]}/{len(tickers)} tickers, {closes.shape[0]} rows")
    return closes


def _fetch_kr(tickers: list[str], start: str, end: str) -> pd.DataFrame:
    import FinanceDataReader as fdr
    out = {}
    fail = 0
    for i, t in enumerate(tickers):
        for attempt in range(3):
            try:
                d = fdr.DataReader(t, start, end)
                if d is not None and not d.empty and "Close" in d.columns:
                    out[t] = d["Close"]
                break
            except Exception:
                time.sleep(1.5 * (attempt + 1))
        else:
            fail += 1
        if (i + 1) % 25 == 0:
            print(f"  [kr] {i+1}/{len(tickers)} fetched (fail={fail})")
    print(f"  [kr] done, failures={fail}")
    return pd.DataFrame(out)


def _fetch_us(tickers: list[str], start: str, end: str) -> pd.DataFrame:
    import yfinance as yf
    # Batch download is far faster and rate-limit friendly.
    data = yf.download(tickers, start=start, end=end, auto_adjust=True,
                       progress=False, threads=True)
    if isinstance(data.columns, pd.MultiIndex):
        closes = data["Close"]
    else:  # single ticker
        closes = data[["Close"]].rename(columns={"Close": tickers[0]})
    return closes


def fetch_benchmark(market: str, start: str, end: str) -> pd.Series:
    if market == "us":
        import yfinance as yf
        d = yf.download("SPY", start=start, end=end, auto_adjust=True, progress=False)
        return d["Close"].squeeze()
    else:
        import FinanceDataReader as fdr
        d = fdr.DataReader("KS11", start, end)  # KOSPI index
        return d["Close"]


# --------------------------------------------------------------------------- #
# RS Rating + scoring
# --------------------------------------------------------------------------- #
def rs_raw(prices: pd.Series, asof: pd.Timestamp) -> Optional[float]:
    """IBD-style raw RS score at `asof`: 2*R63 + R126 + R189 + R252."""
    s = prices.loc[:asof].dropna()
    if len(s) < RS_MIN_HISTORY:
        return None
    p0 = float(s.iloc[-1])
    if p0 <= 0:
        return None

    def ret(w):
        if len(s) <= w:
            return None
        pw = float(s.iloc[-1 - w])
        return (p0 - pw) / pw if pw > 0 else None

    r = [ret(W63), ret(W126), ret(W189), ret(W252)]
    if any(x is None for x in r):
        return None
    return 2.0 * r[0] + r[1] + r[2] + r[3]


def ret_60d(prices: pd.Series, asof: pd.Timestamp) -> Optional[float]:
    """Current #289 proxy: single-window ~63-trading-day return."""
    s = prices.loc[:asof].dropna()
    if len(s) <= W63:
        return None
    p0, pw = float(s.iloc[-1]), float(s.iloc[-1 - W63])
    return (p0 - pw) / pw if pw > 0 else None


def percentile_rank(values: dict[str, float]) -> dict[str, float]:
    """Map raw scores -> 1..99 percentile (higher raw = higher rating)."""
    if not values:
        return {}
    items = sorted(values.items(), key=lambda kv: kv[1])
    n = len(items)
    return {t: 1 + 98 * (i / (n - 1)) if n > 1 else 50 for i, (t, _) in enumerate(items)}


# --------------------------------------------------------------------------- #
# Backtest
# --------------------------------------------------------------------------- #
def month_ends(index: pd.DatetimeIndex) -> list[pd.Timestamp]:
    ser = pd.Series(index, index=index)
    return [g.index.max() for _, g in ser.groupby([index.year, index.month])]


def run_backtest(closes: pd.DataFrame, bench: pd.Series, top: int) -> dict:
    closes = closes.sort_index()
    rebal = month_ends(closes.index)
    rebal = [d for d in rebal if len(closes.loc[:d]) >= RS_MIN_HISTORY]
    if len(rebal) < 6:
        raise RuntimeError(f"Not enough rebalance dates ({len(rebal)}) — check data span")

    curveA, curveB, curveC = [1.0], [1.0], [1.0]
    dates = [rebal[0]]
    pickA_prev, pickB_prev = [], []
    overlap_log, spearman_log = [], []

    for i in range(len(rebal) - 1):
        d0, d1 = rebal[i], rebal[i + 1]

        raw_rs, raw_60 = {}, {}
        for t in closes.columns:
            s = closes[t]
            a, b = rs_raw(s, d0), ret_60d(s, d0)
            if a is not None:
                raw_rs[t] = a
            if b is not None:
                raw_60[t] = b

        rating = percentile_rank(raw_rs)
        pickA = [t for t, _ in sorted(rating.items(), key=lambda kv: -kv[1])
                 if rating[t] >= 80][:top]
        if not pickA:
            pickA = [t for t, _ in sorted(rating.items(), key=lambda kv: -kv[1])][:top]
        pickB = [t for t, _ in sorted(raw_60.items(), key=lambda kv: -kv[1])][:top]

        # forward 1-month return of an equal-weight basket
        def fwd(picks):
            rets = []
            for t in picks:
                s = closes[t]
                try:
                    p0, p1 = float(s.loc[:d0].iloc[-1]), float(s.loc[:d1].iloc[-1])
                    if p0 > 0 and np.isfinite(p1):
                        rets.append(p1 / p0 - 1)
                except Exception:
                    continue
            return float(np.mean(rets)) if rets else 0.0

        curveA.append(curveA[-1] * (1 + fwd(pickA)))
        curveB.append(curveB[-1] * (1 + fwd(pickB)))
        try:
            b0, b1 = float(bench.loc[:d0].iloc[-1]), float(bench.loc[:d1].iloc[-1])
            curveC.append(curveC[-1] * (b1 / b0))
        except Exception:
            curveC.append(curveC[-1])
        dates.append(d1)

        # A-vs-B divergence: overlap of picks + Spearman of scores on shared names
        common = set(raw_rs) & set(raw_60)
        if len(common) >= 5:
            from scipy.stats import spearmanr
            xs = [rating[t] for t in common]
            ys = [raw_60[t] for t in common]
            rho, _ = spearmanr(xs, ys)
            spearman_log.append(rho)
        if pickA and pickB:
            overlap_log.append(len(set(pickA) & set(pickB)) / len(set(pickA) | set(pickB)))

    return {
        "dates": dates,
        "A": curveA, "B": curveB, "C": curveC,
        "overlap": float(np.mean(overlap_log)) if overlap_log else float("nan"),
        "spearman": float(np.nanmean(spearman_log)) if spearman_log else float("nan"),
        "n_rebal": len(rebal),
    }


def stats(curve: list[float], dates: list[pd.Timestamp]) -> dict:
    curve = np.array(curve)
    yrs = (dates[-1] - dates[0]).days / 365.25
    cagr = curve[-1] ** (1 / yrs) - 1 if yrs > 0 and curve[-1] > 0 else float("nan")
    peak = np.maximum.accumulate(curve)
    mdd = float(((curve - peak) / peak).min())
    mret = np.diff(curve) / curve[:-1]
    winrate = float((mret > 0).mean()) if len(mret) else float("nan")
    vol = float(mret.std() * np.sqrt(12)) if len(mret) else float("nan")
    sharpe = (cagr / vol) if vol and np.isfinite(vol) and vol > 0 else float("nan")
    return {"total": float(curve[-1]), "cagr": cagr, "mdd": mdd,
            "winrate": winrate, "vol": vol, "sharpe": sharpe}


def yearly_returns(curve: list[float], dates: list[pd.Timestamp]) -> dict:
    s = pd.Series(curve, index=pd.DatetimeIndex(dates))
    out = {}
    for y, g in s.groupby(s.index.year):
        out[int(y)] = float(g.iloc[-1] / g.iloc[0] - 1)
    return out


# --------------------------------------------------------------------------- #
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--market", choices=["kr", "us"], required=True)
    ap.add_argument("--years", type=int, default=4)
    ap.add_argument("--top", type=int, default=20)
    ap.add_argument("--universe", type=int, default=200)
    ap.add_argument("--refresh", action="store_true")
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    end = dt.date.today()
    start = end - dt.timedelta(days=int(args.years * 365.25) + 400)  # +400d warm-up for RS
    start_s, end_s = start.isoformat(), end.isoformat()
    print(f"=== RS Rating backtest [{args.market.upper()}] {start_s}..{end_s} "
          f"top{args.top} universe{args.universe} ===")

    if args.market == "kr":
        uni = get_kr_universe(args.universe)
    else:
        uni = get_us_universe(args.universe)
    print(f"universe: {len(uni)} tickers")

    closes = fetch_prices(args.market, uni, start_s, end_s, refresh=args.refresh)
    bench = fetch_benchmark(args.market, start_s, end_s)
    res = run_backtest(closes, bench, args.top)

    sA = stats(res["A"], res["dates"])
    sB = stats(res["B"], res["dates"])
    sC = stats(res["C"], res["dates"])
    yA, yB, yC = (yearly_returns(res[k], res["dates"]) for k in ("A", "B", "C"))

    def verdict():
        d = sA["cagr"] - sB["cagr"]
        if not (np.isfinite(sA["cagr"]) and np.isfinite(sB["cagr"])):
            return "판정 불가 (데이터 부족)"
        if d > 0.02 and sA["mdd"] >= sB["mdd"] - 0.03:
            return f"채택 가치 있음 (CAGR +{d*100:.1f}%p, MDD 악화 없음)"
        if d < -0.02:
            return f"채택 가치 없음 (RS Rating이 60d 대비 CAGR {d*100:.1f}%p 열위)"
        return f"애매 (CAGR 차이 {d*100:+.1f}%p — 노이즈 수준, 반영 신중)"

    lines = []
    lines.append(f"## {args.market.upper()} 시장 결과\n")
    lines.append(f"- 기간: {res['dates'][0].date()} ~ {res['dates'][-1].date()} "
                 f"(리밸런스 {len(res['dates'])-1}회), 유니버스 {closes.shape[1]}종목, 상위 {args.top}\n")
    lines.append("전략별 성과 (거래비용 미반영):\n")
    lines.append("| 전략 | 누적배수 | CAGR | MDD | 월승률 | 변동성 | Sharpe |")
    lines.append("|------|---------|------|-----|--------|--------|--------|")
    for name, s in [("A RS Rating", sA), ("B 60일수익률(현행)", sB), ("C 지수", sC)]:
        lines.append(f"| {name} | {s['total']:.2f}x | {s['cagr']*100:.1f}% | "
                     f"{s['mdd']*100:.1f}% | {s['winrate']*100:.0f}% | "
                     f"{s['vol']*100:.1f}% | {s['sharpe']:.2f} |")
    lines.append("")
    lines.append(f"- A vs B 종목 중복률(Jaccard): {res['overlap']*100:.0f}%")
    lines.append(f"- A vs B 점수 순위상관(Spearman): {res['spearman']:.2f} "
                 f"— 1에 가까울수록 '같은 정보', 낮을수록 RS Rating이 다른 신호 제공")
    lines.append("")
    lines.append("연도별 수익률:")
    lines.append("| 연도 | A RS Rating | B 60일 | C 지수 |")
    lines.append("|------|-------------|--------|--------|")
    for y in sorted(set(yA) | set(yB) | set(yC)):
        lines.append(f"| {y} | {yA.get(y,0)*100:+.1f}% | {yB.get(y,0)*100:+.1f}% | {yC.get(y,0)*100:+.1f}% |")
    lines.append("")
    lines.append(f"**판정: {verdict()}**\n")
    report = "\n".join(lines)
    print("\n" + report)

    if args.out:
        with open(args.out, "a") as f:
            f.write(report + "\n\n")
        print(f"[written] {args.out}")


# Small embedded US fallback (only used if Wikipedia is unreachable).
_US_FALLBACK = ("AAPL MSFT NVDA AMZN GOOGL META AVGO TSLA BRK-B LLY JPM V UNH XOM MA "
                "COST HD PG JNJ ABBV NFLX BAC KO CRM CVX MRK AMD PEP WMT ADBE "
                "TMO LIN ACN MCD CSCO ABT INTC QCOM DHR TXN INTU CAT AMAT VZ "
                "PFE CMCSA IBM GE NOW").split()

if __name__ == "__main__":
    main()
