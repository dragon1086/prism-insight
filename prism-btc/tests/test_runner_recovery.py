"""Broker protection cannot depend on market/research availability."""
from types import SimpleNamespace

import pandas as pd
import pytest

from live import runner, tracking


@pytest.fixture
def setup_tick(monkeypatch, tmp_path):
    calls = []
    db = tmp_path / "root.sqlite"
    conn = tracking.get_connection(db)
    tracking.ensure_schema(conn)
    tracking.set_meta(conn, "last_processed_30m_ns", 123, "demo")
    conn.close()
    monkeypatch.setattr(runner, "_broker_recovery", lambda *a: calls.append("recovery") or [])
    from research import overrides
    monkeypatch.setattr(overrides, "apply_active", lambda *a: None)
    return calls, db


def assert_cursor(db):
    conn = tracking.get_connection(db)
    assert tracking.get_meta(conn, "last_processed_30m_ns", "demo") == 123
    conn.close()


def test_update_failure_still_runs_broker_recovery(setup_tick, monkeypatch):
    calls, db = setup_tick
    def fail(*a):
        calls.append("update")
        raise RuntimeError("offline")
    monkeypatch.setattr(runner, "update_all", fail)
    result = runner.tick("demo", root_db_path=db)
    assert calls == ["recovery", "update"]
    assert "offline" in result["error"]
    assert_cursor(db)


def test_indicator_failure_recovers_and_closes_connections(setup_tick, monkeypatch):
    calls, db = setup_tick
    closed = []
    monkeypatch.setattr(runner, "update_all", lambda *a: {})
    monkeypatch.setattr(runner, "market_connection", lambda *a: SimpleNamespace(close=lambda: closed.append(True)))
    monkeypatch.setattr(runner, "_load_tf_data", lambda *a: pd.DataFrame())
    def fail(*a):
        raise ValueError("indicator broken")
    monkeypatch.setattr(runner, "add_indicators", fail)
    result = runner.tick("demo", root_db_path=db)
    assert calls == ["recovery"]
    assert closed == [True]
    assert result["error"] == "tick failed: ValueError"
    assert_cursor(db)


def test_recovery_error_fences_strategy(setup_tick, monkeypatch):
    _, db = setup_tick
    monkeypatch.setattr(runner, "_broker_recovery", lambda *a: ["demo unavailable"])
    monkeypatch.setattr(runner, "update_all", lambda *a: pytest.fail("must not enter strategy path"))
    result = runner.tick("demo", root_db_path=db)
    assert "demo unavailable" in result["error"]
    assert_cursor(db)


def test_version_registered_even_when_recovery_fails(setup_tick, monkeypatch):
    _, db = setup_tick
    import subprocess
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: SimpleNamespace(stdout="new-recovery-code\n"))
    def recovery(conn, mode):
        assert tracking.get_meta(conn, "code_version", mode) == "new-recovery-code"
        return ["unavailable"]
    monkeypatch.setattr(runner, "_broker_recovery", recovery)
    result = runner.tick("demo", root_db_path=db)
    assert result["error"] == "unavailable"
    assert_cursor(db)


def test_normal_inner_result_unchanged_and_connection_closed(setup_tick, monkeypatch):
    calls, db = setup_tick
    seen = []
    def inner(conn, mode, market, result):
        seen.append(conn)
        return {**result, "new_bars": 3}
    monkeypatch.setattr(runner, "_tick_inner", inner)
    result = runner.tick("demo", root_db_path=db)
    assert result["new_bars"] == 3
    assert calls == ["recovery"]
    import sqlite3
    with pytest.raises(sqlite3.ProgrammingError):
        seen[0].execute("SELECT 1")


def test_main_adapter_error_does_not_skip_swing(monkeypatch):
    from live import broker_recovery, demo
    calls = []
    conn = tracking.get_connection(":memory:")
    tracking.ensure_schema(conn)
    def fail(*a, **k):
        raise RuntimeError("api_key=DO_NOT_LOG_BROKER_PAYLOAD")
    monkeypatch.setattr(demo, "DemoAdapter", fail)
    monkeypatch.setattr(broker_recovery, "reconcile_swing", lambda *a: calls.append("swing"))
    errors = runner._broker_recovery(conn, "demo")
    assert calls == ["swing"]
    assert errors == ["demo broker recovery: RuntimeError"]
    assert "DO_NOT_LOG" not in str(conn.execute("SELECT * FROM btc_events").fetchall())
    conn.close()


def test_empty_market_no_fake_bars_and_recovery_still_runs(setup_tick, monkeypatch):
    calls, db = setup_tick
    monkeypatch.setattr(runner, "update_all", lambda *a: {})
    monkeypatch.setattr(runner, "market_connection", lambda *a: SimpleNamespace(close=lambda: None))
    monkeypatch.setattr(runner, "_load_tf_data", lambda *a: pd.DataFrame(index=pd.DatetimeIndex([], tz="UTC")))
    monkeypatch.setattr(runner, "add_indicators", lambda data: data)
    monkeypatch.setattr(runner, "_load_funding", lambda *a: ([], []))
    monkeypatch.setattr(runner, "_load_protection_bars", lambda *a: pd.DataFrame())
    from live import demo, exit_capture, failure_observer
    monkeypatch.setattr(demo, "DemoAdapter", lambda *a, **k: SimpleNamespace())
    def fail(*a, **k):
        raise RuntimeError("optional unavailable")
    monkeypatch.setattr(exit_capture, "capture", fail)
    monkeypatch.setattr(failure_observer, "FailureObserver", fail)
    result = runner.tick("demo", root_db_path=db)
    assert calls == ["recovery"]
    assert result["error"] is None
    assert result["new_bars"] == result["protection_bars"] == 0
    assert_cursor(db)
