"""Deterministic OHLC execution stress; no broker calls or profitability claims."""
from dataclasses import asdict
import json
import sqlite3
from types import SimpleNamespace

import pandas as pd
import pytest

from backtest import engine
from backtest.split_entry import SplitConfig, SplitExecution
from core.actions import OpenIntent
from engine.sizing import SizingResult


TIME = pd.Timestamp("2024-01-01", tz="UTC")


def setup(mode="timed", side="long", qty=1., equity=10000., ordering="fill_first"):
    events = []
    ctl = SplitExecution(SplitConfig(mode, ordering), events.append)
    sign = 1 if side == "long" else -1
    sizing = SizingResult(10., qty, 100.-sign*5, 100.+sign*5,
                          100.+sign*10, 100.+sign*15, 100.-sign*10, 0)
    state = engine.BacktestState(equity)
    state.pending_order = engine.PendingOrder(side, 100., 0, sizing, qty*5, 0)
    ctl.accept(state, TIME)
    return ctl, state, events


def step(ctl, state, slot, *, touch=True, prior=101., action="hold", snapshot=True,
         high=None, low=None, mark=100.):
    bar = {"open": mark, "high": high if high is not None else (102. if touch else 104.),
           "low": low if low is not None else (99. if touch else 103.), "close": 100.}
    ctl.before_bar(state, TIME+pd.Timedelta(minutes=30*(slot+1)), slot+1, bar,
                   SimpleNamespace() if snapshot else None, prior,
                   lambda *args: SimpleNamespace(exit_action=action))


def fills(events):
    return [e for e in events if e["event"] == "child_fill"]


@pytest.mark.parametrize("mode", ["single", "timed", "confirmed", "probe"])
@pytest.mark.parametrize("side", ["long", "short"])
def test_one_parent_one_position_and_cost_conservation(mode, side):
    ctl, state, events = setup(mode, side)
    for slot in range(3):
        step(ctl, state, slot, prior=101 if side == "long" else 99)
    assert len(state.positions) == 1
    pos = state.positions[0]
    expected = .4 if mode == "probe" else 1.
    assert pos.qty == pytest.approx(expected)
    assert pos.initial_qty == pytest.approx(expected)
    assert pos.initial_risk == pytest.approx(expected*5)
    assert pos.entry_fee == pytest.approx(expected*100*engine.MAKER_FEE)
    assert state.equity == pytest.approx(10000-pos.entry_fee)
    assert pos.entry_time == str(TIME+pd.Timedelta(minutes=30))
    assert pos.entry_bar_idx == 1 and pos.tranche_index == 0
    assert pos.sl_price == (95 if side == "long" else 105)
    assert len(fills(events)) == (1 if mode in ("single", "probe") else 3)
    for event in events:
        assert event["filled_qty"]+event["remaining_qty"]+event["cancelled_qty"] == pytest.approx(1.)
    assert all(e["protection_from_first_fill"] for e in fills(events))
    json.dumps(events, allow_nan=False)


@pytest.mark.parametrize("mode", ["single", "timed", "confirmed", "probe"])
def test_expiry_precedes_boundary_touch(mode):
    ctl, state, events = setup(mode)
    step(ctl, state, 3)
    assert not state.positions and not fills(events)
    assert state.pending_order is None
    assert events[-1]["reason"] == "ttl"


def test_single_can_wait_but_missed_split_child_is_not_rolled_forward():
    for mode, expected in (("single", 1.), ("timed", .6), ("confirmed", 0.), ("probe", 0.)):
        ctl, state, events = setup(mode)
        step(ctl, state, 0, touch=False)
        step(ctl, state, 1)
        step(ctl, state, 2)
        assert sum(p.qty for p in state.positions) == pytest.approx(expected)
        assert sum(e["qty"] for e in fills(events)) == pytest.approx(expected)


@pytest.mark.parametrize("action", ["reduce", "exit", None])
@pytest.mark.parametrize("mode", ["single", "timed", "confirmed", "probe"])
def test_unknown_or_flow_change_cancels_before_fill(action, mode):
    ctl, state, events = setup(mode)
    step(ctl, state, 0, action=action, snapshot=action is not None)
    assert not fills(events) and state.pending_order is None


