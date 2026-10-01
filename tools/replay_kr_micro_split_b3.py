#!/usr/bin/env python3
"""Offline replay for docs/entry-quality-experiments/kr-micro-split-b3-v1.md.

Research only: never imported by the trading path, never places orders.

  extract-trades  (db-server, read-only): KR strategy-ledger trades -> JSON
  fetch-bars      (db-server, KIS reads): raw daily bars per ticker -> JSON (cached)
  replay          (anywhere): B3 daily approximation vs full entry -> JSON

Definitions, sample and verdict thresholds are fixed by the preregistration.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import random
import sqlite3
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

RULE_VERSION = "kr-micro-split-b3-v1"
SAMPLE_START, SAMPLE_END = "2025-10-01", "2026-09-30 23:59:59"
HOLDOUT_START = "2026-07-01"
ADD_WINDOW = 10
BAND_TOP = 1.10
STEP_80, STEP_100 = 1.02, 1.04
ADD_COST = 0.0025
BOOT, SEED = 2000, 20261002
BULL_PREFIXES = ("parabolic", "strong_bull", "moderate_bull")
BULL_WORDS = ("상승추세", "강세")
NON_INFERIORITY = -0.0020
LOSS_REDUCTION = 0.25
MIN_HOLDOUT = 30

_SQL = ("SELECT id, ticker, company_name, buy_date, buy_price, sell_date, sell_price, trigger_type, scenario "
        "FROM trading_history WHERE buy_date >= ? AND buy_date <= ? AND sell_date IS NOT NULL ORDER BY buy_date, id")


# ------------------------------------------------------------------ extract / fetch

def _regime(scenario_text):
    try:
        scenario = json.loads(scenario_text or "{}")
    except (TypeError, ValueError):
        return None
    return scenario.get("market_regime") or scenario.get("market_condition")


def extract_trades(db, out):
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    rows = []
    for r in conn.execute(_SQL, (SAMPLE_START, SAMPLE_END)):
        rows.append({"id": r[0], "ticker": r[1], "company_name": r[2], "buy_date": r[3], "buy_price": r[4],
                     "sell_date": r[5], "sell_price": r[6], "trigger_type": r[7], "regime": _regime(r[8])})
    conn.close()
    Path(out).write_text(json.dumps({"rule_version": RULE_VERSION, "rows": rows}, ensure_ascii=False))
    print(f"rows={len(rows)} -> {out}")


def fetch_bars(trades, cache_dir, out, pause=0.35):
    from cores.market_data.kis_source import KisSource

    source = KisSource()
    cache = Path(cache_dir)
    cache.mkdir(parents=True, exist_ok=True)
    rows = json.loads(Path(trades).read_text())["rows"]
    spans = {}
    for r in rows:
        lo, hi = r["buy_date"][:10].replace("-", ""), r["sell_date"][:10].replace("-", "")
        a, b = spans.get(r["ticker"], (lo, hi))
        spans[r["ticker"]] = (min(a, lo), max(b, hi))
    bars = {}
    for ticker, (lo, hi) in sorted(spans.items()):
        path = cache / f"b3daily_{ticker}_{lo}_{hi}.json"
        if not path.exists():
            start = (datetime.strptime(lo, "%Y%m%d") - timedelta(days=60)).strftime("%Y%m%d")
            try:
                frame = source.price_history(ticker, start, hi, adjusted=False)
                data = [{"date": ix.strftime("%Y-%m-%d"), "high": float(x["High"]), "low": float(x["Low"]),
                         "close": float(x["Close"])} for ix, x in frame.iterrows()]
            except Exception as error:  # noqa: BLE001 - missing stays MISSING
                data = {"error": type(error).__name__}
            path.write_text(json.dumps(data))
            time.sleep(pause)
        bars[ticker] = json.loads(path.read_text())
    Path(out).write_text(json.dumps({"rule_version": RULE_VERSION, "bars": bars}))
    print(f"tickers={len(bars)} -> {out}")


# ------------------------------------------------------------------ rules

def is_bull(regime):
    text = str(regime or "").strip()
    return text.lower().startswith(BULL_PREFIXES) or any(word in text for word in BULL_WORDS)


def drop_pyramid_adds(rows):
    """Keep a row only if no earlier row of the same ticker was still open when it started."""
    kept, open_until = [], {}
    for r in sorted(rows, key=lambda x: (x["buy_date"], x["id"])):
        if r["buy_date"] < open_until.get(r["ticker"], ""):
            continue
        kept.append(r)
        open_until[r["ticker"]] = max(open_until.get(r["ticker"], ""), r["sell_date"])
    return kept


def atr14(prior):
    """Simple mean of 14 true ranges from the last 15 completed bars."""
    seg = prior[-15:]
    if len(seg) < 15:
        return None
    trs = [max(b["high"] - b["low"], abs(b["high"] - a["close"]), abs(b["low"] - a["close"]))
           for a, b in zip(seg, seg[1:])]
    return sum(trs) / len(trs)


def simulate(trade, bars, sizing):
    """B3 legs for one trade, or (None, reason). `sizing` = initial_sizing(entry, atr)."""
    if not isinstance(bars, list):
        return None, "no_bars"
    buy_day, sell_day = trade["buy_date"][:10], trade["sell_date"][:10]
    entry, exit_price = float(trade["buy_price"]), float(trade["sell_price"])
    prior = [b for b in bars if b["date"] < buy_day]
    atr = atr14(prior)
    if atr is None or entry <= 0 or exit_price <= 0:
        return None, "no_history"
    _, initial = sizing(entry, atr)
    legs = [(float(initial), entry)]
    after = [b for b in bars if buy_day < b["date"] < sell_day][:ADD_WINDOW]
    if is_bull(trade.get("regime")):
        closes = [b["close"] for b in bars]
        dates = [b["date"] for b in bars]
        for bar in after:
            held = sum(f for f, _ in legs)
            if held >= 1.0:
                break
            idx = dates.index(bar["date"])
            if idx < 19:
                continue
            sma20 = sum(closes[idx - 19:idx + 1]) / 20
            avg_cost = sum(f * p for f, p in legs) / held
            c = bar["close"]
            if not (entry <= c <= entry * BAND_TOP and c > sma20 and c > avg_cost):
                continue
            target = 1.0 if c >= entry * STEP_100 else (0.8 if c >= entry * STEP_80 and held < 0.8 else held)
            if target > held:
                legs.append((target - held, c))
    gross = sum(f * (exit_price / p - 1) for f, p in legs)
    cost = sum(f * ADD_COST for f, _ in legs[1:])
    return {"initial": float(initial), "final_fraction": sum(f for f, _ in legs), "adds": len(legs) - 1,
            "b3": gross, "b3_net": gross - cost, "full": exit_price / entry - 1}, None


# ------------------------------------------------------------------ stats

def _mean(xs):
    return sum(xs) / len(xs) if xs else None


def mdd(rows, key):
    level = peak = worst = 0.0
    for r in sorted(rows, key=lambda x: x["sell_date"]):
        level += r[key] / 10
        peak = max(peak, level)
        worst = min(worst, level - peak)
    return worst


def boot_diff(rows):
    rng = random.Random(SEED)
    by = {}
    for r in rows:
        by.setdefault(r["ticker"], []).append(r)
    tickers = sorted(by)
    diffs = []
    for _ in range(BOOT):
        sample = [r for t in (rng.choice(tickers) for _ in tickers) for r in by[t]]
        if sample:
            diffs.append(_mean([r["b3_net"] - r["full"] for r in sample]))
    diffs.sort()
    return [diffs[int(0.05 * len(diffs))], diffs[int(0.95 * len(diffs)) - 1]] if diffs else [None, None]


def summarize(rows):
    if not rows:
        return {"n": 0}
    losers = [r for r in rows if r["full"] < 0]
    best = max(rows, key=lambda r: r["full"])
    rest = [r for r in rows if r is not best]
    loss_full = _mean([r["full"] for r in losers])
    loss_b3 = _mean([r["b3_net"] for r in losers])
    return {
        "n": len(rows), "n_losers": len(losers), "bull_entries": sum(is_bull(r.get("regime")) for r in rows),
        "mean_initial": _mean([r["initial"] for r in rows]), "mean_final_fraction": _mean([r["final_fraction"] for r in rows]),
        "mean_full": _mean([r["full"] for r in rows]), "mean_b3": _mean([r["b3"] for r in rows]),
        "mean_b3_net": _mean([r["b3_net"] for r in rows]),
        "diff_net": _mean([r["b3_net"] - r["full"] for r in rows]), "diff_net_ci90": boot_diff(rows),
        "diff_net_without_best": _mean([r["b3_net"] - r["full"] for r in rest]) if rest else None,
        "loser_mean_full": loss_full, "loser_mean_b3": loss_b3,
        "loss_reduction": (1 - loss_b3 / loss_full) if losers and loss_full else None,
        "worst_full": min(r["full"] for r in rows), "worst_b3": min(r["b3_net"] for r in rows),
        "mdd_full": mdd(rows, "full"), "mdd_b3": mdd(rows, "b3_net"),
    }


def verdict(s):
    if not s.get("n"):
        return "INSUFFICIENT"
    risk_ok = (s["loss_reduction"] is not None and s["loss_reduction"] >= LOSS_REDUCTION
               and s["mdd_b3"] > s["mdd_full"])
    if not risk_ok:
        return "RETIRE"
    cost_ok = s["diff_net"] >= NON_INFERIORITY and (s["diff_net_without_best"] or 0) >= NON_INFERIORITY
    if not cost_ok:
        return "RETIRE"
    return "LIMITED_LIVE_REVIEW" if s["n"] >= MIN_HOLDOUT else "CONTINUE_CAPTURE"


def replay(trades, bars_file, out, sizing=None):
    if sizing is None:
        from prism_core.oneil_adaptive_policy import initial_sizing as sizing
    rows = json.loads(Path(trades).read_text())["rows"]
    bars = json.loads(Path(bars_file).read_text())["bars"]
    kept = drop_pyramid_adds(rows)
    used, missing = [], {"pyramid_add": len(rows) - len(kept), "no_bars": 0, "no_history": 0}
    for t in kept:
        result, reason = simulate(t, bars.get(t["ticker"]), sizing)
        if result is None:
            missing[reason] += 1
            continue
        used.append({**t, **result})
    disc = [r for r in used if r["buy_date"][:10] < HOLDOUT_START]
    hold = [r for r in used if r["buy_date"][:10] >= HOLDOUT_START]
    summary = {
        "rule_version": RULE_VERSION,
        "inputs_sha256": hashlib.sha256(Path(trades).read_bytes() + Path(bars_file).read_bytes()).hexdigest(),
        "rows": len(rows), "used": len(used), "missing": missing,
        "discovery": summarize(disc), "holdout": summarize(hold), "all": summarize(used),
    }
    summary["verdict"] = verdict(summary["holdout"])
    summary["discovery_direction_same"] = (
        summary["discovery"].get("loss_reduction") is not None
        and summary["discovery"]["loss_reduction"] > 0
        and summary["discovery"]["mdd_b3"] > summary["discovery"]["mdd_full"])
    Path(out).write_text(json.dumps({"summary": summary, "rows": used}, ensure_ascii=False, indent=1, default=str))
    print(json.dumps(summary, ensure_ascii=False, indent=1, default=str))


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = p.add_subparsers(dest="cmd", required=True)
    e = sub.add_parser("extract-trades")
    e.add_argument("--db", default="stock_tracking_db.sqlite")
    e.add_argument("--out", required=True)
    f = sub.add_parser("fetch-bars")
    f.add_argument("--trades", required=True)
    f.add_argument("--cache-dir", default="runtime/research/kr_micro_split_b3")
    f.add_argument("--out", required=True)
    f.add_argument("--pause", type=float, default=0.35)
    r = sub.add_parser("replay")
    r.add_argument("--trades", required=True)
    r.add_argument("--bars", required=True)
    r.add_argument("--out", required=True)
    a = p.parse_args(argv)
    if a.cmd == "extract-trades":
        extract_trades(a.db, a.out)
    elif a.cmd == "fetch-bars":
        fetch_bars(a.trades, a.cache_dir, a.out, a.pause)
    else:
        replay(a.trades, a.bars, a.out)


if __name__ == "__main__":
    sys.exit(main())
