"""
Database Schema for Stock Tracking

Contains table creation SQL and index definitions.
Extracted from stock_tracking_agent.py for LLM context efficiency.
"""

import logging
from pathlib import Path

logger = logging.getLogger(__name__)

# Table: stock_holdings
TABLE_STOCK_HOLDINGS = """
CREATE TABLE IF NOT EXISTS stock_holdings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    account_key TEXT NOT NULL,
    account_name TEXT,
    ticker TEXT NOT NULL,
    company_name TEXT NOT NULL,
    buy_price REAL NOT NULL,
    buy_date TEXT NOT NULL,
    current_price REAL,
    last_updated TEXT,
    scenario TEXT,
    target_price REAL,
    stop_loss REAL,
    trigger_type TEXT,
    trigger_mode TEXT,
    sector TEXT,
    UNIQUE(account_key, ticker)
)
"""

# Table: trading_history
TABLE_TRADING_HISTORY = """
CREATE TABLE IF NOT EXISTS trading_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    account_key TEXT NOT NULL,
    account_name TEXT,
    ticker TEXT NOT NULL,
    company_name TEXT NOT NULL,
    buy_price REAL NOT NULL,
    buy_date TEXT NOT NULL,
    sell_price REAL NOT NULL,
    sell_date TEXT NOT NULL,
    profit_rate REAL NOT NULL,
    holding_days INTEGER NOT NULL,
    scenario TEXT,
    trigger_type TEXT,
    trigger_mode TEXT,
    sector TEXT
)
"""

# Table: trading_journal
TABLE_TRADING_JOURNAL = """
CREATE TABLE IF NOT EXISTS trading_journal (
    id INTEGER PRIMARY KEY AUTOINCREMENT,

    -- Trade basic info
    ticker TEXT NOT NULL,
    company_name TEXT NOT NULL,
    trade_date TEXT NOT NULL,
    trade_type TEXT NOT NULL,

    -- Buy context (for sell retrospective)
    buy_price REAL,
    buy_date TEXT,
    buy_scenario TEXT,
    buy_market_context TEXT,

    -- Sell context
    sell_price REAL,
    sell_reason TEXT,
    profit_rate REAL,
    holding_days INTEGER,

    -- Retrospective results (core)
    situation_analysis TEXT,
    judgment_evaluation TEXT,
    lessons TEXT,
    pattern_tags TEXT,
    one_line_summary TEXT,
    confidence_score REAL,

    -- Compression management
    compression_layer INTEGER DEFAULT 1,
    compressed_summary TEXT,

    -- Metadata
    created_at TEXT NOT NULL,
    last_compressed_at TEXT
)
"""

# Table: trading_intuitions
TABLE_TRADING_INTUITIONS = """
CREATE TABLE IF NOT EXISTS trading_intuitions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,

    -- Classification
    category TEXT NOT NULL,
    subcategory TEXT,

    -- Intuition content
    condition TEXT NOT NULL,
    insight TEXT NOT NULL,
    confidence REAL,

    -- Evidence
    supporting_trades INTEGER,
    success_rate REAL,
    source_journal_ids TEXT,

    -- Management
    created_at TEXT NOT NULL,
    last_validated_at TEXT,
    is_active INTEGER DEFAULT 1,

    -- Scope classification (universal/market/sector/ticker)
    scope TEXT DEFAULT 'universal'
)
"""

# Table: trading_principles
TABLE_TRADING_PRINCIPLES = """
CREATE TABLE IF NOT EXISTS trading_principles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,

    -- Scope classification
    scope TEXT NOT NULL DEFAULT 'universal',  -- universal/market/sector
    scope_context TEXT,  -- market='bull/bear', sector='semiconductor' etc.

    -- Principle content
    condition TEXT NOT NULL,
    action TEXT NOT NULL,
    reason TEXT,
    priority TEXT DEFAULT 'medium',  -- high/medium/low

    -- Evidence
    confidence REAL DEFAULT 0.5,
    supporting_trades INTEGER DEFAULT 1,
    source_journal_ids TEXT,

    -- Metadata
    created_at TEXT NOT NULL,
    last_validated_at TEXT,
    is_active INTEGER DEFAULT 1
)
"""

