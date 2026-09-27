"""Pure pivot/base detection and breakout triggers for re-entry v2 (design:
docs/REENTRY_V2_DESIGN_20260927_ko.md). No I/O; shared by replay and the live loop.

Simplified O'Neil pivot: the highest high of the last 7-65 completed sessions that
is at least 5 sessions old, with no higher high since, and a base no deeper than
35% (US) / 40% (KR). Buy zone: pivot to pivot +5%.

Bars are dicts {date, open, high, low, close, volume} sorted by date. Functions
that look at "day i" only use bars[:i] as completed history plus day i's own
prices where a trigger explicitly needs them, so replay has no look-ahead beyond
the simulated session.
"""
from __future__ import annotations

POLICY_VERSION = "pivot_reentry_v2"
BASE_MIN_BARS = 7
BASE_MAX_BARS = 65
PIVOT_MIN_AGE = 5
MAX_DEPTH = {"KR": 0.40, "US": 0.35}
READY_BAND = 0.05          # previous close within 5% below the pivot
BUY_ZONE = 0.05            # never pay more than pivot +5%
FOLLOW_THROUGH_VOLUME = 1.4
INTRADAY_VOLUME = 1.0      # cumulative session volume >= one full-day 20-day average (lower bound)
STOP_CAP = 0.07
BREAKEVEN_AFTER = 0.10
HOLD_BARS = 40
WATCH_BARS = 30


def _avg(values):
    values = list(values)
    return sum(values) / len(values) if values else 0.0


def find_pivot(bars, i, market):
    """Pivot as of the start of day i (uses bars[:i] only). None when there is no base."""
    window = bars[max(0, i - BASE_MAX_BARS):i]
    if len(window) < BASE_MIN_BARS + PIVOT_MIN_AGE:
        return None
    core = window[:-PIVOT_MIN_AGE]
    k = max(range(len(core)), key=lambda j: (core[j]["high"], -j))
    pivot = core[k]["high"]
    after = window[k + 1:]
    if any(b["high"] > pivot for b in after):
        return None                      # a newer high means no base has formed yet
    low = min(b["low"] for b in after)
    depth = 1 - low / pivot
    if depth > MAX_DEPTH[market]:
        return None
    return {"pivot": pivot, "pivot_date": core[k]["date"], "base_low": low, "depth": round(depth, 4),
            "base_bars": len(after)}


def trend_ok(bars, i):
    """Existing trend-gate semantics on completed bars: not T1 (close<MA50), not T2."""
    closes = [b["close"] for b in bars[:i]]
    if len(closes) < 55:
        return None
    close, ma50 = closes[-1], _avg(closes[-50:])
    ma20, ma20_prev = _avg(closes[-20:]), _avg(closes[-25:-5])
    t1 = close < ma50
    t2 = ma20 < ma20_prev and close <= ma20 * 0.95
    return not (t1 or t2)


def avg_volume(bars, i, n=20):
    prior = bars[max(0, i - n):i]
    return _avg(b["volume"] for b in prior) if len(prior) == n else None


