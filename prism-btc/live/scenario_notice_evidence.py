"""Read-only notice enrichment from an already reconciled observation.

This module does not query an exchange or authorize trading actions.
"""
import math


def number(value, *, positive=False):
    if isinstance(value, bool):
        return None
    try:
        value = float(value)
    except (ValueError, TypeError, OverflowError):
        return None
    return value if math.isfinite(value) and value >= 0 and (not positive or value > 0) else None


def position_snapshot(active, children, observed, protected, pending, accounting):
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
        scenario_initial_equity=active["initial_equity"], scenario_budget=active["initial_equity"]*.02)
    if accounting and accounting.get("status") == "confirmed" and stop:
        from core.llm_scenario import risk_snapshot, ScenarioValidationError
        try:
            risk = risk_snapshot(initial_equity=active["initial_equity"], side=active["side"], hard_stop=stop,
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
