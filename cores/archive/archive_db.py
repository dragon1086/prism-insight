"""
archive_db.py — SQLite + FTS5 store for PRISM report archive.

Tables:
  report_archive      — raw markdown reports (FTS5 indexed)
  report_archive_fts  — FTS5 virtual table (unicode61 tokenizer, Korean/EN)
  report_enrichment   — KIS API / yfinance 후행 data (returns, stop-loss, market phase)
  market_timeline     — daily KOSPI/S&P500 index + market phase per date
  insights            — on-demand LLM query cache
"""

import hashlib
import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

import aiosqlite

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).parent.parent.parent
ARCHIVE_DB_PATH = PROJECT_ROOT / "archive.db"


_FTS_OPS = frozenset({"AND", "OR", "NOT"})


def _sanitize_fts_query(query: str) -> str:
    """
    Prevent FTS5 MATCH injection by quoting every non-operator token.

    Recognized FTS5 boolean operators (AND/OR/NOT) are passed through;
    all other tokens are individually double-quoted to prevent injection.
    Wildcard ``*`` is appended to the preceding quoted token when present.
    """
    tokens = query.strip().split()
    if not tokens:
        return '""'
    safe: list[str] = []
    for token in tokens:
        if token in _FTS_OPS:
            safe.append(token)
        elif token == "*":
            # Attach wildcard to preceding token: "반도"*
            if safe:
                safe[-1] += "*"
        else:
            escaped = token.replace('"', '""')
            safe.append(f'"{escaped}"')
    return " ".join(safe) if safe else '""'


# ---------------------------------------------------------------------------
# DDL
# ---------------------------------------------------------------------------

_DDL_REPORT_ARCHIVE = """
CREATE TABLE IF NOT EXISTS report_archive (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker          TEXT NOT NULL,
    company_name    TEXT NOT NULL,
    report_date     TEXT NOT NULL,
    mode            TEXT NOT NULL,
    model           TEXT NOT NULL,
    market          TEXT NOT NULL,
    language        TEXT DEFAULT 'ko',
    file_path       TEXT NOT NULL,
    file_hash       TEXT NOT NULL,
    content         TEXT NOT NULL,
    content_length  INTEGER,
    created_at      TEXT DEFAULT (datetime('now', 'localtime')),
    UNIQUE(ticker, report_date, mode, market, language)
)
"""

_DDL_REPORT_ARCHIVE_FTS = """
CREATE VIRTUAL TABLE IF NOT EXISTS report_archive_fts USING fts5(
    ticker,
    company_name,
    content,
    tokenize='unicode61 remove_diacritics 1'
)
"""

_DDL_REPORT_ENRICHMENT = """
CREATE TABLE IF NOT EXISTS report_enrichment (
    report_id           INTEGER PRIMARY KEY REFERENCES report_archive(id),
    ticker              TEXT NOT NULL,
    market              TEXT NOT NULL,
    analysis_date       TEXT NOT NULL,
    price_at_analysis   REAL,
    index_at_analysis   REAL,
    index_change_20d    REAL,
    market_phase        TEXT,
    return_7d           REAL,
    return_14d          REAL,
    return_30d          REAL,
    return_60d          REAL,
    return_90d          REAL,
    stop_loss_price     REAL,
    stop_loss_triggered INTEGER DEFAULT 0,
    stop_loss_date      TEXT,
    post_stop_30d       REAL,
    post_stop_60d       REAL,
    stop_was_correct    INTEGER,
    target_1_price      REAL,
    target_1_hit        INTEGER DEFAULT 0,
    days_to_target_1    INTEGER,
    enriched_at         TEXT DEFAULT (datetime('now', 'localtime')),
    data_source         TEXT
)
"""

_DDL_MARKET_TIMELINE = """
CREATE TABLE IF NOT EXISTS market_timeline (
    date            TEXT NOT NULL,
    market          TEXT NOT NULL,
    index_close     REAL,
    index_change    REAL,
    market_phase    TEXT,
    report_count    INTEGER DEFAULT 0,
    PRIMARY KEY (date, market)
)
"""

