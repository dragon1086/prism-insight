"""Research-only model allocation to quantity adapter. No execution authority.

The model owns prices, confidence and allocations. This module only computes
quantities, reserves all existing exposure and rejects infeasible proposals.
The ordinary scenario validator and fresh execution guard remain mandatory.
"""
from copy import deepcopy
from decimal import Decimal, ROUND_FLOOR
import math

from core.llm_scenario import EconomicProposalRejection, ScenarioValidationError, quantity_for_risk, risk_snapshot
from core.scenario_limit_prices import possible_entry_price_anchor


def _number(value, name, *, positive=False):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ScenarioValidationError(f"allocation_{name}: finite number required")
    try:
        valid = math.isfinite(value) and value >= 0 and (not positive or value > 0)
    except OverflowError:
        valid = False
    if not valid:
        raise ScenarioValidationError(f"allocation_{name}: invalid number")
    return Decimal(str(value))


def _risk(plan, context, entries):
    return risk_snapshot(initial_equity=context["initial_equity"], side=plan["side"],
        hard_stop=plan["hard_stop"], positions=context["positions"],
        pending_entries=context["pending_entries"], entries=entries,
        realized_loss=context["realized_loss"], fees_paid=context["fees_paid"],
        funding_paid=context["funding_paid"],
        estimated_cost_rate=context["estimated_cost_rate"],
        slippage_bps=context["slippage_bps"],
        risk_fraction=context.get("scenario_risk_fraction", .02))


def _unit_risk(plan, context, price):
    distance = (_number(price, "price", positive=True) -
                _number(plan["hard_stop"], "hard_stop", positive=True))
    if plan["side"] == "SHORT":
        distance = -distance
    if distance <= 0:
        raise ScenarioValidationError("allocation_stop_direction")
    return (distance + Decimal(str(price)) * _number(context["estimated_cost_rate"], "cost")
            + Decimal(str(plan["hard_stop"])) * _number(context["slippage_bps"], "slippage") / 10000)


def _filters(plan, context):
    tick = _number(context["price_tick"], "price_tick", positive=True)
    step = _number(context["quantity_step"], "quantity_step", positive=True)
    minimum = _number(context["minimum_quantity"], "minimum_quantity", positive=True)
    notional = _number(context["minimum_notional"], "minimum_notional", positive=True)
    prices = [plan["hard_stop"]] + [e["price"] for e in plan["entries"]]
    prices += [e["price"] for e in plan.get("take_profits", [])]
    prices += [e["price"] for e in plan.get("partial_stops", [])]
    if any(_number(p, "price", positive=True) % tick for p in prices):
        raise ScenarioValidationError("allocation_price_off_tick")
    return step, minimum, notional


def _chase_risk_price(plan, context, price):
    """Reserve the full immutable batch at its worst authorized chase price.

    Execution bounds every reprice against the persisted original entry, never
    the last chase price. Partial fills therefore cannot consume more than this
    full-quantity bound. Effective-price validation must recompute the anchor.
    """
    chase = plan.get("chase") or {"max_bps": 0, "max_reprices": 0}
    if not isinstance(chase, dict):
        raise ScenarioValidationError("allocation_chase")
    bps = _number(chase.get("max_bps"), "chase_bps")
    count = chase.get("max_reprices")
    if bps > 50 or type(count) is not int or not 0 <= count <= 3:
        raise ScenarioValidationError("allocation_chase")
    anchor = _number(price, "price", positive=True)
    if not count:
        return anchor
    # For SHORT the risk derivative is cost_rate - 1. Retain the conservative
    # branch even for unusual supplied cost reserves rather than assuming zero.
    rising = plan["side"] == "LONG" or _number(context["estimated_cost_rate"], "cost") >= 1
    return anchor * (1 + bps / 10000 if rising else 1 - bps / 10000)


def _worst_unit_risk(plan, context, anchor):
    # Keep the theoretical bound Decimal-exact even when max_bps produces a
    # non-tick price; execution rounds only within this permitted interval.
    cost = _number(context["estimated_cost_rate"], "cost")
    slope = cost + (1 if plan["side"] == "LONG" else -1)
    return (_unit_risk(plan, context, anchor)
            + (_chase_risk_price(plan, context, anchor) - Decimal(str(anchor))) * slope)


