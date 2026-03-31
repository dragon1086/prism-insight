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
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    account_key TEXT NOT NULL,
    account_name TEXT,
    ticker TEXT NOT NULL,              -- AAPL, MSFT, etc.
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
    sector TEXT,                       -- GICS sector (Technology, Healthcare, etc.)
    UNIQUE(account_key, ticker)
)
"""

# Table: us_trading_history - Completed US trades
TABLE_US_TRADING_HISTORY = """
CREATE TABLE IF NOT EXISTS us_trading_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    account_key TEXT NOT NULL,
    account_name TEXT,
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
    min_score INTEGER,                 -- Minimum required score
    decision TEXT NOT NULL,            -- entry, no_entry, watch
    skip_reason TEXT,                  -- Reason for not entering
    scenario TEXT,                     -- JSON trading scenario
    trigger_type TEXT,
    trigger_mode TEXT,
    sector TEXT,                       -- GICS sector
    market_cap REAL,                   -- Market cap in USD
    current_price REAL,                -- Price at analysis time
    target_price REAL,                 -- Target price in USD
    stop_loss REAL,                    -- Stop loss price in USD
    investment_period TEXT,            -- short, medium, long
    portfolio_analysis TEXT,           -- Portfolio fit analysis
    valuation_analysis TEXT,           -- Valuation analysis
    sector_outlook TEXT,               -- Sector outlook
    market_condition TEXT,             -- Market condition assessment
    rationale TEXT,                    -- Entry/skip rationale
    risk_reward_ratio REAL,            -- Risk/Reward ratio
    was_traded INTEGER DEFAULT 0       -- 0=watched, 1=traded
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

# Table: us_holding_decisions - AI holding/selling decisions for current positions
TABLE_US_HOLDING_DECISIONS = """
CREATE TABLE IF NOT EXISTS us_holding_decisions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    account_key TEXT NOT NULL,
    account_name TEXT,
    ticker TEXT NOT NULL,
    decision_date TEXT NOT NULL,
    decision_time TEXT NOT NULL,

    current_price REAL NOT NULL,
    should_sell BOOLEAN NOT NULL,
    sell_reason TEXT,
    confidence INTEGER,

    technical_trend TEXT,
    volume_analysis TEXT,
    market_condition_impact TEXT,
    time_factor TEXT,

    portfolio_adjustment_needed BOOLEAN,
    adjustment_reason TEXT,
    new_target_price REAL,
    new_stop_loss REAL,
    adjustment_urgency TEXT,

    full_json_data TEXT NOT NULL,
    created_at TEXT DEFAULT (datetime('now', 'localtime'))
)
"""

# Table: us_pending_orders - Queued reserved orders (when placed outside KIS API time window)
# KIS API reserved order window: 10:00~23:20 KST (except 16:30~16:45)
# Orders placed before 10:00 KST are queued here and processed by us_pending_order_batch.py at 10:05 KST
TABLE_US_PENDING_ORDERS = """
CREATE TABLE IF NOT EXISTS us_pending_orders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    account_key TEXT NOT NULL,
    account_name TEXT,
    product_code TEXT,
    mode TEXT,
    ticker TEXT NOT NULL,
    order_type TEXT NOT NULL,          -- 'buy' or 'sell'
    limit_price REAL NOT NULL,         -- USD
    buy_amount REAL,                   -- USD (buy only)
    exchange TEXT,                     -- NASD, NYSE, AMEX
    trigger_type TEXT,
    trigger_mode TEXT,
    status TEXT DEFAULT 'pending',     -- pending, executed, failed, expired, cancelled
    failure_reason TEXT,
    created_at TEXT NOT NULL,
    executed_at TEXT,
    order_result TEXT                  -- JSON result from KIS API
)
"""

# =============================================================================
# Indexes for US Tables
# =============================================================================

US_INDEXES = [
    # us_stock_holdings indexes
    "CREATE INDEX IF NOT EXISTS idx_us_holdings_account_key ON us_stock_holdings(account_key)",
    "CREATE INDEX IF NOT EXISTS idx_us_holdings_account_ticker ON us_stock_holdings(account_key, ticker)",
    "CREATE INDEX IF NOT EXISTS idx_us_holdings_sector ON us_stock_holdings(sector)",
    "CREATE INDEX IF NOT EXISTS idx_us_holdings_trigger ON us_stock_holdings(trigger_type)",

    # us_trading_history indexes
    "CREATE INDEX IF NOT EXISTS idx_us_history_account_key ON us_trading_history(account_key)",
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

    # us_holding_decisions indexes
    "CREATE INDEX IF NOT EXISTS idx_us_holding_dec_account_key ON us_holding_decisions(account_key)",
    "CREATE INDEX IF NOT EXISTS idx_us_holding_dec_ticker ON us_holding_decisions(ticker)",
    "CREATE INDEX IF NOT EXISTS idx_us_holding_dec_date ON us_holding_decisions(decision_date)",

    # us_pending_orders indexes
    "CREATE INDEX IF NOT EXISTS idx_us_pending_account_key ON us_pending_orders(account_key)",
    "CREATE INDEX IF NOT EXISTS idx_us_pending_status ON us_pending_orders(status)",
    "CREATE INDEX IF NOT EXISTS idx_us_pending_created ON us_pending_orders(created_at)",
]

