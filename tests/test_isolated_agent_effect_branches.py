"""Actual method-body tests with explicit decision doubles, NOT model SHADOW."""
import ast
import asyncio
from datetime import datetime
import json
import importlib.util
import logging
import sqlite3
from pathlib import Path
import sys
from types import MethodType, SimpleNamespace
from typing import Any, Dict, List
from unittest.mock import AsyncMock

import pytest

from prism_core import isolated_strategy_effects as effects
from prism_core.isolated_agent_runtime import require_execution_runtime
from prism_core.isolated_agent_runtime import prepare_isolated_runtime
from prism_core.strategy_ledger import StrategyLedger
from test_isolated_strategy_effects import bound as _bound, create_history_table
from test_isolated_effects_context import context, envelope

ROOT = Path(__file__).resolve().parents[1]
bound = _bound


def method(filename, name):
    tree = ast.parse((ROOT / filename).read_text())
    node = next(node for node in ast.walk(tree) if isinstance(node, ast.AsyncFunctionDef) and node.name == name)
    spec = importlib.util.spec_from_file_location("isolated_unit_us_schema", ROOT / "prism-us/tracking/db_schema.py")
    schema = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(schema)
    namespace = {"Dict": Dict, "List": List, "Any": Any, "asyncio": asyncio,
        "datetime": datetime, "json": json, "logger": logging.getLogger(__name__),
        "require_execution_runtime": require_execution_runtime,
        "effects_for": effects.effects_for, "EffectsFailure": effects.EffectsFailure,
        "ExecutionService": SimpleNamespace(domestic=forbidden, us=forbidden),
        "get_us_existing_position_for_ticker": schema.get_us_existing_position_for_ticker,
        "traceback": SimpleNamespace(format_exc=lambda: "unit failure")}
    exec(compile(ast.Module(body=[node], type_ignores=[]), filename, "exec"), namespace)
    return namespace[name]


def forbidden(*args, **kwargs):
    raise AssertionError("Forbidden external effect attempted")


def bind_pipeline(agent, ledger, adapter, monkeypatch, *, corporate=True, ticker="005930"):
    payload = {"quote": {ticker: envelope(110)}}
    if corporate:
        payload["corporate_status"] = {ticker: envelope("00")}
        payload["corporate_event"] = {ticker: envelope({"should_exit": False, "reason": "synthetic fixture"})}
    ctx = context(payload)
    ctx = __import__("dataclasses").replace(ctx, case_id=adapter.registration.case_id)
    registration = __import__("dataclasses").replace(adapter.registration, context_hash=ctx.source_hash)
    adapter = effects.IsolatedStrategyEffects(agent, ledger, registration, pipeline_context=ctx)
    agent._no_order_effects = adapter
    monkeypatch.setattr(effects, "EFFECTS_RUNTIME_ENABLED", True)
    monkeypatch.setattr(effects, "_source_now", lambda: "2026-09-10T00:00:00Z")
    agent._account_scope = lambda: ("virtual:test", "SHADOW test")
    agent._get_live_regime_safe = lambda: {"regime": "sideways", "source": "synthetic unit fixture"}
    agent._get_current_stock_price = AsyncMock(side_effect=forbidden)
    agent._position_pending_kr_enabled = forbidden
    agent._analyze_sell_decision = AsyncMock(return_value=(True, "synthetic decision double"))
    agent.sell_stock = AsyncMock(side_effect=forbidden)
    agent._safe_account_log_label = lambda value: "SHADOW test"
    monkeypatch.setitem(sys.modules, "cores.corporate_status", SimpleNamespace(fetch_status_codes=forbidden, check_event_exit=forbidden))
    return adapter


def test_kr_actual_update_body_stops_before_broker_and_legacy_sell(bound, monkeypatch):
    agent, ledger, adapter = bound
    adapter.record_entry(ticker="005930", company_name="SYNTHETIC test", price=100, scenario={}, is_add=False)
    adapter = bind_pipeline(agent, ledger, adapter, monkeypatch)
    run = MethodType(method("stock_tracking_agent.py", "update_holdings"), agent)
    result = asyncio.run(run())
    assert len(result) == 1
    assert ledger.snapshot("book")["occupied_slots"] == 0
    assert ledger.snapshot("book")["executions"] == []
    agent.sell_stock.assert_not_called()
    agent._get_current_stock_price.assert_not_called()