_DDL_INSIGHTS = """
CREATE TABLE IF NOT EXISTS insights (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    query           TEXT NOT NULL,
    query_hash      TEXT NOT NULL,
    insight_text    TEXT NOT NULL,
    evidence_ids    TEXT,
    insight_type    TEXT,
    market          TEXT,
    model_used      TEXT,
    created_at      TEXT DEFAULT (datetime('now', 'localtime')),
    expires_at      TEXT
)
"""

_DDL_TICKER_PRICE_HISTORY = """
CREATE TABLE IF NOT EXISTS ticker_price_history (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    report_id   INTEGER NOT NULL REFERENCES report_archive(id),
    ticker      TEXT NOT NULL,
    market      TEXT NOT NULL,
    price_date  TEXT NOT NULL,
    close       REAL NOT NULL,
    return_pct  REAL,
    UNIQUE(report_id, price_date)
)
"""

# ---------------------------------------------------------------------------
# Persistent insight layer (accumulated /insight Q&A, weekly summaries, quotas)
# ---------------------------------------------------------------------------

_DDL_PERSISTENT_INSIGHTS = """
CREATE TABLE IF NOT EXISTS persistent_insights (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id             INTEGER,
    chat_id             INTEGER,
    question            TEXT NOT NULL,
    answer              TEXT NOT NULL,
    key_takeaways       TEXT NOT NULL,
    tools_used          TEXT,
    tickers_mentioned   TEXT,
    evidence_report_ids TEXT,
    embedding           BLOB,
    model_used          TEXT,
    previous_insight_id INTEGER,
    superseded_by       INTEGER,
    created_at          TEXT DEFAULT (datetime('now', 'localtime'))
)
"""

_DDL_PERSISTENT_INSIGHTS_FTS = """
CREATE VIRTUAL TABLE IF NOT EXISTS persistent_insights_fts USING fts5(
    question, key_takeaways,
    content='persistent_insights', content_rowid='id',
    tokenize='unicode61 remove_diacritics 1'
)
"""

_DDL_PERSISTENT_INSIGHTS_TRIGGER_AI = """
CREATE TRIGGER IF NOT EXISTS persistent_insights_ai
AFTER INSERT ON persistent_insights BEGIN
    INSERT INTO persistent_insights_fts(rowid, question, key_takeaways)
    VALUES (new.id, new.question, new.key_takeaways);
END
"""

_DDL_PERSISTENT_INSIGHTS_TRIGGER_AD = """
CREATE TRIGGER IF NOT EXISTS persistent_insights_ad
AFTER DELETE ON persistent_insights BEGIN
    INSERT INTO persistent_insights_fts(persistent_insights_fts, rowid, question, key_takeaways)
    VALUES ('delete', old.id, old.question, old.key_takeaways);
END
"""

_DDL_PERSISTENT_INSIGHTS_TRIGGER_AU = """
CREATE TRIGGER IF NOT EXISTS persistent_insights_au
AFTER UPDATE ON persistent_insights BEGIN
    INSERT INTO persistent_insights_fts(persistent_insights_fts, rowid, question, key_takeaways)
    VALUES ('delete', old.id, old.question, old.key_takeaways);
    INSERT INTO persistent_insights_fts(rowid, question, key_takeaways)
    VALUES (new.id, new.question, new.key_takeaways);
END
"""

_DDL_WEEKLY_INSIGHT_SUMMARY = """
CREATE TABLE IF NOT EXISTS weekly_insight_summary (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    week_start         TEXT NOT NULL UNIQUE,
    week_end           TEXT NOT NULL,
    summary_text       TEXT NOT NULL,
    source_insight_ids TEXT,
    insight_count      INTEGER,
    top_tickers        TEXT,
    created_at         TEXT DEFAULT (datetime('now', 'localtime'))
)
"""