# =============================================================================
# Migration: Add 'market' column to shared tables
# =============================================================================

MARKET_COLUMN_MIGRATIONS = [
    ("trading_journal", "market TEXT DEFAULT 'KR'"),
    ("trading_principles", "market TEXT DEFAULT 'KR'"),
    ("trading_intuitions", "market TEXT DEFAULT 'KR'"),
]


def _table_exists(cursor, table_name: str) -> bool:
    cursor.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name = ?",
        (table_name,),
    )
    return cursor.fetchone() is not None


def _get_columns(cursor, table_name: str) -> list[str]:
    cursor.execute(f"PRAGMA table_info({table_name})")
    return [row[1] for row in cursor.fetchall()]


def _get_copy_columns(source_columns: list[str], target_columns: list[str]) -> list[str]:
    return [column for column in target_columns if column in source_columns]


def _get_primary_account_scope() -> tuple[str, str, str, str]:
    try:
        from trading import kis_auth as ka

        default_mode = str(ka.getEnv().get("default_mode", "demo")).strip().lower()
        svr = "vps" if default_mode == "demo" else "prod"
        primary_account = ka.resolve_account(svr=svr, market="us")
        mode = "demo" if primary_account["svr"] == "vps" else "real"
        return primary_account["account_key"], primary_account["name"], primary_account["product"], mode
    except Exception as exc:
        raise RuntimeError(
            "Unable to verify the primary account required for US DB migration. "
            "Please ensure at least one US account is configured in kis_devlp.yaml. "
            f"Migration aborted to prevent data orphaning. Cause: {exc}"
        ) from exc


def _count_rows(cursor, table_name: str) -> int:
    cursor.execute(f"SELECT COUNT(*) FROM {table_name}")
    return cursor.fetchone()[0]


def _table_requires_migration(cursor, table_name: str, marker_columns: list[str]) -> bool:
    if _table_exists(cursor, f"{table_name}_legacy"):
        return True
    if not _table_exists(cursor, table_name):
        return False
    source_columns = _get_columns(cursor, table_name)
    return not all(column in source_columns for column in marker_columns)


def _recover_interrupted_migration(cursor, conn, table_name: str):
    legacy_table = f"{table_name}_legacy"
    if not (_table_exists(cursor, table_name) and _table_exists(cursor, legacy_table)):
        return

    current_count = _count_rows(cursor, table_name)
    legacy_count = _count_rows(cursor, legacy_table)
    if current_count == 0:
        logger.warning(f"Recovering interrupted migration for {table_name} from {legacy_table}")
        cursor.execute(f"DROP TABLE {table_name}")
        cursor.execute(f"ALTER TABLE {legacy_table} RENAME TO {table_name}")
        conn.commit()
        return

    if legacy_count > 0:
        raise RuntimeError(
            f"Ambiguous interrupted migration for {table_name}: both {table_name} and {legacy_table} contain rows. "
            "Manual intervention is required."
        )


