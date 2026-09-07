"""Offline research-hook boundaries; production sizing/entries remain untouched."""
from dataclasses import FrozenInstanceError, asdict, replace
import json
import sqlite3
from types import SimpleNamespace

import pandas as pd
import pytest

from backtest import engine
from core.actions import OpenIntent
from engine.sizing import SizingResult


def intent(tranche=0):
    return OpenIntent("long", 100.0, SizingResult(
        10.0, 2.0, 95.0, 105.0, 110.0, 115.0, 90.0, tranche,
    ), 20.0, tranche)


@pytest.fixture
def simulation(monkeypatch):
    times = pd.date_range("2024-01-01", periods=5, freq="30min", tz="UTC")
    bars = pd.DataFrame({
        "open": [100.] * 5, "high": [104.] * 5, "low": [99.] * 5,
        "close": [100., 101., 102., 103., 104.], "volume": [1.] * 5,
    }, index=times)
    monkeypatch.setattr(engine, "_load_tf_data", lambda conn, tf: bars.copy())
    monkeypatch.setattr(engine, "add_indicators", lambda df: df)
    monkeypatch.setattr(engine, "_get_tf_slice", lambda *args: pd.DataFrame())
    snapshot = SimpleNamespace(marker="causal")
    monkeypatch.setattr(engine, "_build_snapshot_at", lambda *args: snapshot)
    monkeypatch.setattr(engine, "generate_signal", lambda snap: SimpleNamespace(side="long"))
    monkeypatch.setattr(engine, "check_exit_signal", lambda *args: SimpleNamespace(exit_action="hold"))
    monkeypatch.setattr(engine, "evaluate_exits", lambda *args, **kwargs: [])
    monkeypatch.setattr(engine, "ENTRY_EVAL_EVERY_BAR", True)
    calls = []

    def evaluate(*args, current_tranche, **kwargs):
        calls.append(current_tranche)
        return intent(current_tranche)

    monkeypatch.setattr(engine, "evaluate_entry", evaluate)

    def run(**kwargs):
        with sqlite3.connect(":memory:") as conn:
            return engine.run_backtest(conn, times[0], times[-1] + pd.Timedelta(minutes=30), **kwargs)

    return run, times, calls, snapshot


def test_identity_hook_exact_financial_artifacts_and_both_entry_paths(simulation):
    run, _, calls, _ = simulation
    baseline = run()
    seen = []

    def identity(original, snapshot, state, timestamp):
        seen.append(original.tranche_index)
        return original

    observed = []
    hooked = run(entry_hook=identity, observer_hook=lambda *args: observed.append(args))
    assert json.dumps(asdict(baseline), sort_keys=True) == json.dumps(asdict(hooked), sort_keys=True)
    assert seen == [0, 1, 2]
    assert calls == [0, 1, 2, 0, 1, 2]
    assert len(observed) == 5


def test_veto_cannot_fabricate_entries(simulation, monkeypatch):
    run, _, _, _ = simulation
    assert not run(entry_hook=lambda *args: None).trade_logs
    monkeypatch.setattr(engine, "evaluate_entry", lambda *args, **kwargs: None)

    def forbidden(*args):
        pytest.fail("hook called without accepted core intent")

    assert not run(entry_hook=forbidden).trade_logs


def test_downsize_preserves_geometry_and_recomputes_risk(simulation):
    run, _, _, _ = simulation

    def halve(original, *args):
        sizing = replace(original.sizing, qty=original.sizing.qty / 2)
        return replace(original, sizing=sizing,
                       initial_risk=sizing.qty * abs(original.limit_price - sizing.sl_price))

    result = run(entry_hook=halve)
    assert len(result.trade_logs) == 3
    assert all(t.qty == 1 and t.leverage == 10 for t in result.trade_logs)
    assert all(t.r_multiple == pytest.approx(t.net_pnl / 5, abs=0.00051) for t in result.trade_logs)


@pytest.mark.parametrize("field,value", [
    ("side", "short"), ("limit_price", 101.), ("tranche_index", 1),
    ("initial_risk", float("nan")), ("initial_risk", 20.),
])
def test_reject_illegal_intent_fields(field, value):
    with pytest.raises(ValueError):
        engine._research_entry(lambda offered, *args: replace(offered, **{field: value}),
                               intent(), SimpleNamespace(), engine.BacktestState(1000), pd.Timestamp("2024-01-01"))


