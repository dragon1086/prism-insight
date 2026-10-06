"""Pure validation boundary for the demo LLM scenario lane (no broker I/O).

Risk is a conservative *planned* loss, not a guaranteed execution loss. All
money is quote currency and quantities are base BTC. The caller must reconcile
fills, reserve orders atomically, persist action IDs, and supply trusted context.
"""
from __future__ import annotations

import math
from decimal import Decimal
from copy import deepcopy

LEVERAGE = 10
RISK_FRACTION = 0.02


class ScenarioValidationError(ValueError):
    pass


def _number(value, name, *, minimum=0, positive=False):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ScenarioValidationError(f"{name}: finite number required")
    try:
        finite = math.isfinite(value)
    except (OverflowError, TypeError, ValueError):
        raise ScenarioValidationError(f"{name}: invalid number") from None
    if not finite or value < minimum or (positive and value == 0):
        raise ScenarioValidationError(f"{name}: invalid number")
    return float(value)


def _text(value, name):
    if not isinstance(value, str) or not value.strip() or len(value) > 2000:
        raise ScenarioValidationError(f"{name}: nonempty string required")
    return value


def _rows(value, name):
    if not isinstance(value, list) or len(value) > 20:
        raise ScenarioValidationError(f"{name}: bounded list required")
    if any(not isinstance(row, dict) for row in value):
        raise ScenarioValidationError(f"{name}: objects required")
    return value


def quantity_for_risk(*, available_risk, allocation_fraction, entry_price,
                      hard_stop, side, estimated_cost_rate, slippage_bps,
                      quantity_step=0.001):
    """Round DOWN a BTC quantity. Caller still validates the entire scenario."""
    available = _number(available_risk, "available_risk")
    fraction = _number(allocation_fraction, "allocation_fraction")
    step = _number(quantity_step, "quantity_step", positive=True)
    if fraction > 1:
        raise ScenarioValidationError("invalid allocation_fraction")
    unit = risk_snapshot(initial_equity=1, side=side, hard_stop=hard_stop,
                         positions=[], pending_entries=[],
                         entries=[{"price": entry_price, "quantity": 1}],
                         realized_loss=0, fees_paid=0, funding_paid=0,
                         estimated_cost_rate=estimated_cost_rate,
                         slippage_bps=slippage_bps)["total_risk"]
    steps = available * fraction / unit / step
    if not math.isfinite(steps):
        raise ScenarioValidationError("quantity overflow")
    quantity = math.floor(steps) * step
    if not math.isfinite(quantity):
        raise ScenarioValidationError("quantity overflow")
    return quantity


def risk_snapshot(*, initial_equity, side, hard_stop, positions, pending_entries,
                  entries, realized_loss, fees_paid, funding_paid,
                  estimated_cost_rate, slippage_bps, risk_fraction=RISK_FRACTION):
    """No netting profitable lots or realized profits against losing lots.

realized_loss must be cumulative gross losing reductions (not net PnL).
fees_paid/funding_paid are cumulative debits; credits do not replenish budget.
estimated_cost_rate conservatively reserves remaining entry+exit fees/funding.
"""
    equity = _number(initial_equity, "initial_equity", positive=True)
    fraction = _number(risk_fraction, "risk_fraction", positive=True)
    if fraction > RISK_FRACTION:
        raise ScenarioValidationError("risk fraction exceeds maximum")
    stop = _number(hard_stop, "hard_stop", positive=True)
    if side not in ("LONG", "SHORT"):
        raise ScenarioValidationError("invalid side")
    cost = _number(estimated_cost_rate, "estimated_cost_rate")
    slip = _number(slippage_bps, "slippage_bps") / 10000
    if cost > 0.1 or slip > 0.1:
        raise ScenarioValidationError("invalid cost assumptions")
    consumed = sum(_number(v, n) for n, v in (
        ("realized_loss", realized_loss), ("fees_paid", fees_paid),
        ("funding_paid", funding_paid)))
    remaining = 0.0
    proposed = 0.0
    notional = 0.0
    for name, rows in (("positions", positions), ("pending_entries", pending_entries),
                       ("entries", entries)):
        for row in _rows(rows, name):
            price = _number(row.get("price"), name + ".price", positive=True)
            qty = _number(row.get("quantity"), name + ".quantity", positive=True)
            # A profitable trailing stop is valid for already-filled lots only.
            distance = price - stop if side == "LONG" else stop - price
            if name != "positions" and distance <= 0:
                raise ScenarioValidationError("entry stop has wrong direction")
            loss = qty * (max(0.0, distance) + price * cost + stop * slip)
            remaining += loss
            notional += price * qty
            if name == "entries":
                proposed += loss
    total = consumed + remaining
    if not all(math.isfinite(x) for x in (total, notional, proposed)):
        raise ScenarioValidationError("risk overflow")
    budget = equity * fraction
    return {"budget": budget, "consumed": consumed, "remaining_risk": remaining,
            "proposed_risk": proposed, "total_risk": total,
            "available": max(0.0, budget - total), "notional": notional,
            "leverage": LEVERAGE, "within_budget": total <= budget}