def _rebuild_table(
    cursor,
    conn,
    table_name: str,
    create_sql: str,
    target_columns: list[str],
    defaults: dict[str, object],
    marker_columns: list[str],
):
    _recover_interrupted_migration(cursor, conn, table_name)

    if not _table_exists(cursor, table_name):
        return

    if not _table_requires_migration(cursor, table_name, marker_columns):
        return

    legacy_table = f"{table_name}_legacy"
    backup_table = f"{table_name}_pre_multi_account_backup"

    if _table_exists(cursor, legacy_table):
        raise RuntimeError(
            f"Ambiguous migration state for {table_name}: legacy table {legacy_table} already exists. "
            "Manual intervention is required."
        )

    if not _table_exists(cursor, backup_table):
        logger.info(f"Creating backup table {backup_table} before migrating {table_name}")
        cursor.execute(f"CREATE TABLE {backup_table} AS SELECT * FROM {table_name}")
        conn.commit()
    else:
        logger.warning(f"Preserving existing backup table {backup_table} for {table_name}")

    logger.info(f"Migrating {table_name} to multi-account schema")

    try:
        cursor.execute(f"ALTER TABLE {table_name} RENAME TO {legacy_table}")
        cursor.execute(create_sql)

        source_columns = _get_columns(cursor, legacy_table)
        insert_columns = []
        projection = []
        params = []
        for column in target_columns:
            if column in source_columns:
                insert_columns.append(column)
                projection.append(column)
            elif column in defaults:
                insert_columns.append(column)
                projection.append("?")
                params.append(defaults[column])

        if insert_columns:
            cursor.execute(
                f"""
                INSERT INTO {table_name} ({", ".join(insert_columns)})
                SELECT {", ".join(projection)}
                FROM {legacy_table}
                """,
                tuple(params),
            )

        source_count = _count_rows(cursor, legacy_table)
        target_count = _count_rows(cursor, table_name)
        if source_count != target_count:
            raise RuntimeError(
                f"Row count mismatch during {table_name} migration: {legacy_table}={source_count}, {table_name}={target_count}"
            )

        cursor.execute(f"DROP TABLE {legacy_table}")
        conn.commit()
        logger.info(
            f"{table_name} migration complete ({target_count} rows migrated). "
            f"Backup table {backup_table} retained for manual cleanup."
        )
    except Exception as exc:
        logger.error(f"{table_name} migration failed: {exc}")
        logger.error(f"Manual recovery is available from backup table {backup_table}")
        raise


def migrate_multi_account_schema(cursor, conn):
    primary_scope = None

    def get_primary_scope():
        nonlocal primary_scope
        if primary_scope is None:
            try:
                primary_scope = _get_primary_account_scope()
            except Exception as exc:
                raise RuntimeError(
                    "Unable to verify the primary account required for US DB migration. "
                    "Please ensure at least one US account is configured in kis_devlp.yaml. "
                    f"Migration aborted to prevent data orphaning. Cause: {exc}"
                ) from exc
        return primary_scope

    if _table_requires_migration(cursor, "us_stock_holdings", ["id", "account_key", "account_name"]):
        account_key, account_name, _, _ = get_primary_scope()
        _rebuild_table(
            cursor,
            conn,
            "us_stock_holdings",
            TABLE_US_STOCK_HOLDINGS,
            [
                "id",
                "account_key",
                "account_name",
                "ticker",
                "company_name",
                "buy_price",
                "buy_date",
                "current_price",
                "last_updated",
                "scenario",
                "target_price",
                "stop_loss",
                "trigger_type",
                "trigger_mode",
                "sector",
            ],
            {
                "account_key": account_key,
                "account_name": account_name,
            },
            ["id", "account_key", "account_name"],
        )

    if _table_requires_migration(cursor, "us_trading_history", ["account_key", "account_name"]):
        account_key, account_name, _, _ = get_primary_scope()
        _rebuild_table(
            cursor,
            conn,
            "us_trading_history",
            TABLE_US_TRADING_HISTORY,
            [
                "id",
                "account_key",
                "account_name",
                "ticker",
                "company_name",
                "buy_price",
                "buy_date",
                "sell_price",
                "sell_date",
                "profit_rate",
                "holding_days",
                "scenario",
                "trigger_type",
                "trigger_mode",
                "sector",
            ],
            {
                "account_key": account_key,
                "account_name": account_name,
            },
            ["account_key", "account_name"],
        )

    if _table_requires_migration(cursor, "us_holding_decisions", ["account_key", "account_name"]):
        account_key, account_name, _, _ = get_primary_scope()
        _rebuild_table(
            cursor,
            conn,
            "us_holding_decisions",
            TABLE_US_HOLDING_DECISIONS,
            [
                "id",
                "account_key",
                "account_name",
                "ticker",
                "decision_date",
                "decision_time",
                "current_price",
                "should_sell",
                "sell_reason",
                "confidence",
                "technical_trend",
                "volume_analysis",
                "market_condition_impact",
                "time_factor",
                "portfolio_adjustment_needed",
                "adjustment_reason",
                "new_target_price",
                "new_stop_loss",
                "adjustment_urgency",
                "full_json_data",
                "created_at",
            ],
            {
                "account_key": account_key,
                "account_name": account_name,
                "portfolio_adjustment_needed": 0,
            },
            ["account_key", "account_name"],
        )

    if _table_requires_migration(cursor, "us_pending_orders", ["account_key", "account_name", "product_code", "mode"]):
        account_key, account_name, product_code, mode = get_primary_scope()
        _rebuild_table(
            cursor,
            conn,
            "us_pending_orders",
            TABLE_US_PENDING_ORDERS,
            [
                "id",
                "account_key",
                "account_name",
                "product_code",
                "mode",
                "ticker",
                "order_type",
                "limit_price",
                "buy_amount",
                "exchange",
                "trigger_type",
                "trigger_mode",
                "status",
                "failure_reason",
                "created_at",
                "executed_at",
                "order_result",
            ],
            {
                "account_key": account_key,
                "account_name": account_name,
                "product_code": product_code,
                "mode": mode,
            },
            ["account_key", "account_name", "product_code", "mode"],
        )


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
        ("us_holding_decisions", TABLE_US_HOLDING_DECISIONS),
        ("us_pending_orders", TABLE_US_PENDING_ORDERS),
    ]

    for table_name, table_sql in tables:
        try:
            cursor.execute(table_sql)
            logger.info(f"Created/verified table: {table_name}")
        except Exception as e:
            logger.error(f"Error creating table {table_name}: {e}")

    migrate_multi_account_schema(cursor, conn)
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


