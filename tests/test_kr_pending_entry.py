import logging
import sqlite3
import sys
import types
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

import trading.domestic_stock_trading as domestic_trading
from prism_core.order_intents import IntentStore
from prism_core.positions import PositionStore
from stock_tracking_agent import StockTrackingAgent
from tracking.db_schema import TABLE_STOCK_HOLDINGS


def _entry_state(db_path: Path) -> tuple[int, str | None, str | None]:
    with sqlite3.connect(db_path) as connection:
        holding_count = connection.execute(
            "SELECT COUNT(*) FROM stock_holdings WHERE ticker='005930'"
        ).fetchone()[0]
        intent = connection.execute(
            "SELECT status FROM order_intents "
            "WHERE market='KR' AND account_id='vps:kr-primary:01' "
            "AND symbol='005930' AND side='BUY'"
        ).fetchone()
        position = connection.execute(
            "SELECT status FROM positions "
            "WHERE market='KR' AND account_id='vps:kr-primary:01' "
            "AND symbol='005930'"
        ).fetchone()
    return (
        holding_count,
        intent[0] if intent else None,
        position[0] if position else None,
    )


def _pending_entry_agent(db_path: Path):
    connection = sqlite3.connect(db_path)
    connection.row_factory = sqlite3.Row
    connection.execute(TABLE_STOCK_HOLDINGS)
    PositionStore(connection).ensure_schema()
    connection.commit()
    IntentStore(db_path)

    agent = StockTrackingAgent.__new__(StockTrackingAgent)
    agent.db_path = str(db_path)
    agent.conn = connection
    agent.cursor = connection.cursor()
    agent.account_configs = [
        {"name": "kr-primary", "account_key": "vps:kr-primary:01"}
    ]
    agent.active_account = None
    agent.max_slots = 10
    agent.message_queue = []
    agent._msg_types = []
    agent.position_ledger_shadow_enabled = True
    agent._position_pending_kr_ready = True
    agent.trigger_info_map = {}
    agent._get_trigger_win_rate = lambda _trigger: ""

    async def analyze_report(_report_path):
        return {
            "success": True,
            "ticker": "005930",
            "company_name": "Samsung Electronics",
            "current_price": 70000,
            "scenario": {
                "buy_score": 8,
                "min_score": 7,
                "sector": "Technology",
                "target_price": 80000,
                "stop_loss": 65000,
            },
            "decision": "Enter",
            "sector": "Technology",
            "rank_change_msg": "Up",
        }

    agent._analyze_report_core = analyze_report
    agent.update_holdings = AsyncMock(return_value=[])
    agent._is_ticker_in_holdings = AsyncMock(return_value=False)
    agent._get_current_slots_count = AsyncMock(return_value=0)
    agent._check_sector_diversity = AsyncMock(return_value=True)
    agent._save_watchlist_item = AsyncMock(return_value=True)
    return agent, connection


def _install_pending_entry_runtime(
    monkeypatch,
    *,
    agent,
    db_path: Path,
    broker_result: dict | None = None,
    broker_error: BaseException | None = None,
):
    broker_calls = []
    publish_states = []
    redis_calls = []
    gcp_calls = []

    class BrokerContext:
        def __init__(self, account_name=None, **_kwargs):
            self.account_name = account_name

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def async_buy_stock(
            self, stock_code, limit_price=None, buy_amount=None
        ):
            broker_calls.append(
                {
                    "stock_code": stock_code,
                    "limit_price": limit_price,
                    "buy_amount": buy_amount,
                    "state": _entry_state(db_path),
                    "message_count": len(agent.message_queue),
                }
            )
            if broker_error is not None:
                raise broker_error
            return broker_result or {
                "success": True,
                "message": "submitted",
                "order_no": "KR-ORDER-1",
            }

    redis_module = types.ModuleType("messaging.redis_signal_publisher")
    gcp_module = types.ModuleType("messaging.gcp_pubsub_signal_publisher")

    async def publish_redis(**kwargs):
        redis_calls.append(kwargs)
        publish_states.append((_entry_state(db_path), len(agent.message_queue)))

    async def publish_gcp(**kwargs):
        gcp_calls.append(kwargs)
        publish_states.append((_entry_state(db_path), len(agent.message_queue)))

    redis_module.publish_buy_signal = publish_redis
    gcp_module.publish_buy_signal = publish_gcp
    monkeypatch.setitem(sys.modules, "messaging.redis_signal_publisher", redis_module)
    monkeypatch.setitem(
        sys.modules, "messaging.gcp_pubsub_signal_publisher", gcp_module
    )
    monkeypatch.setattr(domestic_trading, "AsyncTradingContext", BrokerContext)
    monkeypatch.setenv("POSITION_PENDING_KR_ENABLED", "true")
    monkeypatch.setenv("POSITION_LEDGER_SHADOW_ENABLED", "true")
    return broker_calls, publish_states, redis_calls, gcp_calls


