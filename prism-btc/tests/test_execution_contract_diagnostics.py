"""Offline characterization, never a claim about realized fills or profit."""
from dataclasses import replace

import pytest

from analysis.execution_contract_diagnostics import OHLCBar, audit_exit_bar, compare_tp_fee_roles
from core.actions import ActivateBETrail, BookPartial, ClosePosition
from core.exits import BarView, ExitContext, PositionView, evaluate_exits


def position(side="long", **overrides):
    values = dict(side=side, entry_price=100., qty=3., sl_price=95. if side == "long" else 105.,
                  tp1_price=105. if side == "long" else 95., liq_price=60. if side == "long" else 140.,
                  trailing_active=False, be_stop_set=False, tp1_hit=False, liq_breach_flagged=False)
    values.update(overrides)
    return PositionView(**values)


def context():
    return ExitContext(False, 0., False, None, 1.5, .5)


@pytest.mark.parametrize("side,bar,reference", [
    ("long", OHLCBar(92., 94., 90., 92.), 92.),
    ("short", OHLCBar(108., 110., 106., 108.), 108.),
])
def test_gap_stop_flags_impossible_legacy_fill_without_rewriting_it(side, bar, reference):
    pos = position(side)
    actions = evaluate_exits(pos, BarView(1, bar.high, bar.low, bar.close), context())
    stop = next(a for a in actions if isinstance(a, ClosePosition))
    assert not bar.low <= stop.price <= bar.high  # Characterize existing model, do not endorse it.
    diagnostic = audit_exit_bar(pos, bar)
    assert "STOP_GAP_ADVERSE_OPEN" in diagnostic["reason_codes"]
    assert "STOP_LEVEL_OUTSIDE_BAR" in diagnostic["reason_codes"]
    assert diagnostic["adverse_open_reference"] == reference
    assert diagnostic["execution_observed"] is False


@pytest.mark.parametrize("side,bar", [
    ("long", OHLCBar(106., 108., 99., 101.)),
    ("short", OHLCBar(94., 101., 92., 99.)),
])
def test_entry_bar_profit_and_be_can_use_prefill_extrema(side, bar):
    pos = position(side)
    actions = evaluate_exits(pos, BarView(1, bar.high, bar.low, bar.close), context())
    assert any(isinstance(a, BookPartial) for a in actions)
    assert any(isinstance(a, ActivateBETrail) for a in actions)
    diagnostic = audit_exit_bar(pos, bar, entered_this_bar=True)
    assert "AMBIGUOUS_ENTRY_BAR_TP" in diagnostic["reason_codes"]
    assert "AMBIGUOUS_ENTRY_BAR_BE" in diagnostic["reason_codes"]
    assert diagnostic["chronology"] == "UNKNOWN"
    assert diagnostic["profitability_evidence"] is False


@pytest.mark.parametrize("side", ["long", "short"])
def test_both_tp_and_sl_touch_is_a_convention_not_observed_sequence(side):
    pos, bar = position(side), OHLCBar(100., 108., 92., 100.)
    actions = evaluate_exits(pos, BarView(1, bar.high, bar.low, bar.close), context())
    assert any(isinstance(a, ClosePosition) for a in actions)
    assert not any(isinstance(a, BookPartial) for a in actions)
    diagnostic = audit_exit_bar(pos, bar)
    assert "AMBIGUOUS_TP_SL_ORDER" in diagnostic["reason_codes"]
    assert diagnostic["tp_sl_convention"] == "SL_BEFORE_TP"


def test_be_activation_and_reversal_in_one_bar_is_unknown():
    diagnostic = audit_exit_bar(position(), OHLCBar(100., 108., 99., 101.))
    assert "AMBIGUOUS_BE_ACTIVATION_ORDER" in diagnostic["reason_codes"]


def test_no_tp_or_be_chronology_warning_when_no_level_touched():
    diagnostic = audit_exit_bar(position(), OHLCBar(100., 102., 99., 101.))
    assert diagnostic["reason_codes"] == []
    assert diagnostic["verdict"] == "NO_FLAGGED_AMBIGUITY"
    assert diagnostic["execution_observed"] is False
    assert diagnostic["profitability_evidence"] is False


def test_tp1_once_retains_runner_and_has_no_tp2_tp3_ladder():
    pos = position()
    actions = evaluate_exits(pos, BarView(1, 108., 99., 107.), context())
    partial = next(a for a in actions if isinstance(a, BookPartial))
    assert partial.fraction == 1 / 3
    runner = replace(pos, qty=pos.qty * (1 - partial.fraction), tp1_hit=True)
    actions = evaluate_exits(runner, BarView(2, 125., 101., 120.), context())
    assert not any(isinstance(a, (BookPartial, ClosePosition)) for a in actions)
    assert runner.qty == pytest.approx(2.)


def test_tp_fee_stress_is_cost_only_not_a_profit_forecast():
    costs = compare_tp_fee_roles(qty=.01, price=50_000., maker_rate=.0002, taker_rate=.00055)
    assert costs["maker_fee"] == pytest.approx(.10)
    assert costs["taker_fee"] == pytest.approx(.275)
    assert costs["taker_minus_maker"] == pytest.approx(.175)
    assert costs["profitability_evidence"] is False


def test_fee_rebate_is_preserved():
    costs = compare_tp_fee_roles(qty=1., price=100., maker_rate=-.0001, taker_rate=.0005)
    assert costs["maker_fee"] == -.01


@pytest.mark.parametrize("bar", [OHLCBar(100., 99., 95., 98.), OHLCBar(100., 105., 101., 102.),
                                 OHLCBar(float("nan"), 105., 95., 100.), OHLCBar(True, 105., 95., 100.)])
def test_malformed_bars_are_rejected(bar):
    with pytest.raises(ValueError):
        audit_exit_bar(position(), bar)


@pytest.mark.parametrize("value", [0., -1., float("nan"), float("inf"), True])
def test_invalid_tp_cost_quantity_rejected(value):
    with pytest.raises(ValueError):
        compare_tp_fee_roles(qty=value, price=100., maker_rate=.0002, taker_rate=.00055)


@pytest.mark.parametrize("field", ["tp1_hit", "trailing_active"])
@pytest.mark.parametrize("value", ["False", None, 0, 1])
def test_nonboolean_state_cannot_suppress_ambiguity(field, value):
    with pytest.raises(ValueError):
        audit_exit_bar(position(**{field: value}), OHLCBar(100., 108., 99., 101.), entered_this_bar=True)


@pytest.mark.parametrize("side", ["long", "short"])
def test_derived_activation_overflow_is_rejected(side):
    pos = position(side, entry_price=1e308, tp1_price=1.1e308 if side == "long" else .9e308)
    with pytest.raises(ValueError):
        audit_exit_bar(pos, OHLCBar(1e308, 1.2e308, .8e308, 1e308), be_trail_activate_r=1e308)
