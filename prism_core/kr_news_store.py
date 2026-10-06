"""Local store of the KIS whole-market headline feed (titles only).

Kept in its own SQLite file under `runtime/` — not in stock_tracking_db, which
is copied to other servers several times a day. One row per KIS serial, plus
the stocks KIS tagged on it. Headlines are evidence, never instructions.
"""

import os
import sqlite3
from contextlib import closing
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PATH = ROOT / "runtime" / "kr_news_titles.sqlite"

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


def db_path():
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
    serials = list(serials)
    if not serials:
        return set()
    marks = ",".join("?" * len(serials))
    return {r[0] for r in conn.execute(f"SELECT serial FROM news_titles WHERE serial IN ({marks})", serials)}


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


def search(conn, *, keywords=(), ticker=None, since=None, until=None, limit=50):
    """Headlines newest first. `keywords` match any (title substring); `ticker` matches KIS tags."""
    where, args = [], []
    if since:
        where.append("t.published_at >= ?")
        args.append(since)
    if until:
        where.append("t.published_at <= ?")
        args.append(until)
    words = [w for w in (k.strip() for k in keywords) if w]
    if words:
        where.append("(" + " OR ".join("t.title LIKE ?" for _ in words) + ")")
        args.extend(f"%{w}%" for w in words)
    if ticker:
        where.append("t.serial IN (SELECT serial FROM news_title_tickers WHERE ticker = ?)")
        args.append(ticker)
    sql = ("SELECT t.serial, t.published_at, t.provider, t.title, "
           "(SELECT group_concat(name, ',') FROM (SELECT name FROM news_title_tickers k "
           " WHERE k.serial = t.serial ORDER BY rank)) AS tag_names "
           "FROM news_titles t" + (" WHERE " + " AND ".join(where) if where else "") +
           " ORDER BY t.published_at DESC LIMIT ?")
    args.append(int(limit))
    with closing(conn.execute(sql, args)) as cur:
        return [dict(r) for r in cur.fetchall()]