@pytest.mark.parametrize("mutation", ["sl", "qty", "tp", "force", "closed", "be"])
def test_no_refill_after_protective_state_changes(mutation):
    ctl, state, events = setup()
    step(ctl, state, 0)
    pos = state.positions[0]
    if mutation == "sl":
        pos.sl_price = 96
    elif mutation == "qty":
        pos.qty /= 2
    elif mutation == "tp":
        pos.tp1_hit = True
    elif mutation == "force":
        pos.had_forced_reduce = True
    elif mutation == "be":
        pos.be_stop_set = True
    else:
        state.positions.clear()
    step(ctl, state, 1)
    assert len(fills(events)) == 1 and state.pending_order is None


@pytest.mark.parametrize("side,prior", [("long", 99.), ("short", 101.)])
def test_confirmation_uses_prior_closed_price_not_current_favorable_high(side, prior):
    ctl, state, events = setup("confirmed", side)
    step(ctl, state, 0)
    step(ctl, state, 1, prior=prior, high=104, low=96)
    assert len(fills(events)) == 1
    step(ctl, state, 2, prior=101 if side == "long" else 99)
    assert state.positions[0].qty == pytest.approx(.7)


@pytest.mark.parametrize("side,high,low", [("long", 102, 94), ("long", 106, 99),
                                          ("short", 106, 99), ("short", 102, 94)])
def test_ambiguous_protection_order_stress(side, high, low):
    counts = []
    for ordering in ("fill_first", "stop_first"):
        ctl, state, events = setup(side=side, ordering=ordering)
        step(ctl, state, 0)
        step(ctl, state, 1, high=high, low=low)
        counts.append(len(fills(events)))
        assert len([e for e in events if e["event"] == "ambiguous"]) == 1
    assert counts == [2, 1]


def test_current_nav_caps_all_future_children_and_rounds_down():
    ctl, state, events = setup(equity=50.)
    step(ctl, state, 0)
    p = ctl.parent
    assert p is not None
    # All retained children, not just released quantity, must fit 5% heat.
    assert (p["filled"]+sum(p["children"]))*5 <= (50.-sum(p["children"])*.02)*.05
    assert p["target"] == 1. and p["cancelled"] > .5
    assert all(round(q*1000) == pytest.approx(q*1000) for q in p["children"])
    state.equity = .001
    step(ctl, state, 1)
    assert len(fills(events)) == 1 and state.pending_order is None


def test_pending_view_contains_only_remaining_quantity():
    ctl, state, _ = setup()
    step(ctl, state, 0)
    assert engine._research_state(state).pending_order.qty == pytest.approx(.6)
    assert state.positions[0].qty == .4


@pytest.mark.parametrize("mode", ["single", "timed", "confirmed", "probe"])
def test_tiny_parent_rejected_consistently(mode):
    _, state, events = setup(mode, qty=.002)
    assert state.pending_order is None
    assert events == [{"event": "parent_rejected", "bar_time": str(TIME),
                       "synthetic": True, "parent_id": None,
                       "reason": "unrepresentable_split"}]


@pytest.mark.parametrize("mode,ordering", [(True, "fill_first"), ("bad", "fill_first"),
                                          ("timed", False), ("timed", "bad")])
def test_configuration_validation(mode, ordering):
    with pytest.raises(ValueError):
        SplitConfig(mode, ordering)


@pytest.mark.parametrize("qty", [True, float("nan"), float("inf"), -1., "1"])
def test_invalid_numeric_parent_fails_closed(qty):
    with pytest.raises(ValueError):
        setup(qty=qty)


def test_closed_trade_has_exact_stable_parent_identity():
    ctl, state, events = setup("single")
    step(ctl, state, 0)
    pos = state.positions.pop()
    engine._close_position(pos, 101, str(TIME+pd.Timedelta(hours=2)), "end_of_period", state)
    ctl.reconcile(state, TIME+pd.Timedelta(hours=2))
    event = events[-1]
    assert event["event"] == "trade_closed"
    assert event["trade_id"] == state.trade_logs[0].trade_id
    assert event["parent_id"] == events[0]["parent_id"]
    assert event["net_pnl"] == state.trade_logs[0].net_pnl


