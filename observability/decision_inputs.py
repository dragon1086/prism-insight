"""Fail-open SHADOW capture of decision-time input features. Never a prompt input or gate.

The BUY path already fetches a daily frame for its trend facts; ``capture_frame``
keeps that frame, and ``emit_decision_inputs`` records deterministic features
beside the decision's ``candidate.evaluated`` event with the same decision_id.
The only extra network read is the US next-earnings date (bounded, optional).
"""
from __future__ import annotations

import concurrent.futures
import hashlib
import os
from datetime import date, datetime, timezone

from observability.events import emit_event
from prism_core.decision_input_features import (
    CONTRACT_VERSION, bars_from_frame, compute, prompt_facts_enabled, render_facts_block,
)

_EARNINGS_TIMEOUT = 3.0
_POOL = concurrent.futures.ThreadPoolExecutor(max_workers=1, thread_name_prefix="decision-input-earnings")


def enabled():
    return os.getenv("DECISION_INPUT_SHADOW_ENABLED", "true").strip().lower() in {"1", "true", "yes", "on"}


def capture_frame(agent, ticker, frame, *, market):
    if not enabled():
        return
    try:
        if not hasattr(agent, "_decision_input_bars"):
            agent._decision_input_bars = {}
        agent._decision_input_bars[ticker] = {"market": market, "bars": bars_from_frame(frame),
                                              "captured_at": datetime.now(timezone.utc).isoformat()}
    except Exception:  # noqa: BLE001 - optional capture must never fail a decision
        return


def peer_valuation_summary(packet):
    """Median PER/PBR of WiseFn-selected comparable peers (target excluded)."""
    try:
        if not packet or not packet.get("ready"):
            return {"status": "MISSING", "reason": (packet or {}).get("skip_reason") or "not_ready"}
        target, peers = packet["peers"][0], packet["peers"][1:]
        out = {"status": "OK", "peer_count": len(peers), "period": packet.get("period"),
               "price_basis": packet.get("price_basis"), "target_per": target.get("per"), "target_pbr": target.get("pbr")}
        for field in ("per", "pbr"):
            values = sorted(p[field] for p in peers if isinstance(p.get(field), (int, float)) and p[field] > 0)
            # Loss-making / missing peers drop out per metric; usability is judged on this count.
            out["peer_valid_" + field] = len(values)
            median = None
            if values:
                mid = len(values) // 2
                median = values[mid] if len(values) % 2 else (values[mid - 1] + values[mid]) / 2
            out["peer_median_" + field] = median
            own = target.get(field)
            out[field + "_discount_vs_median_pct"] = round((1 - own / median) * 100, 2) \
                if median and isinstance(own, (int, float)) and own > 0 else None
        if not peers:
            out.update(status="MISSING", reason="no_comparable_peers")
        return out
    except Exception as error:  # noqa: BLE001
        return {"status": "MISSING", "reason": "summary_error_" + type(error).__name__}


def _next_earnings(ticker, today):
    import yfinance as yf
    calendar = yf.Ticker(ticker).calendar or {}
    dates = calendar.get("Earnings Date") if isinstance(calendar, dict) else None
    upcoming = sorted(d for d in (dates or []) if isinstance(d, date) and d >= today)
    return upcoming[0].isoformat() if upcoming else None


def us_earnings(ticker, today):
    try:
        future = _POOL.submit(_next_earnings, ticker, today)
        found = future.result(timeout=_EARNINGS_TIMEOUT)
        if not found:
            return {"status": "MISSING", "reason": "no_upcoming_date"}
        return {"status": "OK", "next_earnings_date": found,
                "calendar_days_to_earnings": (date.fromisoformat(found) - today).days, "source": "yfinance_calendar"}
    except concurrent.futures.TimeoutError:
        return {"status": "MISSING", "reason": "timeout"}
    except Exception as error:  # noqa: BLE001
        return {"status": "MISSING", "reason": type(error).__name__}


def prompt_facts(agent, ticker, *, market, language="ko", now=None, earnings_lookup=None):
    """Facts block for the BUY prompt ('' when disabled or unavailable). Never raises.

    The computed features, flags and block are cached on the agent so the SHADOW
    event later records exactly what the prompt contained.
    """
    try:
        if not prompt_facts_enabled() or not ticker:
            return ""
        now = now or datetime.now(timezone.utc)
        captured = (getattr(agent, "_decision_input_bars", {}) or {}).get(ticker)
        if not captured or captured.get("market") != market:
            return ""
        result = compute(captured["bars"], market=market, observed_at=now)
        meta = (getattr(agent, "_report_meta", None) or {}).get(ticker) or {}
        peer = meta.get("peer_valuation")
        earnings = (earnings_lookup or us_earnings)(ticker, now.date()) if market == "US" else None
        block, flags = render_facts_block(result, peer, earnings, market=market, language=language)
        if not hasattr(agent, "_decision_input_prompt"):
            agent._decision_input_prompt = {}
        agent._decision_input_prompt[ticker] = {"flags": flags, "block": block, "earnings": earnings,
                                                "observed_at": now.isoformat()}
        return block
    except Exception:  # noqa: BLE001 - optional input must never fail a BUY decision
        return ""


def emit_decision_inputs(agent, *, market, ticker, decision_id, scenario, current_price, decision, source,
                         now=None, earnings_lookup=us_earnings):
    """Emit one idempotent SHADOW event per decision_id; returns the payload or None."""
    try:
        if not enabled() or not decision_id or getattr(agent, "_no_order_effects", None) is not None:
            return None
        now = now or datetime.now(timezone.utc)
        captured = (getattr(agent, "_decision_input_bars", {}) or {}).get(ticker)
        if captured and captured.get("market") == market:
            result = compute(captured["bars"], market=market, observed_at=now,
                             current_price=current_price, scenario=scenario)
        else:
            result = {"status": "MISSING", "features": {"contract_version": CONTRACT_VERSION},
                      "missing": {"daily_frame": "not_captured"}}
        meta = (getattr(agent, "_report_meta", None) or {}).get(ticker) or {}
        result["peer_valuation"] = meta.get("peer_valuation") or {"status": "MISSING", "reason": "not_in_report_meta"}
        shown = (getattr(agent, "_decision_input_prompt", {}) or {}).get(ticker)
        if market == "US":
            result["earnings"] = (shown or {}).get("earnings") or earnings_lookup(ticker, now.date())
        else:
            result["earnings"] = {"status": "NOT_COLLECTED", "reason": "kr_earnings_calendar_not_in_v1"}
        result["prompt_flags"] = (shown or {}).get("flags")
        result["sector_comovement"] = {"status": "NOT_COLLECTED", "reason": "deferred_v2"}
        payload = {"mode": "SHADOW", "trading_impact": "none",
                   "prompt_impact": "facts_block_included" if shown else "none",
                   "contract_version": CONTRACT_VERSION, "decision": decision, "source": source, **result}
        event_id = hashlib.sha256(f"{CONTRACT_VERSION}|{market}|{decision_id}".encode()).hexdigest()[:32]
        emit_event("decision_inputs.shadow_captured", service="prism-" + market.lower() + "-decision-inputs-shadow",
                   event_id=event_id, market=market, ticker=ticker, decision_id=str(decision_id),
                   attributes=payload, event_time=now)
        return payload
    except Exception:  # noqa: BLE001 - observation never changes a decision
        return None
