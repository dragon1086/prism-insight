"""Offline ordinal-quality allocation experiment; never runtime authorization.

Fractions refer to planned initial margin, not loss probability. Existing stops
are immutable inputs. Heat excludes fees, gaps and slippage beyond input bounds;
neither heat nor this offline snapshot guarantees maximum realized loss.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_FLOOR
from math import fsum, isfinite
from types import MappingProxyType
from typing import Mapping

from core.portfolio_risk import (
    ActualPosition, PendingEntry, PortfolioRiskPolicy, ProposedEntry,
    evaluate_portfolio_entry,
)

LEVERAGE = MappingProxyType({"main": 10, "swing": 5})
POLICIES = MappingProxyType({
    "fixed_low": (0.10, 0.10, 0.10),
    "uniform": (0.20, 0.20, 0.20),
    "fixed_high": (0.30, 0.30, 0.30),
    "graded": (0.10, 0.20, 0.30),
    "reversed": (0.30, 0.20, 0.10),
})
LOT = Decimal("0.001")


def grade_agreement(side: str, trends: Mapping[str, str]) -> str:
    """Count completed higher-frame direction agreement, not win probability."""
    if side not in ("long", "short") or not isinstance(trends, Mapping):
        return "unknown"
    values = tuple(trends.get(frame) for frame in ("1h", "4h", "1d"))
    if any(value not in ("up", "down", "flat") for value in values):
        return "unknown"
    count = values.count("up" if side == "long" else "down")
    return "high" if count == 3 else "medium" if count == 2 else "low"


@dataclass(frozen=True)
class AllocationRequest:
    lane: str
    side: str
    equity: float
    price: float
    stop: float
    tranche_index: int
    grade: str
    policy: str
    positions: tuple[ActualPosition, ...]
    pending: tuple[PendingEntry, ...]
    legacy_qty: float


@dataclass(frozen=True)
class AllocationDecision:
    qty: float = 0.0
    target_margin_fraction: float = 0.0
    target_notional: float = 0.0
    desired_qty: float = 0.0
    actual_margin_fraction: float = 0.0
    entry_heat: float = 0.0
    reasons: tuple[str, ...] = ()


def _positive(value: object) -> bool:
    try:
        return (not isinstance(value, bool) and isinstance(value, (float, int))
                and isfinite(value) and value > 0)
    except OverflowError:
        return False


def allocate(req: AllocationRequest) -> AllocationDecision:
    """Size a new leg under target, legacy intent and aggregate admission caps.