@pytest.fixture
def simulation(monkeypatch):
    times = pd.date_range(TIME, periods=8, freq="30min")
    bars = pd.DataFrame({"open": [100.]*8, "high": [104.]*8, "low": [99.]*8,
                         "close": [101.]*8}, index=times)
    monkeypatch.setattr(engine, "_load_tf_data", lambda *args: bars.copy())
    monkeypatch.setattr(engine, "add_indicators", lambda df: df)
    monkeypatch.setattr(engine, "_get_tf_slice", lambda *args: pd.DataFrame())
    monkeypatch.setattr(engine, "_build_snapshot_at", lambda *args: SimpleNamespace())
    monkeypatch.setattr(engine, "generate_signal", lambda *args: SimpleNamespace(side="long"))
    monkeypatch.setattr(engine, "check_exit_signal", lambda *args: SimpleNamespace(exit_action="hold"))
    monkeypatch.setattr(engine, "evaluate_exits", lambda *args: [])
    monkeypatch.setattr(engine, "ENTRY_EVAL_EVERY_BAR", True)
    def intent(*args, current_tranche, **kwargs):
        sz = SizingResult(10., 1., 95., 105., 110., 115., 90., current_tranche)
        return OpenIntent("long", 100., sz, 5., current_tranche)
    monkeypatch.setattr(engine, "evaluate_entry", intent)
    def run(**kwargs):
        with sqlite3.connect(":memory:") as conn:
            return engine.run_backtest(conn, times[0], times[-1]+pd.Timedelta(minutes=30), **kwargs)
    return run, bars


def test_none_execution_identity_is_exact(simulation):
    run, _ = simulation
    assert asdict(run()) == asdict(run(execution_config=None, entry_hook=lambda intent, *args: intent))


def test_integration_split_children_never_consume_outer_tranches(simulation):
    run, _ = simulation
    events = []
    result = run(execution_config=SplitConfig("timed"), execution_event_hook=events.append)
    assert len(result.trade_logs) == 3
    assert [t.tranche_index for t in result.trade_logs] == [0, 1, 2]
    assert [t.qty for t in result.trade_logs] == pytest.approx([1., 1., .4])
    closed = [e for e in events if e["event"] == "trade_closed"]
    assert len(closed) == len(result.trade_logs)
    assert len(set(e["parent_id"] for e in closed)) == 3


def test_new_execution_flow_exit_is_not_blocked_by_three_positions(simulation, monkeypatch):
    run, bars = simulation
    monkeypatch.setattr(engine, "_build_snapshot_at",
                        lambda data, time: SimpleNamespace(time=time))
    def check(snapshot, side):
        return SimpleNamespace(exit_action="exit" if snapshot.time == bars.index[4] else "hold")
    monkeypatch.setattr(engine, "check_exit_signal", check)
    original_close = engine._close_position
    actual_signal_exit_counts = []
    def close(pos, price, time, reason, state, **kwargs):
        if reason == "signal_exit":
            actual_signal_exit_counts.append(len(state.positions))
        return original_close(pos, price, time, reason, state, **kwargs)
    monkeypatch.setattr(engine, "_close_position", close)
    result = run(execution_config=SplitConfig("single"))
    assert any(t.exit_reason == "signal_exit" for t in result.trade_logs)
    assert actual_signal_exit_counts == [3, 2, 1]


def test_future_price_changes_do_not_change_earlier_events(simulation):
    run, bars = simulation
    first, second = [], []
    run(execution_config=SplitConfig("confirmed"), execution_event_hook=first.append)
    boundary = bars.index[5]
    bars.loc[boundary:, ["high", "low", "close"]] = [120., 80., 90.]
    run(execution_config=SplitConfig("confirmed"), execution_event_hook=second.append)
    def prefix(events):
        return [e for e in events if pd.Timestamp(e["bar_time"]) < boundary]
    assert prefix(first) == prefix(second)


