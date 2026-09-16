"""Pure research-only completed-bar watch policy. Never a BUY or order signal."""
from __future__ import annotations

import hashlib
import json
import math
from datetime import date, datetime

POLICY_VERSION = "oneil_watchlist_v1"
KR_POLICY_VERSION = "oneil_watchlist_kr_v1"
ACTIVE = {"WATCHING", "READY", "MISSING"}
MAX_ACTIVE = 20


def policy_version(market="US"):
    if market not in {"US", "KR"}:
        raise ValueError("unsupported_market")
    return KR_POLICY_VERSION if market == "KR" else POLICY_VERSION


def contrarian(trigger):
    return "contrarian" in trigger.lower() or "역발상" in trigger


def ref(*parts):
    return hashlib.sha256(json.dumps(parts, sort_keys=True, separators=(",", ":")).encode()).hexdigest()[:32]


def completed(rows, trade_date):
    """Reject ambiguous dates/prices rather than silently repair research input."""
    cutoff = datetime.strptime(trade_date.replace("-", ""), "%Y%m%d").date()  # noqa: DTZ007 - date-only session key
    result, seen = [], set()
    for row in rows:
        day = date.fromisoformat(row["date"])
        if day >= cutoff:
            continue
        if day in seen:
            raise ValueError("duplicate_bar")
        seen.add(day)
        close, high = float(row["close"]), float(row["high"])
        if not all(math.isfinite(v) and v > 0 for v in (close, high)) or high < close:
            raise ValueError("invalid_price")
        result.append({"date": day.isoformat(), "close": close, "high": high})
    result.sort(key=lambda r: r["date"])
    if len(result) < 66:
        raise ValueError("insufficient_bars")
    return result


def evaluate(watch, rows, benchmark, trade_date, expected_completed_date=None, market_days=None):
    """Frozen 20-high proxy, rising MA50 and positive relative 60-bar return."""
    out = dict(watch)
    try:
        cutoff = datetime.strptime(trade_date.replace("-", ""), "%Y%m%d").date().isoformat()  # noqa: DTZ007
        calendar = sorted(date.fromisoformat(d).isoformat() for d in (market_days or []) if d < cutoff)
        if len(calendar) != len(set(calendar)):
            raise ValueError("duplicate_calendar_session")
        if expected_completed_date:
            calendar = [d for d in calendar if d <= expected_completed_date]
        seed_asof = watch.get("seed_asof")
        if not seed_asof and calendar:
            discovered = datetime.strptime(watch["seeded_trade_date"].replace("-", ""), "%Y%m%d").date().isoformat()  # noqa: DTZ007
            prior = [d for d in calendar if d < discovered]
            if not prior:
                raise ValueError("discovery_calendar_missing")
            seed_asof = prior[-1]
        if seed_asof and calendar:
            elapsed = sum(d > seed_asof for d in calendar)
            out.update(seed_asof=seed_asof, elapsed_bars=elapsed)
            if elapsed >= 5:
                out.update(status="EXPIRED", reason="five_completed_market_bars", asof=calendar[-1])
                return out
        bench = completed(benchmark, trade_date)
        if expected_completed_date and bench[-1]["date"] != expected_completed_date:
            raise ValueError("stale_benchmark")
        if not seed_asof:
            discovered = datetime.strptime(watch.get("seeded_trade_date", trade_date).replace("-", ""), "%Y%m%d").date().isoformat()  # noqa: DTZ007
            prior = [b["date"] for b in bench if b["date"] < discovered]
            if not prior:
                raise ValueError("discovery_session_missing")
            seed_asof = prior[-1]
        elapsed = sum(d > seed_asof for d in calendar) if calendar else sum(b["date"] > seed_asof for b in bench)
        out.update(seed_asof=seed_asof, elapsed_bars=elapsed)
        if elapsed >= 5:
            out.update(status="EXPIRED", reason="five_completed_market_bars", asof=bench[-1]["date"])
            return out
        bars = completed(rows, trade_date)
        prospective_setup = watch.get("setup_initialized") is False
        seed_close = watch.get("seed_close")
        if seed_close is None and watch.get("seed_asof") and not prospective_setup:
            seed_payload = watch.get("delivery_seed_payload", {})
            if seed_payload.get("asof") == watch["seed_asof"]:
                seed_close = seed_payload.get("close")
            if seed_close is None:
                out.update(status="MISSING", reason="historical_seed_basis_unknown",
                           adjustment_basis_status="LEGACY_UNKNOWN")
                out.pop("input_hash", None)
                return out
        if seed_close is not None:
            anchor = next((b for b in bars if b["date"] == watch.get("seed_price_asof", watch["seed_asof"])), None)
            if anchor is None or not math.isclose(anchor["close"], float(seed_close), rel_tol=1e-8):
                out.update(status="MISSING", reason="adjusted_price_basis_changed")
                out.pop("input_hash", None)
                return out
            out["seed_close"] = float(seed_close)
        if watch.get("seed_asof") and not watch.get("pivot") and not prospective_setup:
            raise ValueError("missing_frozen_seed_pivot")
        if [b["date"] for b in bars[-66:]] != [b["date"] for b in bench[-66:]]:
            raise ValueError("benchmark_alignment")
        closes = [b["close"] for b in bars]
        ma = sum(closes[-50:]) / 50
        prior_ma = sum(closes[-55:-5]) / 50
        relative = closes[-1] / closes[-61] - bench[-1]["close"] / bench[-61]["close"]
        asof = bars[-1]["date"]
        if watch.get("asof") and asof < watch["asof"]:
            raise ValueError("stale_history")
        pivot = watch.get("pivot") or max(b["high"] for b in bars[-21:-1])
        status = "WATCHING"
        if closes[-1] <= ma:
            status = "INVALIDATED"
        elif (ma > prior_ma and relative > 0 and closes[-1] > closes[-2]
              and pivot <= closes[-1] <= pivot * 1.05):
            status = "READY"
        out.update(status=status, reason="technical_" + status.lower(), pivot=pivot,
                   seed_asof=seed_asof, asof=asof, elapsed_bars=elapsed,
                   close=closes[-1], previous_close=closes[-2], ma50=ma, ma50_prior5=prior_ma, rs60=relative,
                   input_hash=ref(watch.get("policy_version", POLICY_VERSION), bars[-66:], bench[-66:]))
        if not watch.get("seed_asof") or prospective_setup:
            out["seed_close"] = closes[-1]
            out["setup_asof"] = asof
            out["seed_price_asof"] = asof
            out["setup_initialized"] = True
        out["adjustment_basis_status"] = "FROZEN_SEED_CHECKED"
        out["data_contract_version"] = 2
        if status == "READY" and not out.get("ready_event_id"):
            out["ready_event_id"] = ref("watch-ready", out["watch_id"], asof)
    except (KeyError, TypeError, ValueError, OverflowError):
        out.update(status="MISSING", reason="dataset_missing_or_invalid")
        out.pop("input_hash", None)
    return out