def evaluate_day(bars, i, market):
    """State and trigger for day i. Returns dict with status and optional entry."""
    base = find_pivot(bars, i, market)
    trend = trend_ok(bars, i)
    out = {"date": bars[i]["date"], "base": base, "trend_ok": trend}
    if base is None or trend is not True:
        out["status"] = "NO_BASE" if base is None else "TREND_BLOCKED"
        return out
    pivot, prev, today = base["pivot"], bars[i - 1], bars[i]
    vol_avg = avg_volume(bars, i)
    ceiling = pivot * (1 + BUY_ZONE)
    # Follow-through: yesterday closed above the pivot (as of yesterday) on >=1.4x volume.
    prev_base = find_pivot(bars, i - 1, market)
    prev_avg = avg_volume(bars, i - 1)
    if prev_base and prev_avg and prev["close"] > prev_base["pivot"] and \
            prev["close"] <= prev_base["pivot"] * (1 + BUY_ZONE) and prev["volume"] >= FOLLOW_THROUGH_VOLUME * prev_avg:
        if today["open"] <= prev_base["pivot"] * (1 + BUY_ZONE):
            out.update(status="TRIGGERED", trigger="FOLLOW_THROUGH", entry=today["open"], pivot=prev_base["pivot"])
            return out
    ready = pivot * (1 - READY_BAND) <= prev["close"] <= pivot
    out["status"] = "READY" if ready else "WATCHING"
    if not ready:
        return out
    if today["open"] > ceiling:
        out["status"] = "CHASE_SKIPPED"
        return out
    # Intraday breakout proxy: the session crossed the pivot and its volume reached one
    # full-day average (the live loop requires cumulative volume >= that average).
    if today["high"] > pivot and vol_avg and today["volume"] >= INTRADAY_VOLUME * vol_avg:
        out.update(status="TRIGGERED", trigger="INTRADAY_BREAKOUT", entry=max(today["open"], pivot), pivot=pivot)
    return out


def _price(value):
    """key_levels price: 1700 / "1,700" / "1700~1800" (range midpoint), else None."""
    if isinstance(value, (int, float)):
        return float(value) if value > 0 else None
    if not isinstance(value, str):
        return None
    parts = [p.strip().replace(",", "") for p in value.replace("-", "~").split("~")]
    try:
        nums = [float(p) for p in parts if p]
    except ValueError:
        return None
    return sum(nums) / len(nums) if nums and min(nums) > 0 else None


def scenario_levels(scenario, reference):
    """Pivot and floor from the BUY scenario's key_levels, relative to the enrolment price.

    pivot: first resistance above the reference price (primary, else secondary).
    floor: secondary support (else primary); only armed while the reference sits above it,
    so a stop-out that already closed below the box does not end its own watch.
    """
    levels = ((scenario or {}).get("trading_scenarios") or {}).get("key_levels") or {}
    ref = float(reference or 0)
    pivot = next((v for v in (_price(levels.get("primary_resistance")), _price(levels.get("secondary_resistance")))
                  if v and v > ref), None)
    if pivot is None:
        return None
    floor = next((v for v in (_price(levels.get("secondary_support")), _price(levels.get("primary_support")))
                  if v and v < pivot), None)
    return {"pivot": pivot, "floor": floor if floor and ref >= floor else None, "source": "scenario"}


def evaluate_level_day(bars, i, level):
    """evaluate_day with a given pivot (scenario key level) instead of a detected base."""
    trend = trend_ok(bars, i)
    pivot = level["pivot"]
    out = {"date": bars[i]["date"], "base": level, "trend_ok": trend}
    if trend is not True:
        out["status"] = "TREND_BLOCKED"
        return out
    prev, today = bars[i - 1], bars[i]
    ceiling = pivot * (1 + BUY_ZONE)
    prev_avg = avg_volume(bars, i - 1)
    # Follow-through: yesterday was the FIRST close above the pivot, on >= 1.4x volume.
    if prev_avg and bars[i - 2]["close"] <= pivot < prev["close"] <= ceiling \
            and prev["volume"] >= FOLLOW_THROUGH_VOLUME * prev_avg and today["open"] <= ceiling:
        out.update(status="TRIGGERED", trigger="FOLLOW_THROUGH", entry=today["open"], pivot=pivot)
        return out
    ready = pivot * (1 - READY_BAND) <= prev["close"] <= pivot
    out["status"] = "READY" if ready else "WATCHING"
    if not ready:
        return out
    if today["open"] > ceiling:
        out["status"] = "CHASE_SKIPPED"
        return out
    vol_avg = avg_volume(bars, i)
    if today["high"] > pivot and vol_avg and today["volume"] >= INTRADAY_VOLUME * vol_avg:
        out.update(status="TRIGGERED", trigger="INTRADAY_BREAKOUT", entry=max(today["open"], pivot), pivot=pivot)
    return out