def test_pending_flow_reduction_cancels_remaining_and_reduces_position(simulation, monkeypatch):
    run, bars = simulation
    monkeypatch.setattr(engine, "_build_snapshot_at",
                        lambda data, time: SimpleNamespace(time=time))
    def check(snapshot, side):
        return SimpleNamespace(exit_action="reduce" if snapshot.time == bars.index[2] else "hold")
    monkeypatch.setattr(engine, "check_exit_signal", check)
    original_before_bar = SplitExecution.before_bar
    pending_at_flow_change = []
    def before_bar(controller, state, time, *args):
        if time == bars.index[2]:
            pending_at_flow_change.append((state.pending_order is not None, len(state.positions)))
        return original_before_bar(controller, state, time, *args)
    monkeypatch.setattr(SplitExecution, "before_bar", before_bar)
    events, observations = [], []
    run(execution_config=SplitConfig("timed"), execution_event_hook=events.append,
        observer_hook=lambda state, time, mark: observations.append((time, state)))
    first_id = events[0]["parent_id"]
    assert len([e for e in fills(events) if e["parent_id"] == first_id]) == 1
    after_reduce = dict(observations)[bars.index[3]]
    assert after_reduce.positions[0].qty == pytest.approx(.2)
    assert pending_at_flow_change == [(True, 1)]


def test_position_reserves_are_not_netted_and_future_fee_is_reserved():
    ctl, state, events = setup(equity=100.)
    # An existing short consumes half the main heat; a long must not net it.
    state.positions.append(engine.Position("short", 100., .5, 10., 105., 95., 90., 85.,
                                           110., "earlier", 1, -1, 2.5, initial_qty=.5))
    step(ctl, state, 0)
    accepted = events[0]["parent_qty"]
    p = ctl.parent
    assert p is not None and accepted == 1.
    assert p["filled"] + sum(p["children"]) == pytest.approx(.499)
    # .500 would consume the full 5 dollar heat before allowing entry fees.
    assert p["cancelled"] == pytest.approx(.501)


def test_first_fill_only_on_next_eligible_bar():
    ctl, state, events = setup()
    step(ctl, state, -1)
    assert not fills(events) and not state.positions
    step(ctl, state, 0)
    assert len(fills(events)) == 1


def test_minimum_target_does_not_mint_a_child_lot():
    ctl, state, events = setup(qty=.003)
    assert ctl.parent["children"] == [.001, 0., .002]
    for slot in range(3):
        step(ctl, state, slot)
    assert [e["qty"] for e in fills(events)] == [.001, .002]
    assert state.positions[0].qty == pytest.approx(.003)


def test_just_below_lot_boundary_never_rounds_up():
    ctl, state, events = setup(qty=.004-1e-15)
    assert ctl.parent["target"] == .003
    for slot in range(3):
        step(ctl, state, slot)
    assert sum(e["qty"] for e in fills(events)) <= .004-1e-15


def test_missing_probe_immediately_cancels_all_future_reservations():
    ctl, state, events = setup("confirmed")
    step(ctl, state, 0, touch=False)
    step(ctl, state, 1)
    assert ctl.parent is None and state.pending_order is None
    assert events[-1]["reason"] == "probe_missing"
    assert events[-1]["remaining_qty"] == 0


def test_probe_expiry_releases_pending_immediately():
    ctl, state, events = setup("probe")
    step(ctl, state, 0, touch=False)
    step(ctl, state, 1)
    assert ctl.parent is None and state.pending_order is None
    assert events[-1]["remaining_qty"] == 0


@pytest.mark.parametrize("field,value", [("sl_price", 101.), ("tp1_price", 99.),
    ("tp2_price", 103.), ("tp3_price", 106.), ("liq_price", 96.),
    ("leverage", True), ("tp1_price", float("inf")), ("tp2_price", "110")])
def test_malformed_protection_is_rejected_before_parent_acceptance(field, value):
    _, state, _ = setup()
    setattr(state.pending_order.sizing, field, value)
    ctl = SplitExecution(SplitConfig(), None)
    with pytest.raises(ValueError):
        ctl.accept(state, TIME)