_DDL_INSIGHT_TOOL_USAGE = """
CREATE TABLE IF NOT EXISTS insight_tool_usage (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    insight_id INTEGER NOT NULL,
    tool_name  TEXT NOT NULL,
    call_count INTEGER DEFAULT 1,
    created_at TEXT DEFAULT (datetime('now', 'localtime'))
)
"""

_DDL_USER_INSIGHT_QUOTA = """
CREATE TABLE IF NOT EXISTS user_insight_quota (
    user_id INTEGER NOT NULL,
    date    TEXT NOT NULL,
    count   INTEGER DEFAULT 0,
    PRIMARY KEY (user_id, date)
)
"""

_DDL_INSIGHT_COST_DAILY = """
CREATE TABLE IF NOT EXISTS insight_cost_daily (
    date             TEXT PRIMARY KEY,
    input_tokens     INTEGER DEFAULT 0,
    output_tokens    INTEGER DEFAULT 0,
    embedding_tokens INTEGER DEFAULT 0,
    perplexity_calls INTEGER DEFAULT 0,
    firecrawl_calls  INTEGER DEFAULT 0
)
"""

_DDL_INSIGHT_METRICS_VIEW = """
CREATE VIEW IF NOT EXISTS insight_metrics_daily AS
SELECT
    DATE(created_at) AS d,
    COUNT(*)                 AS queries,
    COUNT(DISTINCT user_id)  AS unique_users,
    AVG(LENGTH(answer))      AS avg_answer_len
FROM persistent_insights
GROUP BY DATE(created_at)
"""

# ---------------------------------------------------------------------------
# Self-improvement layer (Phase B):
#   - insight_feedback           — DPO-lite user signals (👍/👎)
#   - ticker_semantic_facts      — distilled per-ticker facts (Mem0 pattern)
#   - confidence_score column on persistent_insights (boost/deboost retrieval)
# ---------------------------------------------------------------------------

_DDL_INSIGHT_FEEDBACK = """
CREATE TABLE IF NOT EXISTS insight_feedback (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    insight_id  INTEGER NOT NULL,
    user_id     INTEGER NOT NULL,
    score       INTEGER NOT NULL,            -- +1 (good), -1 (bad)
    reason      TEXT,
    created_at  TEXT DEFAULT (datetime('now', 'localtime')),
    UNIQUE(insight_id, user_id)              -- one vote per user per insight
)
"""

_DDL_TICKER_SEMANTIC_FACTS = """
CREATE TABLE IF NOT EXISTS ticker_semantic_facts (
    id                     INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker                 TEXT NOT NULL,
    fact_text              TEXT NOT NULL,             -- distilled fact
    fact_category          TEXT,                      -- 'fundamental'|'momentum'|'risk'|'sentiment'|'thesis'
    confidence             REAL DEFAULT 0.5,          -- 0.0~1.0
    supporting_insight_ids TEXT,                      -- JSON array
    supporting_report_ids  TEXT,                      -- JSON array
    last_validated_at      TEXT DEFAULT (datetime('now', 'localtime')),
    superseded_by          INTEGER,                   -- conflict resolution → another fact id
    created_at             TEXT DEFAULT (datetime('now', 'localtime'))
)
"""

# Migration: add confidence_score column to persistent_insights if missing
_PERSISTENT_INSIGHTS_NEW_COLUMNS = [
    ("confidence_score", "REAL DEFAULT 0.0"),
]


async def _migrate_persistent_insights_columns(db) -> None:
    for col_name, col_type in _PERSISTENT_INSIGHTS_NEW_COLUMNS:
        try:
            await db.execute(
                f"ALTER TABLE persistent_insights ADD COLUMN {col_name} {col_type}"
            )
        except Exception:
            pass  # column already exists

