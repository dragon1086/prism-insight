"""Canonical, fail-open context snapshots for PRISM trading decisions."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from observability.events import emit_event

CONTEXT_SCHEMA_VERSION = 1
PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_REGIME_HISTORY = PROJECT_ROOT / "logs" / "regime_history.jsonl"

_MARKET_FIELDS = (
    "regime",
    "market_regime",
    "primary_trend_regime",
    "effective_entry_regime",
    "swing_state",
    "confidence",
    "regime_confidence",
    "simple_ma_regime",
    "index_summary",
    "leading_sectors",
)


def _stable_hex(*parts: Any, length: int) -> str:
    raw = "|".join(str(part or "") for part in parts)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:length]


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _load_scenario(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    if not isinstance(value, str) or not value.strip():
        return {}
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return dict(parsed) if isinstance(parsed, Mapping) else {}


def latest_regime_snapshot(
    market: str,
    *,
    path: Path = DEFAULT_REGIME_HISTORY,
    tail_bytes: int = 256 * 1024,
) -> dict[str, Any]:
    """Read the latest local regime row without network I/O or pipeline impact."""
    target = str(market or "").upper()
    try:
        with path.open("rb") as handle:
            handle.seek(0, 2)
            size = handle.tell()
            handle.seek(max(0, size - tail_bytes))
            raw = handle.read().decode("utf-8", errors="ignore")
        for line in reversed(raw.splitlines()):
            try:
                row = json.loads(line)
            except (TypeError, ValueError, json.JSONDecodeError):
                continue
            if str(row.get("market") or "").upper() == target:
                return dict(row)
    except OSError:
        pass
    return {}


def market_context_from_scenario(
    scenario: Any,
    *,
    market: str,
    fallback: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Return an explicit live snapshot or the immutable entry-time snapshot."""
    parsed = _load_scenario(scenario)
    scenario_context = _mapping(parsed.get("_deterministic_market_context"))
    if not scenario_context:
        scenario_context = {
            "market_regime": parsed.get("market_regime")
            or parsed.get("_deterministic_market_regime"),
            "primary_trend_regime": parsed.get("primary_trend_regime"),
            "effective_entry_regime": parsed.get("effective_entry_regime"),
            "swing_state": parsed.get("swing_state"),
            "regime_confidence": parsed.get("regime_confidence"),
            "index_summary": parsed.get("index_summary"),
            "source": parsed.get("market_regime_source"),
        }

    explicit_context = dict(fallback or {})
    context = {} if explicit_context else scenario_context
    supplements = (
        (explicit_context, scenario_context)
        if explicit_context
        else (latest_regime_snapshot(market),)
    )
    aliases = {
        "regime": "market_regime",
        "confidence": "regime_confidence",
    }
    for supplement in supplements:
        for key in _MARKET_FIELDS:
            target_key = aliases.get(key, key)
            if context.get(target_key) in (None, "", {}):
                value = supplement.get(key)
                if value not in (None, "", {}):
                    context[target_key] = value
        if supplement.get("ts") and not context.get("observed_at"):
            context["observed_at"] = supplement["ts"]
        if supplement.get("source") and not context.get("source"):
            context["source"] = supplement["source"]
    context.setdefault(
        "source",
        "explicit_live_context" if explicit_context else "scenario_or_latest_regime_log",
    )
    return {key: value for key, value in context.items() if value is not None}


