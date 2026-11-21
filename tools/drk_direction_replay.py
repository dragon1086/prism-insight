"""Replay the Dr. K shadow model against the production Market Pulse on one index (offline, read-only).

Input is a CSV with date, high, low, close, volume (any case); fetch data elsewhere. Output is a
JSON summary (and the per-day disagreement list) for the research report. Nothing here is used by
production; see cores/drk_market_direction.py.

    python tools/drk_direction_replay.py --csv nasdaq.csv --market us --out out.json \
        [--start 1974-07-01] [--book-window 1974-07-01:2009-12-31]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) != sys.path[0]:
    sys.path.insert(0, str(ROOT))

import pandas as pd  # noqa: E402

from cores import drk_market_direction as D  # noqa: E402
from cores.market_pulse import CORRECTION, DailyBar, MarketPulse  # noqa: E402
from cores.regime_policy import _df_to_bars  # noqa: E402


def load_bars(path: str, start: str | None):
    df = pd.read_csv(path)
    df.columns = [c.strip().lower() for c in df.columns]
    df["date"] = pd.to_datetime(df["date"])
    df = df.set_index("date").sort_index()
    if start:
        df = df[df.index >= start]
    return _df_to_bars(df, "close", "volume", DailyBar)


def equity_stats(bars, invested):
    """Index while invested (enter/exit at that day's close), else 0% cash; ``long_short`` is short
    instead of cash. Price only, no costs, dividends or cash interest."""
    years = (pd.Timestamp(bars[-1].date) - pd.Timestamp(bars[0].date)).days / 365.25
    out = {}
    for label, short in (("", False), ("long_short_", True)):
        eq, peak, mdd = 1.0, 1.0, 0.0
        for k in range(1, len(bars)):
            r = bars[k].close / bars[k - 1].close - 1
            eq *= 1 + (r if invested[k - 1] else (-r if short else 0.0))
            peak = max(peak, eq)
            mdd = min(mdd, eq / peak - 1)
        out[label + "cagr_pct"] = round((eq ** (1 / years) - 1) * 100, 2)
        out[label + "max_drawdown_pct"] = round(mdd * 100, 2)
    return out


def segments(dates, flags):
    out, start = [], None
    for d, f in zip(dates + [None], flags + [False]):
        if f and start is None:
            start = d
        if not f and start is not None:
            out.append((start, last))
            start = None
        last = d
    return out


def drawdown_lags(bars, book_cash, pulse_corr, min_decline=0.20):
    """Each close decline >= 20% from a running peak (episode ends at a new closing high):
    sessions from peak to the first CASH / CORRECTION and from trough to the first INVESTED /
    non-CORRECTION (None = never)."""
    n, rows, peak_i, k = len(bars), [], 0, 1
    def first(flags, a, want):
        return next((j - a for j in range(a, n) if flags[j] == want), None)
    while k < n:
        if bars[k].close > bars[peak_i].close:
            peak_i = k
        elif bars[k].close <= bars[peak_i].close * (1 - min_decline):
            j, trough_i = k, k
            while j < n and bars[j].close <= bars[peak_i].close:
                trough_i = j if bars[j].close < bars[trough_i].close else trough_i
                j += 1
            rows.append({
                "peak": bars[peak_i].date, "trough": bars[trough_i].date,
                "decline_pct": round((bars[trough_i].close / bars[peak_i].close - 1) * 100, 1),
                "book_cash_after_peak": first(book_cash, peak_i, True),
                "pulse_correction_after_peak": first(pulse_corr, peak_i, True),
                "book_invested_after_trough": first(book_cash, trough_i, False),
                "pulse_exit_after_trough": first(pulse_corr, trough_i, False),
            })
            peak_i, k = min(j, n - 1), j
        k += 1
    return rows


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--csv", required=True)
    ap.add_argument("--market", choices=("us", "kr"), required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--start")
    ap.add_argument("--book-window", help="START:END for the book backtest comparison")
    args = ap.parse_args(argv)

    bars = load_bars(args.csv, args.start)
    dates = [b.date for b in bars]
    pulse = MarketPulse()
    pulse_states = [pulse.feed(b) for b in bars]
    pulse_corr = [s == CORRECTION for s in pulse_states]
    events = D.event_dates(dates, args.market)

    out = {"csv": args.csv, "market": args.market, "first": dates[0], "last": dates[-1],
           "sessions": len(bars), "event_days": len(events),
           "pulse": {"correction_pct": round(sum(pulse_corr) / len(bars) * 100, 1),
                     **equity_stats(bars, [not c for c in pulse_corr])},
           "variants": {}}
    runs = [(name, False) for name in D.FTD_THRESHOLDS] + [("fixed_1.5", True)]  # low-day option: default only
    for name, low_day in runs:
        cfg = D.DrkConfig(ftd_threshold=name, low_day_is_day1=low_day, event_dates=events)
        recs = D.replay(bars, cfg)
        cash = [r["state"] == D.CASH for r in recs]
        years = len(bars) / 252
        ev = [e for r in recs for e in r["events"]]
        key = name + ("+lowday1" if low_day else "")
        book_only_cash = [c and not p for c, p in zip(cash, pulse_corr)]
        pulse_only_corr = [p and not c for c, p in zip(cash, pulse_corr)]
        item = {
            "invested_pct": round((1 - sum(cash) / len(bars)) * 100, 1),
            "agree_pct": round(sum(c == p for c, p in zip(cash, pulse_corr)) / len(bars) * 100, 1),
            "book_cash_pulse_not_correction_days": sum(book_only_cash),
            "book_invested_pulse_correction_days": sum(pulse_only_corr),
            "buy_signals": ev.count("follow_through_buy"),
            "sell_signals": sum(e.startswith("sell_signal") for e in ev),
            "buy_invalidations": sum(e.startswith("buy_invalid") for e in ev),
            "signals_per_year": round((ev.count("follow_through_buy")
                                       + sum(e.startswith("sell_signal") for e in ev)) / years, 2),
            "dd_days": sum(1 for r in recs if "dd_counted" in r["events"]),
            "candle_unknown_days": sum(r["candle_unknown"] for r in recs),
            **equity_stats(bars, [not c for c in cash]),
        }
        if args.book_window:
            a, b = args.book_window.split(":")
            idx = [i for i, d in enumerate(dates) if a <= d <= b]
            item["book_window"] = equity_stats([bars[i] for i in idx], [not cash[i] for i in idx])
        if key == "fixed_1.5":   # default variant: full detail
            item["disagree_segments"] = {
                "book_cash_pulse_not_correction": segments(dates, book_only_cash),
                "book_invested_pulse_correction": segments(dates, pulse_only_corr),
            }
            item["drawdowns"] = drawdown_lags(bars, cash, pulse_corr)
            item["signals"] = [(r["date"], [e for e in r["events"] if e.startswith(
                ("follow_through_buy", "sell_signal", "buy_invalid"))]) for r in recs
                if any(e.startswith(("follow_through_buy", "sell_signal", "buy_invalid")) for e in r["events"])]
        out["variants"][key] = item
    if args.book_window:
        a, b = args.book_window.split(":")
        idx = [i for i, d in enumerate(dates) if a <= d <= b]
        out["pulse"]["book_window"] = equity_stats([bars[i] for i in idx], [not pulse_corr[i] for i in idx])
        out["buy_and_hold_book_window"] = equity_stats([bars[i] for i in idx], [True] * len(idx))
    Path(args.out).write_text(json.dumps(out, ensure_ascii=False, indent=1))
    print(json.dumps({k: v for k, v in out.items() if k != "variants"}, ensure_ascii=False))
    for k, v in out["variants"].items():
        print(k, {x: y for x, y in v.items() if x not in ("disagree_segments", "drawdowns", "signals")})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
