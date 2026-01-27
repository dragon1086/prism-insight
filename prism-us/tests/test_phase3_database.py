"""
Phase 3: Database Schema Tests

Tests for tracking/db_schema.py module:
- US table creation
- US index creation
- Market column migration
- Utility functions
"""

import os
import sqlite3
import sys
import tempfile
from pathlib import Path

import pytest

# Add paths for imports
PRISM_US_DIR = Path(__file__).parent.parent
PROJECT_ROOT = PRISM_US_DIR.parent
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PRISM_US_DIR))

from tracking.db_schema import (
    initialize_us_database,
    create_us_tables,
    create_us_indexes,
    add_market_column_to_shared_tables,
    get_us_holdings_count,
    get_us_holding,
    is_us_ticker_in_holdings,
    TABLE_US_STOCK_HOLDINGS,
    TABLE_US_TRADING_HISTORY,
    TABLE_US_WATCHLIST_HISTORY,
    TABLE_US_PERFORMANCE_TRACKER,
    US_INDEXES,
    MARKET_COLUMN_MIGRATIONS,
)


# =============================================================================
# Test: initialize_us_database()
# =============================================================================

class TestInitializeDatabase:
    """Tests for initialize_us_database function."""

    def test_initialize_returns_cursor_and_conn(self, temp_database):
        """Test that initialization returns cursor and connection."""
        cursor, conn = initialize_us_database(temp_database)

        assert cursor is not None
        assert conn is not None
        conn.close()

    def test_initialize_creates_database_file(self, temp_database):
        """Test that initialization creates database file."""
        cursor, conn = initialize_us_database(temp_database)
        conn.close()

        assert os.path.exists(temp_database)

    def test_initialize_is_idempotent(self, temp_database):
        """Test that initialization can be called multiple times."""
        cursor1, conn1 = initialize_us_database(temp_database)
        conn1.close()

        # Should not raise on second call
        cursor2, conn2 = initialize_us_database(temp_database)
        conn2.close()


# =============================================================================
# Test: US Tables Creation
# =============================================================================

class TestUSTablesCreation:
    """Tests for US table creation."""

    def test_us_stock_holdings_table_created(self, initialized_temp_database):
        """Test us_stock_holdings table is created."""
        cursor, conn, _ = initialized_temp_database
        cursor.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='us_stock_holdings'"
        )
        result = cursor.fetchone()
        assert result is not None
        assert result[0] == 'us_stock_holdings'

    def test_us_trading_history_table_created(self, initialized_temp_database):
        """Test us_trading_history table is created."""
        cursor, conn, _ = initialized_temp_database
        cursor.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='us_trading_history'"
        )
        result = cursor.fetchone()
        assert result is not None

    def test_us_watchlist_history_table_created(self, initialized_temp_database):
        """Test us_watchlist_history table is created."""
        cursor, conn, _ = initialized_temp_database
        cursor.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='us_watchlist_history'"
        )
        result = cursor.fetchone()
        assert result is not None

    def test_us_analysis_performance_tracker_table_created(self, initialized_temp_database):
        """Test us_analysis_performance_tracker table is created."""
        cursor, conn, _ = initialized_temp_database
        cursor.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='us_analysis_performance_tracker'"
        )
        result = cursor.fetchone()
        assert result is not None

    def test_all_four_us_tables_exist(self, initialized_temp_database):
        """Test all four US tables are created."""
        cursor, conn, _ = initialized_temp_database
        cursor.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'us_%'"
        )
        tables = cursor.fetchall()
        table_names = [t[0] for t in tables]

        expected_tables = [
            'us_stock_holdings',
            'us_trading_history',
            'us_watchlist_history',
            'us_analysis_performance_tracker',
        ]
        for expected in expected_tables:
            assert expected in table_names, f"Missing table: {expected}"


# =============================================================================
# Test: US Indexes Creation
# =============================================================================

class TestUSIndexesCreation:
    """Tests for US index creation."""

    def test_us_indexes_created(self, initialized_temp_database):
        """Test US indexes are created."""
        cursor, conn, _ = initialized_temp_database
        cursor.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND name LIKE 'idx_us_%'"
        )
        indexes = cursor.fetchall()

        assert len(indexes) >= 8, f"Expected at least 8 indexes, got {len(indexes)}"

    def test_holdings_sector_index(self, initialized_temp_database):
        """Test idx_us_holdings_sector index exists."""
        cursor, conn, _ = initialized_temp_database
        cursor.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND name='idx_us_holdings_sector'"
        )
        result = cursor.fetchone()
        assert result is not None

    def test_history_ticker_index(self, initialized_temp_database):
        """Test idx_us_history_ticker index exists."""
        cursor, conn, _ = initialized_temp_database
        cursor.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND name='idx_us_history_ticker'"
        )
        result = cursor.fetchone()
        assert result is not None