def migrate_us_watchlist_history_columns(cursor, conn):
    """
    Migrate us_watchlist_history table to add new columns for 7/14/30-day tracking.

    Adds columns that align with Korean version:
    - min_score: Minimum required score
    - target_price: Target price in USD
    - stop_loss: Stop loss price in USD
    - investment_period: short, medium, long
    - portfolio_analysis: Portfolio fit analysis
    - valuation_analysis: Valuation analysis
    - sector_outlook: Sector outlook
    - market_condition: Market condition assessment
    - rationale: Entry/skip rationale
    - risk_reward_ratio: Risk/Reward ratio
    - was_traded: 0=watched, 1=traded

    Args:
        cursor: SQLite cursor
        conn: SQLite connection
    """
    migrations = [
        ("us_watchlist_history", "min_score INTEGER"),
        ("us_watchlist_history", "target_price REAL"),
        ("us_watchlist_history", "stop_loss REAL"),
        ("us_watchlist_history", "investment_period TEXT"),
        ("us_watchlist_history", "portfolio_analysis TEXT"),
        ("us_watchlist_history", "valuation_analysis TEXT"),
        ("us_watchlist_history", "sector_outlook TEXT"),
        ("us_watchlist_history", "market_condition TEXT"),
        ("us_watchlist_history", "rationale TEXT"),
        ("us_watchlist_history", "risk_reward_ratio REAL"),
        ("us_watchlist_history", "was_traded INTEGER DEFAULT 0"),
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

    # Migrate US watchlist history columns (for existing databases)
    migrate_us_watchlist_history_columns(cursor, conn)

    logger.info(f"US database initialized: {db_path}")

    return cursor, conn


def _initialize_us_database_sync_and_close(db_path: str):
    cursor, conn = initialize_us_database(db_path)
    try:
        cursor.close()
    finally:
        conn.close()


async def async_initialize_us_database(db_path: Optional[str] = None):
    """
    Async version of initialize_us_database.

    Args:
        db_path: Path to SQLite database

    Returns:
        tuple: (connection,) - aiosqlite connection
    """
    import aiosqlite
    import asyncio

    if db_path is None:
        project_root = Path(__file__).resolve().parent.parent.parent
        db_path = project_root / "stock_tracking_db.sqlite"

    await asyncio.to_thread(_initialize_us_database_sync_and_close, str(db_path))
    conn = await aiosqlite.connect(str(db_path))
    logger.info(f"US database initialized (async): {db_path}")

    return conn


# =============================================================================
# Utility Functions
# =============================================================================

def get_us_holdings_count(cursor, account_key: Optional[str] = None) -> int:
    """Get count of current US holdings."""
    if account_key:
        cursor.execute("SELECT COUNT(*) FROM us_stock_holdings WHERE account_key = ?", (account_key,))
    else:
        cursor.execute("SELECT COUNT(*) FROM us_stock_holdings")
    return cursor.fetchone()[0]


def get_us_holding(cursor, ticker: str, account_key: Optional[str] = None) -> Optional[dict]:
    """Get a specific US holding."""
    if account_key:
        cursor.execute(
            "SELECT * FROM us_stock_holdings WHERE ticker = ? AND account_key = ?",
            (ticker, account_key)
        )
    else:
        cursor.execute(
            "SELECT * FROM us_stock_holdings WHERE ticker = ?",
            (ticker,)
        )
    row = cursor.fetchone()
    if row:
        columns = [desc[0] for desc in cursor.description]
        return dict(zip(columns, row))
    return None


def is_us_ticker_in_holdings(cursor, ticker: str, account_key: Optional[str] = None) -> bool:
    """Check if a US ticker is in holdings."""
    if account_key:
        cursor.execute(
            "SELECT COUNT(*) FROM us_stock_holdings WHERE ticker = ? AND account_key = ?",
            (ticker, account_key)
        )
    else:
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