def build_trading_context(
    *,
    market: str,
    scenario: Any = None,
    market_context: Mapping[str, Any] | None = None,
    decision_context: Mapping[str, Any] | None = None,
    portfolio_context: Mapping[str, Any] | None = None,
    execution_context: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build the versioned payload shared by candidate, entry, and exit events."""
    parsed = _load_scenario(scenario)
    entry_market_snapshot = market_context_from_scenario(
        parsed,
        market=market,
    )
    market_snapshot = market_context_from_scenario(
        parsed,
        market=market,
        fallback=market_context,
    )
    return {
        "context_schema_version": CONTEXT_SCHEMA_VERSION,
        "captured_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "market_context": market_snapshot,
        "entry_market_context": entry_market_snapshot,
        "security_context": {
            "trend_facts": parsed.get("_deterministic_trend_facts"),
            "sector": parsed.get("sector"),
            "target_price": parsed.get("target_price"),
            "stop_loss": parsed.get("stop_loss"),
            "risk_reward_ratio": parsed.get("risk_reward_ratio"),
            "investment_period": parsed.get("investment_period"),
        },
        "policy_context": {
            "regime_entry_policy": parsed.get("regime_entry_policy"),
            "score_adjustment": parsed.get("score_adjustment"),
            "macro_adjustment": parsed.get("macro_adjustment"),
            "journal_reflection": parsed.get("journal_reflection"),
        },
        "decision_context": dict(decision_context or {}),
        "portfolio_context": dict(portfolio_context or {}),
        "execution_context": dict(execution_context or {}),
    }


def emit_trading_context(
    event_type: str,
    *,
    market: str,
    ticker: str,
    decision_id: str | None = None,
    position_id: str | None = None,
    company_name: str | None = None,
    trigger_type: str | None = None,
    trigger_mode: str | None = None,
    scenario: Any = None,
    market_context: Mapping[str, Any] | None = None,
    decision_context: Mapping[str, Any] | None = None,
    portfolio_context: Mapping[str, Any] | None = None,
    execution_context: Mapping[str, Any] | None = None,
    source: str | None = None,
) -> dict[str, Any] | None:
    """Emit one linked snapshot; every failure is swallowed by design."""
    try:
        normalized_market = str(market or "").upper()
        normalized_ticker = str(ticker or "").upper()
        normalized_decision = str(decision_id or "").strip() or None
        normalized_position = str(position_id or "").strip() or None
        identity = normalized_decision or normalized_position or normalized_ticker
        context = build_trading_context(
            market=normalized_market,
            scenario=scenario,
            market_context=market_context,
            decision_context=decision_context,
            portfolio_context=portfolio_context,
            execution_context=execution_context,
        )
        market_snapshot = context["market_context"]
        decision_snapshot = context["decision_context"]
        attributes = {
            "context_schema_version": CONTEXT_SCHEMA_VERSION,
            "source": source,
            "company_name": company_name,
            "trigger_type": trigger_type,
            "trigger_mode": trigger_mode,
            "regime": market_snapshot.get("market_regime"),
            "primary_trend_regime": market_snapshot.get("primary_trend_regime"),
            "effective_entry_regime": market_snapshot.get("effective_entry_regime"),
            "swing_state": market_snapshot.get("swing_state"),
            "regime_confidence": market_snapshot.get("regime_confidence"),
            "decision": decision_snapshot.get("decision"),
            "was_traded": decision_snapshot.get("was_traded"),
            "selected_for_entry": decision_snapshot.get("selected_for_entry"),
            "gate_allowed": decision_snapshot.get("gate_allowed"),
            "gate_reason": decision_snapshot.get("gate_reason"),
            "exit_kind": decision_snapshot.get("exit_kind"),
            "profit_rate_pct": decision_snapshot.get("profit_rate_pct"),
            "holding_days": decision_snapshot.get("holding_days"),
            **context,
        }
        return emit_event(
            event_type,
            event_id=_stable_hex(
                "trading-context", event_type, normalized_market, identity, normalized_position,
                length=32,
            ),
            service=f"prism-{normalized_market.lower()}-context-ledger",
            market=normalized_market,
            ticker=normalized_ticker,
            trace_id=_stable_hex("trade-trace", normalized_market, identity, length=32),
            decision_id=normalized_decision,
            position_id=normalized_position,
            attributes=attributes,
        )
    except Exception:  # noqa: BLE001 - observability must never affect trading
        return None


def emit_candidate_outcome(
    *,
    market: str,
    record: Mapping[str, Any],
    updates: Mapping[str, Any],
) -> dict[str, Any] | None:
    """Emit one completed 30-day candidate result linked to its decision."""
    try:
        normalized_market = str(market or "").upper()
        source_id = str(record.get("id") or "")
        decision_id = str(record.get("decision_id") or "").strip() or (
            f"tracker:{normalized_market}:{source_id}"
        )

        def resolved(name: str) -> Any:
            return updates.get(name, record.get(name))

        def resolved_any(*names: str) -> Any:
            for name in names:
                value = resolved(name)
                if value is not None:
                    return value
            return None

        return_7d = resolved_any("return_7d", "tracked_7d_return")
        return_14d = resolved_any("return_14d", "tracked_14d_return")
        return_30d = resolved_any("return_30d", "tracked_30d_return")

        return emit_event(
            "candidate.outcome",
            event_id=_stable_hex(
                "candidate-outcome-live", normalized_market, source_id, length=32
            ),
            service=f"prism-{normalized_market.lower()}-performance-tracker",
            market=normalized_market,
            ticker=str(record.get("ticker") or ""),
            trace_id=_stable_hex(
                "trade-trace", normalized_market, decision_id, length=32
            ),
            decision_id=decision_id,
            attributes={
                "ingestion_mode": "live",
                "source": "performance_tracker",
                "source_id": source_id,
                "company_name": record.get("company_name"),
                "analysis_date": record.get("analysis_date")
                or record.get("analyzed_date"),
                "analysis_price": record.get("analysis_price")
                or record.get("analyzed_price"),
                "trigger_type": record.get("trigger_type"),
                "trigger_mode": record.get("trigger_mode"),
                "sector": record.get("sector"),
                "decision": record.get("decision"),
                "was_traded": int(record.get("was_traded") or 0),
                "skip_reason": record.get("skip_reason"),
                "buy_score": record.get("buy_score"),
                "min_score": record.get("min_score"),
                "target_price": record.get("target_price"),
                "stop_loss": record.get("stop_loss"),
                "risk_reward_ratio": record.get("risk_reward_ratio"),
                "return_7d_pct": (
                    float(return_7d) * 100 if return_7d is not None else None
                ),
                "return_14d_pct": (
                    float(return_14d) * 100 if return_14d is not None else None
                ),
                "return_30d_pct": (
                    float(return_30d) * 100 if return_30d is not None else None
                ),
                "hit_target": resolved("hit_target"),
                "hit_stop_loss": resolved("hit_stop_loss"),
                "outcome_observed_at": updates.get("last_updated")
                or updates.get("updated_at"),
            },
        )
    except Exception:  # noqa: BLE001 - tracker persistence already succeeded
        return None


__all__ = [
    "CONTEXT_SCHEMA_VERSION",
    "build_trading_context",
    "emit_candidate_outcome",
    "emit_trading_context",
    "latest_regime_snapshot",
    "market_context_from_scenario",
]