# =============================================================================
# Test: Table Structure
# =============================================================================

class TestTableStructure:
    """Tests for table column structure."""

    def test_us_stock_holdings_columns(self, initialized_temp_database):
        """Test us_stock_holdings table has correct columns."""
        cursor, conn, _ = initialized_temp_database
        cursor.execute("PRAGMA table_info(us_stock_holdings)")
        columns = {col[1]: col[2] for col in cursor.fetchall()}

        expected_columns = [
            'ticker', 'company_name', 'buy_price', 'buy_date',
            'current_price', 'last_updated', 'scenario',
            'target_price', 'stop_loss', 'trigger_type', 'trigger_mode', 'sector'
        ]
        for col in expected_columns:
            assert col in columns, f"Missing column: {col}"

    def test_us_trading_history_columns(self, initialized_temp_database):
        """Test us_trading_history table has correct columns."""
        cursor, conn, _ = initialized_temp_database
        cursor.execute("PRAGMA table_info(us_trading_history)")
        columns = {col[1]: col[2] for col in cursor.fetchall()}

        expected_columns = [
            'id', 'ticker', 'company_name', 'buy_price', 'buy_date',
            'sell_price', 'sell_date', 'profit_rate', 'holding_days',
            'scenario', 'trigger_type', 'trigger_mode', 'sector'
        ]
        for col in expected_columns:
            assert col in columns, f"Missing column: {col}"

    def test_us_performance_tracker_columns(self, initialized_temp_database):
        """Test us_analysis_performance_tracker has performance columns."""
        cursor, conn, _ = initialized_temp_database
        cursor.execute("PRAGMA table_info(us_analysis_performance_tracker)")
        columns = {col[1]: col[2] for col in cursor.fetchall()}

        # Check performance tracking columns
        performance_columns = [
            'price_7d', 'price_14d', 'price_30d',
            'return_7d', 'return_14d', 'return_30d',
            'hit_target', 'hit_stop_loss'
        ]
        for col in performance_columns:
            assert col in columns, f"Missing performance column: {col}"


# =============================================================================
# Test: CRUD Operations
# =============================================================================