def simulate(bars, i, entry, *, intraday=True):
    """Managed exit from day i: 7% stop, breakeven after +10% close, MA20 trend exit, 40-bar horizon."""
    if i < 20:
        return {"status": "MISSING", "reason": "insufficient_history"}
    stop, mfe, mae = entry * (1 - STOP_CAP), 0.0, 0.0
    for k, bar in enumerate(bars[i:i + HOLD_BARS], start=1):
        mfe, mae = max(mfe, bar["high"] / entry - 1), min(mae, bar["low"] / entry - 1)
        if bar["low"] <= stop:
            price = stop if (k == 1 and intraday) or bar["open"] > stop else bar["open"]
            return {"status": "CLOSED", "exit_reason": "stop" if stop < entry else "breakeven_stop",
                    "exit_index": i + k - 1, "ret": round(price / entry - 1, 6), "bars": k,
                    "mfe": round(mfe, 6), "mae": round(mae, 6)}
        ma20 = _avg(b["close"] for b in bars[i + k - 20:i + k])
        if k >= 3 and bar["close"] < ma20:
            return {"status": "CLOSED", "exit_reason": "trend_exit_ma20", "exit_index": i + k - 1,
                    "ret": round(bar["close"] / entry - 1, 6), "bars": k, "mfe": round(mfe, 6), "mae": round(mae, 6)}
        if bar["close"] >= entry * (1 + BREAKEVEN_AFTER):
            stop = max(stop, entry)
    held = bars[i:i + HOLD_BARS]
    if len(held) < HOLD_BARS:
        return {"status": "PENDING", "bars": len(held)}
    return {"status": "CLOSED", "exit_reason": "horizon", "exit_index": i + HOLD_BARS - 1,
            "ret": round(held[-1]["close"] / entry - 1, 6), "bars": HOLD_BARS, "mfe": round(mfe, 6), "mae": round(mae, 6)}


def run_watch(bars, start_index, market, *, market_ok=None, watch_bars=WATCH_BARS, exit_fn=None, level=None):
    """Scan a watch from start_index for up to watch_bars sessions; first trigger wins.

    market_ok(date) -> bool|None gates triggers on the previous session's market state.
    level: scenario pivot/floor (scenario_levels); None uses the detected base. A close
    below an armed floor ends the watch as INVALIDATED.
    Also returns the READY_OPEN timing control (open of the first READY day).
    """
    ready_control = None
    events = {"chase_skipped": 0, "market_blocked": 0}
    for i in range(start_index, min(len(bars), start_index + watch_bars)):
        if i < 56:
            continue
        if level and level.get("floor") and bars[i - 1]["close"] < level["floor"]:
            return {"status": "INVALIDATED", "index": i - 1, "ready_control": ready_control, "events": events}
        day = evaluate_level_day(bars, i, level) if level else evaluate_day(bars, i, market)
        if day["status"] == "CHASE_SKIPPED":
            events["chase_skipped"] += 1
        if day["status"] in {"READY", "TRIGGERED"} and ready_control is None:
            ready_control = {"index": i, "entry": bars[i]["open"]}
        if day["status"] != "TRIGGERED":
            continue
        gate = market_ok(bars[i - 1]["date"]) if market_ok else True
        if gate is False:
            events["market_blocked"] += 1
            continue
        trade = (exit_fn or simulate)(bars, i, day["entry"], intraday=day["trigger"] == "INTRADAY_BREAKOUT")
        return {"status": "TRIGGERED", "day": day, "index": i, "trade": trade, "ready_control": ready_control,
                "events": events, "market_ok": gate}
    ended = min(len(bars), start_index + watch_bars) - 1
    complete = start_index + watch_bars <= len(bars)
    return {"status": "EXPIRED" if complete else "PENDING", "index": ended, "ready_control": ready_control,
            "events": events}


