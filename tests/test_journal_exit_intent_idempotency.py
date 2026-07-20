import sqlite3

import pytest

from tracking.db_schema import (
    TABLE_TRADING_JOURNAL,
    migrate_trading_journal_exit_intent,
)
from tracking.journal import JournalManager


def _connection():
    connection = sqlite3.connect(":memory:")
    connection.execute(TABLE_TRADING_JOURNAL)
    migrate_trading_journal_exit_intent(connection.cursor(), connection)
    return connection


def test_journal_exit_intent_migration_upgrades_legacy_table():
    connection = sqlite3.connect(":memory:")
    connection.execute(
        """
        CREATE TABLE trading_journal (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ticker TEXT NOT NULL,
            company_name TEXT NOT NULL,
            trade_date TEXT NOT NULL,
            trade_type TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
        """
    )

    migrate_trading_journal_exit_intent(connection.cursor(), connection)

    columns = {
        row[1] for row in connection.execute("PRAGMA table_info(trading_journal)")
    }
    indexes = {
        row[1] for row in connection.execute("PRAGMA index_list(trading_journal)")
    }
    assert "exit_intent_id" in columns
    assert "idx_trading_journal_exit_intent" in indexes


@pytest.mark.asyncio
async def test_existing_exit_intent_skips_llm_and_returns_success():
    connection = _connection()
    connection.execute(
        """
        INSERT INTO trading_journal (
            ticker, company_name, trade_date, trade_type, created_at,
            exit_intent_id
        ) VALUES ('005930', 'Samsung', '2026-07-20', 'sell',
                  '2026-07-20', 'intent-journal-1')
        """
    )
    connection.commit()
    manager = JournalManager(
        cursor=connection.cursor(),
        conn=connection,
        enable_journal=True,
    )

    result = await manager.create_entry(
        stock_data={"ticker": "005930", "company_name": "Samsung"},
        sell_price=75000,
        profit_rate=7.1,
        holding_days=10,
        sell_reason="target",
        exit_intent_id="intent-journal-1",
    )

    assert result is True
    assert connection.execute("SELECT COUNT(*) FROM trading_journal").fetchone()[0] == 1


def test_journal_save_reports_duplicate_exit_intent_without_second_row():
    connection = _connection()
    manager = JournalManager(
        cursor=connection.cursor(),
        conn=connection,
        enable_journal=True,
    )
    journal_data = {
        "situation_analysis": {},
        "judgment_evaluation": {},
        "lessons": [],
        "pattern_tags": [],
        "one_line_summary": "done",
        "confidence_score": 0.8,
    }
    args = (
        "005930",
        "Samsung",
        70000,
        "2026-07-01",
        "{}",
        {},
        75000,
        "target",
        7.1,
        10,
        journal_data,
    )

    first_id, first_created = manager._save_to_database(
        *args, exit_intent_id="intent-journal-race"
    )
    second_id, second_created = manager._save_to_database(
        *args, exit_intent_id="intent-journal-race"
    )

    assert first_created is True
    assert second_created is False
    assert second_id == first_id
    assert connection.execute("SELECT COUNT(*) FROM trading_journal").fetchone()[0] == 1
