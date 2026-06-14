# collector/store.py — SQLite persistence for kline data
from __future__ import annotations

import sqlite3
import logging
from pathlib import Path

log = logging.getLogger(__name__)

CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS klines (
    timeframe  TEXT    NOT NULL,
    open_time  INTEGER NOT NULL,
    open       REAL    NOT NULL,
    high       REAL    NOT NULL,
    low        REAL    NOT NULL,
    close      REAL    NOT NULL,
    volume     REAL    NOT NULL,
    turnover   REAL    NOT NULL,
    confirmed  INTEGER NOT NULL DEFAULT 1,
    PRIMARY KEY (timeframe, open_time)
)
"""

UPSERT = """
INSERT INTO klines (timeframe, open_time, open, high, low, close, volume, turnover, confirmed)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
ON CONFLICT(timeframe, open_time) DO UPDATE SET
    open      = excluded.open,
    high      = excluded.high,
    low       = excluded.low,
    close     = excluded.close,
    volume    = excluded.volume,
    turnover  = excluded.turnover,
    confirmed = excluded.confirmed
"""


def _get_db_path(db_path: str | Path | None = None) -> Path:
    if db_path is not None:
        return Path(db_path)
    # Default: prism-btc/state/btc_market.db relative to this file's package root
    # (비트코인 시세 원본 DB — 루트 stock_tracking_db.sqlite(거래/일지 장부)와 구분)
    return Path(__file__).parent.parent / "state" / "btc_market.db"


def get_connection(db_path: str | Path | None = None) -> sqlite3.Connection:
    """Return an open SQLite connection with WAL mode enabled."""
    path = _get_db_path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute(CREATE_TABLE)
    conn.commit()
    return conn


def upsert_rows(
    conn: sqlite3.Connection,
    tf: str,
    rows: list[list[str]],
    *,
    current_open_time: int | None = None,
) -> int:
    """
    Upsert a batch of raw Bybit kline rows for timeframe `tf`.

    Bybit row format (newest first):
      [startTime, openPrice, highPrice, lowPrice, closePrice, volume, turnover]

    The most recent candle (smallest open_time among the *current* batch that
    equals current_open_time) is marked confirmed=0 (in-progress).

    Returns number of rows upserted.
    """
    records = []
    for row in rows:
        open_time = int(row[0])
        confirmed = 0 if (current_open_time is not None and open_time == current_open_time) else 1
        records.append((
            tf,
            open_time,
            float(row[1]),
            float(row[2]),
            float(row[3]),
            float(row[4]),
            float(row[5]),
            float(row[6]),
            confirmed,
        ))
    with conn:
        conn.executemany(UPSERT, records)
    return len(records)


def get_latest_open_time(conn: sqlite3.Connection, tf: str) -> int | None:
    """Return the most recent confirmed open_time for a timeframe, or None."""
    row = conn.execute(
        "SELECT MAX(open_time) FROM klines WHERE timeframe=? AND confirmed=1",
        (tf,),
    ).fetchone()
    return row[0] if row and row[0] is not None else None


def get_row_count(conn: sqlite3.Connection, tf: str) -> int:
    row = conn.execute(
        "SELECT COUNT(*) FROM klines WHERE timeframe=?", (tf,)
    ).fetchone()
    return row[0] if row else 0
