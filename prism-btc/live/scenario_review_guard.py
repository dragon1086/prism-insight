"""Fresh-state check of a completely replayed review, never order authorization."""
from copy import deepcopy
from functools import partial
import math

from analysis.scenario_review_tape import replay_review
from core.scenario_limit_prices import validate_execution_prices
from live.scenario_calculated_review import (
    ECONOMIC_REJECT_MODES, _supplied_instrument_filters, receipt_hash, research_review,
)
from live.scenario_contract import validate_wire_proposal
from live.scenario_entry_contract import strip_metadata, validate_metadata
from live.scenario_entry_goal import prepare_goal_source, validate_goal_pair
from live.scenario_runtime import snapshot_input_time


_REQUIRED_STABLE = (
    "input_id", "input_captured_at", "max_input_age_seconds", "scenario_id", "revision",
    "account_version", "positions", "pending_entries", "previous_hard_stop",
    "initial_equity", "realized_loss", "fees_paid", "funding_paid",
    "estimated_cost_rate", "slippage_bps", "price_tick", "quantity_step",
    "minimum_quantity", "minimum_notional", "target_status", "new_risk_blocked",
    "accounting_status", "protection_ok", "legacy_fenced",
)
_OPTIONAL_STABLE = (
    'entry_convergence', 'management_quarantine',
    'management_quarantine_block',
    "side", "scenario_risk_fraction", "seen_action_ids", "current_plan", "stop_policy",
    "execution_price_policy", "conditional_entry_version",
    "review_contract_version", "recovery_hard_blocked", "unknown_intents",
    "entry_contract_version", "registered_entry_contract", "entry_contract_status", "entry_contract_failure_rule",
    "entry_goal_untrusted",
    'pending_wait_policy_version', 'pending_renewal_version', 'pending_renewal_offer', 'pending_wait',
    'immediate_reduce_version', 'market_maximum_quantity', 'immediate_reduce_available',
)


def _number(value):
    if type(value) not in (int, float) or not math.isfinite(value):
        raise ValueError("review_guard_invalid_time")
    return value


def _meaningful(plan):
    # Pricing receipt quote times/reasons are evidence, not executable fields.
    return {key: value for key, value in plan.items()
            if key not in {"risk", "execution_pricing", "rationale"}}


def guard_rejected_review(snapshot, context, response_contract, envelope, *, bindings, research_mode):
    """Verify the full rejection tape. This path never exposes an executable wire."""
    if research_mode not in ECONOMIC_REJECT_MODES:
        raise ValueError("review_rejection_mode")
    outcome = replay_review(envelope, partial(research_review, research_mode=research_mode),
                            snapshot, context, response_contract, bindings=bindings)
    result = outcome.get("result", {})
    if outcome.get("status") != "COMPLETE" or result.get("disposition") != "REJECTED_NO_ACTION":
        raise ValueError("review_rejection_incomplete")
    events = [op["event"] for op in envelope["operations"] if op["kind"] == "record"]
    required = dict(kind="RESEARCH_REJECTION", body=result, sha256=receipt_hash(result))
    if sum(event == required for event in events) != 1:
        raise ValueError("review_rejection_audit_missing")
    receipts = result["candidates"]["candidates"]
    if not receipts or any(r.get("eligible") is not False or
                          r.get("rejection_type") != "PREAUTHORITY_ECONOMIC" for r in receipts):
        raise ValueError("review_rejection_invalid")
    return dict(disposition="REJECTED_NO_ACTION", rejection_codes=[r["rejection"] for r in receipts],
                envelope_sha256=envelope["sha256"], bundle_sha256=result["bundle_sha256"],
                execution_authorized=False)


