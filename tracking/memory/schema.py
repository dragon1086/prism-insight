"""
Schema migrations for Memory V2.

Idempotent, additive-only. Each migration runs in isolation; already-applied
migrations are skipped via `memory_schema_version` tracking. Catches
`OperationalError: duplicate column` so re-runs on partially-migrated DBs are safe.
"""

import json
import logging
import sqlite3
from datetime import datetime
from typing import List, Tuple

logger = logging.getLogger(__name__)


# (version, sql) — additive only.
SCHEMA_MIGRATIONS: List[Tuple[int, str]] = [
    (1, "ALTER TABLE user_memories ADD COLUMN embedding BLOB"),
    (2, "ALTER TABLE user_memories ADD COLUMN embedding_model TEXT"),
    (3, "ALTER TABLE user_memories ADD COLUMN fact_extracted INTEGER DEFAULT 0"),
    (4, "ALTER TABLE user_memories ADD COLUMN sentiment TEXT"),
    (5, "ALTER TABLE user_memories ADD COLUMN outcome TEXT"),
    (6, """
        CREATE TABLE IF NOT EXISTS user_facts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            fact TEXT NOT NULL,
            category TEXT NOT NULL,
            confidence REAL DEFAULT 0.5,
            evidence_memory_ids TEXT,
            embedding BLOB,
            embedding_model TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            superseded_by INTEGER,
            active INTEGER DEFAULT 1
        )
    """),
    (7, """
        CREATE INDEX IF NOT EXISTS idx_facts_user ON user_facts(user_id, active);
        CREATE INDEX IF NOT EXISTS idx_facts_cat ON user_facts(user_id, category, active)
    """),
    (8, """
        CREATE VIRTUAL TABLE IF NOT EXISTS user_memories_fts USING fts5(
            content_text, ticker, ticker_name,
            content='', tokenize='unicode61'
        )
    """),
]


def _ensure_base_tables(conn: sqlite3.Connection) -> None:
    """Create V1 base tables if absent (so migrations have something to ALTER)."""
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS user_memories (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            memory_type TEXT NOT NULL,
            content TEXT NOT NULL,
            summary TEXT,
            ticker TEXT,
            ticker_name TEXT,
            market_type TEXT DEFAULT 'kr',
            importance_score REAL DEFAULT 0.5,
            compression_layer INTEGER DEFAULT 1,
            created_at TEXT NOT NULL,
            last_accessed_at TEXT,
            command_source TEXT,
            message_id INTEGER,
            tags TEXT
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS user_preferences (
            user_id INTEGER PRIMARY KEY,
            preferred_tone TEXT DEFAULT 'neutral',
            investment_style TEXT,
            favorite_tickers TEXT,
            total_evaluations INTEGER DEFAULT 0,
            total_journals INTEGER DEFAULT 0,
            created_at TEXT NOT NULL,
            last_active_at TEXT
        )
    """)
    for idx_sql in (
        "CREATE INDEX IF NOT EXISTS idx_memories_user ON user_memories(user_id)",
        "CREATE INDEX IF NOT EXISTS idx_memories_type ON user_memories(user_id, memory_type)",
        "CREATE INDEX IF NOT EXISTS idx_memories_ticker ON user_memories(user_id, ticker)",
        "CREATE INDEX IF NOT EXISTS idx_memories_created ON user_memories(user_id, created_at DESC)",
    ):
        cur.execute(idx_sql)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS memory_schema_version (
            version INTEGER PRIMARY KEY,
            applied_at TEXT
        )
    """)
    conn.commit()


def _applied_versions(conn: sqlite3.Connection) -> set:
    cur = conn.execute("SELECT version FROM memory_schema_version")
    return {row[0] for row in cur.fetchall()}


def _record_applied(conn: sqlite3.Connection, version: int) -> None:
    conn.execute(
        "INSERT OR IGNORE INTO memory_schema_version (version, applied_at) VALUES (?, ?)",
        (version, datetime.now().isoformat()),
    )
    conn.commit()