def update_circuit_breaker(state, *, day, day_start_equity, daily_net_pnl,
                           completed_scenario_id=None, completed_net_pnl=None):
    """Persist returned state. A latch never resets automatically, even next day.

Call completion only after confirmed flat, all pending orders terminal, and
fees/funding settled. Replayed completion IDs cannot double count a loss.
"""
    if not isinstance(state, dict):
        raise ScenarioValidationError("invalid circuit state")
    result = deepcopy(state)
    _text(day, "day")
    equity = _number(day_start_equity, "day_start_equity", positive=True)
    pnl = _number(daily_net_pnl, "daily_net_pnl", minimum=-math.inf)
    count = result.get("consecutive_losses", 0)
    if type(count) is not int or count < 0:
        raise ScenarioValidationError("invalid consecutive_losses")
    ids = result.get("completed_ids", [])
    if not isinstance(ids, list) or any(not isinstance(x, str) for x in ids):
        raise ScenarioValidationError("invalid completed_ids")
    if completed_scenario_id is not None:
        _text(completed_scenario_id, "completed_scenario_id")
        net = _number(completed_net_pnl, "completed_net_pnl", minimum=-math.inf)
        if completed_scenario_id not in ids:
            ids.append(completed_scenario_id)
            count = count + 1 if net < 0 else 0
    if type(result.get("blocked", False)) is not bool:
        raise ScenarioValidationError("invalid circuit latch")
    reasons = result.get("reasons", [])
    if not isinstance(reasons, list) or any(x not in ("daily_loss", "three_losses") for x in reasons):
        raise ScenarioValidationError("invalid circuit reasons")
    if pnl <= -equity * 0.04 and "daily_loss" not in reasons:
        reasons.append("daily_loss")
    if count >= 3 and "three_losses" not in reasons:
        reasons.append("three_losses")
    result.update(day=day, consecutive_losses=count, completed_ids=ids,
                  blocked=bool(result.get("blocked", False) or reasons), reasons=reasons)
    return result


