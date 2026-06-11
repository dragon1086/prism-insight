"""
H1 — Signal predictive power (no trade simulation).
Extracts every signal that passes the LIVE entry gate (score>=55, trend-strength
gate on 4h&1d, 4h trigger-TF candle position) across 2022-01..2025-12, then
measures direction-signed forward returns at +1/+3/+7/+14 days.

Run from prism-btc/ package root:  python -m analysis.h1_signal_power
Pure analysis. Reads market.db only. No code/param modification.
"""
from __future__ import annotations
import sqlite3, math
from pathlib import Path
import pandas as pd
import numpy as np

from engine.indicators import add_indicators
from engine.regime import build_tf_state, compute_alignment_score, TFState
from engine.signal import (
    generate_signal, _long_tf_direction_positive, _long_tf_direction_negative,
    _entry_tf_aligned, chop_filter_passed, trend_strength,
    LONG_ENTRY_POSITIONS, SHORT_ENTRY_POSITIONS, ENTRY_TRIGGER_TF,
)
from engine.regime import RegimeSnapshot
from engine.config import ENTRY_SCORE_MIN
from engine import config as cfg

DB = Path(__file__).resolve().parents[1] / "state" / "market.db"
ALL_TFS = ("30m", "1h", "4h", "12h", "1d", "1w")
EVAL_TF = "4h"          # we evaluate the gate at each new confirmed 4h bar (live cadence)
FWD_TF  = "1h"          # forward returns measured on 1h closes for resolution
HORIZONS = {"1d": 24, "3d": 72, "7d": 168, "14d": 336}  # in 1h bars

def load_tf(conn, tf):
    df = pd.read_sql_query(
        "SELECT open_time, open, high, low, close, volume, turnover "
        "FROM klines WHERE timeframe=? AND confirmed=1 ORDER BY open_time ASC",
        conn, params=(tf,))
    df = add_indicators(df)
    df["dt"] = pd.to_datetime(df["open_time"], unit="ms", utc=True)
    return df

def main():
    conn = sqlite3.connect(DB)
    data = {tf: load_tf(conn, tf) for tf in ALL_TFS}
    conn.close()
    for tf, df in data.items():
        print(f"[load] {tf}: {len(df)} bars {df['dt'].min()} .. {df['dt'].max()}")

    fwd = data[FWD_TF].dropna(subset=["ma10","ma35","atr14"]).reset_index(drop=True)
    fwd_times = fwd["open_time"].values
    fwd_close = fwd["close"].values

    # iterate over confirmed 4h bars (live entry cadence). For each, build a snapshot
    # from the latest confirmed bar of each TF at that instant (no look-ahead).
    eval_df = data[EVAL_TF].dropna(subset=["ma10","ma35","atr14"]).reset_index(drop=True)
    # precompute end-time arrays for slicing
    tf_arr = {tf: data[tf]["open_time"].values for tf in ALL_TFS}
    rows = []
    for i in range(len(eval_df)):
        t = eval_df["open_time"].iloc[i]  # this 4h bar's open_time; it is "confirmed" so usable
        # latest confirmed bar per TF with open_time <= t
        tf_states = {}
        ok = True
        for tf in ALL_TFS:
            arr = tf_arr[tf]
            idx = np.searchsorted(arr, t, side="right") - 1
            if idx < 34:  # need >=35 rows for MA35
                ok = False; break
            sub = data[tf].iloc[:idx+1]
            try:
                tf_states[tf] = build_tf_state(sub)
            except ValueError:
                ok = False; break
        if not ok:
            continue
        score = compute_alignment_score(tf_states)
        snap = RegimeSnapshot(tf_states=tf_states, alignment_score=score, evaluated_at="")
        # chop filter gate (live entry requires it)
        if not chop_filter_passed(tf_states):
            continue
        sig = generate_signal(snap)
        if sig.side not in ("long", "short"):
            continue
        # forward returns from the 1h close nearest at/after t
        j = np.searchsorted(fwd_times, t, side="left")
        if j >= len(fwd_close):
            continue
        p0 = fwd_close[j]
        sgn = 1.0 if sig.side == "long" else -1.0
        rec = {"t": t, "side": sig.side, "score": score,
               "ts_4h": trend_strength(tf_states["4h"]),
               "ts_1d": trend_strength(tf_states["1d"])}
        for hname, hbars in HORIZONS.items():
            k = j + hbars
            if k < len(fwd_close):
                raw = (fwd_close[k] - p0) / p0
                rec[f"fwd_{hname}"] = sgn * raw          # direction-signed
                rec[f"absraw_{hname}"] = raw
            else:
                rec[f"fwd_{hname}"] = np.nan
                rec[f"absraw_{hname}"] = np.nan
        rows.append(rec)

    sig_df = pd.DataFrame(rows)
    print(f"\n[signals] total gated entry signals: {len(sig_df)}")
    print(f"  long={ (sig_df['side']=='long').sum() }  short={ (sig_df['side']=='short').sum() }")

    def binom_p(wins, n):
        # two-sided exact binomial vs p=0.5
        if n == 0: return float("nan")
        from math import comb
        k = wins
        # P(X>=k or X<=n-k) tail for symmetric
        lo = min(k, n-k)
        tail = sum(comb(n, x) for x in range(0, lo+1)) * 2 * (0.5**n)
        return min(1.0, tail)

    def summarize(d, label):
        print(f"\n--- {label} (n={len(d)}) ---")
        print(f"{'horizon':>8} {'mean%':>8} {'med%':>8} {'hit%':>7} {'n':>5} {'binom_p':>9}")
        for h in HORIZONS:
            col = f"fwd_{h}"
            v = d[col].dropna()
            n = len(v)
            if n == 0:
                continue
            hit = (v > 0).sum()
            print(f"{h:>8} {v.mean()*100:>8.3f} {v.median()*100:>8.3f} "
                  f"{hit/n*100:>6.1f}% {n:>5} {binom_p(hit, n):>9.4f}")

    summarize(sig_df, "ALL signals")
    summarize(sig_df[sig_df.side=="long"], "LONG")
    summarize(sig_df[sig_df.side=="short"], "SHORT")

    # score buckets
    for lo, hi, name in [(55,70,"55-70"),(70,85,"70-85"),(85,101,"85+")]:
        b = sig_df[(sig_df.score.abs()>=lo) & (sig_df.score.abs()<hi)]
        summarize(b, f"score {name}")

    # trend-strength buckets (use 4h ts as proxy)
    for lo, hi, name in [(1.0,2.0,"ts 1-2"),(2.0,3.5,"ts 2-3.5"),(3.5,99,"ts 3.5+")]:
        b = sig_df[(sig_df.ts_4h>=lo) & (sig_df.ts_4h<hi)]
        summarize(b, f"4h_{name}")

    out = Path(__file__).resolve().parent / "h1_signals.csv"
    sig_df.to_csv(out, index=False)
    print(f"\n[saved] {out}")

if __name__ == "__main__":
    main()
