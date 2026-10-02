#!/usr/bin/env python3
"""Offline replay for docs/entry-quality-experiments/kr-micro-split-add-v1.md.

Research only: never imported by the trading path, never places orders.

  extract-trades  (db-server, read-only): KR trading_history rows -> JSON
  fetch-daily     (db-server, KIS reads): unadjusted daily bars per ticker -> JSON (cached)
  fetch-minutes   (db-server, KIS reads): 1-min bars for add-candidate days -> JSON (cached)
  replay          (anywhere): arms L/M0/M1/M2/M3 -> JSON

Definitions, sample and verdict thresholds are fixed by the preregistration.
Risk clip uses fee=0 (research approximation; documented limitation).
Two-bar persistence resets at session boundaries.
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
from itertools import pairwise
from pathlib import Path

# ------------------------------------------------------------------ constants

RULE_VERSION = "kr-micro-split-add-v1"
SAMPLE_START, SAMPLE_END = "2025-10-01", "2026-09-30 23:59:59"
HOLDOUT_START = "2026-07-01"
ADD_WINDOW_CAL = 14          # calendar days from entry (inclusive)
BAND_TOP = 1.10              # price cap for adds
STEP_A = 1.02                # 80% target threshold (M0/M1/M3)
STEP_B = 1.04                # 100% target threshold (M0/M1/M3)
ADD_COST = 0.0025            # cost fraction per added allocation unit
BOOT, SEED = 2000, 20261004
MIN_HOLDOUT = 30
MIN_HOLDOUT_ADD = 5          # min add-trades for M2/M3 verdict
MISSING_RATIO_INTRADAY = 0.20  # INCONCLUSIVE threshold for M0/M1/M2
STRONG_RETURN_60 = 0.20
STRONG_VOL_MULT = 1.5
ARMS = ("L", "M0", "M1", "M2", "M3")

_MINUTE_URL = "/uapi/domestic-stock/v1/quotations/inquire-time-dailychartprice"
_MINUTE_TR = "FHKST03010230"

_SQL = (
    "SELECT id, ticker, company_name, buy_date, buy_price, sell_date, sell_price, "
    "exit_kind, trigger_type, scenario "
    "FROM trading_history "
    "WHERE buy_date >= ? AND buy_date <= ? AND sell_date IS NOT NULL ORDER BY buy_date, id"
)


# ------------------------------------------------------------------ shared helpers (from B3 tool)

def _mean(xs):
    return sum(xs) / len(xs) if xs else None


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
    trs = [
        max(b["high"] - b["low"], abs(b["high"] - a["close"]), abs(b["low"] - a["close"]))
        for a, b in pairwise(seg)
    ]
    return sum(trs) / len(trs)


def mdd(rows, key):
    level = peak = worst = 0.0
    for r in sorted(rows, key=lambda x: x["sell_date"]):
        level += r[key] / 10
        peak = max(peak, level)
        worst = min(worst, level - peak)
    return worst


# ------------------------------------------------------------------ regime

def _parse_scenario(text):
    try:
        return json.loads(text or "{}")
    except (TypeError, ValueError):
        return {}


def _scenario_regime(text):
    s = _parse_scenario(text)
    return s.get("market_regime") or s.get("market_condition")


def classify_regime(text):
    """Return 'bull', 'sideways', or 'bear'."""
    t = str(text or "").strip()
    tl = t.lower()
    if (
        tl.startswith(("parabolic", "strong_bull", "moderate_bull"))
        or "상승추세" in t
        or "강세" in t
    ):
        return "bull"
    if tl.startswith("sideways") or "횡보" in t:
        return "sideways"
    return "bear"


# ------------------------------------------------------------------ 5-min aggregation

def agg_5min(minute_dict):
    """Aggregate 1-min bars into 5-min bars on 09:00 session boundaries.

    Args:
        minute_dict: {HHMMSS: {close, open, high, low, vol}}

    Returns:
        Sorted list of (label_HHMMSS, {open, close, high, low, vol}).
        Bars with minute label before 09:00 are excluded.
        open = first 1-min bar's open; close = last 1-min bar's close.
    """
    slots: dict[str, dict] = {}
    for t in sorted(minute_dict):
        if not (isinstance(t, str) and len(t) == 6 and t.isdigit()):
            continue
        h, m = int(t[:2]), int(t[2:4])
        total_min = h * 60 + m
        if total_min < 9 * 60:
            continue
        slot_off = (total_min - 9 * 60) // 5
        slot_min = 9 * 60 + slot_off * 5
        sh, sm = slot_min // 60, slot_min % 60
        label = f"{sh:02d}{sm:02d}00"
        data = minute_dict[t]
        c = float(data.get("close") or 0)
        o = float(data.get("open") or c)
        hi = float(data.get("high") or c)
        lo = float(data.get("low") or c)
        v = int(data.get("vol") or 0)
        if label not in slots:
            slots[label] = {"open": o, "close": c, "high": hi, "low": lo, "vol": v}
        else:
            b = slots[label]
            # open stays as the first minute; close updates to latest
            b["close"] = c
            b["high"] = max(b["high"], hi)
            b["low"] = min(b["low"], lo)
            b["vol"] += v
    return sorted(slots.items())


# ------------------------------------------------------------------ risk clip

def _risk_clip(legs, entry, stop, add_price, nominal_add):
    """Return actual add fraction after applying risk limit (fee=0).

    risk_limit = (entry - stop) / entry
    base_loss = sum(frac_i * (1 - stop/price_i)) for existing legs
    marginal = 1 - stop/add_price
    allowed = (risk_limit - base_loss) / marginal
    """
    if entry <= 0 or stop <= 0 or add_price <= 0 or stop >= entry:
        return nominal_add
    risk_limit = (entry - stop) / entry
    base_loss = sum(f * (1.0 - stop / p) for f, p in legs)
    marginal = 1.0 - stop / add_price
    if marginal <= 0:
        return 0.0
    if base_loss >= risk_limit:
        return 0.0
    allowed = (risk_limit - base_loss) / marginal
    return min(nominal_add, allowed)


# ------------------------------------------------------------------ daily feature precomputation

def _sma(closes, n):
    if len(closes) < n:
        return None
    return sum(closes[-n:]) / n


def _features_from_prior(prior_bars):
    """Compute ATR14, SMA20, MA50+slope, 60-session return, vol20_avg from bars BEFORE a session.

    prior_bars: sorted list of bar dicts {date, high, low, close, vol}.
    Returns dict or None if insufficient history (< 15 bars).
    """
    if len(prior_bars) < 15:
        return None
    closes = [b["close"] for b in prior_bars]
    volumes = [b.get("vol", 0) for b in prior_bars]
    a = atr14(prior_bars)
    sma20 = _sma(closes, 20)
    ma50 = _sma(closes, 50)
    # MA50 slope: compare current MA50 to MA50 20 sessions ago
    ma50_prev = _sma(closes[:-20], 50) if len(closes) >= 70 else None
    ret60 = (closes[-1] / closes[-61] - 1) if len(closes) >= 61 else None
    vol20_avg = _mean(volumes[-20:]) if len(volumes) >= 20 else None
    return {
        "atr14": a,
        "sma20": sma20,
        "ma50": ma50,
        "ma50_prev": ma50_prev,
        "return60": ret60,
        "vol20_avg": vol20_avg,
        "prev_close": closes[-1],
        "prev_vol": volumes[-1] if volumes else 0,
    }


def _precompute_features(sessions, bar_by):
    """Return {session_date: features_dict_for_bars_before_that_session}."""
    result = {}
    for i, day in enumerate(sessions):
        prior = [bar_by[d] for d in sessions[:i]]
        result[day] = _features_from_prior(prior)
    return result


# ------------------------------------------------------------------ strong-stock gate (M1)

def _is_strong_stock(feat):
    """Return True if M1 strong-stock criteria are met at the add session.

    Evaluated using feat = features computed from bars BEFORE the add session.
    Criteria:
      1. 60-session return >= 20%
      2. prev_close > MA50 and MA50 is rising (current > 20-sessions-ago)
      3. prev_vol >= vol20_avg * 1.5
    """
    if feat is None:
        return False
    ret60 = feat.get("return60")
    ma50 = feat.get("ma50")
    ma50_prev = feat.get("ma50_prev")
    vol20_avg = feat.get("vol20_avg")
    prev_close = feat.get("prev_close")
    prev_vol = feat.get("prev_vol", 0)

    if ret60 is None or ret60 < STRONG_RETURN_60:
        return False
    if ma50 is None or prev_close is None or prev_close <= ma50:
        return False
    if ma50_prev is None or ma50 <= ma50_prev:
        return False
    return not (vol20_avg is None or vol20_avg <= 0 or prev_vol < vol20_avg * STRONG_VOL_MULT)


# ------------------------------------------------------------------ intraday simulation (M0 / M1 / M2)

def _simulate_intraday(trade, sessions, session_idx, bar_by, feat_by, minutes_store, arm):
    """Simulate one intraday add arm for one trade.

    Returns (legs, n_missing_candidate_days).
    legs = None signals the trade could not be simulated (no history / no session).
    n_missing_candidate_days = count of candidate days with absent minute data.
    """
    entry = float(trade["buy_price"])
    sell_day = trade["sell_date"][:10]
    buy_day = trade["buy_date"][:10]
    regime = classify_regime(trade.get("regime"))
    stop = trade.get("stop_loss")

    # Regime gate: which regimes allow adds
    if arm in ("M0", "M2"):
        regime_allows = (regime == "bull")
    elif arm == "M1":
        regime_allows = regime in ("bull", "sideways")
    else:
        regime_allows = False

    if buy_day not in session_idx:
        return None, 0

    feat_entry = feat_by.get(buy_day)
    if feat_entry is None or feat_entry.get("atr14") is None:
        return None, 0

    from prism_core.oneil_adaptive_policy import initial_sizing
    _, initial = initial_sizing(entry, feat_entry["atr14"])
    initial_frac = float(initial)

    # M2 ATR-scaled thresholds
    if arm == "M2":
        atr_frac = feat_entry["atr14"] / entry if entry > 0 else 0
        step_a_arm = 1 + max(0.02, 0.5 * atr_frac)
        step_b_arm = 1 + max(0.04, 1.0 * atr_frac)
    else:
        step_a_arm = STEP_A
        step_b_arm = STEP_B

    entry_dt = datetime.strptime(buy_day, "%Y-%m-%d")  # noqa: DTZ007
    add_end_day = (entry_dt + timedelta(days=ADD_WINDOW_CAL)).strftime("%Y-%m-%d")

    # Buy time HHMMSS for filtering bars on entry day
    ts = trade.get("buy_date", "")
    buy_time = (ts[11:13] + ts[14:16] + "00") if len(ts) >= 16 else "000000"

    legs = [(initial_frac, entry)]
    n_missing = 0

    # prev_5min_close: reset at session boundary
    prev5_close = None

    for day in sessions:
        if day < buy_day or day >= sell_day or day > add_end_day:
            prev5_close = None
            continue

        db = bar_by.get(day)
        if db is None:
            prev5_close = None
            continue

        day_high = db.get("high", 0)
        # Candidate day gate: daily high >= entry * STEP_A (1.02)
        if day_high < entry * STEP_A:
            prev5_close = None
            continue

        # This is a candidate day — check minute data presence
        key = f"{trade['ticker']}_{day.replace('-', '')}"
        min_data = minutes_store.get(key)
        if not isinstance(min_data, dict) or not min_data or "error" in min_data:
            n_missing += 1
            prev5_close = None
            continue

        if not regime_allows:
            # No add possible for this arm on this day, but minutes exist; keep looping
            prev5_close = None
            continue

        # SMA20 gate: prior completed daily close > SMA20
        feat = feat_by.get(day)
        if feat is None or feat.get("sma20") is None:
            prev5_close = None
            continue
        if feat["prev_close"] <= feat["sma20"]:
            prev5_close = None
            continue

        # M1 sideways: strong-stock gate
        if arm == "M1" and regime == "sideways" and not _is_strong_stock(feat):
            prev5_close = None
            continue

        # Aggregate 1-min -> 5-min
        five_bars = agg_5min(min_data)

        for label, bar in five_bars:
            # Skip bars at or before buy time on entry day
            if day == buy_day and label <= buy_time:
                prev5_close = bar["close"]
                continue

            close = bar["close"]
            if close <= 0 or close > entry * BAND_TOP:
                prev5_close = close
                continue

            # Two-bar persistence: prev bar close > entry AND this bar close > entry
            if prev5_close is None or prev5_close <= entry or close <= entry:
                prev5_close = close
                continue

            held = sum(f for f, _ in legs)
            if held >= 1.0:
                prev5_close = close
                break

            # Threshold check
            if close >= entry * step_b_arm and held < 1.0:
                target = 1.0
            elif close >= entry * step_a_arm and held < 0.8:
                target = 0.8
            else:
                prev5_close = close
                continue

            nominal_add = target - held
            if nominal_add <= 1e-9:
                prev5_close = close
                continue

            # Risk clip
            if stop is not None and stop > 0:
                actual_add = _risk_clip(legs, entry, float(stop), close, nominal_add)
            else:
                actual_add = nominal_add

            if actual_add > 1e-9:
                legs.append((actual_add, close))
            prev5_close = close

        prev5_close = None  # reset at session boundary

    return legs, n_missing


# ------------------------------------------------------------------ M3 daily simulation

def _simulate_m3(trade, sessions, session_idx, bar_by, feat_by):
    """Simulate M3 (daily close confirmation) arm for one trade.

    Adds from session after entry through min(entry+14 cal days, sell_day-1).
    One step per session. No minute data needed.
    """
    entry = float(trade["buy_price"])
    sell_day = trade["sell_date"][:10]
    buy_day = trade["buy_date"][:10]
    regime = classify_regime(trade.get("regime"))
    stop = trade.get("stop_loss")

    if regime != "bull":
        pass  # no adds but still simulate (initial only)

    if buy_day not in session_idx:
        return None

    feat_entry = feat_by.get(buy_day)
    if feat_entry is None or feat_entry.get("atr14") is None:
        return None

    from prism_core.oneil_adaptive_policy import initial_sizing
    _, initial = initial_sizing(entry, feat_entry["atr14"])
    initial_frac = float(initial)

    entry_dt = datetime.strptime(buy_day, "%Y-%m-%d")  # noqa: DTZ007
    add_end_day = (entry_dt + timedelta(days=ADD_WINDOW_CAL)).strftime("%Y-%m-%d")

    legs = [(initial_frac, entry)]

    if regime != "bull":
        return legs  # no adds allowed

    for day in sessions:
        # M3: starts from session AFTER entry
        if day <= buy_day or day >= sell_day or day > add_end_day:
            continue

        db = bar_by.get(day)
        if db is None:
            continue

        close = db.get("close", 0)
        if close <= 0 or close > entry * BAND_TOP:
            continue

        # SMA20 gate
        feat = feat_by.get(day)
        if feat is None or feat.get("sma20") is None:
            continue
        if feat["prev_close"] <= feat["sma20"]:
            continue

        held = sum(f for f, _ in legs)
        if held >= 1.0:
            break

        if close >= entry * STEP_B and held < 1.0:
            target = 1.0
        elif close >= entry * STEP_A and held < 0.8:
            target = 0.8
        else:
            continue

        nominal_add = target - held
        if nominal_add <= 1e-9:
            continue

        if stop is not None and stop > 0:
            actual_add = _risk_clip(legs, entry, float(stop), close, nominal_add)
        else:
            actual_add = nominal_add

        if actual_add > 1e-9:
            legs.append((actual_add, close))

    return legs


# ------------------------------------------------------------------ slot return

def _slot_net(legs, exit_price):
    """Net slot return = gross - add_costs."""
    gross = sum(f * (exit_price / p - 1) for f, p in legs)
    add_cost = sum(f * ADD_COST for f, _ in legs[1:])
    return gross - add_cost


# ------------------------------------------------------------------ extract

def extract_trades(db, out):
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    rows = []
    for r in conn.execute(_SQL, (SAMPLE_START, SAMPLE_END)):
        (id_, ticker, company_name, buy_date, buy_price, sell_date,
         sell_price, exit_kind, trigger_type, scenario_text) = r
        s = _parse_scenario(scenario_text)
        raw_stop = s.get("stop_loss")
        try:
            stop_loss = float(raw_stop) if raw_stop is not None else None
        except (TypeError, ValueError):
            stop_loss = None
        rows.append({
            "id": id_,
            "ticker": ticker,
            "company_name": company_name,
            "buy_date": buy_date,
            "buy_price": float(buy_price),
            "sell_date": sell_date,
            "sell_price": float(sell_price),
            "exit_kind": exit_kind,
            "trigger_type": trigger_type,
            "stop_loss": stop_loss,
            "regime": _scenario_regime(scenario_text),
        })
    conn.close()
    Path(out).write_text(
        json.dumps({"rule_version": RULE_VERSION, "rows": rows}, ensure_ascii=False)
    )
    print(f"rows={len(rows)} -> {out}")


# ------------------------------------------------------------------ fetch daily

def fetch_daily(trades, cache_dir, out, pause=0.35):
    from cores.market_data.kis_source import KisSource

    source = KisSource()
    cache = Path(cache_dir)
    cache.mkdir(parents=True, exist_ok=True)
    rows = json.loads(Path(trades).read_text())["rows"]

    spans: dict[str, tuple[str, str]] = {}
    for r in rows:
        buy = r["buy_date"][:10]
        sell = r["sell_date"][:10]
        a, b = spans.get(r["ticker"], (buy, sell))
        spans[r["ticker"]] = (min(a, buy), max(b, sell))

    bars: dict[str, object] = {}
    for ticker, (lo, hi) in sorted(spans.items()):
        lo_c = lo.replace("-", "")
        hi_c = hi.replace("-", "")
        path = cache / f"madd_daily_{ticker}_{lo_c}_{hi_c}.json"
        if not path.exists():
            # 130 sessions ≈ 200 cal days before first buy; 25 sessions ≈ 40 cal days after last sell
            start = (datetime.strptime(lo, "%Y-%m-%d") - timedelta(days=200)).strftime("%Y%m%d")  # noqa: DTZ007
            end = (datetime.strptime(hi, "%Y-%m-%d") + timedelta(days=40)).strftime("%Y%m%d")  # noqa: DTZ007
            try:
                frame = source.price_history(ticker, start, end, adjusted=False)
                data = [
                    {
                        "date": ix.strftime("%Y-%m-%d"),
                        "high": float(x["High"]),
                        "low": float(x["Low"]),
                        "close": float(x["Close"]),
                        "vol": int(x.get("Volume", x.get("volume", 0)) or 0),
                    }
                    for ix, x in frame.iterrows()
                ]
            except Exception as error:  # noqa: BLE001
                data = {"error": type(error).__name__}
            path.write_text(json.dumps(data))
            time.sleep(pause)
        bars[ticker] = json.loads(path.read_text())

    Path(out).write_text(json.dumps({"rule_version": RULE_VERSION, "bars": bars}))
    print(f"tickers={len(bars)} -> {out}")


# ------------------------------------------------------------------ fetch minutes

def _minutes_full(source, ticker, day, pause):
    """Return {HHMMSS: {close, low, high, open, vol}} for one session, paging back from 15:30."""
    out, hour = {}, "153000"
    for _ in range(14):
        body = source._fetch(_MINUTE_URL, _MINUTE_TR, {
            "FID_COND_MRKT_DIV_CODE": "J",
            "FID_INPUT_ISCD": ticker,
            "FID_INPUT_HOUR_1": hour,
            "FID_INPUT_DATE_1": day,
            "FID_PW_DATA_INCU_YN": "N",
            "FID_FAKE_TICK_INCU_YN": "",
        })
        rows = [r for r in (getattr(body, "output2", None) or []) if r.get("stck_bsop_date") == day]
        fresh = [r for r in rows if r["stck_cntg_hour"] not in out]
        time.sleep(pause)
        if not fresh:
            break
        for r in fresh:
            out[r["stck_cntg_hour"]] = {
                "close": float(r.get("stck_prpr") or 0),
                "low": float(r.get("stck_lwpr") or 0),
                "high": float(r.get("stck_hgpr") or 0),
                "open": float(r.get("stck_oprc") or 0),
                "vol": int(float(r.get("cntg_vol") or 0)),
            }
        hour = min(out)
        if hour <= "090000":
            break
    return out


def fetch_minutes(trades, daily_file, cache_dir, out, pause=0.35, reuse_cache=None):
    from cores.market_data.kis_source import KisSource

    source = KisSource()
    cache = Path(cache_dir)
    cache.mkdir(parents=True, exist_ok=True)
    reuse = Path(reuse_cache) if reuse_cache else None

    rows = json.loads(Path(trades).read_text())["rows"]
    kept = drop_pyramid_adds(rows)
    daily = json.loads(Path(daily_file).read_text())["bars"]

    needed: set[tuple[str, str]] = set()
    for t in kept:
        ticker = t["ticker"]
        ticker_bars = daily.get(ticker)
        if not isinstance(ticker_bars, list):
            continue
        sessions = sorted(b["date"] for b in ticker_bars)
        bar_by = {b["date"]: b for b in ticker_bars}
        buy_day = t["buy_date"][:10]
        sell_day = t["sell_date"][:10]
        if buy_day not in sessions:
            continue
        entry = float(t["buy_price"])
        entry_dt = datetime.strptime(buy_day, "%Y-%m-%d")  # noqa: DTZ007
        add_end = (entry_dt + timedelta(days=ADD_WINDOW_CAL)).strftime("%Y-%m-%d")

        for day in sessions:
            if day < buy_day or day >= sell_day or day > add_end:
                continue
            db = bar_by.get(day)
            if db and db.get("high", 0) >= entry * STEP_A:
                needed.add((ticker, day.replace("-", "")))

    minutes: dict[str, object] = {}
    fetched = 0
    for ticker, day_compact in sorted(needed):
        key = f"{ticker}_{day_compact}"
        # 1. Own cache
        own_path = cache / f"madd_min_{key}.json"
        if own_path.exists():
            minutes[key] = json.loads(own_path.read_text())
            continue
        # 2. Reuse stop-noise cache (read-only)
        if reuse is not None:
            snoise_path = reuse / f"snoise_min_{key}.json"
            if snoise_path.exists():
                minutes[key] = json.loads(snoise_path.read_text())
                continue
        # 3. Fetch
        try:
            data = _minutes_full(source, ticker, day_compact, pause)
        except Exception as error:  # noqa: BLE001
            data = {"error": type(error).__name__}
        own_path.write_text(json.dumps(data))
        minutes[key] = data
        fetched += 1

    Path(out).write_text(json.dumps({"rule_version": RULE_VERSION, "minutes": minutes}))
    print(f"needed={len(needed)} fetched={fetched} -> {out}")


# ------------------------------------------------------------------ replay

def _boot_ci(rows_a, rows_b, by_ticker):
    """Bootstrap CI for mean(b - a) by ticker cluster, 90% interval."""
    rng = random.Random(SEED)
    tickers = sorted(by_ticker)
    diffs = []
    for _ in range(BOOT):
        sample_a, sample_b = [], []
        for t in (rng.choice(tickers) for _ in tickers):
            for idx in by_ticker[t]:
                sample_a.append(rows_a[idx])
                sample_b.append(rows_b[idx])
        if sample_a:
            diffs.append(_mean([b - a for a, b in zip(sample_a, sample_b)]))
    diffs.sort()
    n = len(diffs)
    return [diffs[int(0.05 * n)], diffs[int(0.95 * n) - 1]] if n >= 2 else [None, None]


def _summarize_arm(arm_results, m0_results, l_results, exit_kinds, regimes, tickers):
    """Compute per-arm metrics over a list of (net_return, n_adds, is_stop) tuples.

    arm_results: [(net_return, n_adds, is_stop_exit), ...]  — parallel to valid trade list
    m0_results: [(net_return, ...), ...]  for M0, same indices (may be None if arm IS M0)
    l_results:  same for L
    exit_kinds: [str, ...]
    regimes: [str, ...]
    tickers: [str, ...]
    """
    if not arm_results:
        return {"n": 0}
    nets = [r[0] for r in arm_results]
    adds = [r[1] for r in arm_results]
    is_stop = [r[2] for r in arm_results]

    n_add = sum(1 for a in adds if a > 0)
    n_add_stop = sum(
        1 for a, s in zip(adds, is_stop) if a > 0 and s
    )
    losers = [n for n in nets if n < 0]

    # Capture ratio for sideways winners (L > 0 and regime=sideways)
    sideways_capture = []
    for i, (net, l_net, reg) in enumerate(zip(nets, [r[0] for r in l_results], regimes)):
        if reg == "sideways" and l_net > 0:
            sideways_capture.append(net / l_net if l_net != 0 else None)
    sideways_capture = [x for x in sideways_capture if x is not None]

    # Diff vs M0 (pairwise)
    if m0_results:
        diffs_m0 = [n - r[0] for n, r in zip(nets, m0_results)]
        by_ticker: dict[str, list[int]] = {}
        for i, tk in enumerate(tickers):
            by_ticker.setdefault(tk, []).append(i)
        ci_m0 = _boot_ci([r[0] for r in m0_results], nets, by_ticker)
    else:
        diffs_m0, ci_m0 = None, [None, None]

    # Diff vs L (pairwise)
    diffs_l = [n - r[0] for n, r in zip(nets, l_results)]

    return {
        "n": len(arm_results),
        "n_add": n_add,
        "n_add_stop": n_add_stop,
        "add_rate": n_add / len(arm_results) if arm_results else None,
        "add_then_stop_rate": n_add_stop / n_add if n_add > 0 else None,
        "mean_net": _mean(nets),
        "mean_diff_vs_M0": _mean(diffs_m0) if diffs_m0 else None,
        "ci90_diff_vs_M0": ci_m0,
        "mean_diff_vs_L": _mean(diffs_l),
        "loser_n": len(losers),
        "loser_mean_net": _mean(losers),
        "worst_net": min(nets),
        "mdd": mdd(
            [{"sell_date": s, "v": n / 10} for s, n in
             zip(sorted(range(len(nets))), nets)],
            "v",
        ),
        "sideways_win_capture": _mean(sideways_capture),
    }


def _mdd_from_rows(rows_with_sell_and_net):
    """rows_with_sell_and_net: list of (sell_date_str, net_return)."""
    level = peak = worst = 0.0
    for _, net in sorted(rows_with_sell_and_net):
        level += net / 10
        peak = max(peak, level)
        worst = min(worst, level - peak)
    return worst


def _verdicts(hold_stats):
    """Compute per-arm verdicts from holdout stats dict."""
    verdicts = {}
    n_hold = hold_stats.get("n_holdout", 0)
    missing_ratio = hold_stats.get("intraday_missing_ratio", 0.0)

    for arm in ("M0", "M1", "M2", "M3"):
        arm_s = hold_stats.get(arm, {})
        n_arm_add = arm_s.get("n_add", 0)

        if n_hold < MIN_HOLDOUT:
            verdicts[arm] = "CONTINUE_CAPTURE"
            continue
        if arm in ("M0", "M1", "M2") and missing_ratio > MISSING_RATIO_INTRADAY:
            verdicts[arm] = "INCONCLUSIVE"
            continue
        if n_arm_add < MIN_HOLDOUT_ADD and arm in ("M2", "M3"):
            verdicts[arm] = "CONTINUE_CAPTURE"
            continue

        m0_s = hold_stats.get("M0", {})

        if arm == "M1":
            diff = arm_s.get("mean_diff_vs_M0")
            ci = arm_s.get("ci90_diff_vs_M0") or [None, None]
            mdd_m0 = m0_s.get("mdd_raw", m0_s.get("mdd"))
            mdd_arm = arm_s.get("mdd_raw", arm_s.get("mdd"))
            loss_m0 = m0_s.get("loser_mean_net")
            loss_arm = arm_s.get("loser_mean_net")
            ok = (
                diff is not None and diff >= 0.0020
                and ci[0] is not None and ci[0] > 0
                and (mdd_m0 is None or mdd_arm is None
                     or mdd_arm >= mdd_m0 - 0.01)
                and (loss_m0 is None or loss_arm is None
                     or loss_arm >= loss_m0 * 1.10)
            )
            verdicts[arm] = "CANDIDATE" if ok else "RETIRE"

        elif arm in ("M2", "M3"):
            diff = arm_s.get("mean_diff_vs_M0")
            m0_add_stop = m0_s.get("add_then_stop_rate")
            arm_add_stop = arm_s.get("add_then_stop_rate")
            mdd_m0 = m0_s.get("mdd_raw", m0_s.get("mdd"))
            mdd_arm = arm_s.get("mdd_raw", arm_s.get("mdd"))
            m0_add_n = m0_s.get("n_add", 0)

            stop_ok = (
                m0_add_n >= MIN_HOLDOUT_ADD
                and m0_add_stop is not None
                and arm_add_stop is not None
                and m0_add_stop > 0
                and arm_add_stop <= m0_add_stop * 0.70  # 30% relative reduction
            )
            diff_ok = diff is not None and diff >= -0.0010
            mdd_ok = mdd_m0 is None or mdd_arm is None or mdd_arm >= mdd_m0
            verdicts[arm] = "CANDIDATE" if (stop_ok and diff_ok and mdd_ok) else "RETIRE"

        else:  # M0: reference, no verdict criterion
            verdicts[arm] = "REFERENCE"

    return verdicts


def replay(trades_file, daily_file, minutes_file, out, sizing=None):
    if sizing is None:
        from prism_core.oneil_adaptive_policy import initial_sizing as sizing

    rows = json.loads(Path(trades_file).read_text())["rows"]
    daily = json.loads(Path(daily_file).read_text())["bars"]
    minutes_store = json.loads(Path(minutes_file).read_text())["minutes"]

    kept = drop_pyramid_adds(rows)
    missing_counts = {"pyramid_add": len(rows) - len(kept), "no_bars": 0, "no_session": 0, "no_history": 0}
    intraday_missing_by_trade = 0

    result_rows = []

    for trade in kept:
        ticker = trade["ticker"]
        ticker_bars = daily.get(ticker)
        if not isinstance(ticker_bars, list):
            missing_counts["no_bars"] += 1
            continue

        sessions = sorted(b["date"] for b in ticker_bars)
        bar_by = {b["date"]: b for b in ticker_bars}
        session_idx = {d: i for i, d in enumerate(sessions)}
        buy_day = trade["buy_date"][:10]

        if buy_day not in session_idx:
            missing_counts["no_session"] += 1
            continue

        feat_by = _precompute_features(sessions, bar_by)
        if feat_by.get(buy_day) is None or feat_by[buy_day].get("atr14") is None:
            missing_counts["no_history"] += 1
            continue

        entry = float(trade["buy_price"])
        exit_price = float(trade["sell_price"])
        sell_day = trade["sell_date"][:10]
        regime = classify_regime(trade.get("regime"))
        exit_kind = trade.get("exit_kind", "")

        # --- L arm ---
        l_legs = [(1.0, entry)]
        l_net = _slot_net(l_legs, exit_price)

        # --- MISSING check for intraday arms ---
        # candidate days = sessions where daily high >= entry*1.02, within add window, before sell
        entry_dt = datetime.strptime(buy_day, "%Y-%m-%d")  # noqa: DTZ007
        add_end_day = (entry_dt + timedelta(days=ADD_WINDOW_CAL)).strftime("%Y-%m-%d")
        n_candidate_missing = 0
        for day in sessions:
            if day < buy_day or day >= sell_day or day > add_end_day:
                continue
            db = bar_by.get(day)
            if db and db.get("high", 0) >= entry * STEP_A:
                key = f"{ticker}_{day.replace('-', '')}"
                md = minutes_store.get(key)
                if not isinstance(md, dict) or not md or "error" in md:
                    n_candidate_missing += 1

        intraday_missing = n_candidate_missing > 0

        # --- Intraday arms ---
        if intraday_missing:
            m0_legs = m1_legs = m2_legs = None
            intraday_missing_by_trade += 1
        else:
            m0_legs, _ = _simulate_intraday(
                trade, sessions, session_idx, bar_by, feat_by, minutes_store, "M0"
            )
            m1_legs, _ = _simulate_intraday(
                trade, sessions, session_idx, bar_by, feat_by, minutes_store, "M1"
            )
            m2_legs, _ = _simulate_intraday(
                trade, sessions, session_idx, bar_by, feat_by, minutes_store, "M2"
            )

        # --- M3 arm ---
        m3_legs = _simulate_m3(trade, sessions, session_idx, bar_by, feat_by)

        def _net_adds(legs, _ep=exit_price):
            if legs is None:
                return None, None
            return _slot_net(legs, _ep), len(legs) - 1

        m0_net, m0_adds = _net_adds(m0_legs)
        m1_net, m1_adds = _net_adds(m1_legs)
        m2_net, m2_adds = _net_adds(m2_legs)
        m3_net, m3_adds = _net_adds(m3_legs)

        result_rows.append({
            "id": trade["id"],
            "ticker": ticker,
            "buy_date": trade["buy_date"],
            "buy_price": entry,
            "sell_date": trade["sell_date"],
            "sell_price": exit_price,
            "exit_kind": exit_kind,
            "regime": trade.get("regime"),
            "regime_class": regime,
            "L_net": l_net,
            "L_adds": 0,
            "M0_net": m0_net,
            "M0_adds": m0_adds,
            "M1_net": m1_net,
            "M1_adds": m1_adds,
            "M2_net": m2_net,
            "M2_adds": m2_adds,
            "M3_net": m3_net,
            "M3_adds": m3_adds,
            "intraday_missing": intraday_missing,
        })

    # --- Split and summarize ---
    disc = [r for r in result_rows if r["buy_date"][:10] < HOLDOUT_START]
    hold = [r for r in result_rows if r["buy_date"][:10] >= HOLDOUT_START]

    def _split_stats(split_rows):
        if not split_rows:
            return {}
        valid_intra = [r for r in split_rows if not r["intraday_missing"]]
        n_missing_intra = sum(1 for r in split_rows if r["intraday_missing"])
        missing_ratio = n_missing_intra / len(split_rows) if split_rows else 0

        def _arm_stats(rows, arm, ref_m0, ref_l):
            results = [
                (r[f"{arm}_net"], r[f"{arm}_adds"] or 0, r["exit_kind"] == "stop")
                for r in rows
                if r[f"{arm}_net"] is not None
            ]
            m0_res = (
                [(r["M0_net"], r["M0_adds"] or 0, False) for r in rows if r["M0_net"] is not None]
                if ref_m0 else None
            )
            l_res = [
                (r["L_net"], 0, False)
                for r in rows
                if r["L_net"] is not None
            ]
            tks = [r["ticker"] for r in rows if r[f"{arm}_net"] is not None]
            regs = [r["regime_class"] for r in rows if r[f"{arm}_net"] is not None]
            exit_ks = [r["exit_kind"] for r in rows if r[f"{arm}_net"] is not None]
            if not results:
                return {"n": 0}
            s = _summarize_arm(results, m0_res, l_res, exit_ks, regs, tks)

            # Compute proper MDD using sell dates
            sell_net_pairs = [
                (r["sell_date"], r[f"{arm}_net"])
                for r in rows
                if r[f"{arm}_net"] is not None
            ]
            s["mdd_raw"] = _mdd_from_rows(sell_net_pairs)
            return s

        # L uses all rows; intraday arms use valid_intra; M3 uses all rows
        stats = {
            "n_total": len(split_rows),
            "n_intraday_missing": n_missing_intra,
            "intraday_missing_ratio": missing_ratio,
            "L": _arm_stats(split_rows, "L", ref_m0=False, ref_l=False),
            "M0": _arm_stats(valid_intra, "M0", ref_m0=False, ref_l=True),
            "M1": _arm_stats(valid_intra, "M1", ref_m0=True, ref_l=True),
            "M2": _arm_stats(valid_intra, "M2", ref_m0=True, ref_l=True),
            "M3": _arm_stats(split_rows, "M3", ref_m0=False, ref_l=True),
        }
        # Compute M3 vs M0 diff separately (different sets)
        # For M3 vs M0, use rows where BOTH are valid
        both_valid = [r for r in split_rows if r["M3_net"] is not None and r["M0_net"] is not None]
        if both_valid:
            m3_m0_diffs = [r["M3_net"] - r["M0_net"] for r in both_valid]
            by_tk: dict[str, list[int]] = {}
            for i, r in enumerate(both_valid):
                by_tk.setdefault(r["ticker"], []).append(i)
            ci = _boot_ci(
                [r["M0_net"] for r in both_valid],
                [r["M3_net"] for r in both_valid],
                by_tk,
            )
            stats["M3"]["mean_diff_vs_M0"] = _mean(m3_m0_diffs)
            stats["M3"]["ci90_diff_vs_M0"] = ci
        return stats

    disc_stats = _split_stats(disc)
    hold_stats = _split_stats(hold)
    all_stats = _split_stats(result_rows)

    hold_for_verdict = dict(hold_stats)
    hold_for_verdict["n_holdout"] = len(hold)

    verdicts = _verdicts(hold_for_verdict)

    summary = {
        "rule_version": RULE_VERSION,
        "inputs_sha256": hashlib.sha256(
            Path(trades_file).read_bytes()
            + Path(daily_file).read_bytes()
            + Path(minutes_file).read_bytes()
        ).hexdigest(),
        "rows_total": len(rows),
        "rows_used": len(result_rows),
        "missing": missing_counts,
        "intraday_missing_trades": intraday_missing_by_trade,
        "discovery": disc_stats,
        "holdout": hold_stats,
        "all": all_stats,
        "verdicts": verdicts,
    }
    Path(out).write_text(
        json.dumps({"summary": summary, "rows": result_rows},
                   ensure_ascii=False, indent=1, default=str)
    )
    print(json.dumps(summary, ensure_ascii=False, indent=1, default=str))


# ------------------------------------------------------------------ CLI

def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = p.add_subparsers(dest="cmd", required=True)

    e = sub.add_parser("extract-trades")
    e.add_argument("--db", default="stock_tracking_db.sqlite")
    e.add_argument("--out", required=True)

    fd = sub.add_parser("fetch-daily")
    fd.add_argument("--trades", required=True)
    fd.add_argument("--cache", default="runtime/research/kr_micro_split_add")
    fd.add_argument("--out", required=True)
    fd.add_argument("--pause", type=float, default=0.35)

    fm = sub.add_parser("fetch-minutes")
    fm.add_argument("--trades", required=True)
    fm.add_argument("--daily", required=True)
    fm.add_argument("--cache", default="runtime/research/kr_micro_split_add")
    fm.add_argument("--out", required=True)
    fm.add_argument("--pause", type=float, default=0.35)
    fm.add_argument(
        "--reuse-cache",
        default=None,
        help="Directory with existing snoise_min_*.json files to read before fetching",
    )

    r = sub.add_parser("replay")
    r.add_argument("--trades", required=True)
    r.add_argument("--daily", required=True)
    r.add_argument("--minutes", required=True)
    r.add_argument("--out", required=True)

    a = p.parse_args(argv)
    if a.cmd == "extract-trades":
        extract_trades(a.db, a.out)
    elif a.cmd == "fetch-daily":
        fetch_daily(a.trades, a.cache, a.out, a.pause)
    elif a.cmd == "fetch-minutes":
        fetch_minutes(a.trades, a.daily, a.cache, a.out, a.pause, a.reuse_cache)
    else:
        replay(a.trades, a.daily, a.minutes, a.out)


if __name__ == "__main__":
    sys.exit(main())
