"""Offline contract tests only; no runtime adapter or profitability evidence."""
from dataclasses import FrozenInstanceError, replace

import pytest

from core.scalp import (
    ScalpExitPlan, ScalpSnapshot, ScalpTrailPolicy, Target,
    propose_scalp_exit, select_check_interval,
)


def plan(side="long", **changes):
    values = dict(side=side, entry_price=100, initial_lots=20, min_lots=1,
                  tp1_price=105 if side == "long" else 95,
                  further_targets=(Target(110 if side == "long" else 90, 5),))
    return ScalpExitPlan(**(values | changes))


def snapshot(side="long", **changes):
    values = dict(remaining_lots=20, tp_filled_lots=(0, 0),
                  external_reduced_lots=0, current_stop=95 if side == "long" else 105,
                  mark_price=102 if side == "long" else 98,
                  favorable_extreme=103 if side == "long" else 97,
                  elapsed_seconds=30, ledger_confirmed=True, protection_confirmed=True)
    return ScalpSnapshot(**(values | changes))


def propose(p=None, s=None, *, trend=False, policy=None):
    return propose_scalp_exit(p or plan(), s or snapshot(),
                              policy or ScalpTrailPolicy(4, 10, 3600),
                              trend_permission=trend)


@pytest.mark.parametrize("side,stop", [("long", 99), ("short", 101)])
def test_symmetric_fixed_half_and_no_price_touch_fill(side, stop):
    p = plan(side)
    s = snapshot(side, mark_price=120 if side == "long" else 80,
                 favorable_extreme=120 if side == "long" else 80)
    result = propose(p, s, trend=True)
    assert result.status == "maintain"
    assert p.targets[0].lots == 10
    assert result.targets[0].lots == 10  # Even beyond every TP, no fills invented.
    assert not result.runner_enabled
    assert propose(p, snapshot(side)).desired_stop == stop


@pytest.mark.parametrize("side", ["long", "short"])
def test_partial_tp_and_repeat_proposals_never_reslice_remaining(side):
    p = plan(side)
    s = snapshot(side, remaining_lots=17, tp_filled_lots=(3, 0))
    first = propose(p, s, trend=True)
    assert first.targets[0].lots == 7
    assert first == propose(p, s, trend=True)
    assert not first.runner_enabled
    assert p.initial_lots == 20
    assert s.remaining_lots == 17
    with pytest.raises(FrozenInstanceError):
        p.initial_lots = 17


@pytest.mark.parametrize("side", ["long", "short"])
@pytest.mark.parametrize("filled,trend,expected", [(9, True, False), (10, False, False),
                                                  (10, True, True)])
def test_runner_requires_actual_tp1_completion_and_explicit_permission(side, filled, trend, expected):
    s = snapshot(side, remaining_lots=20-filled, tp_filled_lots=(filled, 0))
    assert propose(plan(side), s, trend=trend).runner_enabled is expected


def test_small_residual_from_external_reduction_does_not_unlock_runner():
    result = propose(s=snapshot(remaining_lots=2, external_reduced_lots=18), trend=True)
    assert result.status == "maintain"
    assert result.targets == (Target(105, 2),)
    assert not result.runner_enabled


@pytest.mark.parametrize("side", ["long", "short"])
def test_stop_monotone_when_wider_runner_then_early_profile_returns(side):
    p = plan(side)
    first = propose(p, snapshot(side))
    s = snapshot(side, remaining_lots=10, tp_filled_lots=(10, 0),
                 current_stop=first.desired_stop)
    wider = propose(p, s, trend=True)
    assert wider.runner_enabled
    assert wider.desired_stop == first.desired_stop
    assert propose(p, replace(s, current_stop=wider.desired_stop)).desired_stop == first.desired_stop


@pytest.mark.parametrize("side", ["long", "short"])
@pytest.mark.parametrize("kind,expected", [("current", "confirmed_stop_breached"),
                                         ("trail", "new_trail_already_crossed"),
                                         ("deadline", "holding_deadline")])
def test_close_is_only_intent_preserving_current_stop(side, kind, expected):
    s = snapshot(side)
    if kind == "current":
        s = replace(s, current_stop=s.mark_price)
    elif kind == "trail":
        s = replace(s, favorable_extreme=110 if side == "long" else 90)
    else:
        s = replace(s, elapsed_seconds=3600)
    result = propose(plan(side), s, trend=True)
    assert result.status == "close"
    assert result.reason == expected
    assert result.desired_stop == s.current_stop
    assert result.targets == ()
    assert not hasattr(result, "price")


def test_current_stop_breach_precedes_wider_runner_profile_and_expiry():
    s = snapshot(remaining_lots=10, tp_filled_lots=(10, 0),
                 current_stop=103, elapsed_seconds=4000)
    assert propose(s=s, trend=True).reason == "confirmed_stop_breached"


@pytest.mark.parametrize("side", ["long", "short"])
def test_extreme_includes_entry_and_cannot_be_future_wrong_side(side):
    s = snapshot(side, mark_price=98 if side == "long" else 102,
                 favorable_extreme=99 if side == "long" else 101)
    assert propose(plan(side), s).reason == "invalid_point_in_time_extreme"


def test_invalid_trend_permission_and_equal_urgent_active():
    with pytest.raises(ValueError):
        propose(trend=1)
    assert cadence(urgent_seconds=10, pending=True) == 10


