#!/usr/bin/env python3
"""Offline replay for docs/entry-quality-experiments/kr-intraday-volume-pace-v1.md.

Research only: never imported by the trading path, never places orders.

  extract-decisions  (db-server, read-only): tracker rows -> JSON
  fetch-bars         (db-server, KIS reads): minute + daily bars per decision -> JSON (cached)
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
import statistics
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

RULE_VERSION = "kr-intraday-volume-pace-v1"
SAMPLE_START = "2025-12-10"
SAMPLE_END = "2026-09-30 23:59:59"
HOLDOUT_START = "2026-07-01"
SESSION_MINUTES = 390          # 09:00 -> 15:30
GAP_BUCKET = 0.01              # trigger definition: open / prev close - 1 >= 1%
SURGE = 2.0                    # momentum signal 1: >= 200% of the prior-20 average
BOOT = 2000
SEED = 20261001
H12 = {"max_false_positive": 0.05, "min_recall": 0.90, "max_median_abs_log": 0.10, "min_count": 10}
H3_MIN_N = 30

_MINUTE_URL = "/uapi/domestic-stock/v1/quotations/inquire-time-dailychartprice"
_MINUTE_TR = "FHKST03010230"
_TRACKER_SQL = ("SELECT id, ticker, trigger_type, trigger_mode, analyzed_date, analyzed_price, decision, "
                "tracked_7d_return, tracked_14d_return FROM analysis_performance_tracker "
                "WHERE analyzed_date >= ? AND analyzed_date <= ? ORDER BY id")


# ------------------------------------------------------------------ extract / fetch

def extract_decisions(db, out):
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    cols = ["id", "ticker", "trigger_type", "trigger_mode", "analyzed_date", "analyzed_price", "decision",
            "tracked_7d_return", "tracked_14d_return"]
    rows = [dict(zip(cols, r)) for r in conn.execute(_TRACKER_SQL, (SAMPLE_START, SAMPLE_END))]
    conn.close()
    Path(out).write_text(json.dumps({"rule_version": RULE_VERSION, "rows": rows}, ensure_ascii=False))
    print(f"rows={len(rows)} -> {out}")


def _minutes(source, ticker, day, pause):
    """{HHMMSS: volume} for one regular session, walking back from the close."""
    out, hour = {}, "153000"
    for _ in range(8):
        body = source._fetch(_MINUTE_URL, _MINUTE_TR, {
            "FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": ticker, "FID_INPUT_HOUR_1": hour,
            "FID_INPUT_DATE_1": day, "FID_PW_DATA_INCU_YN": "N", "FID_FAKE_TICK_INCU_YN": ""})
        rows = [r for r in (getattr(body, "output2", None) or []) if r.get("stck_bsop_date") == day]
        fresh = [r for r in rows if r["stck_cntg_hour"] not in out]
        time.sleep(pause)
        if not fresh:
            break
        for r in fresh:
            out[r["stck_cntg_hour"]] = int(float(r.get("cntg_vol") or 0))
        hour = min(out)
        if hour <= "090000":
            break
    return out


def fetch_bars(decisions, cache_dir, out, pause=0.35):
    from cores.market_data.kis_source import KisSource

    source = KisSource()
    cache = Path(cache_dir)
    cache.mkdir(parents=True, exist_ok=True)
    rows = json.loads(Path(decisions).read_text())["rows"]
    days = sorted({(r["ticker"], str(r["analyzed_date"])[:10].replace("-", "")) for r in rows})
    by_ticker = {}
    for ticker, day in days:
        by_ticker.setdefault(ticker, []).append(day)
    bars = {"minutes": {}, "daily": {}}
    for i, (ticker, ticker_days) in enumerate(sorted(by_ticker.items()), 1):
        daily_file = cache / f"daily_{ticker}_{ticker_days[0]}_{ticker_days[-1]}.json"
        if not daily_file.exists():
            start = (datetime.strptime(ticker_days[0], "%Y%m%d") - timedelta(days=60)).strftime("%Y%m%d")
            try:
                frame = source.price_history(ticker, start, ticker_days[-1], adjusted=False)
                daily = [{"date": ix.strftime("%Y%m%d"), "open": float(r["Open"]), "close": float(r["Close"]),
                          "volume": float(r["Volume"])} for ix, r in frame.iterrows()]
            except Exception as error:  # noqa: BLE001 - missing stays MISSING
                daily = {"error": type(error).__name__}
            daily_file.write_text(json.dumps(daily))
            time.sleep(pause)
        bars["daily"][ticker] = json.loads(daily_file.read_text())
        for day in ticker_days:
            minute_file = cache / f"minutes_{ticker}_{day}.json"
            if not minute_file.exists():
                try:
                    minute_file.write_text(json.dumps(_minutes(source, ticker, day, pause)))
                except Exception as error:  # noqa: BLE001
                    minute_file.write_text(json.dumps({"error": type(error).__name__}))
            bars["minutes"][f"{ticker}_{day}"] = json.loads(minute_file.read_text())
        if i % 25 == 0:
            print(f"tickers {i}/{len(by_ticker)}", flush=True)
    Path(out).write_text(json.dumps({"rule_version": RULE_VERSION, "bars": bars}))
    print(f"stock_days={len(days)} tickers={len(by_ticker)} -> {out}")


# ------------------------------------------------------------------ features

def minute_index(hhmmss):
    """Minutes since 09:00 of a bar label or decision time."""
    h, m = int(str(hhmmss)[:2]), int(str(hhmmss)[2:4])
    return (h - 9) * 60 + m


def cumulative(minutes):
    """cum[m] = volume of bars starting before minute m (m = 0..SESSION_MINUTES)."""
    per = [0] * (SESSION_MINUTES + 1)
    for label, volume in minutes.items():
        idx = minute_index(label)
        if 0 <= idx <= SESSION_MINUTES:
            per[idx] += volume
    cum, total = [0] * (SESSION_MINUTES + 1), 0
    for m in range(SESSION_MINUTES + 1):
        cum[m] = total
        total += per[m]
    return cum


def day_features(row, daily, minutes):
    """Decision-time volume facts for one tracker row, or (None, reason)."""
    if not isinstance(daily, list) or not isinstance(minutes, dict) or "error" in minutes or not minutes:
        return None, "no_bars"
    day = str(row["analyzed_date"])[:10].replace("-", "")
    idx = next((i for i, b in enumerate(daily) if b["date"] == day), None)
    if idx is None or idx < 20:
        return None, "no_daily_history"
    prior = daily[idx - 20:idx]
    a20 = sum(b["volume"] for b in prior) / 20
    final = daily[idx]["volume"]
    if a20 <= 0 or final <= 0:
        return None, "no_daily_history"
    stamp = datetime.fromisoformat(str(row["analyzed_date"])[:19])
    m = min(max(minute_index(stamp.strftime("%H%M%S")), 1), SESSION_MINUTES)
    cum = cumulative(minutes)
    gap = daily[idx]["open"] / daily[idx - 1]["close"] - 1 if daily[idx - 1]["close"] else None
    return {"minute": m, "cum": cum, "v_cum": cum[m], "v_final": final, "a20": a20,
            "v_prev": daily[idx - 1]["volume"], "gap": gap,
            "bucket": "GAP" if gap is not None and gap >= GAP_BUCKET else "NORMAL"}, None


def share_curves(train):
    """Median cum(m)/final per bucket from discovery rows only."""
    curves = {}
    for bucket in ("GAP", "NORMAL"):
        group = [f for f in train if f["bucket"] == bucket]
        curves[bucket] = [statistics.median(f["cum"][m] / f["v_final"] for f in group) if group else None
                          for m in range(SESSION_MINUTES + 1)]
    return curves


def project(feature, curves):
    share = curves[feature["bucket"]][feature["minute"]]
    if not share:
        return None
    return feature["v_cum"] / share


# ------------------------------------------------------------------ stats

def _mean(xs):
    return sum(xs) / len(xs) if xs else None


def accuracy(rows):
    """H1/H2 metrics on rows carrying v_hat, v_final, a20, v_cum."""
    rows = [r for r in rows if r["v_hat"]]
    actual = [r["v_final"] >= SURGE * r["a20"] for r in rows]
    pred = [r["v_hat"] >= SURGE * r["a20"] for r in rows]
    lower = [r["v_cum"] >= SURGE * r["a20"] for r in rows]
    n_pred, n_act = sum(pred), sum(actual)
    false_pos = sum(p and not a for p, a in zip(pred, actual))
    hits = sum(p and a for p, a in zip(pred, actual))
    errors = [abs(math.log(r["v_hat"] / r["v_final"])) for r in rows]
    return {"n": len(rows), "actual_surge": n_act, "pred_surge": n_pred,
            "false_positive_rate": false_pos / n_pred if n_pred else None,
            "recall": hits / n_act if n_act else None,
            "median_abs_log_error": statistics.median(errors) if errors else None,
            "lower_bound_recall": sum(lo and a for lo, a in zip(lower, actual)) / n_act if n_act else None}


def accuracy_verdict(m):
    if m["actual_surge"] < H12["min_count"] or m["pred_surge"] < H12["min_count"]:
        return "INSUFFICIENT"
    ok = (m["false_positive_rate"] <= H12["max_false_positive"] and m["recall"] >= H12["min_recall"]
          and m["median_abs_log_error"] <= H12["max_median_abs_log"])
    return "ADOPT_CANDIDATE" if ok else "RETIRE"


def cluster_boot_diff(rows, key, value):
    rng = random.Random(SEED)
    by = {}
    for r in rows:
        by.setdefault(r["ticker"], []).append(r)
    tickers = sorted(by)
    diffs = []
    for _ in range(BOOT):
        sample = [r for t in (rng.choice(tickers) for _ in tickers) for r in by[t]]
        a = [r[value] for r in sample if key(r)]
        b = [r[value] for r in sample if not key(r)]
        if a and b:
            diffs.append(_mean(a) - _mean(b))
    diffs.sort()
    if not diffs:
        return [None, None]
    return [diffs[int(0.05 * len(diffs))], diffs[int(0.95 * len(diffs)) - 1]]


def compare(rows, key, value="tracked_7d_return"):
    rows = [r for r in rows if r.get(value) is not None]
    a = [r[value] for r in rows if key(r)]
    b = [r[value] for r in rows if not key(r)]
    return {"n_a": len(a), "n_b": len(b), "mean_a": _mean(a), "mean_b": _mean(b),
            "diff": (_mean(a) - _mean(b)) if a and b else None, "ci90": cluster_boot_diff(rows, key, value),
            "enough": len(a) >= H3_MIN_N and len(b) >= H3_MIN_N}


def outcome_verdict(disc, hold):
    if not (disc["enough"] and hold["enough"]) or hold["diff"] is None:
        return "INSUFFICIENT"
    if hold["diff"] > 0 and hold["ci90"][0] is not None and hold["ci90"][0] > 0 and (disc["diff"] or 0) > 0:
        return "ADOPT_CANDIDATE"
    return "RETIRE"


def replay(decisions, bars_file, out):
    rows = json.loads(Path(decisions).read_text())["rows"]
    bars = json.loads(Path(bars_file).read_text())["bars"]
    enriched, missing = [], {"no_mode": 0, "no_bars": 0, "no_daily_history": 0}
    for r in rows:
        if r.get("trigger_mode") not in ("morning", "afternoon"):
            missing["no_mode"] += 1
            continue
        day = str(r["analyzed_date"])[:10].replace("-", "")
        feature, reason = day_features(r, bars["daily"].get(r["ticker"]),
                                       bars["minutes"].get(f"{r['ticker']}_{day}"))
        if feature is None:
            missing[reason] += 1
            continue
        enriched.append({**r, **feature, "session": str(r["analyzed_date"])[:10]})
    disc = [r for r in enriched if r["session"] < HOLDOUT_START]
    hold = [r for r in enriched if r["session"] >= HOLDOUT_START]
    curves = share_curves(disc)
    for r in enriched:
        r["v_hat"] = project(r, curves)
        r["actual_surge"] = r["v_final"] >= SURGE * r["a20"]
        r["pred_surge"] = bool(r["v_hat"]) and r["v_hat"] >= SURGE * r["a20"]
        r["prev_day_reached"] = r["v_cum"] >= r["v_prev"]

    def acc(group, mode, bucket=None):
        return accuracy([r for r in group if r["trigger_mode"] == mode and (bucket is None or r["bucket"] == bucket)])

    h1 = {"discovery": acc(disc, "afternoon"), "holdout": acc(hold, "afternoon")}
    h1["verdict"] = accuracy_verdict(h1["holdout"])
    h2 = {"discovery": acc(disc, "morning"), "holdout": acc(hold, "morning"),
          "holdout_GAP": acc(hold, "morning", "GAP"), "holdout_NORMAL": acc(hold, "morning", "NORMAL")}
    h2["verdict"] = accuracy_verdict(h2["holdout"])
    morning_d = [r for r in disc if r["trigger_mode"] == "morning"]
    morning_h = [r for r in hold if r["trigger_mode"] == "morning"]
    h3 = {}
    for name, key in (("a_actual_surge", lambda r: r["actual_surge"]),
                      ("b_projected_surge", lambda r: r["pred_surge"]),
                      ("c_prev_day_reached", lambda r: r["prev_day_reached"])):
        d, h = compare(morning_d, key), compare(morning_h, key)
        h3[name] = {"discovery": d, "holdout": h, "verdict": outcome_verdict(d, h)}
    if h3["a_actual_surge"]["verdict"] != "ADOPT_CANDIDATE":
        for name in ("b_projected_surge", "c_prev_day_reached"):
            h3[name]["verdict"] = "RETIRE_UPPER_BOUND_NOT_ADOPTED"
    checkpoints = {f"{9 + m // 60:02d}:{m % 60:02d}": {b: curves[b][m] for b in curves}
                   for m in (30, 60, 67, 120, 240, 330, 350)}
    summary = {
        "rule_version": RULE_VERSION,
        "inputs_sha256": hashlib.sha256(Path(decisions).read_bytes() + Path(bars_file).read_bytes()).hexdigest(),
        "rows": len(rows), "used": len(enriched), "missing": missing,
        "discovery_n": len(disc), "holdout_n": len(hold),
        "curve_share_at": checkpoints, "H1": h1, "H2": h2, "H3": h3,
    }
    slim = [{k: v for k, v in r.items() if k != "cum"} for r in enriched]
    Path(out).write_text(json.dumps({"summary": summary, "curves": curves, "rows": slim},
                                    ensure_ascii=False, indent=1))
    print(json.dumps(summary, ensure_ascii=False, indent=1))


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = p.add_subparsers(dest="cmd", required=True)
    e = sub.add_parser("extract-decisions")
    e.add_argument("--db", default="stock_tracking_db.sqlite")
    e.add_argument("--out", required=True)
    f = sub.add_parser("fetch-bars")
    f.add_argument("--decisions", required=True)
    f.add_argument("--cache-dir", default="runtime/research/kr_intraday_volume_pace")
    f.add_argument("--out", required=True)
    f.add_argument("--pause", type=float, default=0.35)
    r = sub.add_parser("replay")
    r.add_argument("--decisions", required=True)
    r.add_argument("--bars", required=True)
    r.add_argument("--out", required=True)
    a = p.parse_args(argv)
    if a.cmd == "extract-decisions":
        extract_decisions(a.db, a.out)
    elif a.cmd == "fetch-bars":
        fetch_bars(a.decisions, a.cache_dir, a.out, a.pause)
    else:
        replay(a.decisions, a.bars, a.out)


if __name__ == "__main__":
    sys.exit(main())