def test_missing_corporate_source_fails_case_without_ambient_probe(bound, monkeypatch):
    agent, ledger, adapter = bound
    adapter.record_entry(ticker="005930", company_name="SYNTHETIC test", price=100, scenario={}, is_add=False)
    bind_pipeline(agent, ledger, adapter, monkeypatch, corporate=False)
    run = MethodType(method("stock_tracking_agent.py", "update_holdings"), agent)
    with pytest.raises(effects.EffectsFailure):
        asyncio.run(run())
    assert ledger.snapshot("book")["occupied_slots"] == 1
    agent._analyze_sell_decision.assert_not_called()


def test_frozen_case_clock_cannot_hide_quote_age_after_model(bound, monkeypatch):
    agent, ledger, adapter = bound
    adapter = bind_pipeline(agent, ledger, adapter, monkeypatch)
    monkeypatch.setattr(effects, "_source_now", lambda: "2026-09-10T00:03:00Z")
    with pytest.raises(effects.EffectsFailure, match="stale"):
        adapter.quote("005930")


def test_sell_analysis_cannot_commit_pre_model_quote_after_expiry(bound, monkeypatch):
    agent, ledger, adapter = bound
    adapter.record_entry(ticker="005930", company_name="SYNTHETIC test", price=100, scenario={}, is_add=False)
    bind_pipeline(agent, ledger, adapter, monkeypatch)
    async def delayed_decision(stock):
        monkeypatch.setattr(effects, "_source_now", lambda: "2026-09-10T00:03:00Z")
        return True, "synthetic decision after elapsed clock"
    agent._analyze_sell_decision = delayed_decision
    run = MethodType(method("stock_tracking_agent.py", "update_holdings"), agent)
    with pytest.raises(effects.EffectsFailure, match="stale"):
        asyncio.run(run())
    assert ledger.snapshot("book")["occupied_slots"] == 1


@pytest.fixture
def bound_us(tmp_path):
    tmp_path.chmod(0o700)
    marker = tmp_path / ".prism-agent-isolation.json"
    marker.write_text(json.dumps({"schema_version": 1, "purpose": "PRISM_AGENT_SHADOW", "market": "US", "runtime_id": "test"}))
    marker.chmod(0o600)
    runtime = prepare_isolated_runtime(str(tmp_path / "state.sqlite"),
        [{"name": "SHADOW test", "account_key": "virtual:test", "market": "us", "virtual": True}], tmp_path, lambda: {}, "US")
    conn = runtime.connect()
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE us_stock_holdings (id INTEGER PRIMARY KEY, account_key TEXT, account_name TEXT, ticker TEXT, company_name TEXT, buy_price REAL, buy_date TEXT, current_price REAL, last_updated TEXT, scenario TEXT, target_price REAL, stop_loss REAL, trigger_type TEXT, trigger_mode TEXT, sector TEXT)")
    conn.commit()
    create_history_table(conn, "US")
    agent = SimpleNamespace(_isolated_runtime=runtime, conn=conn, cursor=conn.cursor(), db_path=runtime.db_path)
    ledger = StrategyLedger(tmp_path / "strategy.sqlite")
    ledger.create_book("book", "US", mode="SHADOW")
    registration = effects.EffectsRegistration("case", "book", "a" * 64, "2026-09-10T00:00:00Z", (("SNDK", "campaign"),))
    adapter = effects.IsolatedStrategyEffects(agent, ledger, registration)
    yield agent, ledger, adapter
    conn.close()


def test_us_actual_update_body_does_not_probe_broker_window(bound_us, monkeypatch):
    agent, ledger, adapter = bound_us
    adapter.record_entry(ticker="SNDK", company_name="SYNTHETIC test", price=100, scenario={}, is_add=False)
    adapter = bind_pipeline(agent, ledger, adapter, monkeypatch, corporate=False, ticker="SNDK")
    run = MethodType(method("prism-us/us_stock_tracking_agent.py", "update_holdings"), agent)
    result = asyncio.run(run(raise_on_error=True))
    assert len(result) == 1
    assert ledger.snapshot("book")["occupied_slots"] == 0
    assert ledger.snapshot("book")["executions"] == []
    agent.sell_stock.assert_not_called()
    assert any(item["kind"] == "broker_window" and item["status"] == "NOT_APPLICABLE_NO_ORDER" for item in adapter.observations)