def run_migrations(conn: sqlite3.Connection) -> List[int]:
    """
    Apply all pending Memory V2 migrations idempotently.

    Returns: list of versions newly applied this call.
    """
    _ensure_base_tables(conn)
    applied = _applied_versions(conn)
    newly_applied: List[int] = []

    for version, sql in SCHEMA_MIGRATIONS:
        if version in applied:
            continue
        try:
            # Some migration blocks contain multiple statements separated by ';'.
            for stmt in [s.strip() for s in sql.split(";") if s.strip()]:
                try:
                    conn.execute(stmt)
                except sqlite3.OperationalError as e:
                    msg = str(e).lower()
                    # Re-run safety: column already added or table already exists.
                    if (
                        "duplicate column" in msg
                        or "already exists" in msg
                    ):
                        logger.debug(
                            "memory.schema.migration.idempotent_skip version=%d stmt=%s err=%s",
                            version, stmt[:60], e,
                        )
                        continue
                    raise
            conn.commit()
            _record_applied(conn, version)
            newly_applied.append(version)
            logger.info("memory.schema.migration.applied version=%d", version)
        except Exception as e:
            logger.error(
                "memory.schema.migration.failed version=%d err=%s", version, e
            )
            conn.rollback()
            # Continue; partial migration leaves DB in safe state thanks to ADD COLUMN atomicity.

    return newly_applied


def bootstrap_fts(conn: sqlite3.Connection, batch_size: int = 1000) -> int:
    """
    Populate user_memories_fts from existing user_memories rows that aren't indexed yet.

    Uses the FTS5 contentless mode (rowid is the user_memories.id).
    Returns total rows inserted.
    """
    # Find rows whose id is not yet in the FTS table.
    cur = conn.execute("""
        SELECT m.id,
               COALESCE(json_extract(m.content, '$.text'), m.content),
               m.ticker,
               m.ticker_name
        FROM user_memories m
        WHERE m.id NOT IN (SELECT rowid FROM user_memories_fts)
        ORDER BY m.id
    """)

    inserted = 0
    batch: List[Tuple] = []
    for row in cur.fetchall():
        memory_id, content_text, ticker, ticker_name = row
        if content_text is None:
            content_text = ""
        batch.append((memory_id, content_text, ticker or "", ticker_name or ""))
        if len(batch) >= batch_size:
            conn.executemany(
                "INSERT INTO user_memories_fts(rowid, content_text, ticker, ticker_name) "
                "VALUES (?, ?, ?, ?)",
                batch,
            )
            inserted += len(batch)
            batch = []
    if batch:
        conn.executemany(
            "INSERT INTO user_memories_fts(rowid, content_text, ticker, ticker_name) "
            "VALUES (?, ?, ?, ?)",
            batch,
        )
        inserted += len(batch)
    conn.commit()
    if inserted:
        logger.info("memory.schema.bootstrap_fts.inserted count=%d", inserted)
    return inserted


def fts_insert(
    conn: sqlite3.Connection,
    memory_id: int,
    content_text: str,
    ticker: str = "",
    ticker_name: str = "",
) -> None:
    """Insert a single row into the FTS5 contentless table."""
    try:
        conn.execute(
            "INSERT INTO user_memories_fts(rowid, content_text, ticker, ticker_name) "
            "VALUES (?, ?, ?, ?)",
            (memory_id, content_text or "", ticker or "", ticker_name or ""),
        )
        conn.commit()
    except sqlite3.OperationalError as e:
        logger.warning("memory.schema.fts_insert.failed id=%d err=%s", memory_id, e)


def extract_text_from_content(content_json: str) -> str:
    """Best-effort: pull a searchable text blob from a content JSON string."""
    try:
        obj = json.loads(content_json)
    except Exception:
        return content_json or ""
    if isinstance(obj, dict):
        for key in ("text", "raw_input", "response_summary", "summary"):
            v = obj.get(key)
            if isinstance(v, str) and v:
                return v
        # last resort — concat all string values
        return " ".join(str(v) for v in obj.values() if isinstance(v, str))
    return str(obj)
