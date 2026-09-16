"""Pure prospective price-path proxies, never fills or portfolio performance."""
from __future__ import annotations

import hashlib
import json
import math
from datetime import datetime
from zoneinfo import ZoneInfo

VERSION = "watch_price_path_v1"
HORIZONS = (1, 3, 5, 10, 20)


def identity(*parts):
    return hashlib.sha256(json.dumps(parts, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def day(value):
    return datetime.fromisoformat(str(value).replace("Z", "+00:00")).date().isoformat()


def bars(rows):
    result = {}
    for row in rows:
        key = day(row["date"])
        if key in result:
            raise ValueError("duplicate_session")
        values = {k: float(row[k]) for k in ("open", "high", "low", "close")}
        if not all(math.isfinite(v) and v > 0 for v in values.values()):
            raise ValueError("invalid_price")
        if not values["low"] <= min(values["open"], values["close"]) <= max(values["open"], values["close"]) <= values["high"]:
            raise ValueError("invalid_ohlc")
        result[key] = values
    return result


def new_anchor(market, row, kind, observed_at, snapshot_hash, batch_ref):
    stamp = datetime.fromisoformat(observed_at.replace("Z", "+00:00"))
    if stamp.tzinfo is None:
        raise ValueError("naive_event_time")
    local_day = stamp.astimezone(ZoneInfo("Asia/Seoul" if market == "KR" else "America/New_York")).date().isoformat()
    late = str(row.get("seeded_trade_date", "")).replace("-", "") < local_day.replace("-", "")
    prior_ready = row.get("delivery_ready_payload", {}).get("observed_at")
    if kind == "FIRST_READY" and prior_ready:
        late = datetime.fromisoformat(prior_ready.replace("Z", "+00:00")) < stamp
    return {"anchor_id": identity(VERSION, market, row["watch_id"], kind),
            "contract_version": VERSION, "market": market, "currency": "KRW" if market == "KR" else "USD",
            "watch_ref": row["watch_id"], "ticker": row["ticker"], "anchor_kind": kind,
            "observed_at": observed_at, "observed_local_date": local_day, "batch_ref": batch_ref,
            "watch_status": row["status"], "trigger": row.get("trigger", "UNKNOWN"),
            "regime": row.get("regime_context", {}).get("market_regime", row.get("regime", "UNKNOWN")),
            "regime_source": row.get("regime_context", {}).get("source", "UNKNOWN"),
            "regime_asof": row.get("regime_context", {}).get("reference_date", row.get("regime_context", {}).get("asof")),
            "regime_reference_date": row.get("regime_context", {}).get("reference_date"),
            "policy_version": row.get("policy_version"), "data_contract": VERSION,
            "observation_event_id": row.get("observation_event_id"), "seed_event_id": row.get("seed_event_id"),
            "ready_event_id": row.get("ready_event_id"), "seed_snapshot_hash": snapshot_hash,
            "enrollment": "LATE_ENROLLMENT" if late else "ON_TIME",
            "research_kind": "PRICE_PATH_PROXY_NOT_FILL", "completed_horizons": [], "done": False}


def primary_endpoint(anchor, base, calendar, prices, benchmark_prices, reference_date):
    """READY open to exact BASELINE twentieth close; no equal-duration substitution."""
    event = {**base, "measurement": "BASELINE20_COMMON_ENDPOINT", "horizon": 20}
    observed = anchor.get("baseline_observed_local_date")
    if not observed or not anchor.get("baseline_anchor_id"):
        return {**event, "status": "MISSING", "reason": "baseline_link_unavailable"}
    if not calendar or calendar[0] > observed:
        return {**event, "status": "MISSING", "reason": "baseline_calendar_unavailable"}
    baseline_days = [d for d in calendar if d > observed]
    if len(baseline_days) < 20:
        return {**event, "status": "PENDING", "reason": "baseline20_immature"}
    endpoint = baseline_days[19]
    window = [d for d in calendar if reference_date <= d <= endpoint]
    if not window or any(d not in prices or d not in benchmark_prices for d in window):
        return {**event, "status": "MISSING", "reason": "primary_window_unavailable", "endpoint_date": endpoint}
    start = prices[reference_date]["open"]
    gross = prices[endpoint]["close"] / start - 1
    benchmark_return = benchmark_prices[endpoint]["close"] / benchmark_prices[reference_date]["open"] - 1
    return {**event, "status": "COMPLETE", "endpoint_date": endpoint, "actual_holding_bars": len(window),
            "gross_return": gross, "benchmark_return": benchmark_return, "relative_return": gross - benchmark_return,
            "mfe": max(prices[d]["high"] for d in window) / start - 1,
            "mae": min(prices[d]["low"] for d in window) / start - 1}


def evaluate(anchor, frames, frozen_frames):
    """Future-session OPEN -> horizon CLOSE, with exact exchange-calendar alignment."""
    result = dict(anchor)
    base = {k: v for k, v in anchor.items() if k not in {"completed_horizons", "done", "last_attempt"}}
    base.update(research_kind="PRICE_PATH_PROXY_NOT_FILL", asof=frames.get("__expected_completed_date"))
    try:
        expected = frames["__expected_completed_date"]
        calendar = sorted({day(x) for x in frames["__market_days"] if day(x) <= expected})
        if not calendar or expected != calendar[-1] or calendar[0] > anchor["observed_local_date"]:
            raise ValueError("missing_calendar_coverage")
        future = [d for d in calendar if d > anchor["observed_local_date"]]
        if not future:
            return result, [{**base, "status": "PENDING", "reason": "await_next_full_session"}]
        result["done"] = len(future) >= 20
        ticker = anchor["ticker"]
        benchmark = frames.get("__benchmarks", {}).get(ticker, "SPY" if anchor["market"] == "US" else None)
        if not benchmark:
            raise ValueError("missing_benchmark_identity")
        prices, bench = bars(frames[ticker]), bars(frames[benchmark])
        # Freeze original observed basis; revisions cannot rewrite past proxies.
        for symbol in (ticker, benchmark):
            before = bars(frozen_frames.get(symbol, []))
            now = prices if symbol == ticker else bench
            overlap = set(before) & set(now)
            if before and not overlap:
                raise ValueError("basis_overlap_unavailable")
            if any(not math.isclose(before[d][k], now[d][k], rel_tol=1e-8) for d in overlap for k in before[d]):
                result["done"] = True
                return result, [{**base, "status": "BASIS_CHANGED", "reason": "frozen_input_revision"}]
        ref = future[0]
        if ref not in prices or ref not in bench:
            raise ValueError("missing_exact_reference_open")
        if anchor.get("reference_open") is not None and not math.isclose(anchor["reference_open"], prices[ref]["open"], rel_tol=1e-8):
            result["done"] = True
            return result, [{**base, "status": "BASIS_CHANGED", "reason": "reference_open_revision"}]
        result.update(reference_date=ref, reference_open=prices[ref]["open"], benchmark=benchmark)
        base.update(reference_date=ref, reference_open=prices[ref]["open"], benchmark=benchmark)
        events = []
        complete = set(anchor.get("completed_horizons", []))
        for horizon in HORIZONS:
            if horizon in complete:
                continue
            event = {**base, "horizon": horizon}
            if len(future) < horizon:
                events.append({**event, "status": "PENDING", "reason": "immature"})
                continue
            window = future[:horizon]
            if any(d not in prices or d not in bench for d in window):
                events.append({**event, "status": "MISSING", "reason": "missing_exact_session_bar"})
                continue
            start, end = prices[ref]["open"], prices[window[-1]]["close"]
            gross = end / start - 1
            benchmark_return = bench[window[-1]]["close"] / bench[ref]["open"] - 1
            events.append({**event, "status": "COMPLETE", "endpoint_date": window[-1],
                           "gross_return": gross, "benchmark_return": benchmark_return,
                           "relative_return": gross - benchmark_return,
                           "mfe": max(prices[d]["high"] for d in window) / start - 1,
                           "mae": min(prices[d]["low"] for d in window) / start - 1,
                           "hypothetical_roundtrip_cost_returns": {str(bps): gross - bps / 10000 for bps in (0, 10, 25, 50)},
                           "cost_label": "HYPOTHETICAL_NOT_ACTUAL_FEES_OR_NET_PROFIT"})
            complete.add(horizon)
        result["completed_horizons"] = sorted(complete)
        if anchor["anchor_kind"] == "FIRST_READY" and not anchor.get("primary_complete"):
            primary = primary_endpoint(anchor, base, calendar, prices, bench, ref)
            events.append(primary)
            result["primary_complete"] = primary["status"] == "COMPLETE"
        # End collection at 20 sessions even where bars remain missing; explicit missing remains in ledger.
        result["done"] = len(future) >= 20
        return result, events
    except (KeyError, ValueError, TypeError, OverflowError):
        missing = [{**base, "horizon": h, "status": "MISSING", "reason": "calendar_or_price_input_unavailable"}
                   for h in HORIZONS if h not in anchor.get("completed_horizons", [])]
        if anchor["anchor_kind"] == "FIRST_READY" and not anchor.get("primary_complete"):
            missing.append({**base, "measurement": "BASELINE20_COMMON_ENDPOINT", "horizon": 20,
                            "status": "MISSING", "reason": "calendar_or_price_input_unavailable"})
        return result, missing