# --- Production-mirroring exit (cores/oneil_fallback + hardstop/trend-exit loops) -------------
PROD_STOP = 0.07            # TIER1 absolute stop, evaluated intraday by the hard-stop loop
PROD_WICK = 0.005           # STOP_WICK_BUFFER
PROD_TRAIL_ACTIVATION = 0.05
PROD_TRAIL_BULL, PROD_TRAIL_WEAK = 0.92, 0.95
PROD_MAX_BARS = 60          # production has no time exit; mark-to-market cap for replay only


def simulate_production(bars, i, entry, *, intraday=True, bull=None, close_stop=False, breakeven_lock=False):
    """Exit path of the live system, fixed as of 2026-09-27 (peak read from the scenario).

    * TIER1: last-trade <= entry*(1-7%)*(1-0.5%) intraday -> exit at that level (or the open on a gap).
      close_stop=True (hypothesis H1) checks the close instead, with a -10% intraday catastrophe floor.
    * TIER1.5: close below MA50 while losing -> exit at the close (close-window confirmation).
    * TIER2: once the closing peak >= +5%, close <= peak*band*(1-0.5%) -> exit at the close;
      band 0.92 when bull(date) else 0.95. breakeven_lock=True (hypothesis H2) also never lets
      an activated position close below entry.
    """
    if i < 50:
        return {"status": "MISSING", "reason": "insufficient_history"}
    hard = entry * (1 - PROD_STOP) * (1 - PROD_WICK)
    floor = entry * 0.90
    peak = entry
    mfe = mae = 0.0
    for k, bar in enumerate(bars[i:i + PROD_MAX_BARS], start=1):
        j = i + k - 1
        mfe, mae = max(mfe, bar["high"] / entry - 1), min(mae, bar["low"] / entry - 1)
        first_intraday = k == 1 and intraday
        if first_intraday:
            # Entry-day intraday order is unknown: a momentum entry is usually bought after the
            # session low, so the day's low must not count as a stop. Judge day 0 on its close.
            if bar["close"] <= hard:
                return _prod_close(entry, bar["close"], "tier1_day0_close", j, k, mfe, max(mae, bar["close"] / entry - 1))
            peak = max(peak, bar["close"])
            mfe, mae = max(bar["close"] / entry - 1, 0.0), min(bar["close"] / entry - 1, 0.0)
            continue
        if close_stop:
            if bar["low"] <= floor:
                price = floor if bar["open"] > floor else bar["open"]
                return _prod_close(entry, price, "tier1_floor", j, k, mfe, mae)
            if bar["close"] <= hard:
                return _prod_close(entry, bar["close"], "tier1_close", j, k, mfe, mae)
        elif bar["low"] <= hard:
            price = hard if bar["open"] > hard else bar["open"]
            return _prod_close(entry, price, "tier1_stop", j, k, mfe, mae)
        closes = [b["close"] for b in bars[:j + 1]]
        ma50 = _avg(closes[-50:])
        if bar["close"] < entry and bar["close"] < ma50 * (1 - PROD_WICK):
            return _prod_close(entry, bar["close"], "tier15_ma50", j, k, mfe, mae)
        if peak >= entry * (1 + PROD_TRAIL_ACTIVATION):
            band = PROD_TRAIL_BULL if (bull(bars[j - 1]["date"]) if bull else True) else PROD_TRAIL_WEAK
            line = peak * band * (1 - PROD_WICK)
            if breakeven_lock:
                line = max(line, entry)
            if bar["close"] <= line:
                return _prod_close(entry, bar["close"], "tier2_trail", j, k, mfe, mae)
        peak = max(peak, bar["close"])
    held = bars[i:i + PROD_MAX_BARS]
    if len(held) < PROD_MAX_BARS:
        return {"status": "PENDING", "bars": len(held)}
    return _prod_close(entry, held[-1]["close"], "horizon_mark", i + PROD_MAX_BARS - 1, PROD_MAX_BARS, mfe, mae)


def _prod_close(entry, price, reason, index, bars_held, mfe, mae):
    return {"status": "CLOSED", "exit_reason": reason, "exit_index": index, "ret": round(price / entry - 1, 6),
            "bars": bars_held, "mfe": round(mfe, 6), "mae": round(mae, 6)}
