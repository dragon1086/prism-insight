"""M4.1 OFFLINE exit-policy contract; no adapter, signal, sizing or live mode.

All lots are integer exchange quantity steps, not BTC. Freeze a plan only after
the entry's FINAL confirmed fill quantity is known; additions need a new,
reconciled plan. Odd/unfinished entry fills are unsupported planning states,
NOT permission to defer immediate native stop protection while awaiting fills.
Input prices/extremes must be available at the decision time,
not future candle highs/lows. Cumulative executions must come from a deduplicated
confirmed fill ledger, never from price touches or order acknowledgements.

Proposals describe desired state, NOT orders, fills, profit or SL guarantees.
An eventual adapter must reconcile existing orders, handle quantity/price filters,
serialize changes and preserve protection. Repeating a proposal is not permission
to resubmit it. M0/M1/M2 and strategy-promotion gates remain unresolved.
"""
from __future__ import annotations

from dataclasses import dataclass
from math import isfinite


def _positive(value: object, name: str, *, zero: bool = False) -> None:
    if (isinstance(value, bool) or not isinstance(value, (int, float))
            or not isfinite(value) or value < 0 or (value == 0 and not zero)):
        raise ValueError(name)


def _lots(value: object, name: str, *, zero: bool = False) -> None:
    if type(value) is not int or value < (0 if zero else 1):
        raise ValueError(name)


def _flag(value: object, name: str) -> None:
    if type(value) is not bool:
        raise ValueError(name)


@dataclass(frozen=True)
class Target:
    price: float
    lots: int

    def __post_init__(self) -> None:
        _positive(self.price, "target_price")
        _lots(self.lots, "target_lots")