@pytest.mark.asyncio
async def test_pending_kr_buy_opens_position_before_publishing_submitted_order(
    monkeypatch, tmp_path, caplog
):
    db_path = tmp_path / "pending-entry.sqlite"
    agent, connection = _pending_entry_agent(db_path)
    broker_calls, publish_states, redis_calls, gcp_calls = (
        _install_pending_entry_runtime(
            monkeypatch,
            agent=agent,
            db_path=db_path,
        )
    )
    caplog.set_level(logging.CRITICAL)

    try:
        result = await StockTrackingAgent.process_reports(agent, ["report-a.pdf"])
    finally:
        connection.close()

    assert result == (1, 0)
    assert len(broker_calls) == 1
    assert broker_calls[0]["state"] == (1, "SUBMITTING", "PENDING_ENTRY")
    assert broker_calls[0]["message_count"] == 0
    assert publish_states == [
        ((1, "SUBMITTED", "OPEN"), 1),
        ((1, "SUBMITTED", "OPEN"), 1),
    ]
    assert len(agent.message_queue) == 1
    assert len(redis_calls) == 1
    assert len(gcp_calls) == 1
    assert not [record for record in caplog.records if record.levelno >= logging.CRITICAL]


@pytest.mark.asyncio
async def test_pending_kr_buy_marks_explicit_broker_failure_without_publishing(
    monkeypatch, tmp_path, caplog
):
    db_path = tmp_path / "failed-entry.sqlite"
    agent, connection = _pending_entry_agent(db_path)
    broker_calls, publish_states, redis_calls, gcp_calls = (
        _install_pending_entry_runtime(
            monkeypatch,
            agent=agent,
            db_path=db_path,
            broker_result={"success": False, "message": "order rejected"},
        )
    )
    caplog.set_level(logging.CRITICAL)

    try:
        result = await StockTrackingAgent.process_reports(agent, ["report-a.pdf"])
        state = _entry_state(db_path)
    finally:
        connection.close()

    assert result == (0, 0)
    assert state == (0, "FAILED", "ENTRY_FAILED")
    assert len(broker_calls) == 1
    assert agent.message_queue == []
    assert publish_states == []
    assert redis_calls == []
    assert gcp_calls == []
    assert len(
        [record for record in caplog.records if record.levelno >= logging.CRITICAL]
    ) == 1


@pytest.mark.asyncio
async def test_pending_kr_buy_keeps_unknown_outcome_for_manual_review(
    monkeypatch, tmp_path, caplog
):
    db_path = tmp_path / "unknown-entry.sqlite"
    agent, connection = _pending_entry_agent(db_path)
    broker_calls, publish_states, redis_calls, gcp_calls = (
        _install_pending_entry_runtime(
            monkeypatch,
            agent=agent,
            db_path=db_path,
            broker_error=TimeoutError("broker response timed out"),
        )
    )
    caplog.set_level(logging.CRITICAL)

    try:
        result = await StockTrackingAgent.process_reports(agent, ["report-a.pdf"])
        state = _entry_state(db_path)
    finally:
        connection.close()

    assert result == (0, 0)
    assert state == (1, "UNKNOWN", "PENDING_ENTRY")
    assert len(broker_calls) == 1
    assert agent.message_queue == []
    assert publish_states == []
    assert redis_calls == []
    assert gcp_calls == []
    assert len(
        [record for record in caplog.records if record.levelno >= logging.CRITICAL]
    ) == 1


@pytest.mark.asyncio
async def test_pending_kr_buy_rolls_back_when_position_prepare_fails(
    monkeypatch, tmp_path, caplog
):
    db_path = tmp_path / "prepare-failed-entry.sqlite"
    agent, connection = _pending_entry_agent(db_path)
    broker_calls, publish_states, redis_calls, gcp_calls = (
        _install_pending_entry_runtime(
            monkeypatch,
            agent=agent,
            db_path=db_path,
        )
    )

    def fail_prepare_entry(_store, **_kwargs):
        raise RuntimeError("injected position prepare failure")

    monkeypatch.setattr(PositionStore, "prepare_entry", fail_prepare_entry)
    caplog.set_level(logging.CRITICAL)

    try:
        result = await StockTrackingAgent.process_reports(agent, ["report-a.pdf"])
        state = _entry_state(db_path)
    finally:
        connection.close()

    assert result == (0, 0)
    assert state == (0, None, None)
    assert broker_calls == []
    assert agent.message_queue == []
    assert publish_states == []
    assert redis_calls == []
    assert gcp_calls == []
    assert len(
        [record for record in caplog.records if record.levelno >= logging.CRITICAL]
    ) == 1


@pytest.mark.parametrize("readiness", [None, False])
def test_pending_entry_requires_explicit_successful_ledger_readiness(
    monkeypatch, tmp_path, readiness
):
    db_path = tmp_path / "readiness.sqlite"
    connection = sqlite3.connect(db_path)
    agent = StockTrackingAgent.__new__(StockTrackingAgent)
    agent.db_path = str(db_path)
    agent.conn = connection
    agent.position_ledger_shadow_enabled = True
    if readiness is not None:
        agent._position_pending_kr_ready = readiness
    monkeypatch.setenv("POSITION_PENDING_KR_ENABLED", "true")

    try:
        with pytest.raises(RuntimeError, match="initialization is not ready"):
            agent._require_pending_entry_ready()
    finally:
        connection.close()