Main campaign allocation is split 40/30/30. A higher later grade never retrofits
an earlier smaller leg: both the current leg and cumulative ceiling must fit.
Actual positions use marks, pending entries use their upper fill-price bound;
short pending heat additionally needs a lower fill-price bound. No side netting.
Price is an explicit fixed fill-price assumption for the candidate, not a claim
about unbounded market execution. Use a fresh bounded snapshot before live use.
"""
    if not isinstance(req, AllocationRequest):
        return AllocationDecision(reasons=("invalid_request",))
    if (not isinstance(req.lane, str) or req.lane not in LEVERAGE
            or req.side not in ("long", "short")):
        return AllocationDecision(reasons=("invalid_lane_or_side",))
    if (not all(_positive(v) for v in (req.equity, req.price, req.stop, req.legacy_qty))
            or isinstance(req.tranche_index, bool)
            or not isinstance(req.tranche_index, int)
            or req.tranche_index not in ((0, 1, 2) if req.lane == "main" else (0,))
            or not isinstance(req.positions, tuple) or not isinstance(req.pending, tuple)):
        return AllocationDecision(reasons=("invalid_input",))
    if req.grade not in ("low", "medium", "high"):
        return AllocationDecision(reasons=("unknown_grade",))
    if not isinstance(req.policy, str) or req.policy not in POLICIES:
        return AllocationDecision(reasons=("unknown_policy",))
    loss_per_qty = (req.price - req.stop) * (1 if req.side == "long" else -1)
    if loss_per_qty <= 0:
        return AllocationDecision(reasons=("invalid_stop_direction",))
    policy = PortfolioRiskPolicy()

    def evaluate(qty: float):
        return evaluate_portfolio_entry(
            capital=req.equity, positions=req.positions, pending_entries=req.pending,
            proposed=ProposedEntry(req.lane, req.side, qty, req.price, req.stop,
                                   req.price if req.side == "short" else None),
            policy=policy,
        )

    # Validate every input through the existing risk engine before arithmetic.
    validation = evaluate(1.0)
    if validation.metrics is None:
        return AllocationDecision(reasons=validation.reasons)
    leverage = LEVERAGE[req.lane]
    fraction = POLICIES[req.policy][("low", "medium", "high").index(req.grade)]
    target = req.equity * fraction * leverage
    lane_gross = {lane: fsum(
        [p.qty * p.mark for p in req.positions if p.lane == lane]
        + [p.remaining_qty * p.price_bound for p in req.pending if p.lane == lane]
    ) for lane in LEVERAGE}
    lane_heat = {lane: fsum(
        [p.qty * max(0.0, (p.entry - p.confirmed_stop)
                     * (1 if p.side == "long" else -1))
         for p in req.positions if p.lane == lane]
        + [p.remaining_qty * max(0.0, (p.price_bound - p.stop) if p.side == "long"
                                 else (p.stop - p.min_fill_price))
           for p in req.pending if p.lane == lane]
    ) for lane in LEVERAGE}
    leg = (0.4, 0.3, 0.3)[req.tranche_index] if req.lane == "main" else 1.0
    cumulative = (0.4, 0.7, 1.0)[req.tranche_index] if req.lane == "main" else 1.0
    desired = max(0.0, min(target * leg, target * cumulative - lane_gross[req.lane])) / req.price
    margin_used = fsum(lane_gross[lane] / LEVERAGE[lane] for lane in LEVERAGE)
    caps = {
        "target": desired,
        "legacy_qty_cap": req.legacy_qty,
        "lane_heat_cap": (req.equity * (policy.main_heat_fraction if req.lane == "main"
                                       else policy.swing_heat_fraction)
                          - lane_heat[req.lane]) / loss_per_qty,
        "combined_heat_cap": (req.equity * policy.combined_heat_fraction
                              - fsum(lane_heat.values())) / loss_per_qty,
        "gross_cap": (req.equity * policy.gross_multiple
                      - fsum(lane_gross.values())) / req.price,
        "margin_cap": (req.equity - margin_used) * leverage / req.price,
    }
    if req.lane == "swing":
        caps["swing_gross_cap"] = (req.equity * policy.swing_gross_multiple
                                   - lane_gross["swing"]) / req.price
    if not all(isfinite(v) for v in (target, desired, *caps.values())):
        return AllocationDecision(reasons=("nonfinite_calculation",))
    bound = max(0.0, min(caps.values()))
    qty = float((Decimal(str(bound)) / LOT).to_integral_value(rounding=ROUND_FLOOR) * LOT)
    reasons = tuple(name for name, cap in caps.items() if name != "target" and cap < desired)
    # Float arithmetic at exact boundaries can round upwards; subtract one lot
    # rather than adding a tolerance that could authorize an exceeded budget.
    if qty > 0:
        check = evaluate(qty)
        if not check.allowed or margin_used + qty * req.price / leverage > req.equity:
            qty = max(0.0, float(Decimal(str(qty)) - LOT))
        if qty > 0:
            check = evaluate(qty)
            if not check.allowed or margin_used + qty * req.price / leverage > req.equity:
                return AllocationDecision(target_margin_fraction=fraction,
                                          target_notional=target, desired_qty=desired,
                                          reasons=check.reasons or ("margin_cap",))
    if qty == 0:
        reasons = reasons + (("target_exhausted",) if desired == 0 else ("below_lot",))
    return AllocationDecision(qty, fraction, target, desired,
                              qty * req.price / leverage / req.equity,
                              qty * loss_per_qty, reasons)