def advance(state, seeds, frames, trade_date, batch_ref, *, market="US"):
    """Deterministic lifetime update; reselection never refreshes a live pivot/TTL."""
    state = dict(state)
    version = policy_version(market)
    if state.get("policy_version", version) != version or state.get("market", market) != market:
        raise ValueError("state_market_mismatch")
    existing = list(state.get("watches", []))
    active_tickers = {w["ticker"] for w in existing if w["status"] in ACTIVE}
    active_count = len(active_tickers)
    for seed in seeds:
        ticker, trigger = seed["ticker"], seed["trigger"]
        if (contrarian(trigger) or ticker in active_tickers or active_count >= MAX_ACTIVE
                or (market == "KR" and (len(ticker) != 6 or not ticker.isascii() or not ticker.isdigit()))):
            continue
        benchmark = "SPY" if market == "US" else frames.get("__benchmarks", {}).get(ticker)
        # A completed lifetime cannot be reseeded on its terminal bar/date.
        try:
            latest_bar = completed(frames.get(benchmark, []), trade_date)[-1]["date"]
        except (KeyError, TypeError, ValueError):
            latest_bar = None
        if any(w["ticker"] == ticker and (w.get("terminal_trade_date") == trade_date
                   or (latest_bar and w.get("asof", "") >= latest_bar))
               for w in existing if w["status"] not in ACTIVE):
            continue
        watch_id = ref(version, market, ticker, trade_date, batch_ref)
        existing.append({"watch_id": watch_id, "ticker": ticker, "market": market, "trigger": trigger,
                         "seed_event_id": ref("watch-seed", watch_id), "policy_version": version,
                         "status": "WATCHING", "seeded_trade_date": trade_date, "setup_initialized": False})
        expected = frames.get("__expected_completed_date")
        if expected:
            discovery = datetime.strptime(trade_date.replace("-", ""), "%Y%m%d").date()  # noqa: DTZ007
            if date.fromisoformat(expected) < discovery:
                existing[-1]["seed_asof"] = expected  # TTL only; no historical price/pivot fabrication.
        if market == "KR":
            existing[-1].update(currency="KRW", benchmark=benchmark)
        active_tickers.add(ticker)
        active_count += 1
    updated, observations = [], []
    for watch in existing:
        if watch["status"] not in ACTIVE:
            updated.append(watch)
            continue
        benchmark = "SPY" if market == "US" else frames.get("__benchmarks", {}).get(watch["ticker"])
        result = evaluate(watch, frames.get(watch["ticker"], []), frames.get(benchmark, []), trade_date,
                          frames.get("__expected_completed_date"), frames.get("__market_days"))
        if market == "KR":
            result.update(currency="KRW", benchmark=benchmark)
        result["batch_ref"] = batch_ref
        result["observation_event_id"] = ref("watch-observed", watch["watch_id"], batch_ref,
                                              result.get("asof"), result.get("input_hash"), result["status"])
        if result["status"] not in ACTIVE:
            result["terminal_trade_date"] = trade_date
        updated.append(result)
        observations.append(result)
    terminal = [w for w in updated if w["status"] not in ACTIVE][-100:]
    state.update(schema_version=1, policy_version=version,
                 watches=[w for w in updated if w["status"] in ACTIVE] + terminal)
    if market == "KR":
        state["market"] = market
    return state, observations