class TestCRUDOperations:
    """Tests for database CRUD operations."""

    def test_insert_holding(self, initialized_temp_database, sample_holding):
        """Test inserting a holding record."""
        cursor, conn, _ = initialized_temp_database

        cursor.execute("""
            INSERT INTO us_stock_holdings
            (ticker, company_name, buy_price, buy_date, current_price,
             target_price, stop_loss, trigger_type, trigger_mode, sector)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            sample_holding['ticker'],
            sample_holding['company_name'],
            sample_holding['buy_price'],
            sample_holding['buy_date'],
            sample_holding['current_price'],
            sample_holding['target_price'],
            sample_holding['stop_loss'],
            sample_holding['trigger_type'],
            sample_holding['trigger_mode'],
            sample_holding['sector'],
        ))
        conn.commit()

        # Verify insert
        cursor.execute("SELECT * FROM us_stock_holdings WHERE ticker=?",
                      (sample_holding['ticker'],))
        result = cursor.fetchone()
        assert result is not None

    def test_get_us_holdings_count_empty(self, initialized_temp_database):
        """Test get_us_holdings_count on empty database."""
        cursor, conn, _ = initialized_temp_database
        count = get_us_holdings_count(cursor)
        assert count == 0

    def test_get_us_holdings_count_with_data(self, initialized_temp_database, sample_holding):
        """Test get_us_holdings_count with data."""
        cursor, conn, _ = initialized_temp_database

        # Insert a holding
        cursor.execute("""
            INSERT INTO us_stock_holdings
            (ticker, company_name, buy_price, buy_date)
            VALUES (?, ?, ?, ?)
        """, (
            sample_holding['ticker'],
            sample_holding['company_name'],
            sample_holding['buy_price'],
            sample_holding['buy_date'],
        ))
        conn.commit()

        count = get_us_holdings_count(cursor)
        assert count == 1

    def test_is_us_ticker_in_holdings_false(self, initialized_temp_database):
        """Test is_us_ticker_in_holdings returns False for non-existent ticker."""
        cursor, conn, _ = initialized_temp_database
        result = is_us_ticker_in_holdings(cursor, "NONEXISTENT")
        assert result is False

    def test_is_us_ticker_in_holdings_true(self, initialized_temp_database, sample_holding):
        """Test is_us_ticker_in_holdings returns True for existing ticker."""
        cursor, conn, _ = initialized_temp_database

        # Insert a holding
        cursor.execute("""
            INSERT INTO us_stock_holdings
            (ticker, company_name, buy_price, buy_date)
            VALUES (?, ?, ?, ?)
        """, (
            sample_holding['ticker'],
            sample_holding['company_name'],
            sample_holding['buy_price'],
            sample_holding['buy_date'],
        ))
        conn.commit()

        result = is_us_ticker_in_holdings(cursor, sample_holding['ticker'])
        assert result is True

    def test_get_us_holding_not_found(self, initialized_temp_database):
        """Test get_us_holding returns None for non-existent ticker."""
        cursor, conn, _ = initialized_temp_database
        result = get_us_holding(cursor, "NONEXISTENT")
        assert result is None

    def test_get_us_holding_found(self, initialized_temp_database, sample_holding):
        """Test get_us_holding returns dict for existing ticker."""
        cursor, conn, _ = initialized_temp_database

        # Insert a holding
        cursor.execute("""
            INSERT INTO us_stock_holdings
            (ticker, company_name, buy_price, buy_date, sector)
            VALUES (?, ?, ?, ?, ?)
        """, (
            sample_holding['ticker'],
            sample_holding['company_name'],
            sample_holding['buy_price'],
            sample_holding['buy_date'],
            sample_holding['sector'],
        ))
        conn.commit()

        result = get_us_holding(cursor, sample_holding['ticker'])
        assert result is not None
        assert isinstance(result, dict)
        assert result['ticker'] == sample_holding['ticker']
        assert result['company_name'] == sample_holding['company_name']


# =============================================================================
# Test: SQL Constants
# =============================================================================

class TestSQLConstants:
    """Tests for SQL constant definitions."""

    def test_table_definitions_not_empty(self):
        """Test table definition strings are not empty."""
        assert len(TABLE_US_STOCK_HOLDINGS) > 0
        assert len(TABLE_US_TRADING_HISTORY) > 0
        assert len(TABLE_US_WATCHLIST_HISTORY) > 0
        assert len(TABLE_US_PERFORMANCE_TRACKER) > 0

    def test_us_indexes_count(self):
        """Test US_INDEXES has expected number of indexes."""
        assert len(US_INDEXES) >= 8

    def test_market_column_migrations_defined(self):
        """Test market column migrations are defined."""
        assert len(MARKET_COLUMN_MIGRATIONS) == 3
        table_names = [m[0] for m in MARKET_COLUMN_MIGRATIONS]
        assert 'trading_journal' in table_names
        assert 'trading_principles' in table_names
        assert 'trading_intuitions' in table_names


# =============================================================================
# Integration Tests
# =============================================================================

@pytest.mark.integration
class TestDatabaseIntegration:
    """Integration tests for database operations."""

    def test_full_holding_lifecycle(self, initialized_temp_database, sample_holding):
        """Test full lifecycle: insert, query, update, delete."""
        cursor, conn, _ = initialized_temp_database

        # Insert
        cursor.execute("""
            INSERT INTO us_stock_holdings
            (ticker, company_name, buy_price, buy_date, current_price, sector)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (
            sample_holding['ticker'],
            sample_holding['company_name'],
            sample_holding['buy_price'],
            sample_holding['buy_date'],
            sample_holding['current_price'],
            sample_holding['sector'],
        ))
        conn.commit()

        # Query
        holding = get_us_holding(cursor, sample_holding['ticker'])
        assert holding is not None
        assert holding['buy_price'] == sample_holding['buy_price']

        # Update
        new_price = 195.00
        cursor.execute("""
            UPDATE us_stock_holdings SET current_price=? WHERE ticker=?
        """, (new_price, sample_holding['ticker']))
        conn.commit()

        holding = get_us_holding(cursor, sample_holding['ticker'])
        assert holding['current_price'] == new_price

        # Delete
        cursor.execute("DELETE FROM us_stock_holdings WHERE ticker=?",
                      (sample_holding['ticker'],))
        conn.commit()

        assert get_us_holdings_count(cursor) == 0

    def test_trading_history_insert(self, initialized_temp_database, sample_holding):
        """Test trading history record insertion."""
        cursor, conn, _ = initialized_temp_database

        cursor.execute("""
            INSERT INTO us_trading_history
            (ticker, company_name, buy_price, buy_date, sell_price, sell_date,
             profit_rate, holding_days, sector)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            sample_holding['ticker'],
            sample_holding['company_name'],
            sample_holding['buy_price'],
            sample_holding['buy_date'],
            195.00,  # sell_price
            '2026-01-20',  # sell_date
            8.03,  # profit_rate
            5,  # holding_days
            sample_holding['sector'],
        ))
        conn.commit()

        cursor.execute("SELECT COUNT(*) FROM us_trading_history")
        count = cursor.fetchone()[0]
        assert count == 1
