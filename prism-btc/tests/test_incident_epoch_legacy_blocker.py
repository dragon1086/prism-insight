"""Diagnostic witnesses, NOT incident-epoch acceptance or execution authority.

These tests document why a DB-only cutover cannot fence an unmodified worker.
They deliberately preserve the current protection fallback; do not deploy a
cutover based on their passing. All transports are the existing in-memory fake.
"""
import pytest
import ast
import hashlib
import json
from pathlib import Path
import types

from live.scenario_control import _write as write_control
from tests.test_scenario_execution import live  # noqa: F401


def baseline_source(module):
    snapshot = json.loads((Path(__file__).parent / 'fixtures' / 'incident_epoch_legacy_8be96d.json').read_text())
    assert snapshot['commit'] == '8be96d5cf3b7361d984fad5fbee46e5e9da0540c'
    expected = {'scenario_execution': '2232e5701d87a1b93b36033b2d9decb9bf43950f77b401ba3bde218c071015a1',
        'scenario_control': '94765e5e1b8c37a3f935c1cc415cf409e2da9fa1d502af3160408f0330abbe1d'}
    row = snapshot['functions'][module]
    assert hashlib.sha256(row['source'].encode()).hexdigest() == row['sha256'] == expected[module]
    return row['source']


@pytest.fixture(autouse=True)
def frozen_legacy_transport(live, monkeypatch):  # noqa: F811
    """Run the actual approved baseline method, not a predicate-shaped mock."""
    from live import scenario_execution
    source = baseline_source('scenario_execution')
    tree = ast.parse(source)
    method = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == '_write')
    namespace = dict(vars(scenario_execution))
    exec(compile(ast.Module(body=[method], type_ignores=[]), '<frozen-baseline>', 'exec'), namespace)
    monkeypatch.setattr(live[0], '_write', types.MethodType(namespace['_write'], live[0]))


@pytest.mark.parametrize("method,params", [
    ("place_order", dict(category="linear", symbol="BTCUSDT", positionIdx=0,
        orderLinkId="old-reduce", side="Sell", orderType="Market", qty="0.001",
        reduceOnly=True, closeOnTrigger=True)),
    ("cancel_order", dict(category="linear", symbol="BTCUSDT", orderLinkId="old-reduce")),
    ("set_trading_stop", dict(category="linear", symbol="BTCUSDT", positionIdx=0,
        stopLoss="98", tpslMode="Full")),
])
def test_legacy_transport_survives_paused_control_and_unreadable_state(live, monkeypatch, method, params):  # noqa: F811
    broker, exchange = live
    write_control(broker.conn, dict(version=1, state="paused", main_uid="123"))
    exchange.orders.update({"old-reduce": {}, "native": {}})

    def unavailable():
        raise RuntimeError("Epoch state is unavailable to this legacy process.")

    monkeypatch.setattr(broker, "_recovery_state", unavailable)
    before = len(exchange.writes)
    broker._write(method, **params)
    assert len(exchange.writes) == before + 1


def test_legacy_final_transport_does_not_acquire_mutation_mutex(live, monkeypatch):  # noqa: F811
    from live import shared_entry_coordinator
    broker, exchange = live

    def forbidden_lock(*args, **kwargs):
        raise AssertionError("A final transport mutex was actually acquired.")

    monkeypatch.setattr(shared_entry_coordinator, "mutation_lock", forbidden_lock)
    broker._write("place_order", category="linear", symbol="BTCUSDT", positionIdx=0,
        orderLinkId="legacy-unlocked", side="Sell", orderType="Market", qty="0.001",
        reduceOnly=True, closeOnTrigger=True)
    assert len(exchange.writes) == 1