# New long-term performance columns for report_enrichment (added via migration)
_ENRICHMENT_PERF_COLUMNS = [
    ("return_current",    "REAL"),
    ("price_current",     "REAL"),
    ("return_180d",       "REAL"),
    ("return_365d",       "REAL"),
    ("max_return_since",  "REAL"),
    ("max_return_date",   "TEXT"),
    ("max_drawdown",      "REAL"),
    ("max_drawdown_date", "TEXT"),
    ("drawdown_from_peak","REAL"),
    ("last_price_update", "TEXT"),
]


# ---------------------------------------------------------------------------
# DB init
# ---------------------------------------------------------------------------

_initialized_paths: set = set()


async def _migrate_enrichment_columns(db) -> None:
    """Add long-term performance columns to report_enrichment if missing."""
    for col_name, col_type in _ENRICHMENT_PERF_COLUMNS:
        try:
            await db.execute(f"ALTER TABLE report_enrichment ADD COLUMN {col_name} {col_type}")
        except Exception:
            pass  # Column already exists


async def init_db(db_path: Optional[str] = None) -> None:
    """Create all tables and indexes. Runs DDL at most once per process per db_path."""
    path = db_path or str(ARCHIVE_DB_PATH)
    if path in _initialized_paths:
        return
    async with aiosqlite.connect(path) as db:
        await db.execute("PRAGMA journal_mode=WAL")
        await db.execute("PRAGMA foreign_keys=ON")
        await db.execute(_DDL_REPORT_ARCHIVE)
        await db.execute(_DDL_REPORT_ARCHIVE_FTS)
        await db.execute(_DDL_REPORT_ENRICHMENT)
        await db.execute(_DDL_MARKET_TIMELINE)
        await db.execute(_DDL_INSIGHTS)
        await db.execute(_DDL_TICKER_PRICE_HISTORY)
        # Persistent insight layer
        await db.execute(_DDL_PERSISTENT_INSIGHTS)
        await db.execute(_DDL_PERSISTENT_INSIGHTS_FTS)
        await db.execute(_DDL_PERSISTENT_INSIGHTS_TRIGGER_AI)
        await db.execute(_DDL_PERSISTENT_INSIGHTS_TRIGGER_AD)
        await db.execute(_DDL_PERSISTENT_INSIGHTS_TRIGGER_AU)
        await db.execute(_DDL_WEEKLY_INSIGHT_SUMMARY)
        await db.execute(_DDL_INSIGHT_TOOL_USAGE)
        await db.execute(_DDL_USER_INSIGHT_QUOTA)
        await db.execute(_DDL_INSIGHT_COST_DAILY)
        await db.execute(_DDL_INSIGHT_METRICS_VIEW)
        # Self-improvement layer (Phase B)
        await db.execute(_DDL_INSIGHT_FEEDBACK)
        await db.execute(_DDL_TICKER_SEMANTIC_FACTS)
        # Indexes
        await db.execute("CREATE INDEX IF NOT EXISTS idx_ra_ticker ON report_archive(ticker)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_ra_date ON report_archive(report_date)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_ra_market ON report_archive(market)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_tph_ticker_date ON ticker_price_history(ticker, price_date)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_pi_chat ON persistent_insights(chat_id, created_at DESC)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_pi_created ON persistent_insights(created_at DESC)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_wis_week ON weekly_insight_summary(week_start DESC)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_itu_insight ON insight_tool_usage(insight_id)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_if_insight ON insight_feedback(insight_id)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_tsf_ticker ON ticker_semantic_facts(ticker, last_validated_at DESC)")
        await _migrate_enrichment_columns(db)
        await _migrate_persistent_insights_columns(db)
        await db.commit()
    _initialized_paths.add(path)


