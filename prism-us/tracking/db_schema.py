"""
Database Schema for US Stock Tracking

Contains table creation SQL and index definitions for US market.
Tables use us_* prefix to separate from Korean market tables.

Shared tables (trading_journal, trading_principles, trading_intuitions)
are used with 'market' column to distinguish between KR and US.
"""

import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# =============================================================================
# US-Specific Tables (us_* prefix)
# =============================================================================

# Table: us_stock_holdings - Current US stock positions
TABLE_US_STOCK_HOLDINGS = """
CREATE TABLE IF NOT EXISTS us_stock_holdings (
    ticker TEXT PRIMARY KEY,           -- AAPL, MSFT, etc.
    company_name TEXT NOT NULL,
    buy_price REAL NOT NULL,           -- USD
    buy_date TEXT NOT NULL,
    current_price REAL,
    last_updated TEXT,
    scenario TEXT,                     -- JSON trading scenario
    target_price REAL,                 -- USD
    stop_loss REAL,                    -- USD
    trigger_type TEXT,                 -- intraday_surge, volume_surge, gap_up, etc.
    trigger_mode TEXT,                 -- morning, afternoon
    sector TEXT                        -- GICS sector (Technology, Healthcare, etc.)
)
"""

# Table: us_trading_history - Completed US trades
TABLE_US_TRADING_HISTORY = """
CREATE TABLE IF NOT EXISTS us_trading_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker TEXT NOT NULL,
    company_name TEXT NOT NULL,
    buy_price REAL NOT NULL,           -- USD
    buy_date TEXT NOT NULL,
    sell_price REAL NOT NULL,          -- USD
    sell_date TEXT NOT NULL,
    profit_rate REAL NOT NULL,         -- Percentage
    holding_days INTEGER NOT NULL,
    scenario TEXT,                     -- JSON trading scenario
    trigger_type TEXT,
    trigger_mode TEXT,
    sector TEXT                        -- GICS sector
)
"""

# Table: us_watchlist_history - Analyzed but not entered US stocks
TABLE_US_WATCHLIST_HISTORY = """
CREATE TABLE IF NOT EXISTS us_watchlist_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker TEXT NOT NULL,
    company_name TEXT NOT NULL,
    analyzed_date TEXT NOT NULL,
    buy_score INTEGER,                 -- 0-100 score
    decision TEXT NOT NULL,            -- 진입, 미진입, 관망
    skip_reason TEXT,                  -- Reason for not entering
    scenario TEXT,                     -- JSON trading scenario
    trigger_type TEXT,
    trigger_mode TEXT,
    sector TEXT,                       -- GICS sector
    market_cap REAL,                   -- Market cap in USD
    current_price REAL                 -- Price at analysis time
)
"""

# Table: us_analysis_performance_tracker - Track analysis accuracy
TABLE_US_PERFORMANCE_TRACKER = """
CREATE TABLE IF NOT EXISTS us_analysis_performance_tracker (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker TEXT NOT NULL,
    company_name TEXT NOT NULL,
    analysis_date TEXT NOT NULL,
    analysis_price REAL NOT NULL,      -- Price at analysis time (USD)

    -- Analysis predictions
    predicted_direction TEXT,          -- UP, DOWN, NEUTRAL
    target_price REAL,
    stop_loss REAL,
    buy_score INTEGER,
    decision TEXT,
    skip_reason TEXT,                  -- Reason for not entering (if watched)
    risk_reward_ratio REAL,            -- Risk/Reward ratio at analysis time

    -- Performance tracking (updated daily)
    price_7d REAL,                     -- Price after 7 days
    price_14d REAL,                    -- Price after 14 days
    price_30d REAL,                    -- Price after 30 days

    return_7d REAL,                    -- Return % after 7 days
    return_14d REAL,                   -- Return % after 14 days
    return_30d REAL,                   -- Return % after 30 days

    hit_target INTEGER DEFAULT 0,      -- 1 if target was hit
    hit_stop_loss INTEGER DEFAULT 0,   -- 1 if stop loss was hit

    -- Tracking status (matches Korean version)
    tracking_status TEXT DEFAULT 'pending',  -- pending, in_progress, completed
    was_traded INTEGER DEFAULT 0,            -- 0=watched, 1=traded

    -- Metadata
    trigger_type TEXT,
    trigger_mode TEXT,
    sector TEXT,
    created_at TEXT NOT NULL,
    last_updated TEXT
)
"""

# =============================================================================
# Indexes for US Tables
# =============================================================================

