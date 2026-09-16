"""Fail-open SHADOW observability for PRISM's 초분할 policy."""

from __future__ import annotations

import hashlib
import json
import os
import uuid
from contextvars import ContextVar
from pathlib import Path
from typing import Any

from observability.events import emit_event
from prism_core.micro_split import (
    DEFAULT_POLICY,
    advance_target,
    project_execution_on_advance,
)

SHADOW_SCHEMA_VERSION = 2
_BATCH = ContextVar("micro_split_batch", default=None)


def get_shadow_batch_context() -> dict[str, Any] | None:
    """Copy the explicit run identity for optional observer threads."""
    context = _BATCH.get()
    return dict(context) if context else None


def _emit_watchlist_link(event: dict[str, Any]) -> None:
    """Observe an existing eligible scout; never manufacture one from READY."""
    from observability.oneil_watchlist import read_ready_context

    attrs = event["attributes"]
    batch_ref = attrs.get("batch_ref")
    market = event.get("market")
    if market not in {"US", "KR"} or not batch_ref:
        return
    ready = read_ready_context(market, event["ticker"], batch_ref)
    if not ready or any(ready.get(k) != v for k, v in {
        "market": market, "ticker": event["ticker"], "batch_ref": batch_ref,
    }.items()):
        return
    required = ("watch_ref", "seed_event_id", "ready_event_id",
                "ready_observation_event_id", "policy_version", "observation_price_ref")
    if any(not ready.get(key) for key in required):
        return
    emit_event(
        "watchlist_micro_split.shadow_linked",
        event_id=_stable_ref("watchlist-micro-link", event["event_id"],
                             ready["ready_observation_event_id"], length=32),
        service=f"prism-{market.lower()}-watchlist-micro-shadow", market=market, ticker=event["ticker"],
        parent_event_id=event["event_id"],
        attributes={
            "mode": "SHADOW", "link_schema_version": 1,
            "trading_impact": "none", "execution_provenance": "NOT_REQUESTED",
            "batch_ref": batch_ref, "watch_ref": ready["watch_ref"],
            "seed_event_id": ready["seed_event_id"],
            "ready_event_id": ready["ready_event_id"],
            "ready_observation_event_id": ready["ready_observation_event_id"],
            "observation_price_ref": ready["observation_price_ref"],
            "watch_policy_version": ready["policy_version"],
            "micro_policy_version": attrs["policy_version"],
            "micro_event_id": event["event_id"],
            "source_decision_ref": attrs["decision_ref"],
            "execution_profile_ref": attrs["execution_profile_ref"],
            "entry_boundary": (
                "LEGACY_ELIGIBLE_PRE_REFRESH" if market == "US"
                else attrs.get("entry_boundary", "UNKNOWN")
            ),
            "baseline_position_fraction": attrs.get("baseline_position_fraction"),
            "baseline_sizing_status": (
                "CAPTURED" if attrs.get("baseline_position_fraction") is not None else "UNKNOWN"
            ),
            "broker_approved": False, "confirmed_fill": False,
        },
    )


def begin_shadow_batch(*, market: str, trade_date: str, trigger_mode: str):
    """Bind a new run, never infer completion from decisions or wall-clock dates."""
    try:
        context = None
        if shadow_enabled(market=market) and trigger_mode in {"morning", "afternoon"}:
            context = {
                "batch_ref": uuid.uuid4().hex,
                "market": market,
                "trade_date": trade_date,
                "trigger_mode": trigger_mode,
            }
        return _BATCH.set(context)
    except Exception:  # noqa: BLE001 - capture must not interrupt trading
        return None


def end_shadow_batch(token):
    try:
        if token is not None:
            _BATCH.reset(token)
    except Exception:  # noqa: BLE001 - capture cleanup is fail-open
        return None  # noqa: RET501 - preserve legacy cleanup behavior


def complete_shadow_batch(*, tracking_success, selected_count, report_count, pdf_count):
    """Mark core analysis/tracking completion, not broker fills or translations."""
    try:
        context = _BATCH.get()
        if not context or tracking_success is not True:
            return None
        if selected_count <= 0 or report_count != selected_count or pdf_count != selected_count:
            return None
        return emit_event(
            "micro_split.shadow_batch_completed",
            event_id=_stable_ref("micro-split-completed", context["batch_ref"], length=32),
            service=f"prism-{context['market'].lower()}-micro-split-shadow",
            market=context["market"],
            attributes={
                **context,
                "mode": "SHADOW",
                "completion_scope": "analysis_and_tracking",
                "status": "COMPLETED",
                "selected_count": selected_count,
                "trading_impact": "none",
            },
        )
    except Exception:  # noqa: BLE001 - observability is fail-open
        return None


def shadow_enabled(value: str | None = None, *, market: str = "US") -> bool:
    if market == "KR":
        try:
            path = Path(__file__).resolve().parents[1] / "trading/config/oneil_watchlist_kr_shadow.json"
            return json.loads(path.read_text()) == {
                "mode": "SHADOW", "market": "KR",
                "policy_version": "oneil_watchlist_kr_v1", "enabled": True,
            }
        except (OSError, ValueError):
            return False
    if market != "US":
        return False
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
    baseline_position_fraction: Any = None,
) -> dict[str, Any] | None:
    """Append one initial-target SHADOW event; never affect the caller."""
    if not shadow_enabled(market=market):
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
        if market == "KR":
            context.update(
                currency="KRW", entry_boundary="FRESH_QUOTE_REVALIDATED",
                unit_amount_source="account.buy_amount_krw",
                execution_price_source="refresh_buy_boundary",
                baseline_position_fraction=(
                    baseline_position_fraction
                    if type(baseline_position_fraction) in (int, float)
                    and 0 < baseline_position_fraction <= 1 else None
                ),
            )
        batch = _BATCH.get()
        if batch and batch["market"] == str(market).upper():
            context.update(batch)
        event = emit_event(
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
        try:
            if event:
                _emit_watchlist_link(event)
        except Exception:  # noqa: BLE001 - optional observer cannot change original result
            return event
        return event
    except Exception:  # noqa: BLE001 - observability must never affect trading
        return None


__all__ = [
    "SHADOW_SCHEMA_VERSION",
    "build_initial_shadow_context",
    "emit_initial_shadow",
    "get_shadow_batch_context",
    "shadow_enabled",
]
