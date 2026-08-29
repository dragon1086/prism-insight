"""US entry-quality capture built only from already available local facts.

This module is deliberately observation-only.  It does not fetch market data,
call a model, or return an entry decision.  Missing evidence stays ``MISSING``
instead of being interpreted as a pass.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import sys
from collections.abc import Mapping
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any

from observability.events import emit_event

ENTRY_QUALITY_CONTEXT_SCHEMA_VERSION = 1
ENTRY_QUALITY_EXTRACTOR_VERSION = "us-local-facts-v1"
PROJECT_ROOT = Path(__file__).resolve().parents[1]
_FEEDBACK_MODULE_NAME = "prism_root_entry_quality_performance_feedback"

COMPLETENESS_STATUSES = frozenset({"OK", "MISSING", "ERROR"})
FILL_PROVENANCE_STATUSES = frozenset(
    {"SUBMITTED_ONLY", "PARTIAL", "CONFIRMED", "REJECTED", "CANCELLED", "UNKNOWN"}
)

_FALSE_VALUES = frozenset({"0", "false", "no", "off"})


def capture_enabled(value: str | None = None) -> bool:
    """Return whether additive capture is enabled (default on, explicit off)."""

    configured = (
        os.getenv("ENTRY_QUALITY_CAPTURE_ENABLED", "1")
        if value is None
        else value
    )
    return str(configured or "").strip().lower() not in _FALSE_VALUES


def validate_completeness_status(value: str) -> str:
    normalized = str(value or "").strip().upper()
    if normalized not in COMPLETENESS_STATUSES:
        raise ValueError(f"unsupported completeness status: {value!r}")
    return normalized


def validate_fill_provenance_status(value: str) -> str:
    normalized = str(value or "").strip().upper()
    if normalized not in FILL_PROVENANCE_STATUSES:
        raise ValueError(f"unsupported fill provenance status: {value!r}")
    return normalized


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _as_utc(value: datetime | None) -> datetime:
    current = value or _utc_now()
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    return current.astimezone(timezone.utc)


def _iso(value: datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _number(value: Any) -> float | int | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return value
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return None


def _distance_pct(level: Any, current_price: Any) -> float | None:
    normalized_level = _number(level)
    normalized_price = _number(current_price)
    if normalized_level is None or normalized_price is None or normalized_price <= 0:
        return None
    return round((normalized_level / normalized_price - 1.0) * 100.0, 4)


def _selected_trigger_stats(value: Any, *, actual: bool) -> dict[str, Any] | None:
    stats = _mapping(value)
    if not stats:
        return None
    if actual:
        keys = (
            "source",
            "n",
            "win_rate",
            "median_return_pct",
            "profit_factor",
        )
    else:
        keys = (
            "source",
            "n",
            "positive_rate_30d",
            "median_7d_pct",
            "median_14d_pct",
            "median_30d_pct",
        )
    return {key: stats.get(key) for key in keys}


@lru_cache(maxsize=1)
def _load_performance_feedback_module() -> Any:
    """Load root feedback code without relying on the shadowable tracking package."""

    existing = sys.modules.get(_FEEDBACK_MODULE_NAME)
    if existing is not None and hasattr(existing, "get_trigger_feedback"):
        return existing
    module_path = PROJECT_ROOT / "tracking" / "performance_feedback.py"
    specification = importlib.util.spec_from_file_location(
        _FEEDBACK_MODULE_NAME, module_path
    )
    if specification is None or specification.loader is None:
        raise ImportError(f"cannot load performance feedback module: {module_path}")
    module = importlib.util.module_from_spec(specification)
    sys.modules[_FEEDBACK_MODULE_NAME] = module
    try:
        specification.loader.exec_module(module)
    except Exception:
        sys.modules.pop(_FEEDBACK_MODULE_NAME, None)
        raise
    return module


def trigger_prior_snapshot(cursor: Any, trigger_type: str | None) -> dict[str, Any]:
    """Read the existing local feedback tables; never raise into trading."""

    if not trigger_type:
        return {
            "status": "MISSING",
            "reason_code": "TRIGGER_TYPE_MISSING",
            "source": "tracking.performance_feedback",
        }
    try:
        feedback_module = _load_performance_feedback_module()
        feedback = feedback_module.get_trigger_feedback(cursor, "US", trigger_type)
        candidate = _selected_trigger_stats(
            feedback.get("candidate_trigger"), actual=False
        )
        actual = _selected_trigger_stats(feedback.get("actual_trigger"), actual=True)
        if candidate is None and actual is None:
            return {
                "status": "MISSING",
                "reason_code": "NO_MATURED_TRIGGER_HISTORY",
                "source": "tracking.performance_feedback",
                "trigger_type": trigger_type,
                "window": "all_available_at_capture",
            }
        return {
            "status": "OK",
            "source": "tracking.performance_feedback",
            "trigger_type": trigger_type,
            "window": "all_available_at_capture",
            "candidate": candidate,
            "actual": actual,
        }
    except Exception as error:  # noqa: BLE001 - observation must be fail-open
        return {
            "status": "ERROR",
            "reason_code": "TRIGGER_PRIOR_READ_ERROR",
            "error_type": type(error).__name__,
            "source": "tracking.performance_feedback",
            "trigger_type": trigger_type,
        }


def build_entry_quality_context(
    *,
    scenario: Mapping[str, Any] | None,
    current_price: Any,
    cursor: Any = None,
    trigger_type: str | None = None,
    as_of: datetime | None = None,
    captured_at: datetime | None = None,
) -> dict[str, Any]:
    """Build a compact, versioned context from the existing US decision input."""

    captured = _as_utc(captured_at)
    observed = _as_utc(as_of or captured)
    if observed > captured:
        raise ValueError("entry-quality as_of cannot be in the future")

    parsed = _mapping(scenario)
    trading_scenarios = _mapping(parsed.get("trading_scenarios"))
    key_levels = _mapping(trading_scenarios.get("key_levels"))
    level_fields = (
        "primary_support",
        "secondary_support",
        "primary_resistance",
        "secondary_resistance",
    )
    normalized_levels = {
        key: _number(key_levels.get(key)) for key in level_fields
    }
    normalized_levels = {
        key: value for key, value in normalized_levels.items() if value is not None
    }
    distances = {
        f"{key}_distance_pct": _distance_pct(value, current_price)
        for key, value in normalized_levels.items()
    }
    setup_status = "OK" if normalized_levels else "MISSING"
    setup_quality = {
        "status": setup_status,
        "source": "scenario.trading_scenarios.key_levels",
        "entry_position": {
            "levels": normalized_levels,
            "distances_from_entry_pct": distances,
        },
        "structured_checks": {
            "entry_checklist_passed": _number(parsed.get("entry_checklist_passed")),
            "momentum_signal_count": _number(parsed.get("momentum_signal_count")),
            "additional_confirmation_count": _number(
                parsed.get("additional_confirmation_count")
            ),
        },
        "daily": {
            "status": "MISSING",
            "reason_code": "DAILY_BASE_NOT_STRUCTURED_IN_SCENARIO",
        },
        "weekly": {
            "status": "MISSING",
            "reason_code": "WEEKLY_BASE_NOT_STRUCTURED_IN_SCENARIO",
        },
    }
    if setup_status == "MISSING":
        setup_quality["reason_code"] = "KEY_LEVELS_MISSING"

    trigger_prior = trigger_prior_snapshot(cursor, trigger_type)
    trigger_prior["as_of"] = _iso(observed)

    # The current scenario contains prose about news, not a versioned event
    # classification.  Do not silently promote keyword guesses into evidence.
    event_risk = {
        "status": "MISSING",
        "reason_code": "STRUCTURED_EVENT_EVIDENCE_UNAVAILABLE",
        "source": "existing_report_scenario",
    }

    component_statuses = (
        setup_quality["status"],
        event_risk["status"],
        trigger_prior["status"],
        setup_quality["daily"]["status"],
        setup_quality["weekly"]["status"],
    )
    component_names = (
        "setup_quality",
        "event_risk",
        "trigger_prior",
        "setup_quality.daily",
        "setup_quality.weekly",
    )
    missing_components = [
        name
        for name, status in zip(component_names, component_statuses)
        if status != "OK"
    ]
    overall_status = (
        "ERROR"
        if "ERROR" in component_statuses
        else "MISSING"
        if missing_components
        else "OK"
    )
    hash_input = {
        "as_of": _iso(observed),
        "current_price": _number(current_price),
        "setup_quality": setup_quality,
        "event_risk": event_risk,
        "trigger_prior": trigger_prior,
    }
    encoded = json.dumps(
        hash_input, ensure_ascii=True, separators=(",", ":"), sort_keys=True
    )
    return {
        "context_schema_version": ENTRY_QUALITY_CONTEXT_SCHEMA_VERSION,
        "status": validate_completeness_status(overall_status),
        "missing_components": missing_components,
        "as_of": _iso(observed),
        "source": "existing_us_scenario_and_local_feedback",
        "extractor_version": ENTRY_QUALITY_EXTRACTOR_VERSION,
        "input_hash": hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:24],
        "setup_quality": setup_quality,
        "event_risk": event_risk,
        "trigger_prior": trigger_prior,
    }


def build_fill_provenance(
    result: Mapping[str, Any] | None = None,
    *,
    outcome_unknown: bool = False,
) -> dict[str, Any]:
    """Classify only what the current order response proves about a fill."""

    payload = _mapping(result)
    intent_status = str(payload.get("intent_status") or "").strip().upper()
    if outcome_unknown or intent_status == "UNKNOWN":
        status = "UNKNOWN"
        reason_code = "ORDER_OUTCOME_UNKNOWN"
    elif intent_status == "QUEUED":
        status = "UNKNOWN"
        reason_code = "LOCAL_QUEUE_NOT_SUBMITTED"
    elif intent_status == "SUBMITTED" or payload.get("success") or payload.get(
        "partial_success"
    ):
        status = "SUBMITTED_ONLY"
        reason_code = "BROKER_ACCEPTED_FILL_NOT_CONFIRMED"
    elif intent_status == "FAILED" or payload:
        status = "REJECTED"
        reason_code = "ORDER_NOT_ACCEPTED"
    else:
        status = "UNKNOWN"
        reason_code = "ORDER_RESULT_UNAVAILABLE"
    return {
        "schema_version": 1,
        "status": validate_fill_provenance_status(status),
        "reason_code": reason_code,
        "order_status": intent_status or None,
        "broker": payload.get("intent_broker"),
        "submission_scope": (
            "PARTIAL_ACCOUNTS" if payload.get("partial_success") else "SINGLE_ACCOUNT"
        ),
        "confirmed_fill_price": None,
        "confirmed_fill_at": None,
    }


def emit_fill_reconciliation(
    *,
    market: str,
    ticker: str,
    decision_id: str | None,
    position_id: str | None,
    intent_id: str | None,
    result: Mapping[str, Any] | None = None,
    outcome_unknown: bool = False,
) -> dict[str, Any] | None:
    """Append one deterministic fill-provenance observation when capture is on."""

    if not capture_enabled():
        return None
    try:
        provenance = build_fill_provenance(result, outcome_unknown=outcome_unknown)
        identity = intent_id or decision_id or position_id or ticker
        event_key = f"entry-fill-reconciled|{market}|{identity}"
        event_id = hashlib.sha256(event_key.encode()).hexdigest()[:32]
        trace_identity = decision_id or position_id or ticker
        trace_key = f"trade-trace|{str(market).upper()}|{trace_identity}"
        trace_id = hashlib.sha256(trace_key.encode()).hexdigest()[:32]
        attributes = {
            "source": "us_order_intent_result",
            "fill_provenance": provenance,
            "intent_ref": (
                hashlib.sha256(str(intent_id).encode("utf-8")).hexdigest()[:16]
                if intent_id
                else None
            ),
        }
        return emit_event(
            "entry.fill_reconciled",
            event_id=event_id,
            service=f"prism-{str(market).lower()}-context-ledger",
            market=market,
            ticker=ticker,
            trace_id=trace_id,
            decision_id=decision_id,
            position_id=position_id,
            attributes=attributes,
        )
    except Exception:  # noqa: BLE001 - observability must never affect trading
        return None


__all__ = [
    "COMPLETENESS_STATUSES",
    "ENTRY_QUALITY_CONTEXT_SCHEMA_VERSION",
    "ENTRY_QUALITY_EXTRACTOR_VERSION",
    "FILL_PROVENANCE_STATUSES",
    "build_entry_quality_context",
    "build_fill_provenance",
    "capture_enabled",
    "emit_fill_reconciliation",
    "trigger_prior_snapshot",
    "validate_completeness_status",
    "validate_fill_provenance_status",
]
