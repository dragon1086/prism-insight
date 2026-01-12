"""
Database Schema for Stock Tracking

Contains table creation SQL and index definitions.
Extracted from stock_tracking_agent.py for LLM context efficiency.
"""

import logging

logger = logging.getLogger(__name__)

# Table: stock_holdings
TABLE_STOCK_HOLDINGS = """
CREATE TABLE IF NOT EXISTS stock_holdings (
    ticker TEXT PRIMARY KEY,
    company_name TEXT NOT NULL,
    buy_price REAL NOT NULL,
    buy_date TEXT NOT NULL,
    current_price REAL,
    last_updated TEXT,
    scenario TEXT,
    target_price REAL,
    stop_loss REAL,
    trigger_type TEXT,
    trigger_mode TEXT
)
"""

# Table: trading_history
TABLE_TRADING_HISTORY = """
CREATE TABLE IF NOT EXISTS trading_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
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
    trigger_mode TEXT
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
    scope_context TEXT,  -- market='bull/bear', sector='반도체' etc.

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

# Indexes
INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_journal_ticker ON trading_journal(ticker)",
    "CREATE INDEX IF NOT EXISTS idx_journal_pattern ON trading_journal(pattern_tags)",
    "CREATE INDEX IF NOT EXISTS idx_journal_date ON trading_journal(trade_date)",
    "CREATE INDEX IF NOT EXISTS idx_intuitions_category ON trading_intuitions(category)",
    "CREATE INDEX IF NOT EXISTS idx_intuitions_scope ON trading_intuitions(scope)",
    "CREATE INDEX IF NOT EXISTS idx_principles_scope ON trading_principles(scope)",
    "CREATE INDEX IF NOT EXISTS idx_principles_priority ON trading_principles(priority)",
]


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
    ]

    for table_sql in tables:
        cursor.execute(table_sql)

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
