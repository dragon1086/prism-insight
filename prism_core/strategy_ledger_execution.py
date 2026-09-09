"""Pure, no-order account projection; strategy capital is never account cash."""
from decimal import ROUND_FLOOR, Decimal, InvalidOperation


def _amount(value):
    if isinstance(value, bool):
        raise TypeError("boolean is not an amount")
    try:
        result = Decimal(str(value))
    except (InvalidOperation, TypeError) as error:
        raise ValueError("invalid amount") from error
    if not result.is_finite() or result < 0:
        raise ValueError("amount must be finite and nonnegative")
    return result


def project_account_target(*, execution_profile_ref, account_unit_budget,
                           target_pct, limit_price, confirmed_buy_notional,
                           reserved_buy_notional, unknown_execution=False,
                           previously_submitted_target_pct=None):
    """Plan whole shares under a cumulative notional cap, never submit them.

    Callers must reconcile executions first. Unknown reservations block action.
    A target already submitted cannot create another add merely because price
    declined; advancing a target requires a new explicitly higher target.
    """
    result = {"status": "BLOCKED", "quantity": 0, "no_order": True}
    if not execution_profile_ref or execution_profile_ref == "[REDACTED]":
        return dict(result, reason="MISSING_EXECUTION_PROFILE")
    if unknown_execution or reserved_buy_notional is None or confirmed_buy_notional is None:
        return dict(result, reason="UNKNOWN_EXECUTION_RESERVATION")
    unit, target, price, confirmed, reserved = map(_amount, (
        account_unit_budget, target_pct, limit_price,
        confirmed_buy_notional, reserved_buy_notional))
    if target > 100 or price == 0:
        raise ValueError("target must be <=100 and limit price positive")
    cap = unit * target / 100
    remaining = max(Decimal(0), cap - confirmed - reserved)
    result.update(target_budget=str(cap), confirmed_buy_notional=str(confirmed),
                  reserved_buy_notional=str(reserved), remaining_budget=str(remaining))
    if previously_submitted_target_pct is not None and target <= _amount(previously_submitted_target_pct):
        return dict(result, reason="TARGET_ALREADY_SUBMITTED")
    quantity = int((remaining / price).to_integral_value(rounding=ROUND_FLOOR))
    return dict(result, status="PLANNED" if quantity else "BLOCKED",
                reason="NO_ORDER_PROJECTION" if quantity else "BELOW_ONE_SHARE",
                quantity=quantity, projected_notional=str(quantity * price),
                unused_budget=str(remaining - quantity * price))