def validate_scenario(payload, context):
    """Validate incremental orders against trusted, reconciled runtime context.

Required context: now,input_id,input_captured_at,max_input_age_seconds,
scenario_id (None if flat),revision,seen_action_ids,initial_equity,positions,
pending_entries,previous_hard_stop,realized_loss,fees_paid,funding_paid,
estimated_cost_rate,slippage_bps,new_risk_blocked. Active contexts also supply
side and mark_price. ADJUST replaces protection, not existing entry orders;
the caller must first reconcile cancellations before dropping pending risk.
"""
    if not isinstance(payload, dict) or not isinstance(context, dict):
        raise ScenarioValidationError("objects required")
    allowed = {"schema_version", "scenario_id", "revision", "input_id", "action_id",
               "action", "side", "confidence", "expires_at", "hard_stop", "entries",
               "take_profits", "partial_stops", "chase", "rationale", "leverage",
               "cancel_entry_ids"}
    if set(payload) - allowed:
        raise ScenarioValidationError("unknown scenario fields")
    p = deepcopy(payload)
    try:
        if type(p["schema_version"]) is not int or p["schema_version"] != 1:
            raise ScenarioValidationError("unsupported schema")
        if type(context["revision"]) is not int or context["revision"] < 0 or type(p["revision"]) is not int or p["revision"] != context["revision"] + 1:
            raise ScenarioValidationError("stale revision")
        for name in ("scenario_id", "input_id", "action_id", "rationale"):
            _text(p[name], name)
        if p["input_id"] != context["input_id"]:
            raise ScenarioValidationError("stale input id")
        if p["action_id"] in context["seen_action_ids"]:
            raise ScenarioValidationError("duplicate action")
        now = _number(context["now"], "now")
        captured = _number(context["input_captured_at"], "input_captured_at")
        age = _number(context["max_input_age_seconds"], "max_input_age_seconds", positive=True)
        expires = _number(p["expires_at"], "expires_at", positive=True)
        if not 0 <= now - captured <= age or not now < expires <= now + 3600:
            raise ScenarioValidationError("stale input or invalid expiry")
        action = p["action"]
        if action not in ("WAIT", "OPEN", "ADJUST", "EXIT"):
            raise ScenarioValidationError("invalid action")
        active = context["scenario_id"] is not None
        if active and p["scenario_id"] != context["scenario_id"]:
            raise ScenarioValidationError("different active scenario")
        if (action == "OPEN" and active) or (action in ("ADJUST", "EXIT") and not active):
            raise ScenarioValidationError("action incompatible with lifecycle")
        confidence = _number(p["confidence"], "confidence")
        if confidence > 1 or type(p.get("leverage", LEVERAGE)) not in (int, float) or p.get("leverage", LEVERAGE) != LEVERAGE:
            raise ScenarioValidationError("invalid confidence or leverage")
        if "hard_stop" in p:
            _number(p["hard_stop"], "hard_stop", positive=True)
        entries = _rows(p.get("entries", []), "entries")
        tps = _rows(p.get("take_profits", []), "take_profits")
        stops = _rows(p.get("partial_stops", []), "partial_stops")
        cancellations = p.get("cancel_entry_ids", [])
        if not isinstance(cancellations, list) or any(not isinstance(x, str) for x in cancellations) or len(set(cancellations)) != len(cancellations):
            raise ScenarioValidationError("invalid cancellation ids")
        pending_rows = _rows(context["pending_entries"], "pending_entries")
        pending_ids = {_text(row.get("id"), "pending.id") for row in pending_rows}
        if len(pending_ids) != len(pending_rows):
            raise ScenarioValidationError("duplicate pending id")
        if not set(cancellations) <= pending_ids:
            raise ScenarioValidationError("unknown cancellation id")
        if action in ("WAIT", "EXIT"):
            if {"hard_stop", "side", "chase"} & p.keys():
                raise ScenarioValidationError("WAIT/EXIT cannot change protection or side")
            if entries or tps or stops:
                raise ScenarioValidationError("WAIT/EXIT cannot add orders")
            return p
        side = p["side"]
        if side not in ("LONG", "SHORT") or (active and side != context["side"]):
            raise ScenarioValidationError("invalid or opposite side")
        stop = _number(p["hard_stop"], "hard_stop", positive=True)
        positions = _rows(context["positions"], "positions")
        pending = _rows(context["pending_entries"], "pending_entries")
        if not active and (positions or pending):
            raise ScenarioValidationError("unreconciled existing exposure")
        if action == "OPEN" and not entries:
            raise ScenarioValidationError("OPEN needs entries")
        if type(context["new_risk_blocked"]) is not bool:
            raise ScenarioValidationError("invalid risk latch")
        if entries and context["new_risk_blocked"]:
            raise ScenarioValidationError("new risk blocked")
        if active:
            old = _number(context["previous_hard_stop"], "previous_hard_stop", positive=True)
            if (side == "LONG" and stop < old) or (side == "SHORT" and stop > old):
                raise ScenarioValidationError("stop widening forbidden")
            mark = _number(context["mark_price"], "mark_price", positive=True)
            if (side == "LONG" and stop >= mark) or (side == "SHORT" and stop <= mark):
                raise ScenarioValidationError("stop crossed market; use EXIT")
        identifiers = set()
        # Cancellation intent never releases risk before exchange confirmation.
        for name, rows in (("entries", entries), ("take_profits", tps), ("partial_stops", stops)):
            fraction = 0.0
            for row in rows:
                expected = {"id", "price", "quantity" if name == "entries" else "fraction"}
                if name == "entries" and "trigger_price" in row:
                    expected.add("trigger_price")
                    if context.get("conditional_entry_version") != 1:
                        raise ScenarioValidationError("conditional entries not enabled")
                    trigger = _number(row["trigger_price"], "trigger_price", positive=True)
                    tick = _number(context.get("price_tick"), "price_tick", positive=True)
                    if Decimal(str(trigger)) % Decimal(str(tick)):
                        raise ScenarioValidationError("trigger price off tick")
                    mark = _number(context.get("mark_price"), "mark_price", positive=True)
                    limit = _number(row.get("price"), "order.price", positive=True)
                    if ((side == "LONG" and not stop < mark < trigger <= limit) or
                            (side == "SHORT" and not stop > mark > trigger >= limit)):
                        raise ScenarioValidationError("conditional entry price direction")
                    if expires > (math.floor(captured / 300) + 1) * 300:
                        raise ScenarioValidationError("conditional entry expiry exceeds next decision")
                if set(row) != expected:
                    raise ScenarioValidationError("invalid order fields")
                ident = _text(row["id"], "order.id")
                if ident in identifiers or ident in pending_ids:
                    raise ScenarioValidationError("duplicate order id")
                identifiers.add(ident)
                price = _number(row["price"], "order.price", positive=True)
                if name == "entries":
                    _number(row["quantity"], "quantity", positive=True)
                else:
                    fraction += _number(row["fraction"], "fraction", positive=True)
                    reference = context["mark_price"] if active else sum(r["price"] * r["quantity"] for r in entries) / sum(r["quantity"] for r in entries)
                    is_above = price > reference
                    if (name == "take_profits" and (price == reference or is_above != (side == "LONG"))) or (name == "partial_stops" and (price == reference or is_above == (side == "LONG"))):
                        raise ScenarioValidationError("exit price wrong direction")
                    if name == "partial_stops" and ((side == "LONG" and price <= stop) or (side == "SHORT" and price >= stop)):
                        raise ScenarioValidationError("partial stop outside hard stop")
            if fraction > 1.0:
                raise ScenarioValidationError("exit fractions exceed position")
        chase = p["chase"]
        if not isinstance(chase, dict) or set(chase) != {"max_bps", "max_reprices"}:
            raise ScenarioValidationError("invalid chase")
        if _number(chase["max_bps"], "max_bps") > 50 or type(chase["max_reprices"]) is not int or not 0 <= chase["max_reprices"] <= 3:
            raise ScenarioValidationError("unbounded chase")
        if any("trigger_price" in row for row in entries) and (chase["max_bps"] or chase["max_reprices"]):
            raise ScenarioValidationError("conditional entry chase forbidden")
        risk = risk_snapshot(initial_equity=context["initial_equity"], side=side,
                             risk_fraction=context.get("scenario_risk_fraction", RISK_FRACTION),
                             hard_stop=stop, positions=positions, pending_entries=pending,
                             entries=entries, **{k: context[k] for k in (
                                 "realized_loss", "fees_paid", "funding_paid",
                                 "estimated_cost_rate", "slippage_bps")})
        # Never block pure protection improvement when a prior fill exceeded budget.
        if entries and (not risk["within_budget"] or risk["proposed_risk"] > risk["budget"] * confidence):
            raise ScenarioValidationError("risk budget exceeded")
        p["leverage"] = LEVERAGE
        p["risk"] = risk
        return p
    except (KeyError, TypeError, ZeroDivisionError, OverflowError):
        raise ScenarioValidationError("incomplete or malformed scenario/context") from None