@pytest.mark.parametrize("changes", [
    {"ledger_confirmed": False}, {"protection_confirmed": False},
    {"remaining_lots": 19}, {"tp_filled_lots": (21, 0), "remaining_lots": 0},
    {"tp_filled_lots": (0,)}, {"external_reduced_lots": 21},
    {"favorable_extreme": 101},
])
def test_unknown_or_inconsistent_snapshot_reconciles_without_new_orders(changes):
    s = snapshot(**changes)
    result = propose(s=s, trend=True)
    assert result.status == "reconcile"
    assert result.desired_stop == s.current_stop
    assert result.targets == ()
    assert not result.runner_enabled


@pytest.mark.parametrize("external,expected", [(0, (10, 5)), (4, (10, 5)),
                                             (6, (10, 4)), (12, (8,))])
def test_external_reductions_retire_runner_then_farthest_targets(external, expected):
    result = propose(s=snapshot(remaining_lots=20-external, external_reduced_lots=external))
    assert tuple(target.lots for target in result.targets) == expected
    assert sum(expected) <= 20-external


def test_all_ledger_combinations_conserve_lots_without_exposure_growth():
    p = plan()
    for first in range(11):
        for second in range(6):
            for external in range(21-first-second):
                remaining = 20-first-second-external
                result = propose(p, snapshot(remaining_lots=remaining,
                    tp_filled_lots=(first, second), external_reduced_lots=external), trend=True)
                assert result.status == ("flat" if remaining == 0 else "maintain")
                assert sum(t.lots for t in result.targets) <= remaining
                assert result.runner_enabled == (remaining > 0 and first == 10)


def test_no_extra_target_required_and_zero_runner_is_allowed():
    assert plan(further_targets=()).runner_lots == 10
    assert plan(further_targets=(Target(110, 10),)).runner_lots == 0


@pytest.mark.parametrize("changes", [
    {"initial_lots": 19}, {"initial_lots": 0}, {"initial_lots": True},
    {"initial_lots": 20.0}, {"min_lots": 0}, {"min_lots": True},
    {"min_lots": 6}, {"side": "both"}, {"tp1_price": 100},
    {"tp1_price": 99}, {"further_targets": [Target(110, 5)]},
    {"further_targets": (Target(104, 5),)},
    {"further_targets": (Target(110, 11),)},
    {"min_lots": 3, "further_targets": (Target(110, 8),)},
])
def test_invalid_plan_rejected_without_rounding_or_sizing(changes):
    with pytest.raises(ValueError):
        plan(**changes)


def test_partial_target_dust_and_residual_position_dust_reconcile():
    p = plan(min_lots=2, further_targets=(Target(110, 6),))
    result = propose(p, snapshot(remaining_lots=11, tp_filled_lots=(9, 0)))
    assert result.reason == "outstanding_target_dust"
    result = propose(p, snapshot(remaining_lots=1, tp_filled_lots=(10, 6),
                                external_reduced_lots=3))
    assert result.reason == "remaining_position_dust"


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -float("inf"), True, 0, -1])
@pytest.mark.parametrize("field", ["entry_price", "tp1_price"])
def test_nonfinite_and_invalid_plan_prices(field, value):
    with pytest.raises(ValueError):
        plan(**{field: value})


@pytest.mark.parametrize("value", [float("nan"), float("inf"), True, -1])
@pytest.mark.parametrize("field", ["current_stop", "mark_price", "favorable_extreme",
                                  "elapsed_seconds", "remaining_lots", "external_reduced_lots"])
def test_invalid_snapshot_numerics_rejected(field, value):
    with pytest.raises(ValueError):
        propose(s=snapshot(**{field: value}))


@pytest.mark.parametrize("values", [(True, 1), (1, True), (float("nan"), 1),
                                  (1, 0), (1, 1.5)])
def test_invalid_target(values):
    with pytest.raises(ValueError):
        Target(*values)


@pytest.mark.parametrize("field", ["early_distance", "runner_distance", "max_hold_seconds"])
@pytest.mark.parametrize("value", [0, -1, True, float("nan"), float("inf")])
def test_invalid_policy(field, value):
    values = dict(early_distance=2, runner_distance=5, max_hold_seconds=300)
    with pytest.raises(ValueError):
        ScalpTrailPolicy(**(values | {field: value}))


@pytest.mark.parametrize("changes", [{"tp_filled_lots": [0, 0]},
    {"tp_filled_lots": (True, 0)}, {"tp_filled_lots": (float("nan"), 0)},
    {"ledger_confirmed": 1}, {"protection_confirmed": "yes"}])
def test_invalid_confirmation_contract(changes):
    with pytest.raises(ValueError):
        propose(s=snapshot(**changes))


def cadence(**changes):
    values = dict(idle_seconds=60, active_seconds=10, urgent_seconds=2,
                  position_lots=0, has_orders=False, pending=False, cleanup=False,
                  state_known=True, protection_ok=True)
    return select_check_interval(**(values | changes))


@pytest.mark.parametrize("changes,expected", [({}, 60), ({"position_lots": 1}, 10),
    ({"has_orders": True}, 10), ({"pending": True}, 10), ({"cleanup": True}, 10),
    ({"position_lots": None}, 2), ({"state_known": False}, 2),
    ({"protection_ok": False}, 2), ({"pending": True, "protection_ok": False}, 2)])
def test_cadence_only_slows_for_exact_known_clean_flat(changes, expected):
    assert cadence(**changes) == expected


@pytest.mark.parametrize("changes", [{"idle_seconds": 10}, {"urgent_seconds": 11},
    {"active_seconds": 0}, {"urgent_seconds": float("nan")},
    {"idle_seconds": float("inf")}, {"idle_seconds": True},
    {"position_lots": -1}, {"position_lots": True}, {"position_lots": 1.5},
    {"cleanup": 1}, {"pending": "false"}])
def test_cadence_rejects_invalid_parameters(changes):
    with pytest.raises(ValueError):
        cadence(**changes)
