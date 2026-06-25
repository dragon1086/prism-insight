"""Portfolio-summary broadcast de-duplication (market-keyed debounce).

The realtime portfolio summary ("실시간 포트폴리오") is emitted from several
independent run-ends — the KR/US batch agents and the intraday loops (A/B). With
no coordination, a sell event whose window overlaps two or more of those runs
produced 2-3 identical portfolio messages in the channel.

This module provides a tiny, self-contained (stdlib-only, no project imports so it
is import-safe under both the root and prism-us/cores-shadowed runtimes) debounce:
`should_send_portfolio(market)` returns True at most once per debounce window per
market, atomically recording the send time. Other queued messages (sell notices,
etc.) are unaffected — only the portfolio-summary append is gated by this.

Fail-open: any DB error returns True (send), so a hiccup never silently drops the
user's portfolio update — at worst it falls back to the old (possibly duplicated)
behavior.
"""
from __future__ import annotations

import os
import sqlite3
import time
from pathlib import Path

# Debounce window: collapse portfolio summaries emitted within this many seconds
# of each other (per market) into one. Near-simultaneous batch/loop run-ends fall
# inside it; genuinely separate sell events (minutes apart) do not.
DEBOUNCE_SEC = int(os.getenv("PORTFOLIO_BROADCAST_DEBOUNCE_SEC", "120"))


def _db_path() -> str:
    """Same DB the batch agents and loops use (single stock_tracking_db.sqlite)."""
    return (
        os.getenv("PORTFOLIO_BROADCAST_DB")
        or os.getenv("STOCK_TRACKING_DB")
        or str(Path(__file__).resolve().parent / "stock_tracking_db.sqlite")
    )


def should_send_portfolio(market: str, debounce_sec: int | None = None,
                          db_path: str | None = None) -> bool:
    """Return True iff no portfolio summary was broadcast for `market` within the
    debounce window, recording 'now' atomically when it returns True.

    Fail-open: returns True on any error (never suppresses on a DB hiccup).
    """
    window = DEBOUNCE_SEC if debounce_sec is None else debounce_sec
    path = db_path or _db_path()
    key = (market or "DEFAULT").upper()
    now = time.time()
    try:
        conn = sqlite3.connect(path, timeout=5)
        try:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS portfolio_broadcast_log ("
                " market TEXT PRIMARY KEY, last_sent_ts REAL NOT NULL)"
            )
            # Serialise concurrent run-ends: take an immediate write lock, then
            # read-modify-write under it so two racing senders can't both pass.
            conn.isolation_level = None
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT last_sent_ts FROM portfolio_broadcast_log WHERE market=?",
                (key,),
            ).fetchone()
            if row is not None and (now - float(row[0])) < window:
                conn.execute("ROLLBACK")
                return False
            conn.execute(
                "INSERT INTO portfolio_broadcast_log(market, last_sent_ts) VALUES(?,?) "
                "ON CONFLICT(market) DO UPDATE SET last_sent_ts=excluded.last_sent_ts",
                (key, now),
            )
            conn.execute("COMMIT")
            return True
        finally:
            conn.close()
    except Exception:
        # Fail-open: better to occasionally duplicate than to drop the update.
        return True
