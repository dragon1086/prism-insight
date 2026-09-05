"""Offline M1.1 contract tests; no runtime reservations or broker integration."""
import importlib.util
import sys
from dataclasses import replace
from itertools import permutations
from pathlib import Path

import pytest


_spec = importlib.util.spec_from_file_location(
    "portfolio_risk_under_test",
    Path(__file__).resolve().parents[1] / "prism-btc/core/portfolio_risk.py",
)
risk = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = risk
_spec.loader.exec_module(risk)


def evaluate(**kwargs):
    defaults = dict(
        capital=1000, positions=(), pending_entries=(),
        proposed=risk.ProposedEntry("main", "long", 1, 100, 99),
    )
    defaults.update(kwargs)
    return risk.evaluate_portfolio_entry(**defaults)


@pytest.mark.parametrize("lane,qty,stop,reason", [
    ("main", 50, 99, "main_heat_cap"),
    ("swing", 15, 99, "swing_heat_cap"),
    ("main", 80, 100, "gross_cap"),
    ("swing", 50, 100, "swing_gross_cap"),
])
def test_exact_caps_allow_and_above_reject_without_clipping(lane, qty, stop, reason):
    entry = risk.ProposedEntry(lane, "long", qty, 100, stop)
    assert evaluate(proposed=entry).allowed
    over = replace(entry, qty=qty + 0.001)
    result = evaluate(proposed=over)
    assert not result.allowed
    assert reason in result.reasons
    assert result.metrics.gross == over.qty * over.price_bound
    assert over.qty == qty + 0.001


def test_combined_budget_boundary_and_explicit_policy():
    pending = (risk.PendingEntry("main", "long", 50, 100, 99),)
    candidate = risk.ProposedEntry("swing", "long", 15, 100, 99)
    assert evaluate(pending_entries=pending, proposed=candidate).allowed
    result = evaluate(pending_entries=pending, proposed=candidate,
                      policy=risk.PortfolioRiskPolicy(combined_heat_fraction=0.064))
    assert result.reasons == ("combined_heat_cap",)
    assert result.metrics.entry_heat == 65


@pytest.mark.parametrize("lane_order", [("main", "swing"), ("swing", "main")])
def test_lane_and_snapshot_order_do_not_change_accounting(lane_order):
    positions = tuple(risk.ActualPosition(lane, "long", 1, 100, 100, 99, True)
                      for lane in lane_order)
    pending = tuple(risk.PendingEntry(lane, "long", 1, 100, 99) for lane in lane_order)
    for p in permutations(positions):
        for orders in permutations(pending):
            result = evaluate(positions=p, pending_entries=orders)
            assert result.allowed
            assert result.metrics.gross == 500
            assert result.metrics.entry_heat == 5


@pytest.mark.parametrize("first", ["main", "swing"])
def test_main_first_or_swing_first_same_final_limits(first):
    other = "swing" if first == "main" else "main"
    quantities = {"main": 50, "swing": 15}
    result = evaluate(
        pending_entries=(risk.PendingEntry(first, "long", quantities[first], 100, 99),),
        proposed=risk.ProposedEntry(other, "long", quantities[other], 100, 99),
    )
    assert result.allowed
    assert result.metrics.entry_heat == 65


@pytest.mark.parametrize("first", ["main", "swing"])
def test_either_lane_candidate_rejected_at_common_gross_cap(first):
    other = "swing" if first == "main" else "main"
    result = evaluate(
        pending_entries=(risk.PendingEntry(first, "long", 40, 100, 100),),
        proposed=risk.ProposedEntry(other, "short", 41, 100, 100, 100),
    )
    assert result.reasons == ("gross_cap",)
    assert result.metrics.gross == 8100


def test_opposite_sides_never_net():
    result = evaluate(
        positions=(risk.ActualPosition("main", "long", 40, 100, 100, 100, True),),
        pending_entries=(risk.PendingEntry("swing", "short", 40, 100, 100, 100),),
    )
    assert not result.allowed
    assert "gross_cap" in result.reasons
    assert result.metrics.gross == 8100


