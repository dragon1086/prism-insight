#!/usr/bin/env python3
"""Offline replay for docs/entry-quality-experiments/us-udvr-institutional-proxy-v1.md.

Research only: never imported by the trading path, never places orders.

  extract-decisions  (db-server, read-only): tracker rows -> JSON
  fetch-bars         (anywhere): yfinance daily bars for those tickers -> JSON
  replay             (anywhere): deterministic features + preregistered tests -> JSON

Definitions, sample and verdict thresholds are fixed by the preregistration.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import random
import sqlite3
import sys
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

RULE_VERSION = "us-udvr-institutional-proxy-v1"
KST = ZoneInfo("Asia/Seoul")
NY = ZoneInfo("America/New_York")
UDVR_WINDOW = 50
HOLDOUT_START = "2026-07-01"
BOOT = 2000
SEED = 20260930

_TRACKER_SQL = ("SELECT id, ticker, analysis_date, analysis_price, decision, trigger_type, return_14d, return_30d "
                "FROM us_analysis_performance_tracker ORDER BY id")


# ------------------------------------------------------------------ extract / fetch

def extract_decisions(db, out):
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    cols = ["id", "ticker", "analysis_date", "analysis_price", "decision", "trigger_type", "return_14d", "return_30d"]
    rows = [dict(zip(cols, r)) for r in conn.execute(_TRACKER_SQL)]
    conn.close()
    Path(out).write_text(json.dumps({"rule_version": RULE_VERSION, "rows": rows}, ensure_ascii=False))
    print(f"rows={len(rows)} -> {out}")


def fetch_bars(decisions, out):
    import yfinance as yf

    tickers = sorted({r["ticker"] for r in json.loads(Path(decisions).read_text())["rows"]})
    bars = {}
    for t in tickers:
        try:
            df = yf.Ticker(t).history(start="2025-09-01", end="2026-09-30", auto_adjust=True)
            bars[t] = [{"date": str(ix.tz_convert(NY).date()), "close": float(r["Close"]), "volume": float(r["Volume"])}
                       for ix, r in df.iterrows()
                       if not any(math.isnan(float(r[c])) for c in ("Close", "Volume"))]
        except Exception as error:  # noqa: BLE001 - missing stays MISSING
            bars[t] = {"error": type(error).__name__}
        time.sleep(0.2)
    Path(out).write_text(json.dumps({"rule_version": RULE_VERSION, "bars": bars}))
    print(f"tickers={len(bars)} -> {out}")


# ------------------------------------------------------------------ features

def session_date(recorded_at):
    stamp = datetime.fromisoformat(str(recorded_at)[:19]).replace(tzinfo=KST)
    return stamp.astimezone(NY).date().isoformat()


def udvr(confirmed, window=UDVR_WINDOW):
    """Up-close volume / down-close volume over the last `window` completed sessions."""
    if len(confirmed) < window + 1:
        return None
    seg = confirmed[-(window + 1):]
    up = down = 0.0
    for prev, cur in zip(seg, seg[1:]):
        if cur["close"] > prev["close"]:
            up += cur["volume"]
        elif cur["close"] < prev["close"]:
            down += cur["volume"]
    return up / down if down > 0 else None


def extension(confirmed):
    """(close vs MA20 %, simple-average RSI14) from completed sessions; None if short."""
    if len(confirmed) < 21:
        return None, None
    closes = [b["close"] for b in confirmed]
    ma20 = sum(closes[-20:]) / 20
    diffs = [b - a for a, b in zip(closes[-15:], closes[-14:])]
    gains = sum(d for d in diffs if d > 0) / 14
    losses = sum(-d for d in diffs if d < 0) / 14
    rsi = 100.0 if losses == 0 else 100 - 100 / (1 + gains / losses)
    return (closes[-1] / ma20 - 1) * 100, rsi


def volume_signal1(confirmed):
    """Existing momentum signal 1 on completed sessions: any of last 3 >= 2x prior-20 average."""
    vols = [b["volume"] for b in confirmed]
    hits = []
    for i in range(len(vols) - 3, len(vols)):
        if i >= 20:
            avg = sum(vols[i - 20:i]) / 20
            hits.append(avg > 0 and vols[i] >= 2 * avg)
    return any(hits) if hits else None


# ------------------------------------------------------------------ stats

def _mean(xs):
    return sum(xs) / len(xs) if xs else None


def cluster_boot_diff(rows, key_a, key_b, value="return_30d"):
    """Ticker-clustered bootstrap of mean(value | group a) - mean(value | group b)."""
    rng = random.Random(SEED)
    by = {}
    for r in rows:
        by.setdefault(r["ticker"], []).append(r)
    tickers = sorted(by)
    diffs = []
    for _ in range(BOOT):
        sample = [r for t in (rng.choice(tickers) for _ in tickers) for r in by[t]]
        a = [r[value] for r in sample if key_a(r)]
        b = [r[value] for r in sample if key_b(r)]
        if a and b:
            diffs.append(_mean(a) - _mean(b))
    diffs.sort()
    if not diffs:
        return None, None
    return diffs[int(0.05 * len(diffs))], diffs[int(0.95 * len(diffs)) - 1]


def compare(rows, key_a, key_b, min_n):
    a = [r["return_30d"] for r in rows if key_a(r)]
    b = [r["return_30d"] for r in rows if key_b(r)]
    lo, hi = cluster_boot_diff(rows, key_a, key_b)
    return {"n_a": len(a), "n_b": len(b), "mean_a": _mean(a), "mean_b": _mean(b),
            "diff": (_mean(a) - _mean(b)) if a and b else None, "ci90": [lo, hi],
            "enough": len(a) >= min_n and len(b) >= min_n}


def verdict(disc, hold):
    if not (disc["enough"] and hold["enough"]) or hold["diff"] is None:
        return "INSUFFICIENT"
    if hold["diff"] > 0 and hold["ci90"][0] is not None and hold["ci90"][0] > 0 and (disc["diff"] or 0) > 0:
        return "ADOPT_CANDIDATE"
    return "RETIRE"


def point_biserial(xs, flags):
    pairs = [(x, 1.0 if f else 0.0) for x, f in zip(xs, flags) if x is not None and f is not None]
    if len(pairs) < 3:
        return None
    mx = _mean([p[0] for p in pairs])
    my = _mean([p[1] for p in pairs])
    sxy = sum((x - mx) * (y - my) for x, y in pairs)
    sx = math.sqrt(sum((x - mx) ** 2 for x, _ in pairs))
    sy = math.sqrt(sum((y - my) ** 2 for _, y in pairs))
    return sxy / (sx * sy) if sx and sy else None


def replay(decisions, bars_file, out):
    rows = json.loads(Path(decisions).read_text())["rows"]
    bars = json.loads(Path(bars_file).read_text())["bars"]
    enriched, missing = [], {"no_return": 0, "no_bars": 0, "no_udvr": 0}
    for r in rows:
        if r.get("return_30d") is None:
            missing["no_return"] += 1
            continue
        b = bars.get(r["ticker"])
        if not isinstance(b, list):
            missing["no_bars"] += 1
            continue
        day = session_date(r["analysis_date"])
        confirmed = [x for x in b if x["date"] < day]
        u = udvr(confirmed)
        if u is None:
            missing["no_udvr"] += 1
            continue
        ext, rsi = extension(confirmed)
        enriched.append({**r, "session": day, "udvr": u, "ext_ma20_pct": ext, "rsi14": rsi,
                         "overextended": (ext is not None and ext >= 25) or (rsi is not None and rsi >= 85),
                         "signal1": volume_signal1(confirmed),
                         "volume_trigger": "volume" in str(r.get("trigger_type") or "").lower()})
    disc = [r for r in enriched if r["session"] < HOLDOUT_START]
    hold = [r for r in enriched if r["session"] >= HOLDOUT_START]
    h1 = {s: compare(g, lambda r: r["udvr"] > 1.0, lambda r: r["udvr"] <= 1.0, 30)
          for s, g in (("discovery", disc), ("holdout", hold))}
    ext_d = [r for r in disc if r["overextended"]]
    ext_h = [r for r in hold if r["overextended"]]
    # H2 direction: >=1.0 minus <1.0 (positive = distribution group did worse)
    h2 = {s: compare(g, lambda r: r["udvr"] >= 1.0, lambda r: r["udvr"] < 1.0, 15)
          for s, g in (("discovery", ext_d), ("holdout", ext_h))}
    overlap = {"udvr_vs_signal1": point_biserial([r["udvr"] for r in enriched], [r["signal1"] for r in enriched]),
               "udvr_vs_volume_trigger": point_biserial([r["udvr"] for r in enriched],
                                                        [r["volume_trigger"] for r in enriched])}
    dup = any(v is not None and abs(v) >= 0.5 for v in overlap.values())
    summary = {
        "rule_version": RULE_VERSION,
        "inputs_sha256": hashlib.sha256(Path(decisions).read_bytes() + Path(bars_file).read_bytes()).hexdigest(),
        "rows": len(rows), "used": len(enriched), "missing": missing,
        "discovery_n": len(disc), "holdout_n": len(hold),
        "H1": {**h1, "verdict": "RETIRE_DUPLICATE" if dup else verdict(h1["discovery"], h1["holdout"])},
        "H2": {**h2, "verdict": "RETIRE_DUPLICATE" if dup else verdict(h2["discovery"], h2["holdout"])},
        "overlap": overlap,
    }
    Path(out).write_text(json.dumps({"summary": summary, "rows": enriched}, ensure_ascii=False, indent=1))
    print(json.dumps(summary, ensure_ascii=False, indent=1))


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = p.add_subparsers(dest="cmd", required=True)
    e = sub.add_parser("extract-decisions")
    e.add_argument("--db", default="stock_tracking_db.sqlite")
    e.add_argument("--out", required=True)
    f = sub.add_parser("fetch-bars")
    f.add_argument("--decisions", required=True)
    f.add_argument("--out", required=True)
    r = sub.add_parser("replay")
    r.add_argument("--decisions", required=True)
    r.add_argument("--bars", required=True)
    r.add_argument("--out", required=True)
    a = p.parse_args(argv)
    if a.cmd == "extract-decisions":
        extract_decisions(a.db, a.out)
    elif a.cmd == "fetch-bars":
        fetch_bars(a.decisions, a.out)
    else:
        replay(a.decisions, a.bars, a.out)


if __name__ == "__main__":
    sys.exit(main())
