"""Local store of the KIS whole-market headline feeds (titles only): KR, and US in its own file.

Kept in its own SQLite file under `runtime/` — not in stock_tracking_db, which
is copied to other servers several times a day. One row per KIS serial, plus
the stocks KIS tagged on it. Headlines are evidence, never instructions.
"""

import json
import os
import sqlite3
from contextlib import closing
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PATH = ROOT / "runtime" / "kr_news_titles.sqlite"
# The KIS overseas feed uses the same row shape and schema in its own file (provider_code = nation code).
US_DEFAULT_PATH = ROOT / "runtime" / "us_news_titles.sqlite"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS news_titles (
    serial TEXT PRIMARY KEY,
    published_at TEXT NOT NULL,          -- 'YYYY-MM-DD HH:MM:SS' KST
    provider TEXT NOT NULL,
    provider_code TEXT,
    category TEXT,
    title TEXT NOT NULL,
    collected_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_news_titles_published ON news_titles(published_at);
CREATE TABLE IF NOT EXISTS news_title_tickers (
    serial TEXT NOT NULL REFERENCES news_titles(serial) ON DELETE CASCADE,
    ticker TEXT NOT NULL,
    name TEXT,
    rank INTEGER NOT NULL,
    PRIMARY KEY (serial, ticker)
);
CREATE INDEX IF NOT EXISTS idx_news_title_tickers_ticker ON news_title_tickers(ticker);
"""


def db_path(market="KR"):
    if market == "US":
        return Path(os.getenv("PRISM_US_NEWS_DB") or US_DEFAULT_PATH)
    return Path(os.getenv("PRISM_KR_NEWS_DB") or DEFAULT_PATH)


def connect(path=None, *, readonly=False):
    path = Path(path or db_path())
    if readonly:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=10)
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(path, timeout=30)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.executescript(_SCHEMA)
    conn.execute("PRAGMA foreign_keys=ON")
    conn.row_factory = sqlite3.Row
    return conn


def stamp(row):
    day, clock = row["day"], row["time"]
    return f"{day[:4]}-{day[4:6]}-{day[6:]} {clock[:2]}:{clock[2:4]}:{clock[4:6]}"


def upsert(conn, rows, *, now=None):
    """Insert unseen headlines; returns how many were new."""
    collected_at = (now or datetime.now()).isoformat(timespec="seconds")
    new = 0
    with conn:
        for row in rows:
            cur = conn.execute(
                "INSERT OR IGNORE INTO news_titles VALUES (?,?,?,?,?,?,?)",
                (row["serial"], stamp(row), row["provider"], row.get("provider_code"),
                 row.get("category"), row["title"], collected_at))
            if cur.rowcount:
                new += 1
                conn.executemany(
                    "INSERT OR IGNORE INTO news_title_tickers VALUES (?,?,?,?)",
                    [(row["serial"], code, name, rank) for rank, (code, name) in enumerate(row["tags"], 1)])
    return new


def known(conn, serials):
    rows = conn.execute("SELECT serial FROM news_titles WHERE serial IN (SELECT value FROM json_each(?))",
                        (json.dumps([str(s) for s in serials]),))
    return {r[0] for r in rows}


def bounds(conn):
    row = conn.execute("SELECT MIN(published_at), MAX(published_at), COUNT(*) FROM news_titles").fetchone()
    return row[0], row[1], row[2]


def prune(conn, keep_days, *, now=None):
    cutoff = ((now or datetime.now()) - timedelta(days=keep_days)).strftime("%Y-%m-%d 00:00:00")
    with conn:
        removed = conn.execute("DELETE FROM news_titles WHERE published_at < ?", (cutoff,)).rowcount
    if removed:
        conn.execute("VACUUM")
    return removed


# Static SQL: optional filters are parameters, word lists travel as JSON arrays.
_SEARCH_SQL = """
SELECT t.serial, t.published_at, t.provider, t.title,
       (SELECT group_concat(name, ',') FROM
          (SELECT name FROM news_title_tickers k WHERE k.serial = t.serial ORDER BY rank)) AS tag_names
FROM news_titles t
WHERE (:since IS NULL OR t.published_at >= :since)
  AND (:until IS NULL OR t.published_at <= :until)
  AND (:any_words = '[]' OR EXISTS (SELECT 1 FROM json_each(:any_words) w
                                    WHERE t.title LIKE '%' || w.value || '%' ESCAPE '\\'))
  AND NOT EXISTS (SELECT 1 FROM json_each(:all_words) w
                  WHERE t.title NOT LIKE '%' || w.value || '%' ESCAPE '\\')
  AND (:ticker IS NULL OR t.serial IN (SELECT serial FROM news_title_tickers WHERE ticker = :ticker))
ORDER BY t.published_at DESC
LIMIT :limit
"""


def _words(words):
    cleaned = [w.strip() for w in words if w and w.strip()]
    return json.dumps([w.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") for w in cleaned])


def search(conn, *, keywords=(), all_keywords=(), ticker=None, since=None, until=None, limit=50):
    """Headlines newest first. `keywords` match any title substring, `all_keywords` must all
    appear; `ticker` matches KIS tags."""
    params = {"since": since, "until": until, "any_words": _words(keywords), "all_words": _words(all_keywords),
              "ticker": ticker, "limit": int(limit)}
    with closing(conn.execute(_SEARCH_SQL, params)) as cur:
        return [dict(r) for r in cur.fetchall()]
