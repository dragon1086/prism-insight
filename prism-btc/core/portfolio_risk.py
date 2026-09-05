"""Offline M1.1 arithmetic only; NOT a runtime authorization or reservation.

Callers must supply one coherent main+swing snapshot, confirmed protection and
filled quantities, and only the *remaining* entry quantity of pending orders.
Atomic reservation, a shared mutex, reconciliation and restart recovery remain
prerequisites to runtime use. No quantity is clipped or order submitted here.
"""
from __future__ import annotations

from dataclasses import dataclass, fields
from math import fsum, isfinite


@dataclass(frozen=True)
class PortfolioRiskPolicy:
    main_heat_fraction: float = 0.05
    swing_heat_fraction: float = 0.015
    # Proposed sum of lane budgets, NOT a previously validated LIVE gate.
    combined_heat_fraction: float = 0.065
    gross_multiple: float = 8.0
    swing_gross_multiple: float = 5.0


@dataclass(frozen=True)
class ActualPosition:
    lane: str
    side: str
    qty: float
    entry: float
    mark: float
    confirmed_stop: float
    stop_confirmed: bool = False


@dataclass(frozen=True)
class PendingEntry:
    lane: str
    side: str
    remaining_qty: float
    # Upper admissible fill price for gross; short heat needs a lower bound too.
    price_bound: float
    stop: float
    min_fill_price: float | None = None


@dataclass(frozen=True)
class ProposedEntry:
    lane: str
    side: str
    qty: float
    price_bound: float
    stop: float
    min_fill_price: float | None = None


@dataclass(frozen=True)
class PortfolioRiskMetrics:
    gross: float
    swing_gross: float
    entry_heat: float
    main_entry_heat: float
    swing_entry_heat: float
    # Actual positions only; informational, never used as entry-loss heat.
    mark_to_stop_giveback: float


@dataclass(frozen=True)
class PortfolioRiskDecision:
    allowed: bool
    reasons: tuple[str, ...]
    metrics: PortfolioRiskMetrics | None = None


def _positive(value: object, name: str, *, allow_zero: bool = False) -> None:
    if (isinstance(value, bool) or not isinstance(value, (int, float))
            or not isfinite(value) or value < 0
            or (not allow_zero and value == 0)):
        raise ValueError(name)


def _direction(lane: str, side: str) -> int:
    if lane not in ("main", "swing") or side not in ("long", "short"):
        raise ValueError("lane_or_side")
    return 1 if side == "long" else -1


def evaluate_portfolio_entry(
    *,
    capital: float,
    positions: tuple[ActualPosition, ...],
    pending_entries: tuple[PendingEntry, ...],
    proposed: ProposedEntry,
    policy: PortfolioRiskPolicy = PortfolioRiskPolicy(),
) -> PortfolioRiskDecision:
    """Reject malformed snapshots or any exceeded cap; equality is admissible.

    Gross is sum(abs(qty * mark)) for actual positions plus remaining pending
    and proposed qty * price_bound. Opposite sides never net. Entry-loss heat is
    max(0, sign * (entry - stop)) * qty. Tighter profitable actual stops therefore
    have zero entry heat, without cancelling other positions' loss exposure.

    Pending/proposed price_bound is an upper execution bound for gross. Shorts
    also require min_fill_price, a lower execution bound for conservative heat.
    Callers must establish these bounds and recheck a fresh coherent snapshot
    before runtime reservation; this helper does not establish those guarantees.
    """
    try:
        _positive(capital, "capital")
        if not isinstance(policy, PortfolioRiskPolicy):
            raise ValueError("policy")
        for field in fields(policy):
            _positive(getattr(policy, field.name), field.name)
        if not isinstance(proposed, ProposedEntry):
            raise ValueError("proposed")
        if not isinstance(positions, (tuple, list)) or not isinstance(
            pending_entries, (tuple, list)
        ):
            raise ValueError("snapshot")

        gross = {"main": [], "swing": []}
        heat = {"main": [], "swing": []}
        giveback = []
        for position in positions:
            if not isinstance(position, ActualPosition):
                raise ValueError("position")
            sign = _direction(position.lane, position.side)
            for name in ("qty", "entry", "mark", "confirmed_stop"):
                _positive(getattr(position, name), name)
            if position.stop_confirmed is not True:
                raise ValueError("unconfirmed_stop")
            gross[position.lane].append(position.qty * position.mark)
            heat[position.lane].append(
                max(0, sign * (position.entry - position.confirmed_stop)) * position.qty
            )
            giveback.append(
                max(0, sign * (position.mark - position.confirmed_stop)) * position.qty
            )

        if not all(isinstance(order, PendingEntry) for order in pending_entries):
            raise ValueError("pending_entry")
        for order in (*pending_entries, proposed):
            sign = _direction(order.lane, order.side)
            qty = order.remaining_qty if isinstance(order, PendingEntry) else order.qty
            _positive(qty, "entry_qty", allow_zero=isinstance(order, PendingEntry))
            _positive(order.price_bound, "price_bound")
            _positive(order.stop, "stop")
            if order.min_fill_price is not None:
                _positive(order.min_fill_price, "min_fill_price")
                if order.min_fill_price > order.price_bound:
                    raise ValueError("price_bound_order")
            heat_price = order.price_bound
            if sign == -1:
                _positive(order.min_fill_price, "short_min_fill_price")
                heat_price = order.min_fill_price
            if sign * (heat_price - order.stop) < 0:
                raise ValueError("entry_stop_direction")
            gross[order.lane].append(qty * order.price_bound)
            heat[order.lane].append(qty * max(0, sign * (heat_price - order.stop)))

        metrics = PortfolioRiskMetrics(
            gross=fsum(gross["main"] + gross["swing"]),
            swing_gross=fsum(gross["swing"]),
            entry_heat=fsum(heat["main"] + heat["swing"]),
            main_entry_heat=fsum(heat["main"]),
            swing_entry_heat=fsum(heat["swing"]),
            mark_to_stop_giveback=fsum(giveback),
        )
        limits = (
            ("gross_cap", metrics.gross, capital * policy.gross_multiple),
            ("swing_gross_cap", metrics.swing_gross, capital * policy.swing_gross_multiple),
            ("main_heat_cap", metrics.main_entry_heat, capital * policy.main_heat_fraction),
            ("swing_heat_cap", metrics.swing_entry_heat, capital * policy.swing_heat_fraction),
            ("combined_heat_cap", metrics.entry_heat, capital * policy.combined_heat_fraction),
        )
        if not all(isfinite(getattr(metrics, field.name)) for field in fields(metrics)):
            raise ValueError("nonfinite_metrics")
        if not all(isfinite(limit) for _, _, limit in limits):
            raise ValueError("nonfinite_limits")
    except (ValueError, TypeError, OverflowError) as exc:
        return PortfolioRiskDecision(False, (f"invalid_input:{exc}",))

    reasons = tuple(reason for reason, value, limit in limits if value > limit)
    return PortfolioRiskDecision(not reasons, reasons, metrics)