# Table: user_memories (per-user memory storage)
TABLE_USER_MEMORIES = """
CREATE TABLE IF NOT EXISTS user_memories (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    memory_type TEXT NOT NULL,          -- 'journal', 'evaluation', 'report', 'conversation'
    content TEXT NOT NULL,              -- JSON: detailed content
    summary TEXT,                       -- compressed summary (for long-term memory)
    ticker TEXT,
    ticker_name TEXT,
    market_type TEXT DEFAULT 'kr',      -- 'kr' or 'us'
    importance_score REAL DEFAULT 0.5,
    compression_layer INTEGER DEFAULT 1, -- 1=detailed, 2=summary, 3=compressed
    created_at TEXT NOT NULL,
    last_accessed_at TEXT,
    command_source TEXT,
    message_id INTEGER,
    tags TEXT                           -- JSON array
)
"""

# Table: user_preferences (user preference settings)
TABLE_USER_PREFERENCES = """
CREATE TABLE IF NOT EXISTS user_preferences (
    user_id INTEGER PRIMARY KEY,
    preferred_tone TEXT DEFAULT 'neutral',
    investment_style TEXT,
    favorite_tickers TEXT,              -- JSON array
    total_evaluations INTEGER DEFAULT 0,
    total_journals INTEGER DEFAULT 0,
    created_at TEXT NOT NULL,
    last_active_at TEXT
)
"""

# Indexes
INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_stock_holdings_account_key ON stock_holdings(account_key)",
    "CREATE INDEX IF NOT EXISTS idx_stock_holdings_account_ticker ON stock_holdings(account_key, ticker)",
    "CREATE INDEX IF NOT EXISTS idx_trading_history_account_key ON trading_history(account_key)",
    "CREATE INDEX IF NOT EXISTS idx_journal_ticker ON trading_journal(ticker)",
    "CREATE INDEX IF NOT EXISTS idx_journal_pattern ON trading_journal(pattern_tags)",
    "CREATE INDEX IF NOT EXISTS idx_journal_date ON trading_journal(trade_date)",
    "CREATE INDEX IF NOT EXISTS idx_intuitions_category ON trading_intuitions(category)",
    "CREATE INDEX IF NOT EXISTS idx_intuitions_scope ON trading_intuitions(scope)",
    "CREATE INDEX IF NOT EXISTS idx_principles_scope ON trading_principles(scope)",
    "CREATE INDEX IF NOT EXISTS idx_principles_priority ON trading_principles(priority)",
    # User memory indexes
    "CREATE INDEX IF NOT EXISTS idx_memories_user ON user_memories(user_id)",
    "CREATE INDEX IF NOT EXISTS idx_memories_type ON user_memories(user_id, memory_type)",
    "CREATE INDEX IF NOT EXISTS idx_memories_ticker ON user_memories(user_id, ticker)",
    "CREATE INDEX IF NOT EXISTS idx_memories_created ON user_memories(user_id, created_at DESC)",
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


def _build_column_projection(source_columns: list[str], target_columns: list[str], defaults: dict[str, str]) -> list[str]:
    projection = []
    for column in target_columns:
        if column in source_columns:
            projection.append(column)
        else:
            projection.append(f"{defaults[column]} AS {column}")
    return projection


def _get_primary_account_scope() -> tuple[str, str]:
    try:
        from trading import kis_auth as ka

        default_mode = str(ka.getEnv().get("default_mode", "demo")).strip().lower()
        svr = "vps" if default_mode == "demo" else "prod"
        primary_account = ka.resolve_account(svr=svr, market="kr")
        return primary_account["account_key"], primary_account["name"]
    except Exception as exc:
        logger.warning(f"Falling back to legacy KR account scope during migration: {exc}")
        return "legacy:kr:default", "legacy-primary-kr"


def _rebuild_stock_holdings_for_multi_account(cursor, conn):
    if not _table_exists(cursor, "stock_holdings"):
        return

    columns = _get_columns(cursor, "stock_holdings")
    if "account_key" in columns and "id" in columns:
        return

    logger.info("Migrating stock_holdings to multi-account schema")
    account_key, account_name = _get_primary_account_scope()
    legacy_table = "stock_holdings_legacy"

    cursor.execute(f"DROP TABLE IF EXISTS {legacy_table}")
    cursor.execute(f"ALTER TABLE stock_holdings RENAME TO {legacy_table}")
    cursor.execute(TABLE_STOCK_HOLDINGS)

    target_columns = [
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
    ]
    defaults = {
        "account_key": f"'{account_key}'",
        "account_name": f"'{account_name}'",
        "current_price": "NULL",
        "last_updated": "NULL",
        "scenario": "NULL",
        "target_price": "NULL",
        "stop_loss": "NULL",
        "trigger_type": "NULL",
        "trigger_mode": "NULL",
        "sector": "NULL",
    }
    source_columns = _get_columns(cursor, legacy_table)
    projection = _build_column_projection(source_columns, target_columns, defaults)
    cursor.execute(
        f"""
        INSERT INTO stock_holdings ({", ".join(target_columns)})
        SELECT {", ".join(projection)}
        FROM {legacy_table}
        """
    )
    cursor.execute(f"DROP TABLE {legacy_table}")
    conn.commit()