@pytest.mark.parametrize("field,value", [
    ("qty", 3.), ("qty", float("nan")), ("qty", float("inf")), ("qty", 0.),
    ("qty", -1.), ("sl_price", 96.), ("tp1_price", 106.), ("tp2_price", 111.),
    ("tp3_price", 116.), ("liq_price", 91.), ("leverage", 20.),
    ("tranche_index", 1), ("rejected", True), ("reject_reason", "changed"),
])
def test_reject_illegal_sizing_fields(field, value):
    def change(offered, *args):
        return replace(offered, sizing=replace(offered.sizing, **{field: value}), initial_risk=10.)

    with pytest.raises(ValueError):
        engine._research_entry(change, intent(), SimpleNamespace(), engine.BacktestState(1000),
                               pd.Timestamp("2024-01-01"))


def test_callback_mutations_cannot_escape(simulation):
    run, _, _, snapshot = simulation

    def mutate(offered, copied_snapshot, state, timestamp):
        copied_snapshot.marker = "changed"
        with pytest.raises(FrozenInstanceError):
            state.equity = 0
        if state.positions:
            with pytest.raises(FrozenInstanceError):
                state.positions[0].qty = 0
        return offered

    run(entry_hook=mutate)
    assert snapshot.marker == "causal"
    original = intent()

    def mutate_sizing(offered, *args):
        offered.sizing.leverage = 20
        return offered

    with pytest.raises(ValueError):
        engine._research_entry(mutate_sizing, original, snapshot, engine.BacktestState(1000),
                               pd.Timestamp("2024-01-01"))
    assert original.sizing.leverage == 10


def test_observer_is_postbar_no_future_fill_and_before_final_close(simulation):
    run, times, _, _ = simulation
    seen = []
    final = run(observer_hook=lambda *args: seen.append(args))
    assert [timestamp for _, timestamp, _ in seen] == list(times + pd.Timedelta(minutes=30))
    assert [mark for _, _, mark in seen] == [100., 101., 102., 103., 104.]
    assert [len(state.positions) for state, _, _ in seen] == [0, 1, 2, 3, 3]
    assert seen[0][0].pending_order.qty == 2
    assert seen[-1][0].trade_count == 0
    assert len(final.trade_logs) == 3 and not final.positions
    assert seen[-1][0].positions  # retained view is independent of final mutation


def test_observer_flushes_empty_run_zero_times(simulation, monkeypatch):
    run, times, _, _ = simulation
    empty = pd.DataFrame(columns=["open", "high", "low", "close"],
                         index=pd.DatetimeIndex([], tz="UTC"))
    monkeypatch.setattr(engine, "_load_tf_data", lambda *args: empty.copy())
    observations = []
    run(observer_hook=lambda *args: observations.append(args))
    assert observations == []


def test_observer_survives_outer_loop_continue(simulation, monkeypatch):
    run, times, _, _ = simulation
    monkeypatch.setattr(engine, "ENTRY_EVAL_EVERY_BAR", False)
    # Deliberately repeated confirmation reproduces the hard-cap early return.
    # The middle confirmation carries no signal, leaving the old evaluated cap.
    confirmed = [0, 1, 2, 1, 1]

    def slices(data, timestamp, tf):
        if tf != "4h":
            return pd.DataFrame()
        return pd.DataFrame({"close": [100.]}, index=[times[confirmed[times.get_loc(timestamp)]]])

    signals = iter(["long", "none", "long"])
    monkeypatch.setattr(engine, "_get_tf_slice", slices)
    monkeypatch.setattr(engine, "generate_signal", lambda *args: SimpleNamespace(side=next(signals)))
    monkeypatch.setattr(engine, "evaluate_entry", lambda *args, **kwargs: None)
    seen = []
    run(observer_hook=lambda *args: seen.append(args))
    assert [timestamp for _, timestamp, _ in seen] == list(times + pd.Timedelta(minutes=30))


@pytest.mark.parametrize("value", [True, False, "1", None, complex(1, 0)])
@pytest.mark.parametrize("field", ["qty", "initial_risk"])
def test_reject_boolean_and_nonnumeric_quantity_or_risk(value, field):
    def change(offered, *args):
        if field == "qty":
            return replace(offered, sizing=replace(offered.sizing, qty=value), initial_risk=5.)
        return replace(offered, sizing=replace(offered.sizing, qty=0.2), initial_risk=value)

    with pytest.raises(ValueError):
        engine._research_entry(change, intent(), SimpleNamespace(), engine.BacktestState(1000),
                               pd.Timestamp("2024-01-01"))
