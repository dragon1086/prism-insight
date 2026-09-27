"""Daily-bar replay of re-entry v2 (pivot breakout) on past skipped / blocked / stopped names.

    python tools/replay_pivot_reentry.py --db stock_tracking_db.sqlite --market KR \
        --bars bars_KR.json --bench bench_KR.json [--bench-code 1001]

bars JSON: {ticker: {YYYY-MM-DD: [open, high, low, close, volume]}}
bench JSON: {"bars": {code: {YYYY-MM-DD: [o, h, l, c, v]}}}

Replays the pure rules in prism_core/pivot_reentry.py day by day. The market gate is
the production MarketPulse state machine replayed over the benchmark (CORRECTION
blocks triggers). Controls on the same watch: ORIGINAL (next open after the
decision), READY_OPEN (first day near the pivot, no breakout needed) and no entry.
Returns are also reported relative to the benchmark over the same holding window.
Development evidence only: intraday timing is approximated from daily bars.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import statistics
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from observability.reentry_shadow import session_date  # noqa: E402
from prism_core import pivot_reentry as P  # noqa: E402

GAP = 2
# Fixed statements per market: no table name is ever interpolated into SQL.
STOP_SQL = {
    "KR": "SELECT ticker, sell_date, sell_price, profit_rate, exit_kind, scenario FROM trading_history",
    "US": "SELECT ticker, sell_date, sell_price, profit_rate, exit_kind, scenario FROM us_trading_history",
}
SKIP_SQL = {
    "KR": """SELECT p.ticker, p.analyzed_date, p.analyzed_price, p.decision, p.buy_score, p.min_score, w.scenario,
                    p.decision_id, p.skip_reason
             FROM analysis_performance_tracker p LEFT JOIN watchlist_history w ON w.id = p.watchlist_id
             WHERE COALESCE(p.was_traded, 0) = 0""",
    # Older US tracker rows carry no decision_id, so the skip list itself is the source.
    "US": """SELECT ticker, analyzed_date, current_price, decision, buy_score, min_score, scenario,
                    json_extract(scenario, '$._decision_id'), skip_reason
             FROM us_watchlist_history WHERE COALESCE(was_traded, 0) = 0""",
}


def load_bars(raw):
    return [{"date": d, "open": v[0], "high": v[1], "low": v[2], "close": v[3], "volume": v[4]}
            for d, v in sorted(raw.items()) if min(v[:4]) > 0 and v[4] > 0]


def _scenario(text):
    try:
        value = json.loads(text or "{}")
        return value if isinstance(value, dict) else {}
    except ValueError:
        return {}


def enrolments(db_path, market):
    """Enrolment dicts from the trade DB, read-only, oldest first."""
    rows = []
    with sqlite3.connect("file:" + str(db_path) + "?mode=ro", uri=True) as conn:
        for ticker, sell_date, sell_price, pnl, kind, scenario in conn.execute(STOP_SQL[market]):
            if kind == "stop" or (kind is None and pnl is not None and pnl <= -3):
                scen = _scenario(scenario)
                rows.append({"source": "STOP_EXIT", "ticker": str(ticker), "date": session_date(sell_date, market),
                             "price": sell_price, "scenario": scen,
                             "fundamentals": (scen.get("fundamental_check") or {}).get("all_passed"),
                             "decision_id": scen.get("_decision_id"), "reason": f"stopped out ({pnl:.1f}%)"})
        for ticker, stamp, price, decision, score, minimum, scenario, decision_id, skip in conn.execute(SKIP_SQL[market]):
            scen = _scenario(scenario)
            passed = (scen.get("fundamental_check") or {}).get("all_passed")
            entered = str(decision or "").strip().lower() in {"enter", "진입", "entry"}
            if entered:
                source = "ENTER_BLOCKED"
            elif score is not None and minimum is not None and minimum - score <= GAP and passed is not False:
                source = "LOCATION_SKIP"
            else:
                continue
            rows.append({"source": source, "ticker": str(ticker), "date": session_date(stamp, market), "price": price,
                         "scenario": scen, "fundamentals": passed, "decision_id": decision_id, "reason": skip,
                         "score": score, "min_score": minimum})
    rows.sort(key=lambda r: (r["date"], r["ticker"], r["source"]))
    return rows


def pulse_gate(bench_bars):
    """date -> False when the production MarketPulse state after that session is CORRECTION."""
    from cores.market_pulse import CORRECTION, DailyBar, MarketPulse
    pulse, states = MarketPulse(), {}
    for bar in bench_bars:
        states[bar["date"]] = pulse.feed(DailyBar(date=bar["date"], close=bar["close"],
                                                  volume=bar["volume"] if bar["volume"] > 1 else None))
    return lambda day: None if day not in states else states[day] != CORRECTION


def bench_return(bench_by_date, bars, entry_index, exit_index, entry_at_open=True):
    start, end = bars[entry_index]["date"], bars[exit_index]["date"]
    a, b = bench_by_date.get(start), bench_by_date.get(end)
    if not a or not b:
        return None
    return b["close"] / (a["open"] if entry_at_open else a["close"]) - 1


def bull_fn(bench_bars):
    """Live-regime proxy for the trailing band: benchmark close above its MA50 = bull."""
    closes, flags = [], {}
    for bar in bench_bars:
        closes.append(bar["close"])
        flags[bar["date"]] = len(closes) >= 50 and closes[-1] > sum(closes[-50:]) / 50
    return lambda day: flags.get(day, True)


def replay(db_path, market, bars_by_ticker, bench_bars, exit_mode="production", levels="computed", **exit_kwargs):
    gate = pulse_gate(bench_bars)
    if exit_mode == "production":
        bull = bull_fn(bench_bars)

        def exit_fn(bars, i, entry, intraday=True):
            return P.simulate_production(bars, i, entry, intraday=intraday, bull=bull, **exit_kwargs)
    else:
        exit_fn = P.simulate
    bench_by_date = {b["date"]: b for b in bench_bars}
    live_until = {}
    results = []
    for item in enrolments(db_path, market):
        source, ticker, day, price, passed = item["source"], item["ticker"], item["date"], item["price"], item["fundamentals"]
        bars = bars_by_ticker.get(ticker)
        if not bars:
            results.append({"source": source, "ticker": ticker, "status": "MISSING", "reason": "no_bars"})
            continue
        start = next((k for k, b in enumerate(bars) if b["date"] > day), None)
        if start is None:
            continue
        anchor = next((b for b in bars if b["date"] == day), None)
        if anchor is None or not (anchor["low"] * 0.97 <= float(price or 0) <= anchor["high"] * 1.03):
            results.append({"source": source, "ticker": ticker, "status": "MISSING", "reason": "price_basis"})
            continue
        key = (source, ticker)
        if live_until.get(key, "") >= day:
            continue                     # an earlier watch of this name/source was still live
        level = P.scenario_levels(item.get("scenario"), price) if levels == "scenario" else None
        watch = P.run_watch(bars, start, market, market_ok=gate, exit_fn=exit_fn, level=level)
        live_until[key] = bars[watch["index"]]["date"]
        row = {"source": source, "ticker": ticker, "date": day, "fundamentals": passed, "status": watch["status"],
               "level_source": (level or {}).get("source", "computed"), "level": level,
               "events": watch["events"], "decision_id": item.get("decision_id"), "reason": item.get("reason"),
               "score": item.get("score"), "min_score": item.get("min_score")}
        if watch["status"] == "TRIGGERED":
            trade = watch["trade"]
            row.update(trigger=watch["day"]["trigger"], trade=trade, trigger_index=watch["index"],
                       trigger_date=bars[watch["index"]]["date"], entry=watch["day"]["entry"],
                       pivot=watch["day"].get("pivot"))
            if trade.get("status") == "CLOSED":
                bench = bench_return(bench_by_date, bars, watch["index"], trade["exit_index"])
                row["excess"] = None if bench is None else trade["ret"] - bench
        controls = {}
        if source != "STOP_EXIT" and start + 1 < len(bars):
            controls["ORIGINAL"] = (start, exit_fn(bars, start, bars[start]["open"], intraday=False))
        if watch.get("ready_control"):
            i = watch["ready_control"]["index"]
            controls["READY_OPEN"] = (i, exit_fn(bars, i, bars[i]["open"], intraday=False))
        row["controls"] = {}
        for name, (i, trade) in controls.items():
            if trade.get("status") == "CLOSED":
                bench = bench_return(bench_by_date, bars, i, trade["exit_index"])
                row["controls"][name] = {"ret": trade["ret"], "excess": None if bench is None else trade["ret"] - bench}
        results.append(row)
    return results


def _stats(values):
    values = [v for v in values if v is not None]
    if not values:
        return {"n": 0}
    out = {"n": len(values), "mean": round(statistics.mean(values), 4), "median": round(statistics.median(values), 4),
           "win": round(sum(v > 0 for v in values) / len(values), 3)}
    if len(values) > 2:
        out["mean_wo_best"] = round(statistics.mean(sorted(values)[:-1]), 4)
    return out


def summarize(results):
    groups = defaultdict(list)
    counts = defaultdict(lambda: defaultdict(int))
    for row in results:
        counts[row["source"]][row["status"]] += 1
        trade = row.get("trade") or {}
        if trade.get("status") == "CLOSED":
            for label in ("ALL", row["trigger"]):
                groups[(row["source"], "PIVOT_" + label, "ret")].append(trade["ret"])
                groups[(row["source"], "PIVOT_" + label, "excess")].append(row.get("excess"))
            # Paired: the same watch's controls, only where the pivot trade closed.
            for name, value in row["controls"].items():
                groups[(row["source"], "PAIRED_" + name, "ret")].append(value["ret"])
                groups[(row["source"], "PAIRED_" + name, "excess")].append(value["excess"])
        for name, value in (row.get("controls") or {}).items():
            groups[(row["source"], "ALL_" + name, "ret")].append(value["ret"])
            groups[(row["source"], "ALL_" + name, "excess")].append(value["excess"])
    return {"counts": {k: dict(v) for k, v in counts.items()},
            "stats": {"|".join(k): _stats(v) for k, v in sorted(groups.items())}}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", required=True)
    parser.add_argument("--market", choices=["KR", "US"], required=True)
    parser.add_argument("--bars", required=True)
    parser.add_argument("--bench", required=True)
    parser.add_argument("--bench-code", default=None)
    parser.add_argument("--out")
    parser.add_argument("--exit", choices=["production", "v1"], default="production")
    parser.add_argument("--levels", choices=["computed", "scenario"], default="computed",
                        help="pivot from the detected base, or from the BUY scenario key_levels (fallback: computed)")
    args = parser.parse_args(argv)
    bars = {t: load_bars(v) for t, v in json.loads(Path(args.bars).read_text()).items()}
    bench_raw = json.loads(Path(args.bench).read_text())["bars"]
    bench = load_bars(bench_raw[args.bench_code or next(iter(bench_raw))])
    results = replay(args.db, args.market, bars, bench, exit_mode=args.exit, levels=args.levels)
    if args.out:
        Path(args.out).write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in results) + "\n")
    print(json.dumps(summarize(results), ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