def _rebuild_trading_history_for_multi_account(cursor, conn):
    if not _table_exists(cursor, "trading_history"):
        return

    columns = _get_columns(cursor, "trading_history")
    if "account_key" in columns and "account_name" in columns:
        return

    logger.info("Migrating trading_history to multi-account schema")
    account_key, account_name = _get_primary_account_scope()
    legacy_table = "trading_history_legacy"

    cursor.execute(f"DROP TABLE IF EXISTS {legacy_table}")
    cursor.execute(f"ALTER TABLE trading_history RENAME TO {legacy_table}")
    cursor.execute(TABLE_TRADING_HISTORY)

    target_columns = [
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
    ]
    defaults = {
        "account_key": f"'{account_key}'",
        "account_name": f"'{account_name}'",
        "scenario": "NULL",
        "trigger_type": "NULL",
        "trigger_mode": "NULL",
        "sector": "NULL",
    }
    source_columns = _get_columns(cursor, legacy_table)
    projection = _build_column_projection(source_columns, target_columns, defaults)
    cursor.execute(
        f"""
        INSERT INTO trading_history ({", ".join(target_columns)})
        SELECT {", ".join(projection)}
        FROM {legacy_table}
        """
    )
    cursor.execute(f"DROP TABLE {legacy_table}")
    conn.commit()


def migrate_multi_account_schema(cursor, conn):
    _rebuild_stock_holdings_for_multi_account(cursor, conn)
    _rebuild_trading_history_for_multi_account(cursor, conn)


def create_all_tables(cursor, conn):
    """
    Create all database tables.

    Args:
        cursor: SQLite cursor
        conn: SQLite connection
    """
    tables = [
        TABLE_STOCK_HOLDINGS,
        TABLE_TRADING_HISTORY,
        TABLE_TRADING_JOURNAL,
        TABLE_TRADING_INTUITIONS,
        TABLE_TRADING_PRINCIPLES,
        TABLE_USER_MEMORIES,
        TABLE_USER_PREFERENCES,
    ]

    for table_sql in tables:
        cursor.execute(table_sql)

    migrate_multi_account_schema(cursor, conn)
    conn.commit()
    logger.info("Database tables created")


def create_indexes(cursor, conn):
    """
    Create all indexes.

    Args:
        cursor: SQLite cursor
        conn: SQLite connection
    """
    for index_sql in INDEXES:
        cursor.execute(index_sql)

    conn.commit()
    logger.info("Database indexes created")


def add_scope_column_if_missing(cursor, conn):
    """
    Add scope column to trading_intuitions if not exists (migration).

    Args:
        cursor: SQLite cursor
        conn: SQLite connection
    """
    try:
        cursor.execute("ALTER TABLE trading_intuitions ADD COLUMN scope TEXT DEFAULT 'universal'")
        conn.commit()
        logger.info("Added scope column to trading_intuitions table")
    except Exception:
        pass  # Column already exists


def add_trigger_columns_if_missing(cursor, conn):
    """
    Add trigger_type, trigger_mode columns to stock_holdings and trading_history
    if they don't exist (migration for v1.16.5).

    Args:
        cursor: SQLite cursor
        conn: SQLite connection
    """
    tables = ["stock_holdings", "trading_history"]
    columns = ["trigger_type TEXT", "trigger_mode TEXT"]

    for table in tables:
        for col_def in columns:
            col_name = col_def.split()[0]
            try:
                cursor.execute(f"ALTER TABLE {table} ADD COLUMN {col_def}")
                conn.commit()
                logger.info(f"Added {col_name} column to {table} table")
            except Exception:
                pass  # Column already exists


def add_sector_column_if_missing(cursor, conn):
    """
    Add sector column to stock_holdings and trading_history if missing.

    This migration ensures the sector column exists for AI agents that
    need to analyze sector concentration in portfolios.

    Args:
        cursor: SQLite cursor
        conn: SQLite connection
    """
    tables = ["stock_holdings", "trading_history"]

    for table in tables:
        try:
            cursor.execute(f"ALTER TABLE {table} ADD COLUMN sector TEXT")
            conn.commit()
            logger.info(f"Added sector column to {table} table")
        except Exception:
            pass  # Column already exists
