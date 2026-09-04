"""Fail-open SHADOW observability for PRISM's 초분할 policy."""

from __future__ import annotations

import hashlib
import os
from typing import Any

from observability.events import emit_event
from prism_core.micro_split import (
    DEFAULT_POLICY,
    advance_target,
    project_execution_on_advance,
)

SHADOW_SCHEMA_VERSION = 2


def shadow_enabled(value: str | None = None) -> bool:
    raw = (
        value if value is not None else os.getenv("MICRO_SPLIT_SHADOW_ENABLED", "false")
    )
    return str(raw or "").strip().lower() in {"1", "true", "yes", "on"}


def _stable_ref(*parts: Any, length: int) -> str:
    raw = "|".join(str(part or "") for part in parts)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:length]


def _base_stage_projections(
    *, unit_amount: Any, current_price: Any
) -> tuple[dict[str, int], int | None]:
    quantities: dict[str, int] = {}
    try:
        for target_pct in DEFAULT_POLICY.base_steps_pct:
            projection = project_execution_on_advance(
                unit_amount=unit_amount,
                previous_target_pct=0,
                target_pct=target_pct,
                execution_price=current_price,
                confirmed_strategy_quantity=0,
            )
            quantities[str(target_pct)] = projection.desired_quantity
    except (TypeError, ValueError):
        return {}, None
    first_executable = next(
        (
            target_pct
            for target_pct in DEFAULT_POLICY.base_steps_pct
            if quantities[str(target_pct)] > 0
        ),
        None,
    )
    return quantities, first_executable


def build_initial_shadow_context(
    *,
    market: str,
    decision_id: str,
    account_id: str,
    unit_amount: Any,
    current_price: Any,
    regime: str,
) -> dict[str, Any]:
    """Build a secret-minimized 0→10% scout projection without I/O."""
    transition = advance_target(DEFAULT_POLICY, 0, 10, regime=regime)
    projection_status = "PROJECTED"
    projected_quantity = None
    projected_delta = None
    stage_quantities, first_executable = _base_stage_projections(
        unit_amount=unit_amount,
        current_price=current_price,
    )
    if stage_quantities:
        projected_quantity = stage_quantities["10"]
        projected_delta = projected_quantity
    else:
        projection_status = "INPUT_UNAVAILABLE"

    return {
        "shadow_schema_version": SHADOW_SCHEMA_VERSION,
        "mode": "SHADOW",
        "policy_version": DEFAULT_POLICY.policy_version,
        "reason_code": "ENTRY_ELIGIBLE_SCOUT",
        "market": str(market or "").upper(),
        "regime": transition.regime,
        "previous_target_pct": transition.previous_target_pct,
        "target_pct": transition.target_pct,
        "target_slot_units": transition.target_slot_units,
        "max_target_pct": transition.max_target_pct,
        "is_pyramid": transition.is_pyramid,
        "execution_profile_ref": _stable_ref(
            "micro-split-execution-profile", account_id, length=16
        ),
        "unit_amount_snapshot_ref": (
            _stable_ref(
                "micro-split-unit-snapshot",
                account_id,
                unit_amount,
                DEFAULT_POLICY.policy_version,
                length=16,
            )
            if stage_quantities
            else None
        ),
        "unit_amount_available": unit_amount not in (None, "", 0, 0.0),
        "execution_price_available": current_price not in (None, "", 0, 0.0),
        "projection_status": projection_status,
        "projected_whole_share_quantity": projected_quantity,
        "projected_buy_delta_quantity": projected_delta,
        "base_stage_projection_quantities": stage_quantities,
        "first_executable_target_pct": first_executable,
        "internal_target_independent_of_execution": True,
        "decision_ref": _stable_ref("micro-split-decision", decision_id, length=16),
    }


def emit_initial_shadow(
    *,
    market: str,
    ticker: str,
    decision_id: str,
    account_id: str,
    unit_amount: Any,
    current_price: Any,
    regime: str,
) -> dict[str, Any] | None:
    """Append one initial-target SHADOW event; never affect the caller."""
    if not shadow_enabled():
        return None
    try:
        context = build_initial_shadow_context(
            market=market,
            decision_id=decision_id,
            account_id=account_id,
            unit_amount=unit_amount,
            current_price=current_price,
            regime=regime,
        )
        profile_ref = context["execution_profile_ref"]
        return emit_event(
            "micro_split.shadow_evaluated",
            event_id=_stable_ref(
                "micro-split-shadow",
                market,
                decision_id,
                profile_ref,
                DEFAULT_POLICY.policy_version,
                length=32,
            ),
            service=f"prism-{str(market or '').lower()}-micro-split-shadow",
            market=market,
            ticker=ticker,
            trace_id=_stable_ref("trade-trace", market, decision_id, length=32),
            decision_id=decision_id,
            attributes=context,
        )
    except Exception:  # noqa: BLE001 - observability must never affect trading
        return None


__all__ = [
    "SHADOW_SCHEMA_VERSION",
    "build_initial_shadow_context",
    "emit_initial_shadow",
    "shadow_enabled",
]
