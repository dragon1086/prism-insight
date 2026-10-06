"""Read-only notice enrichment from an already reconciled observation.

This module does not query an exchange or authorize trading actions.
"""
import json
import logging
import math
import sqlite3


def number(value, *, positive=False):
    if isinstance(value, bool):
        return None
    try:
        value = float(value)
    except (ValueError, TypeError, OverflowError):
        return None
    return value if math.isfinite(value) and value >= 0 and (not positive or value > 0) else None


def entry_plan_reference(conn, active, child):
    """Exact entry intent only, never a later plan or proof of live TP orders."""
    if not child.get("intent_id") or not child.get("local_id"):
        return {}
    try:
        row = conn.execute("SELECT payload FROM llm_scenario_intents WHERE id=? AND scenario_id=?",
                           (child["intent_id"], active["scenario_id"])).fetchone()
        plan = json.loads(row[0]) if row else None
        if (not isinstance(plan, dict) or plan.get("action") not in {"OPEN", "ADJUST"}
                or plan.get("action_id") != child["intent_id"]
                or plan.get("scenario_id") != active["scenario_id"] or plan.get("side") != active["side"]
                or not isinstance(plan.get("entries"), list)
                or not any(isinstance(e, dict) and e.get("id") == child["local_id"] for e in plan["entries"])):
            return {}
        targets = plan.get("take_profits")
        if not isinstance(targets, list) or len(targets) > 20:
            return {}
        normalized = []
        for target in targets:
            if not isinstance(target, dict):
                return {}
            price = number(target.get("price"), positive=True)
            fraction = number(target.get("fraction"), positive=True)
            if price is None or fraction is None or fraction > 1:
                return {}
            normalized.append(dict(price=price, fraction=fraction))
        total = sum(t["fraction"] for t in normalized)
        if total > 1 and not math.isclose(total, 1, rel_tol=1e-12, abs_tol=0):
            return {}
        return dict(take_profits=normalized, take_profits_scope="entry_intent_plan",
                    plan_action_id=child["intent_id"])
    except (sqlite3.Error, ValueError, TypeError):
        logging.getLogger(__name__).warning("NOTICE entry_plan_reference_unavailable")
        return {}  # Optional detail must never drop a confirmed fill.


def position_snapshot(active, children, observed, protected, pending, accounting, *, scenario_risk_fraction=.02):
    from core.llm_scenario import risk_snapshot, ScenarioValidationError
    try:
        budget = risk_snapshot(initial_equity=active["initial_equity"], side=active["side"],
            hard_stop=active["hard_stop"], positions=[], pending_entries=[], entries=[],
            realized_loss=0, fees_paid=0, funding_paid=0, estimated_cost_rate=0,
            slippage_bps=0, risk_fraction=scenario_risk_fraction)["budget"]
    except (ScenarioValidationError, KeyError, TypeError, ValueError):
        return None
    if not protected or pending or observed.get("legacy_fenced"):
        return None
    if any(not c.get("evidence") or c.get("status") not in {"LIVE", "TERMINAL"} for c in children):
        return None
    position = observed["position"]
    quantity = number(position.get("size"))
    average = number(position.get("avgPrice"), positive=True) if quantity else None
    stop = number(position.get("stopLoss"), positive=True) if quantity else None
    if quantity is None or (quantity and (average is None or stop is None)):
        return None
    if quantity and position.get("side") != ("Buy" if active["side"] == "LONG" else "Sell"):
        return None
    targets = {"tp": [], "partial_sl": []}
    entries = []
    for child in children:
        if child["status"] != "LIVE" or child["kind"] not in {"entry", "tp", "partial_sl"}:
            continue
        order = child["evidence"]["order"]
        price = number(order.get("triggerPrice") if child["kind"] == "partial_sl" else order.get("price"), positive=True)
        leaves = number(order.get("leavesQty"))
        if leaves is None or (leaves and price is None):
            return None
        if leaves:
            (entries if child["kind"] == "entry" else targets[child["kind"]]).append(dict(price=price, quantity=leaves))
    for kind, rows in targets.items():
        descending=(kind == "tp" and active["side"] == "SHORT") or (kind == "partial_sl" and active["side"] == "LONG")
        rows.sort(key=lambda row: (-row["price"] if descending else row["price"], row["quantity"]))
    snapshot = dict(verified=True, timestamp=observed["captured_at"], side=active["side"],
        quantity=quantity, average_entry_price=average, hard_stop=stop,
        exchange_leverage=number(position.get("leverage"), positive=True),
        take_profits=targets["tp"], partial_stops=targets["partial_sl"],
        account_snapshot=dict(same_event=True, same_account=True, timestamp=observed["captured_at"],
            position_margin=number(position.get("positionIM")), equity=observed["equity"], margin_mode="REGULAR_MARGIN"),
        scenario_initial_equity=active["initial_equity"], scenario_budget=budget)
    if accounting and accounting.get("status") == "confirmed" and accounting.get("accounting_complete") is True:
        # Reuse the reconciler's totals, including recorded costs on open lots.
        # This is not a fee allocation or a per-exit settlement calculation.
        keys = ("gross_pnl", "fees", "funding_net", "net_pnl")
        values = [accounting.get(key) for key in keys]
        if all(isinstance(value, (int, float)) and not isinstance(value, bool)
               and math.isfinite(value) for value in values):
            gross, fees, funding, net = values
            if math.isclose(gross - fees + funding, net, rel_tol=0, abs_tol=1e-8):
                snapshot.update(scenario_realized_net_pnl=net, scenario_accounting_confirmed=True)
    if accounting and accounting.get("status") == "confirmed" and stop:
        try:
            risk = risk_snapshot(initial_equity=active["initial_equity"], side=active["side"], hard_stop=stop,
                risk_fraction=scenario_risk_fraction,
                positions=accounting["positions"], pending_entries=entries, entries=[],
                realized_loss=accounting["realized_loss"], fees_paid=accounting["fees_paid"],
                funding_paid=accounting["funding_paid"], estimated_cost_rate=.002, slippage_bps=20)
            snapshot.update(scenario_risk=risk["total_risk"], scenario_risk_includes_pending=True)
        except (ScenarioValidationError, KeyError, TypeError, ValueError):
            pass  # Missing optional accounting never becomes zero cost.
    return snapshot


def economic_fingerprint(snapshot):
    return {key: snapshot[key] for key in (
        "side", "quantity", "average_entry_price", "hard_stop", "take_profits", "partial_stops")}