def allocate_entry_risk(proposal, context):
    """Return (ordinary quantity proposal, auditable allocation receipt).

    No missing allocation defaults, cancellation netting or upward sizing.
    Caller retains the original response and binds the receipt to its digest.
    """
    plan = deepcopy(proposal)
    entries = plan.get("entries")
    if not isinstance(entries, list) or len(entries) > 20:
        raise ScenarioValidationError("allocation_entries")
    confidence = _number(plan.get("confidence"), "confidence")
    if confidence > 1:
        raise ScenarioValidationError("allocation_confidence")
    if not entries:
        return plan, dict(version="risk-allocation-v1", entries=[], executable=False)
    if plan.get("action") not in ("OPEN", "ADJUST") or plan.get("side") not in ("LONG", "SHORT"):
        raise ScenarioValidationError("allocation_action_or_side")
    fractions, ids = [], set()
    for row in entries:
        if not isinstance(row, dict) or "quantity" in row:
            raise ScenarioValidationError("allocation_quantity_ambiguity")
        entry_id = row.get("id")
        if not isinstance(entry_id, str) or not entry_id or entry_id in ids:
            raise ScenarioValidationError("allocation_entry_id")
        ids.add(entry_id)
        fractions.append(_number(row.get("allocation_fraction"), "fraction", positive=True))
    if sum(fractions) > 1:
        raise ScenarioValidationError("allocation_fraction_sum")
    step, minimum, min_notional = _filters(plan, context)
    base = _risk(plan, context, [])
    budget = Decimal(str(base["budget"]))
    reserved = Decimal(str(base["total_risk"]))
    confidence_cap = budget * confidence
    available = max(Decimal(0), budget - reserved)
    ceiling = min(confidence_cap, available)
    receipt = dict(version="risk-allocation-v1", executable=False,
        model_side=plan["side"], model_hard_stop=plan["hard_stop"],
        budget=float(budget), confidence=float(confidence), confidence_cap=float(confidence_cap),
        reserved_risk_at_proposed_stop=float(reserved), available_risk=float(available),
        new_risk_ceiling=float(ceiling), allocation_fraction_sum=float(sum(fractions)),
        cancellation_requests_release_risk=False, model_chase=deepcopy(plan.get("chase")),
        quantity_basis="FULL_BATCH_WORST_AUTHORIZED_CHASE_NO_RISK_RECYCLING", entries=[])
    for row, fraction in zip(entries, fractions):
        cap = ceiling * fraction
        immediate_unit = _unit_risk(plan, context, row["price"])
        anchors = [row['price']]
        possible = possible_entry_price_anchor(row, context, plan['side'])
        if possible is not None:
            anchors.append(float(possible))
        anchor = max(anchors, key=lambda value: _worst_unit_risk(plan, context, value))
        worst_price = _chase_risk_price(plan, context, anchor)
        unit = _worst_unit_risk(plan, context, anchor)
        raw_quantity = quantity_for_risk(available_risk=float(ceiling),
            allocation_fraction=float(fraction), entry_price=float(worst_price),
            hard_stop=plan["hard_stop"], side=plan["side"],
            estimated_cost_rate=context["estimated_cost_rate"], slippage_bps=context["slippage_bps"],
            quantity_step=float(step))
        # Normalize binary multiplication artifacts without ever raising quantity
        # above the independently computed exact Decimal risk/step ceiling.
        steps = min(Decimal(str(raw_quantity / float(step))).to_integral_value(),
                    (cap / unit / step).to_integral_value(rounding=ROUND_FLOOR))
        quantity = steps * step
        if quantity <= 0 or quantity < minimum or quantity * Decimal(str(row["price"])) < min_notional:
            raise EconomicProposalRejection("allocation_below_minimum", dict(
                allocation_receipt=receipt, entry_id=row["id"], quantity=float(quantity),
                minimum_quantity=float(minimum), minimum_notional=float(min_notional)))
        receipt["entries"].append(dict(id=row["id"], allocation_fraction=float(fraction),
            allocation_budget=float(cap), model_price=row["price"],
            quantity=float(quantity), risk_per_btc=float(immediate_unit),
            computed_risk=float(quantity * immediate_unit), worst_chase_price=float(worst_price),
            worst_chase_risk_per_btc=float(unit), worst_chase_risk=float(quantity * unit)))
        if possible is not None:
            receipt['quantity_basis']='FULL_BATCH_MAX_RAW_OR_POLICY_ANCHOR_CHASE_NOT_EXECUTION_PRICE'
            receipt['entries'][-1].update(possible_policy_price=float(possible),risk_anchor_price=float(anchor),
                price_policy_anchor_is_execution_price=False)
        row.pop("allocation_fraction")
        row["quantity"] = float(quantity)
    validate_allocation_prices(plan, receipt, context)
    return plan, receipt


def validate_allocation_prices(effective_plan, receipt, context):
    """Reject post-price-policy cap overruns; never resize or reallocate."""
    rows = effective_plan["entries"]
    if len(rows) != len(receipt["entries"]):
        raise ScenarioValidationError("allocation_entry_mismatch")
    if not rows:
        return
    if (effective_plan["side"] != receipt["model_side"]
            or effective_plan["hard_stop"] != receipt["model_hard_stop"]
            or effective_plan.get("chase") != receipt["model_chase"]
            or effective_plan["confidence"] != receipt["confidence"]):
        raise ScenarioValidationError("allocation_model_decision_changed")
    step, minimum, min_notional = _filters(effective_plan, context)
    for row, allocated in zip(rows, receipt["entries"]):
        qty = _number(row["quantity"], "quantity", positive=True)
        if row["id"] != allocated["id"] or qty != Decimal(str(allocated["quantity"])):
            raise ScenarioValidationError("allocation_quantity_changed")
        if qty % step or qty < minimum or qty * Decimal(str(row["price"])) < min_notional:
            raise ScenarioValidationError("allocation_instrument_filters")
        if qty * _worst_unit_risk(effective_plan, context, row["price"]) > Decimal(str(allocated["allocation_budget"])):
            raise EconomicProposalRejection("allocation_budget_exceeded", dict(allocation_receipt=receipt))
    risk = _risk(effective_plan, context, rows)
    if not risk["within_budget"]:
        raise EconomicProposalRejection("allocation_total_budget_exceeded", dict(risk=risk, allocation_receipt=receipt))