US_INDEXES = [
    # us_stock_holdings indexes
    "CREATE INDEX IF NOT EXISTS idx_us_holdings_sector ON us_stock_holdings(sector)",
    "CREATE INDEX IF NOT EXISTS idx_us_holdings_trigger ON us_stock_holdings(trigger_type)",

    # us_trading_history indexes
    "CREATE INDEX IF NOT EXISTS idx_us_history_ticker ON us_trading_history(ticker)",
    "CREATE INDEX IF NOT EXISTS idx_us_history_date ON us_trading_history(sell_date)",
    "CREATE INDEX IF NOT EXISTS idx_us_history_sector ON us_trading_history(sector)",

    # us_watchlist_history indexes
    "CREATE INDEX IF NOT EXISTS idx_us_watchlist_ticker ON us_watchlist_history(ticker)",
    "CREATE INDEX IF NOT EXISTS idx_us_watchlist_date ON us_watchlist_history(analyzed_date)",
    "CREATE INDEX IF NOT EXISTS idx_us_watchlist_decision ON us_watchlist_history(decision)",

    # us_analysis_performance_tracker indexes
    "CREATE INDEX IF NOT EXISTS idx_us_perf_ticker ON us_analysis_performance_tracker(ticker)",
    "CREATE INDEX IF NOT EXISTS idx_us_perf_date ON us_analysis_performance_tracker(analysis_date)",
    "CREATE INDEX IF NOT EXISTS idx_us_perf_status ON us_analysis_performance_tracker(tracking_status)",
]

# =============================================================================
# Migration: Add 'market' column to shared tables
# =============================================================================

MARKET_COLUMN_MIGRATIONS = [
    ("trading_journal", "market TEXT DEFAULT 'KR'"),
    ("trading_principles", "market TEXT DEFAULT 'KR'"),
    ("trading_intuitions", "market TEXT DEFAULT 'KR'"),
]


def create_us_tables(cursor, conn):
    """
    Create all US-specific database tables.

    Args:
        cursor: SQLite cursor
        conn: SQLite connection
    """
    tables = [
        ("us_stock_holdings", TABLE_US_STOCK_HOLDINGS),
        ("us_trading_history", TABLE_US_TRADING_HISTORY),
        ("us_watchlist_history", TABLE_US_WATCHLIST_HISTORY),
        ("us_analysis_performance_tracker", TABLE_US_PERFORMANCE_TRACKER),
    ]

    for table_name, table_sql in tables:
        try:
            cursor.execute(table_sql)
            logger.info(f"Created/verified table: {table_name}")
        except Exception as e:
            logger.error(f"Error creating table {table_name}: {e}")

    conn.commit()
    logger.info("US database tables created")


def create_us_indexes(cursor, conn):
    """
    Create all US indexes.

    Args:
        cursor: SQLite cursor
        conn: SQLite connection
    """
    for index_sql in US_INDEXES:
        try:
            cursor.execute(index_sql)
        except Exception as e:
            logger.warning(f"Index creation warning: {e}")

    conn.commit()
    logger.info("US database indexes created")


def add_market_column_to_shared_tables(cursor, conn):
    """
    Add 'market' column to shared tables for KR/US distinction.

    This allows trading_journal, trading_principles, and trading_intuitions
    to be shared between Korean and US markets with proper filtering.

    Args:
        cursor: SQLite cursor
        conn: SQLite connection
    """
    for table_name, column_def in MARKET_COLUMN_MIGRATIONS:
        try:
            cursor.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_def}")
            conn.commit()
            logger.info(f"Added market column to {table_name}")
        except Exception as e:
            # Column likely already exists
            if "duplicate column name" in str(e).lower():
                logger.debug(f"market column already exists in {table_name}")
            else:
                logger.warning(f"Migration warning for {table_name}: {e}")


def add_sector_column_if_missing(cursor, conn):
    """
    Add sector column to us_stock_holdings and us_trading_history if missing.

    Args:
        cursor: SQLite cursor
        conn: SQLite connection
    """
    tables = ["us_stock_holdings", "us_trading_history", "us_watchlist_history"]

    for table in tables:
        try:
            cursor.execute(f"ALTER TABLE {table} ADD COLUMN sector TEXT")
            conn.commit()
            logger.info(f"Added sector column to {table}")
        except Exception:
            pass  # Column already exists


def migrate_us_performance_tracker_columns(cursor, conn):
    """
    Migrate us_analysis_performance_tracker table to add new columns.

    Adds columns that align with Korean version:
    - tracking_status: 'pending', 'in_progress', 'completed'
    - was_traded: 0=watched, 1=traded
    - risk_reward_ratio: Risk/Reward ratio
    - skip_reason: Reason for not entering

    Args:
        cursor: SQLite cursor
        conn: SQLite connection
    """
    migrations = [
        ("us_analysis_performance_tracker", "tracking_status TEXT DEFAULT 'pending'"),
        ("us_analysis_performance_tracker", "was_traded INTEGER DEFAULT 0"),
        ("us_analysis_performance_tracker", "risk_reward_ratio REAL"),
        ("us_analysis_performance_tracker", "skip_reason TEXT"),
    ]

    for table_name, column_def in migrations:
        try:
            cursor.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_def}")
            conn.commit()
            logger.info(f"Added column to {table_name}: {column_def}")
        except Exception as e:
            if "duplicate column name" in str(e).lower():
                logger.debug(f"Column already exists in {table_name}: {column_def}")
            else:
                logger.warning(f"Migration warning for {table_name}: {e}")

    # Update existing records to set tracking_status based on populated fields
    try:
        cursor.execute("""
            UPDATE us_analysis_performance_tracker
            SET tracking_status = CASE
                WHEN return_30d IS NOT NULL THEN 'completed'
                WHEN return_7d IS NOT NULL THEN 'in_progress'
                ELSE 'pending'
            END
            WHERE tracking_status IS NULL OR tracking_status = 'pending'
        """)
        conn.commit()
        logger.info("Updated tracking_status for existing records")
    except Exception as e:
        logger.warning(f"Error updating tracking_status: {e}")


