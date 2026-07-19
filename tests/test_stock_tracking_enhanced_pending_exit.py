import json
import sqlite3
from types import SimpleNamespace

import pytest

from stock_tracking_enhanced_agent import EnhancedStockTrackingAgent
from tracking.db_schema import TABLE_STOCK_HOLDINGS


def _enhanced_agent(db_path):
    connection = sqlite3.connect(db_path)
    connection.row_factory = sqlite3.Row
    connection.execute(TABLE_STOCK_HOLDINGS)
    connection.execute(
        """
        CREATE TABLE holding_decisions (
            ticker TEXT NOT NULL
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE portfolio_adjustment_log (
            ticker TEXT NOT NULL,
            account_key TEXT,
            adjusted_at TEXT,
            old_target_price REAL,
            new_target_price REAL,
            old_stop_loss REAL,
            new_stop_loss REAL,
            adjustment_reason TEXT,
            urgency TEXT
        )
        """
    )
    connection.execute(
        """
        INSERT INTO stock_holdings
        (account_key, account_name, ticker, company_name, buy_price, buy_date,
         current_price, scenario, target_price, stop_loss)
        VALUES ('ACC1', 'primary', '005930', 'Samsung', 70000,
                '2026-07-01 09:00:00', 72000, ?, 80000, 65000)
        """,
        (json.dumps({"highest_price": 73000, "sector": "Technology"}),),
    )
    connection.execute("INSERT INTO holding_decisions VALUES ('005930')")
    connection.execute(
        """
        INSERT INTO portfolio_adjustment_log
        (ticker, account_key, adjusted_at, adjustment_reason, urgency)
        VALUES ('005930', 'ACC1', '2026-07-18 09:00:00', 'test', 'low')
        """
    )
    connection.commit()

    agent = EnhancedStockTrackingAgent.__new__(EnhancedStockTrackingAgent)
    agent.db_path = str(db_path)
    agent.conn = connection
    agent.cursor = connection.cursor()
    agent.active_account = {"name": "primary", "account_key": "ACC1"}
    agent.max_slots = 10
    agent.language = "ko"

    class LLM:
        async def generate_str(self, **_kwargs):
            return json.dumps(
                {
                    "should_sell": True,
                    "sell_reason": "risk exit",
                    "confidence": 9,
                    "analysis_summary": {"technical_trend": "weak"},
                    "portfolio_adjustment": {"needed": False},
                }
            )

    class DecisionAgent:
        async def attach_llm(self, _factory):
            return LLM()

    agent.sell_decision_agent = DecisionAgent()
    stock = dict(
        connection.execute(
            "SELECT * FROM stock_holdings WHERE ticker='005930'"
        ).fetchone()
    )
    return agent, connection, stock


def _cleanup_counts(connection):
    return (
        connection.execute("SELECT COUNT(*) FROM holding_decisions").fetchone()[0],
        connection.execute(
            "SELECT COUNT(*) FROM portfolio_adjustment_log"
        ).fetchone()[0],
    )


@pytest.mark.asyncio
async def test_enhanced_gate_false_keeps_legacy_decision_time_cleanup(
    monkeypatch, tmp_path
):
    monkeypatch.setenv("POSITION_PENDING_KR_ENABLED", "false")
    agent, connection, stock = _enhanced_agent(tmp_path / "enhanced-legacy.sqlite")
    try:
        should_sell, _reason = await agent._analyze_sell_decision(stock)
        counts = _cleanup_counts(connection)
    finally:
        connection.close()

    assert should_sell is True
    assert counts == (0, 0)


@pytest.mark.asyncio
async def test_enhanced_gate_true_defers_cleanup_until_closed_hook(
    monkeypatch, tmp_path
):
    monkeypatch.setenv("POSITION_PENDING_KR_ENABLED", "true")
    agent, connection, stock = _enhanced_agent(tmp_path / "enhanced-pending.sqlite")
    try:
        should_sell, _reason = await agent._analyze_sell_decision(stock)
        before_closed = _cleanup_counts(connection)
        await agent._after_pending_kr_exit_closed(
            SimpleNamespace(symbol="005930")
        )
        after_closed = _cleanup_counts(connection)
    finally:
        connection.close()

    assert should_sell is True
    assert before_closed == (1, 1)
    assert after_closed == (0, 0)