def guard_review(snapshot, original_context, response_contract, envelope, fresh_context,
                 *, bindings=None, research_mode="legacy"):
    """Replay exact evidence, then reprice the original wire once on fresh facts.

    The caller still owns locking, recovery authorization and broker preflight.
    A receipt here does not assert any order was submitted or protection exists.
    """
    if research_mode not in {"legacy", "allocation-single", "allocation-calculated",
                             "action-allocation-single-v1", "action-allocation-calculated-v1"} | ECONOMIC_REJECT_MODES:
        raise ValueError("research_review_mode")
    review = research_review if research_mode == "legacy" else partial(research_review, research_mode=research_mode)
    outcome = replay_review(envelope, review, snapshot, original_context,
                            response_contract, bindings=bindings)
    if outcome.get("status") != "COMPLETE":
        raise ValueError("review_guard_incomplete_review")
    result = outcome["result"]
    selected = result["selected"]
    renewal = bool(selected['raw_proposal'].get('pending_renewal'))
    actionable = selected["raw_proposal"]["action"] in {"OPEN", "ADJUST", "REDUCE"} or renewal
    original, fresh = deepcopy(original_context), deepcopy(fresh_context)
    selected_plan = selected['raw_proposal']
    goal_independent = (original.get('entry_contract_version') == 2 and not renewal
        and (selected_plan['action'] in {'WAIT', 'EXIT', 'REDUCE'}
             or (selected_plan['action'] == 'ADJUST' and not selected_plan.get('entries')
                 and not selected_plan.get('take_profits'))))
    goal_context = original
    if original.get('entry_contract_version') == 2:
        goal_context = deepcopy(original)
        prepare_goal_source(snapshot, goal_context)
    if 'entry_convergence' in original:
        from core.scenario_convergence import entry_convergence
        if original['entry_convergence'] != entry_convergence(snapshot):
            raise ValueError('review_convergence_context_changed')
    now = _number(fresh.get("now"))
    last_review_time = max(operation["value"] for operation in envelope["operations"]
                           if operation["kind"] == "clock")
    if now < last_review_time:
        raise ValueError("review_guard_stale_review_clock")
    max_age = min(120, _number(original.get("max_input_age_seconds")))
    if max_age <= 0:
        raise ValueError("review_guard_invalid_time")
    for stamp in (snapshot_input_time(snapshot), original.get("input_captured_at")):
        if not 0 <= now - _number(stamp) <= max_age:
            raise ValueError("review_guard_stale_input")
    if not 0 <= now - _number(fresh.get("account_captured_at")) <= 120:
        raise ValueError("review_guard_stale_account")
    for key in _REQUIRED_STABLE:
        if (key not in original or key not in fresh
                or (original[key] != fresh[key] and (actionable or key != "new_risk_blocked"))):
            raise ValueError("review_guard_context_changed:" + key)
    for key in _OPTIONAL_STABLE:
        if key == 'immediate_reduce_available' and selected_plan['action'] != 'REDUCE':
            continue
        if goal_independent and key == 'entry_goal_untrusted':
            continue
        if (goal_independent and fresh.get('entry_goal_untrusted') is True
                and key in {'pending_renewal_version', 'pending_renewal_offer'} and key not in fresh):
            continue  # A disappearing unused offer grants no new authority.
        if not actionable and key in {"recovery_hard_blocked", "stop_policy"}:
            continue
        if (key in original) != (key in fresh) or original.get(key) != fresh.get(key):
            raise ValueError("review_guard_context_changed:" + key)
    from live.scenario_management_quarantine import entry_aggregate_context
    limited_entry_management = entry_aggregate_context(fresh, selected_plan['action'])
    if ((actionable and fresh["accounting_status"] != "confirmed")
            or fresh["accounting_status"] not in {"confirmed", "pending"}
            or fresh["protection_ok"] is not True
            or (fresh["legacy_fenced"] is not False and not limited_entry_management)
            or type(fresh["new_risk_blocked"]) is not bool
            or ("recovery_hard_blocked" in fresh and type(fresh["recovery_hard_blocked"]) is not bool)):
        raise ValueError("review_guard_unsafe_lifecycle")
    if fresh["scenario_id"] is not None and fresh.get("side") not in ("LONG", "SHORT"):
        raise ValueError("review_guard_missing_side")
    # Recovery observations may advance their market facts; authority is separately
    # checked by the runtime. Never equate full recovery-object equality to permission.
    # prepare_input adds the wire protocol version; a fresh broker observation
    # need not repeat it. Decode only against the original trusted contract;
    # fresh facts below still decide economic validity, never the model metadata.
    payload = validate_wire_proposal(selected["raw_proposal"], original)
    if original.get("entry_contract_version") in (1, 2):
        # Samples may advance without replacing the original premise. A newly
        # confirmed failure still invalidates any non-EXIT reviewed action.
        validate_metadata(payload, fresh)
    # Keep the selected candidate's rationale; selection prose remains separate audit evidence.
    core = {key: value for key, value in strip_metadata(payload).items() if key not in {"review", "recovery"}}
    if core.get('pending_renewal') is None:
        core.pop('pending_renewal', None)
    if core['action'] in {'OPEN', 'ADJUST'}:
        _supplied_instrument_filters(core, fresh)
    validate_goal_pair(payload, None, goal_context)
    validated = validate_execution_prices(core, fresh)
    validate_goal_pair(payload, validated, goal_context)
    from live.scenario_explanation import enabled as explanation_enabled, compile_explanation
    if explanation_enabled(original):
        explanation = compile_explanation(selected['raw_proposal'].get('rationale'), original,
                                          validated, scope=selected['id'], snapshot=snapshot)
        expected_explanation = selected.get('explanation')
        if expected_explanation != explanation:
            raise ValueError('review_guard_explanation_receipt_changed')
        payload = dict(payload, rationale=explanation['text'])
        validated = dict(validated, rationale=explanation['text'])
    if research_mode != "legacy" and selected["id"] != "WAIT" and not renewal:
        from live.scenario_risk_allocation import validate_allocation_prices
        validate_allocation_prices(validated, selected["allocation_receipt"], fresh)
    if core['action'] in {'OPEN', 'ADJUST'}:
        _supplied_instrument_filters(validated, fresh)
    if core["action"] != "WAIT" or renewal:
        expected = selected["effective_plan"]
        if _meaningful(validated) != _meaningful(expected):
            raise ValueError("review_guard_effective_plan_changed")
        if validated.get("risk") != expected.get("risk"):
            raise ValueError("review_guard_risk_changed")
    receipt = dict(status="FRESH_VALIDATED_NOT_AUTHORIZED", checked_at=now,
        envelope_sha256=envelope["sha256"], bundle_sha256=result["bundle_sha256"],
        selected_receipt_sha256=selected["receipt_sha256"],
        original_context_sha256=receipt_hash(original), fresh_context_sha256=receipt_hash(fresh),
        payload_sha256=receipt_hash(payload), validated_sha256=receipt_hash(validated),
        execution_authorized=False, orders_submitted=0)
    receipt["sha256"] = receipt_hash(receipt)
    return dict(payload=deepcopy(payload), validated=deepcopy(validated), receipt=receipt)
