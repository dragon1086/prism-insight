# collector/update.py — Incremental update (library function for daemon use)
from __future__ import annotations

import logging
import time

from collector.bybit_public import fetch_klines_page
from collector.store import get_connection, upsert_rows, get_latest_open_time, get_row_count
from engine.config import TF_INTERVAL_MAP

log = logging.getLogger(__name__)


def update_tf(tf: str, db_path=None) -> int:
    """
    Fetch the latest candles for `tf` and upsert into DB.
    - Fetches newest page (no end_ms = latest).
    - Marks the most recent candle as confirmed=0 (in-progress).
    Returns count of rows upserted.
    """
    conn = get_connection(db_path)
    rows = fetch_klines_page(tf)
    if not rows:
        conn.close()
        return 0

    # rows[0] is the most recent (in-progress) candle from Bybit
    current_open_time = int(rows[0][0])
    n = upsert_rows(conn, tf, rows, current_open_time=current_open_time)
    log.debug("%s: upserted %d rows, latest open_time=%d", tf, n, current_open_time)
    conn.close()
    return n


def update_all(db_path=None) -> dict[str, int]:
    """Update all timeframes. Returns dict of tf → rows upserted."""
    results: dict[str, int] = {}
    for tf in TF_INTERVAL_MAP:
        try:
            results[tf] = update_tf(tf, db_path)
        except Exception as exc:
            log.error("update_tf failed for %s: %s", tf, exc)
            results[tf] = 0
    return results
