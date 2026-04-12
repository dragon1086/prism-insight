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


# ---------------------------------------------------------------------------
# DB init
# ---------------------------------------------------------------------------

_initialized_paths: set = set()


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
        await db.execute("CREATE INDEX IF NOT EXISTS idx_ra_ticker ON report_archive(ticker)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_ra_date ON report_archive(report_date)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_ra_market ON report_archive(market)")
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
