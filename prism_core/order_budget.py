"""Whole-share order allocation, not evidence of an actual broker fill."""
import math
from decimal import Decimal, ROUND_FLOOR


def resolve_order_budget(provided, default):
    """Only an omitted budget may use the default; invalid budgets fail closed."""
    value = default if provided is None else provided
    try:
        if isinstance(value, bool) or not math.isfinite(value) or value <= 0:
            return 0
    except (TypeError, ValueError, OverflowError):
        return 0
    return value


def whole_share_quantity(budget, price):
    """Round down at the sizing price without a one-share minimum or tolerance."""
    if not resolve_order_budget(budget, 0) or not resolve_order_budget(price, 0):
        return 0
    return int((Decimal(str(budget)) / Decimal(str(price))).to_integral_value(rounding=ROUND_FLOOR))


def order_budget_evidence(budget, price, quantity=None):
    """Describe proposed allocation; market fills and fees can differ."""
    quantity = whole_share_quantity(budget, price) if quantity is None else quantity
    notional = Decimal(str(price)) * quantity
    return {
        "order_budget": budget,
        "sizing_price": price,
        "proposed_order_notional": float(notional),
        "unallocated_order_budget": float(Decimal(str(budget)) - notional),
        "budget_evidence_basis": "proposed_order_not_fill",
    }