def test_partial_fill_counts_actual_plus_remaining_not_original_order():
    result = evaluate(
        positions=(risk.ActualPosition("main", "long", 3, 100, 100, 99, True),),
        pending_entries=(risk.PendingEntry("main", "long", 7, 100, 99),),
    )
    assert result.allowed
    assert result.metrics.gross == 1100
    assert result.metrics.entry_heat == 11


@pytest.mark.parametrize("side,mark,stop", [("long", 120, 110), ("short", 80, 90)])
def test_profitable_stop_zero_entry_heat_but_giveback_is_informational(side, mark, stop):
    result = evaluate(positions=(risk.ActualPosition("main", side, 6, 100, mark, stop, True),))
    assert result.allowed
    assert result.metrics.entry_heat == 1
    assert result.metrics.mark_to_stop_giveback == 60


def test_short_uses_separate_upper_gross_and_lower_heat_bounds():
    result = evaluate(proposed=risk.ProposedEntry("main", "short", 1, 105, 110, 95))
    assert result.allowed
    assert result.metrics.gross == 105
    assert result.metrics.entry_heat == 15


def test_profitable_heat_cannot_cancel_another_positions_loss_heat():
    result = evaluate(positions=(
        risk.ActualPosition("main", "long", 1, 100, 120, 110, True),
        risk.ActualPosition("main", "short", 1, 100, 100, 110, True),
    ))
    assert result.metrics.entry_heat == 11


@pytest.mark.parametrize("capital", [None, 0, -1, True, "1000", float("nan"), float("inf")])
def test_invalid_capital_fails_closed(capital):
    assert not evaluate(capital=capital).allowed


@pytest.mark.parametrize("field,value", [
    ("lane", "unknown"), ("side", "buy"), ("qty", -1), ("qty", None),
    ("qty", float("nan")), ("entry", 0), ("mark", float("inf")),
    ("confirmed_stop", None), ("confirmed_stop", 0), ("stop_confirmed", False),
    ("stop_confirmed", 1),
])
def test_invalid_actual_position_fails_closed(field, value):
    position = risk.ActualPosition("main", "long", 1, 100, 100, 99, True)
    result = evaluate(positions=(replace(position, **{field: value}),))
    assert not result.allowed
    assert result.metrics is None


@pytest.mark.parametrize("candidate", [
    None, risk.ProposedEntry("main", "short", 1, 100, 110),
    risk.ProposedEntry("main", "short", 1, 100, 110, 101),
    risk.ProposedEntry("main", "long", 1, 100, 101),
    risk.ProposedEntry("main", "short", 1, 100, 99, 100),
    risk.ProposedEntry("main", "long", 0, 100, 99),
    risk.ProposedEntry("main", "long", 1, None, 99),
    risk.ProposedEntry("main", "long", 1, 100, None),
    risk.ProposedEntry("main", "long", 1e308, 1e308, 99),
])
def test_invalid_candidate_fails_closed(candidate):
    assert not evaluate(proposed=candidate).allowed


@pytest.mark.parametrize("pending", [None, (None,), (risk.PendingEntry("main", "long", -1, 100, 99),),
    (risk.PendingEntry("main", "long", None, 100, 99),),
    (risk.ProposedEntry("main", "long", 1, 100, 99),)])
def test_invalid_pending_fails_closed(pending):
    assert not evaluate(pending_entries=pending).allowed


@pytest.mark.parametrize("field", ["remaining_qty", "price_bound", "stop"])
@pytest.mark.parametrize("value", [None, -1, True, float("nan"), float("inf")])
def test_invalid_pending_numeric_fields_fail_closed(field, value):
    pending = risk.PendingEntry("main", "long", 1, 100, 99)
    assert not evaluate(pending_entries=(replace(pending, **{field: value}),)).allowed


def test_zero_pending_remaining_does_not_reserve_original_quantity():
    result = evaluate(pending_entries=(risk.PendingEntry("main", "long", 0, 100, 99),))
    assert result.allowed
    assert result.metrics.gross == 100


@pytest.mark.parametrize("policy", [None, risk.PortfolioRiskPolicy(gross_multiple=float("nan")),
    risk.PortfolioRiskPolicy(main_heat_fraction=-1)])
def test_invalid_policy_fails_closed(policy):
    assert not evaluate(policy=policy).allowed