def _sha256_short(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()[:16]


# ---------------------------------------------------------------------------
# report_archive CRUD
# ---------------------------------------------------------------------------

async def insert_report(
    ticker: str,
    company_name: str,
    report_date: str,
    mode: str,
    model: str,
    market: str,
    file_path: str,
    content: str,
    language: str = "ko",
    db_path: Optional[str] = None,
) -> Optional[int]:
    """
    Insert a report into the archive.
    Returns row ID of new or existing duplicate record, None on unexpected failure.
    """
    path = db_path or str(ARCHIVE_DB_PATH)
    file_hash = _sha256_short(content)

    async with aiosqlite.connect(path) as db:
        await db.execute("PRAGMA foreign_keys=ON")
        # IntegrityError scope limited to main INSERT only (duplicate detection)
        try:
            cur = await db.execute(
                """
                INSERT INTO report_archive
                    (ticker, company_name, report_date, mode, model, market,
                     language, file_path, file_hash, content, content_length)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (ticker, company_name, report_date, mode, model, market,
                 language, file_path, file_hash, content, len(content)),
            )
            report_id = cur.lastrowid
        except aiosqlite.IntegrityError:
            cur = await db.execute(
                "SELECT id FROM report_archive WHERE ticker=? AND report_date=? AND mode=? AND market=? AND language=?",
                (ticker, report_date, mode, market, language),
            )
            row = await cur.fetchone()
            if row:
                logger.debug(f"[{market.upper()}] Already archived: {ticker} {report_date}/{mode}")
                return row[0]
            return None

        # FTS index — outside IntegrityError scope
        await db.execute(
            "INSERT OR IGNORE INTO report_archive_fts(rowid, ticker, company_name, content) VALUES (?, ?, ?, ?)",
            (report_id, ticker, company_name, content),
        )
        await db.commit()
        logger.info(f"[{market.upper()}] Archived {ticker} {report_date}/{mode} → id={report_id}")
        return report_id


async def get_report_ids(
    ticker: Optional[str] = None,
    market: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    db_path: Optional[str] = None,
) -> List[Dict]:
    """Return list of report metadata dicts matching filters."""
    path = db_path or str(ARCHIVE_DB_PATH)
    clauses, params = [], []
    if ticker:
        clauses.append("ticker = ?"); params.append(ticker)
    if market:
        clauses.append("market = ?"); params.append(market)
    if date_from:
        clauses.append("report_date >= ?"); params.append(date_from)
    if date_to:
        clauses.append("report_date <= ?"); params.append(date_to)
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    async with aiosqlite.connect(path) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            f"SELECT id, ticker, company_name, report_date, mode, model, market FROM report_archive {where} ORDER BY report_date DESC",
            params,
        )
        rows = await cur.fetchall()
        return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# FTS5 search
# ---------------------------------------------------------------------------

async def search_fts(
    query: str,
    market: Optional[str] = None,
    limit: int = 20,
    db_path: Optional[str] = None,
) -> List[Dict]:
    """
    Full-text search across ticker, company name, and report content.

    Args:
        query: Search string (plain Korean/English or FTS5 boolean syntax)
        market: 'kr', 'us', or None for both
        limit: Max results
    """
    safe_query = _sanitize_fts_query(query)
    path = db_path or str(ARCHIVE_DB_PATH)
    try:
        async with aiosqlite.connect(path) as db:
            db.row_factory = aiosqlite.Row
            if market:
                cur = await db.execute(
                    """
                    SELECT ra.id, ra.ticker, ra.company_name, ra.report_date, ra.mode, ra.market,
                           snippet(report_archive_fts, 2, '[', ']', '...', 48) AS snippet
                    FROM report_archive_fts
                    JOIN report_archive ra ON ra.id = report_archive_fts.rowid
                    WHERE report_archive_fts MATCH ? AND ra.market = ?
                    ORDER BY rank
                    LIMIT ?
                    """,
                    (safe_query, market, limit),
                )
            else:
                cur = await db.execute(
                    """
                    SELECT ra.id, ra.ticker, ra.company_name, ra.report_date, ra.mode, ra.market,
                           snippet(report_archive_fts, 2, '[', ']', '...', 48) AS snippet
                    FROM report_archive_fts
                    JOIN report_archive ra ON ra.id = report_archive_fts.rowid
                    WHERE report_archive_fts MATCH ?
                    ORDER BY rank
                    LIMIT ?
                    """,
                    (safe_query, limit),
                )
            rows = await cur.fetchall()
            return [dict(r) for r in rows]
    except aiosqlite.OperationalError as e:
        logger.warning(f"FTS5 search failed for query {query!r}: {e}")
        return []


# ---------------------------------------------------------------------------
# report_enrichment CRUD
# ---------------------------------------------------------------------------

async def upsert_enrichment(
    report_id: int,
    data: Dict[str, Any],
    db_path: Optional[str] = None,
) -> None:
    """Insert or replace enrichment record for a report."""
    path = db_path or str(ARCHIVE_DB_PATH)
    row = dict(data)
    row["report_id"] = report_id
    async with aiosqlite.connect(path) as db:
        await db.execute("PRAGMA foreign_keys=ON")
        await db.execute(
            """
            INSERT OR REPLACE INTO report_enrichment
                (report_id, ticker, market, analysis_date,
                 price_at_analysis, index_at_analysis, index_change_20d, market_phase,
                 return_7d, return_14d, return_30d, return_60d, return_90d,
                 stop_loss_price, stop_loss_triggered, stop_loss_date,
                 post_stop_30d, post_stop_60d, stop_was_correct,
                 target_1_price, target_1_hit, days_to_target_1,
                 enriched_at, data_source)
            VALUES
                (:report_id, :ticker, :market, :analysis_date,
                 :price_at_analysis, :index_at_analysis, :index_change_20d, :market_phase,
                 :return_7d, :return_14d, :return_30d, :return_60d, :return_90d,
                 :stop_loss_price, :stop_loss_triggered, :stop_loss_date,
                 :post_stop_30d, :post_stop_60d, :stop_was_correct,
                 :target_1_price, :target_1_hit, :days_to_target_1,
                 datetime('now', 'localtime'), :data_source)
            """,
            row,
        )
        await db.commit()
        logger.debug(f"Enrichment saved: report_id={report_id}")


async def update_enrichment_performance(
    report_id: int,
    perf: Dict[str, Any],
    db_path: Optional[str] = None,
) -> None:
    """
    Update long-term performance columns in report_enrichment.

    perf keys: return_current, price_current, return_180d, return_365d,
               max_return_since, max_return_date, max_drawdown,
               max_drawdown_date, drawdown_from_peak, last_price_update
    """
    path = db_path or str(ARCHIVE_DB_PATH)
    cols = [c for c, _ in _ENRICHMENT_PERF_COLUMNS if c in perf]
    if not cols:
        return
    set_clause = ", ".join(f"{c} = ?" for c in cols)
    values = [perf[c] for c in cols] + [report_id]
    async with aiosqlite.connect(path) as db:
        await db.execute(
            f"UPDATE report_enrichment SET {set_clause} WHERE report_id = ?",
            values,
        )
        await db.commit()


async def bulk_upsert_price_history(
    rows: List[Dict[str, Any]],
    db_path: Optional[str] = None,
) -> int:
    """
    Insert or replace daily close rows into ticker_price_history.

    Each row dict: {report_id, ticker, market, price_date, close, return_pct}
    Returns count of rows written.
    """
    if not rows:
        return 0
    path = db_path or str(ARCHIVE_DB_PATH)
    async with aiosqlite.connect(path) as db:
        await db.execute("PRAGMA foreign_keys=ON")
        await db.executemany(
            """
            INSERT OR REPLACE INTO ticker_price_history
                (report_id, ticker, market, price_date, close, return_pct)
            VALUES (:report_id, :ticker, :market, :price_date, :close, :return_pct)
            """,
            rows,
        )
        await db.commit()
    return len(rows)


async def get_reports_for_price_update(
    market: Optional[str] = None,
    ticker: Optional[str] = None,
    db_path: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """
    Return reports that need long-term price update:
    - Have price_at_analysis (needed as base price)
    - Either last_price_update IS NULL or was > 6 days ago

    Returns list of dicts: {id, ticker, company_name, market, report_date, price_at_analysis}
    """
    path = db_path or str(ARCHIVE_DB_PATH)
    clauses = [
        "re.price_at_analysis IS NOT NULL",
        "(re.last_price_update IS NULL OR re.last_price_update < date('now', '-6 days'))",
    ]
    params: List[Any] = []
    if market:
        clauses.append("ra.market = ?")
        params.append(market)
    if ticker:
        clauses.append("ra.ticker = ?")
        params.append(ticker)
    where = " AND ".join(clauses)
    async with aiosqlite.connect(path) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            f"""
            SELECT ra.id, ra.ticker, ra.company_name, ra.market, ra.report_date,
                   re.price_at_analysis
            FROM report_archive ra
            JOIN report_enrichment re ON re.report_id = ra.id
            WHERE {where}
            ORDER BY ra.report_date DESC
            """,
            params,
        )
        rows = await cur.fetchall()
        return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# market_timeline CRUD
# ---------------------------------------------------------------------------

async def upsert_market_timeline(
    date: str,
    market: str,
    index_close: Optional[float] = None,
    index_change: Optional[float] = None,
    market_phase: Optional[str] = None,
    increment_report_count: bool = False,
    db_path: Optional[str] = None,
) -> None:
    path = db_path or str(ARCHIVE_DB_PATH)
    async with aiosqlite.connect(path) as db:
        await db.execute(
            """
            INSERT INTO market_timeline (date, market, index_close, index_change, market_phase, report_count)
            VALUES (?, ?, ?, ?, ?, 0)
            ON CONFLICT(date, market) DO UPDATE SET
                index_close  = COALESCE(excluded.index_close, market_timeline.index_close),
                index_change = COALESCE(excluded.index_change, market_timeline.index_change),
                market_phase = COALESCE(excluded.market_phase, market_timeline.market_phase)
            """,
            (date, market, index_close, index_change, market_phase),
        )
        if increment_report_count:
            await db.execute(
                "UPDATE market_timeline SET report_count = report_count + 1 WHERE date=? AND market=?",
                (date, market),
            )
        await db.commit()


# ---------------------------------------------------------------------------
# insights cache
# ---------------------------------------------------------------------------

async def get_cached_insight(query_hash: str, db_path: Optional[str] = None) -> Optional[str]:
    path = db_path or str(ARCHIVE_DB_PATH)
    async with aiosqlite.connect(path) as db:
        cur = await db.execute(
            """
            SELECT insight_text FROM insights
            WHERE query_hash = ?
              AND (expires_at IS NULL OR expires_at > datetime('now', 'localtime'))
            ORDER BY created_at DESC LIMIT 1
            """,
            (query_hash,),
        )
        row = await cur.fetchone()
        return row[0] if row else None


async def cache_insight(
    query: str,
    query_hash: str,
    insight_text: str,
    evidence_ids: Optional[List[int]] = None,
    insight_type: Optional[str] = None,
    market: Optional[str] = None,
    model_used: Optional[str] = None,
    expires_at: Optional[str] = None,
    db_path: Optional[str] = None,
) -> None:
    path = db_path or str(ARCHIVE_DB_PATH)
    evidence_json = json.dumps(evidence_ids) if evidence_ids else None
    async with aiosqlite.connect(path) as db:
        await db.execute(
            """
            INSERT INTO insights (query, query_hash, insight_text, evidence_ids,
                                  insight_type, market, model_used, expires_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (query, query_hash, insight_text, evidence_json,
             insight_type, market, model_used, expires_at),
        )
        await db.commit()