@dataclass(frozen=True)
class ScalpExitPlan:
    side: str
    entry_price: float
    initial_lots: int
    min_lots: int
    tp1_price: float
    further_targets: tuple[Target, ...] = ()

    def __post_init__(self) -> None:
        if self.side not in ("long", "short"):
            raise ValueError("side")
        _positive(self.entry_price, "entry_price")
        _positive(self.tp1_price, "tp1_price")
        _lots(self.initial_lots, "initial_lots")
        _lots(self.min_lots, "min_lots")
        if self.initial_lots % 2:
            raise ValueError("exact_half_requires_even_initial_lots")
        if type(self.further_targets) is not tuple:
            raise ValueError("immutable_target_tuple_required")
        previous = self.entry_price
        for target in self.targets:
            if not isinstance(target, Target):
                raise ValueError("target_type")
            if target.lots < self.min_lots:
                raise ValueError("target_dust")
            if self.direction * (target.price - previous) <= 0:
                raise ValueError("targets_must_be_ordered_in_profit_direction")
            previous = target.price
        if self.runner_lots < 0:
            raise ValueError("target_quota_exceeds_entry")
        if 0 < self.runner_lots < self.min_lots:
            raise ValueError("runner_dust")

    @property
    def direction(self) -> int:
        return 1 if self.side == "long" else -1

    @property
    def targets(self) -> tuple[Target, ...]:
        return (Target(self.tp1_price, self.initial_lots // 2),) + self.further_targets

    @property
    def runner_lots(self) -> int:
        return self.initial_lots - sum(target.lots for target in self.targets)


@dataclass(frozen=True)
class ScalpTrailPolicy:
    """Caller-supplied absolute price distances; no optimized/default thresholds."""
    early_distance: float
    runner_distance: float
    max_hold_seconds: float

    def __post_init__(self) -> None:
        for name in ("early_distance", "runner_distance", "max_hold_seconds"):
            _positive(getattr(self, name), name)


@dataclass(frozen=True)
class ScalpSnapshot:
    remaining_lots: int
    tp_filled_lots: tuple[int, ...]
    external_reduced_lots: int
    current_stop: float
    mark_price: float
    favorable_extreme: float
    elapsed_seconds: float
    ledger_confirmed: bool
    protection_confirmed: bool


@dataclass(frozen=True)
class ScalpExitProposal:
    """Keep existing protection even for close/reconcile intents.

    desired_stop is not exchange-confirmed until a future adapter verifies it.
    close has no execution price: a market/stop request cannot promise a fill.
    targets are outstanding desired quotas, not newly authorized order amounts.
    """
    status: str
    reason: str
    desired_stop: float
    targets: tuple[Target, ...] = ()
    runner_enabled: bool = False


def propose_scalp_exit(
    plan: ScalpExitPlan,
    snapshot: ScalpSnapshot,
    policy: ScalpTrailPolicy,
    *,
    trend_permission: bool,
) -> ScalpExitProposal:
    """Fail closed on unknown/inconsistent ledgers without removing protection.

    External reductions consume runner quota first, then farthest outstanding
    targets. This is deterministic accounting, not a claim about the actual
    reduction's price or reason. Untradable residual target dust requests
    reconciliation rather than rounding up or silently dropping protection.
    """
    _flag(trend_permission, "trend_permission")
    _flag(snapshot.ledger_confirmed, "ledger_confirmed")
    _flag(snapshot.protection_confirmed, "protection_confirmed")
    for name in ("current_stop", "mark_price", "favorable_extreme"):
        _positive(getattr(snapshot, name), name)
    _positive(snapshot.elapsed_seconds, "elapsed_seconds", zero=True)
    _lots(snapshot.remaining_lots, "remaining_lots", zero=True)
    _lots(snapshot.external_reduced_lots, "external_reduced_lots", zero=True)
    if type(snapshot.tp_filled_lots) is not tuple:
        raise ValueError("immutable_fill_tuple_required")
    for filled in snapshot.tp_filled_lots:
        _lots(filled, "tp_filled_lots", zero=True)

    def result(status: str, reason: str) -> ScalpExitProposal:
        return ScalpExitProposal(status, reason, snapshot.current_stop)

    if not snapshot.ledger_confirmed or not snapshot.protection_confirmed:
        return result("reconcile", "unconfirmed_state_preserve_protection")
    targets = plan.targets
    fills = snapshot.tp_filled_lots
    if (len(fills) != len(targets)
            or any(filled > target.lots for filled, target in zip(fills, targets))
            or sum(fills) + snapshot.external_reduced_lots
            + snapshot.remaining_lots != plan.initial_lots):
        return result("reconcile", "inconsistent_quantity_ledger")
    if snapshot.remaining_lots == 0:
        return result("flat", "confirmed_flat_requires_order_cleanup")
    if (plan.direction * (snapshot.favorable_extreme - snapshot.mark_price) < 0
            or plan.direction * (snapshot.favorable_extreme - plan.entry_price) < 0):
        return result("reconcile", "invalid_point_in_time_extreme")
    if plan.direction * (snapshot.mark_price - snapshot.current_stop) <= 0:
        return result("close", "confirmed_stop_breached")
    if snapshot.elapsed_seconds >= policy.max_hold_seconds:
        return result("close", "holding_deadline")

    outstanding = [target.lots - filled for target, filled in zip(targets, fills)]
    to_retire = max(0, snapshot.external_reduced_lots - plan.runner_lots)
    for index in reversed(range(len(outstanding))):
        retired = min(outstanding[index], to_retire)
        outstanding[index] -= retired
        to_retire -= retired
    if to_retire or sum(outstanding) > snapshot.remaining_lots:
        return result("reconcile", "inconsistent_outstanding_quota")
    if any(0 < lots < plan.min_lots for lots in outstanding):
        return result("reconcile", "outstanding_target_dust")
    if snapshot.remaining_lots < plan.min_lots:
        return result("reconcile", "remaining_position_dust")

    runner_enabled = fills[0] == targets[0].lots and trend_permission
    distance = policy.runner_distance if runner_enabled else policy.early_distance
    candidate = snapshot.favorable_extreme - plan.direction * distance
    desired_stop = (max(snapshot.current_stop, candidate) if plan.direction == 1
                    else min(snapshot.current_stop, candidate))
    if plan.direction * (snapshot.mark_price - desired_stop) <= 0:
        return result("close", "new_trail_already_crossed")
    desired_targets = tuple(Target(target.price, lots)
                            for target, lots in zip(targets, outstanding) if lots)
    return ScalpExitProposal("maintain", "confirmed_exit_plan", desired_stop,
                             desired_targets, runner_enabled)


def select_check_interval(
    *,
    idle_seconds: float,
    active_seconds: float,
    urgent_seconds: float,
    position_lots: int | None,
    has_orders: bool,
    pending: bool,
    cleanup: bool,
    state_known: bool,
    protection_ok: bool,
) -> float:
    """Pure schedule advice; never sleeps or changes a runner/cron schedule.

    Only exact known clean-flat state can idle. Unknown state or protection
    failure is urgent even after a local flat observation.
    """
    for name, value in (("idle_seconds", idle_seconds),
                        ("active_seconds", active_seconds),
                        ("urgent_seconds", urgent_seconds)):
        _positive(value, name)
    if not urgent_seconds <= active_seconds < idle_seconds:
        raise ValueError("require_urgent_le_active_lt_idle")
    for name, value in (("has_orders", has_orders), ("pending", pending),
                        ("cleanup", cleanup), ("state_known", state_known),
                        ("protection_ok", protection_ok)):
        _flag(value, name)
    if position_lots is not None:
        _lots(position_lots, "position_lots", zero=True)
    if position_lots is None or not state_known or not protection_ok:
        return urgent_seconds
    if position_lots or has_orders or pending or cleanup:
        return active_seconds
    return idle_seconds
