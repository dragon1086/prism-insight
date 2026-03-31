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

# Table: watchlist_history
TABLE_WATCHLIST_HISTORY = """
CREATE TABLE IF NOT EXISTS watchlist_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker TEXT NOT NULL,
    company_name TEXT NOT NULL,
    current_price REAL NOT NULL,
    analyzed_date TEXT NOT NULL,
    buy_score INTEGER NOT NULL,
    min_score INTEGER NOT NULL,
    decision TEXT NOT NULL,
    skip_reason TEXT NOT NULL,
    target_price REAL,
    stop_loss REAL,
    investment_period TEXT,
    sector TEXT,
    scenario TEXT,
    portfolio_analysis TEXT,
    valuation_analysis TEXT,
    sector_outlook TEXT,
    market_condition TEXT,
    rationale TEXT,
    trigger_type TEXT,
    trigger_mode TEXT,
    risk_reward_ratio REAL,
    was_traded INTEGER DEFAULT 0
)
"""

# Table: analysis_performance_tracker
TABLE_ANALYSIS_PERFORMANCE_TRACKER = """
CREATE TABLE IF NOT EXISTS analysis_performance_tracker (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    watchlist_id INTEGER,
    ticker TEXT NOT NULL,
    company_name TEXT,
    trigger_type TEXT,
    trigger_mode TEXT,
    analyzed_date TEXT NOT NULL,
    analyzed_price REAL,
    decision TEXT,
    was_traded INTEGER DEFAULT 0,
    skip_reason TEXT,
    buy_score REAL,
    min_score REAL,
    target_price REAL,
    stop_loss REAL,
    risk_reward_ratio REAL,
    tracked_7d_date TEXT,
    tracked_7d_price REAL,
    tracked_7d_return REAL,
    tracked_14d_date TEXT,
    tracked_14d_price REAL,
    tracked_14d_return REAL,
    tracked_30d_date TEXT,
    tracked_30d_price REAL,
    tracked_30d_return REAL,
    tracking_status TEXT DEFAULT 'pending',
    created_at TEXT,
    updated_at TEXT
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
    "CREATE INDEX IF NOT EXISTS idx_watchlist_ticker ON watchlist_history(ticker)",
    "CREATE INDEX IF NOT EXISTS idx_watchlist_date ON watchlist_history(analyzed_date)",
    "CREATE INDEX IF NOT EXISTS idx_watchlist_decision ON watchlist_history(decision)",
    "CREATE INDEX IF NOT EXISTS idx_perf_ticker ON analysis_performance_tracker(ticker)",
    "CREATE INDEX IF NOT EXISTS idx_perf_date ON analysis_performance_tracker(analyzed_date)",
    "CREATE INDEX IF NOT EXISTS idx_perf_status ON analysis_performance_tracker(tracking_status)",
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


def _get_copy_columns(source_columns: list[str], target_columns: list[str]) -> list[str]:
    return [column for column in target_columns if column in source_columns]


def _get_primary_account_scope() -> tuple[str, str]:
    try:
        from trading import kis_auth as ka

        default_mode = str(ka.getEnv().get("default_mode", "demo")).strip().lower()
        svr = "vps" if default_mode == "demo" else "prod"
        primary_account = ka.resolve_account(svr=svr, market="kr")
        return primary_account["account_key"], primary_account["name"]
    except Exception as exc:
        raise RuntimeError(
            "Unable to verify the primary account required for KR DB migration. "
            "Please ensure at least one account is configured in kis_devlp.yaml. "
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
    stock_defaults = history_defaults = None

    if _table_requires_migration(cursor, "stock_holdings", ["id", "account_key", "account_name"]):
        try:
            account_key, account_name = _get_primary_account_scope()
        except Exception as exc:
            raise RuntimeError(
                "Unable to verify the primary account required for KR DB migration. "
                "Please ensure at least one account is configured in kis_devlp.yaml. "
                f"Migration aborted to prevent data orphaning. Cause: {exc}"
            ) from exc
        stock_defaults = {
            "account_key": account_key,
            "account_name": account_name,
        }
        _rebuild_table(
            cursor,
            conn,
            "stock_holdings",
            TABLE_STOCK_HOLDINGS,
            [
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
            stock_defaults,
            ["id", "account_key", "account_name"],
        )

    if _table_requires_migration(cursor, "trading_history", ["account_key", "account_name"]):
        if history_defaults is None:
            if stock_defaults is None:
                try:
                    account_key, account_name = _get_primary_account_scope()
                except Exception as exc:
                    raise RuntimeError(
                        "Unable to verify the primary account required for KR DB migration. "
                        "Please ensure at least one account is configured in kis_devlp.yaml. "
                        f"Migration aborted to prevent data orphaning. Cause: {exc}"
                    ) from exc
            history_defaults = {
                "account_key": account_key,
                "account_name": account_name,
            }
        _rebuild_table(
            cursor,
            conn,
            "trading_history",
            TABLE_TRADING_HISTORY,
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
            history_defaults,
            ["account_key", "account_name"],
        )


def migrate_watchlist_history_columns(cursor, conn):
    migrations = [
        ("watchlist_history", "min_score INTEGER"),
        ("watchlist_history", "target_price REAL"),
        ("watchlist_history", "stop_loss REAL"),
        ("watchlist_history", "investment_period TEXT"),
        ("watchlist_history", "portfolio_analysis TEXT"),
        ("watchlist_history", "valuation_analysis TEXT"),
        ("watchlist_history", "sector_outlook TEXT"),
        ("watchlist_history", "market_condition TEXT"),
        ("watchlist_history", "rationale TEXT"),
        ("watchlist_history", "trigger_type TEXT"),
        ("watchlist_history", "trigger_mode TEXT"),
        ("watchlist_history", "risk_reward_ratio REAL"),
        ("watchlist_history", "was_traded INTEGER DEFAULT 0"),
        ("watchlist_history", "sector TEXT"),
    ]

    for table_name, column_def in migrations:
        try:
            cursor.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_def}")
            conn.commit()
            logger.info(f"Added column to {table_name}: {column_def}")
        except Exception as exc:
            if "duplicate column name" in str(exc).lower():
                logger.debug(f"Column already exists in {table_name}: {column_def}")
            else:
                logger.warning(f"Migration warning for {table_name}: {exc}")


def migrate_analysis_performance_tracker_columns(cursor, conn):
    migrations = [
        ("analysis_performance_tracker", "watchlist_id INTEGER"),
        ("analysis_performance_tracker", "company_name TEXT"),
        ("analysis_performance_tracker", "trigger_type TEXT"),
        ("analysis_performance_tracker", "trigger_mode TEXT"),
        ("analysis_performance_tracker", "decision TEXT"),
        ("analysis_performance_tracker", "was_traded INTEGER DEFAULT 0"),
        ("analysis_performance_tracker", "skip_reason TEXT"),
        ("analysis_performance_tracker", "buy_score REAL"),
        ("analysis_performance_tracker", "min_score REAL"),
        ("analysis_performance_tracker", "target_price REAL"),
        ("analysis_performance_tracker", "stop_loss REAL"),
        ("analysis_performance_tracker", "risk_reward_ratio REAL"),
        ("analysis_performance_tracker", "tracked_7d_date TEXT"),
        ("analysis_performance_tracker", "tracked_7d_price REAL"),
        ("analysis_performance_tracker", "tracked_7d_return REAL"),
        ("analysis_performance_tracker", "tracked_14d_date TEXT"),
        ("analysis_performance_tracker", "tracked_14d_price REAL"),
        ("analysis_performance_tracker", "tracked_14d_return REAL"),
        ("analysis_performance_tracker", "tracked_30d_date TEXT"),
        ("analysis_performance_tracker", "tracked_30d_price REAL"),
        ("analysis_performance_tracker", "tracked_30d_return REAL"),
        ("analysis_performance_tracker", "tracking_status TEXT DEFAULT 'pending'"),
        ("analysis_performance_tracker", "updated_at TEXT"),
    ]

    for table_name, column_def in migrations:
        try:
            cursor.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_def}")
            conn.commit()
            logger.info(f"Added column to {table_name}: {column_def}")
        except Exception as exc:
            if "duplicate column name" in str(exc).lower():
                logger.debug(f"Column already exists in {table_name}: {column_def}")
            else:
                logger.warning(f"Migration warning for {table_name}: {exc}")

    try:
        cursor.execute(
            """
            UPDATE analysis_performance_tracker
            SET tracking_status = CASE
                WHEN tracked_30d_return IS NOT NULL THEN 'completed'
                WHEN tracked_7d_return IS NOT NULL THEN 'in_progress'
                ELSE 'pending'
            END
            WHERE tracking_status IS NULL OR tracking_status = 'pending'
            """
        )
        conn.commit()
    except Exception as exc:
        logger.warning(f"Error updating KR tracking_status: {exc}")


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
        TABLE_WATCHLIST_HISTORY,
        TABLE_ANALYSIS_PERFORMANCE_TRACKER,
        TABLE_TRADING_JOURNAL,
        TABLE_TRADING_INTUITIONS,
        TABLE_TRADING_PRINCIPLES,
        TABLE_USER_MEMORIES,
        TABLE_USER_PREFERENCES,
    ]

    for table_sql in tables:
        cursor.execute(table_sql)

    migrate_multi_account_schema(cursor, conn)
    migrate_watchlist_history_columns(cursor, conn)
    migrate_analysis_performance_tracker_columns(cursor, conn)
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