def initialize_us_database(db_path: Optional[str] = None):
    """
    Initialize the US database with all tables and indexes.

    Uses the shared SQLite database (same as Korean version).

    Args:
        db_path: Path to SQLite database (defaults to project root)

    Returns:
        tuple: (cursor, connection)
    """
    import sqlite3

    if db_path is None:
        # Default to project root database
        project_root = Path(__file__).resolve().parent.parent.parent
        db_path = project_root / "stock_tracking_db.sqlite"

    conn = sqlite3.connect(str(db_path))
    cursor = conn.cursor()

    # Create US tables
    create_us_tables(cursor, conn)

    # Create US indexes
    create_us_indexes(cursor, conn)

    # Add market column to shared tables
    add_market_column_to_shared_tables(cursor, conn)

    # Migrate US performance tracker columns (for existing databases)
    migrate_us_performance_tracker_columns(cursor, conn)

    logger.info(f"US database initialized: {db_path}")

    return cursor, conn


async def async_initialize_us_database(db_path: Optional[str] = None):
    """
    Async version of initialize_us_database.

    Args:
        db_path: Path to SQLite database

    Returns:
        tuple: (connection,) - aiosqlite connection
    """
    import aiosqlite

    if db_path is None:
        project_root = Path(__file__).resolve().parent.parent.parent
        db_path = project_root / "stock_tracking_db.sqlite"

    conn = await aiosqlite.connect(str(db_path))

    # Create US tables
    tables = [
        TABLE_US_STOCK_HOLDINGS,
        TABLE_US_TRADING_HISTORY,
        TABLE_US_WATCHLIST_HISTORY,
        TABLE_US_PERFORMANCE_TRACKER,
    ]

    for table_sql in tables:
        await conn.execute(table_sql)

    # Create US indexes
    for index_sql in US_INDEXES:
        try:
            await conn.execute(index_sql)
        except Exception:
            pass

    # Add market column to shared tables
    for table_name, column_def in MARKET_COLUMN_MIGRATIONS:
        try:
            await conn.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_def}")
        except Exception:
            pass

    await conn.commit()
    logger.info(f"US database initialized (async): {db_path}")

    return conn


# =============================================================================
# Utility Functions
# =============================================================================

def get_us_holdings_count(cursor) -> int:
    """Get count of current US holdings."""
    cursor.execute("SELECT COUNT(*) FROM us_stock_holdings")
    return cursor.fetchone()[0]


def get_us_holding(cursor, ticker: str) -> Optional[dict]:
    """Get a specific US holding."""
    cursor.execute(
        "SELECT * FROM us_stock_holdings WHERE ticker = ?",
        (ticker,)
    )
    row = cursor.fetchone()
    if row:
        columns = [desc[0] for desc in cursor.description]
        return dict(zip(columns, row))
    return None


def is_us_ticker_in_holdings(cursor, ticker: str) -> bool:
    """Check if a US ticker is in holdings."""
    cursor.execute(
        "SELECT COUNT(*) FROM us_stock_holdings WHERE ticker = ?",
        (ticker,)
    )
    return cursor.fetchone()[0] > 0


if __name__ == "__main__":
    # Test database initialization
    import logging
    logging.basicConfig(level=logging.INFO)

    print("\n=== Testing US Database Schema ===\n")

    # Use test database
    test_db = Path(__file__).parent.parent / "tests" / "test_us_db.sqlite"
    test_db.parent.mkdir(exist_ok=True)

    cursor, conn = initialize_us_database(str(test_db))

    # Verify tables exist
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'us_%'")
    tables = cursor.fetchall()

    print("Created US tables:")
    for table in tables:
        print(f"  - {table[0]}")

    # Verify indexes
    cursor.execute("SELECT name FROM sqlite_master WHERE type='index' AND name LIKE 'idx_us_%'")
    indexes = cursor.fetchall()

    print("\nCreated US indexes:")
    for index in indexes:
        print(f"  - {index[0]}")

    # Check shared table migrations
    print("\nShared table migrations:")
    for table_name, _ in MARKET_COLUMN_MIGRATIONS:
        cursor.execute(f"PRAGMA table_info({table_name})")
        columns = [col[1] for col in cursor.fetchall()]
        has_market = "market" in columns
        status = "✅" if has_market else "⚠️ (table may not exist)"
        print(f"  - {table_name}: market column {status}")

    conn.close()

    # Clean up test database
    test_db.unlink(missing_ok=True)

    print("\n=== Test Complete ===")
