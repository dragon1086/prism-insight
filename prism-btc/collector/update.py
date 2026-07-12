# collector/update.py — Incremental update (library function for daemon use)
from __future__ import annotations

import logging

from collector.bybit_public import fetch_klines_page
from collector.store import get_connection, upsert_rows
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
    """Update all timeframes or raise after collecting every failure.

    A zero count is a valid no-change result. Exceptions indicate that the
    daemon cannot prove its multi-timeframe snapshot is fresh and must skip the
    tick rather than advertise a successful update using stale data.
    """
    results: dict[str, int] = {}
    failures: dict[str, Exception] = {}
    for tf in TF_INTERVAL_MAP:
        try:
            results[tf] = update_tf(tf, db_path)
        except Exception as exc:
            log.error("update_tf failed for %s: %s", tf, exc)
            failures[tf] = exc
    if failures:
        details = ", ".join(f"{tf}: {exc}" for tf, exc in failures.items())
        raise RuntimeError(f"timeframe update failed ({details})")
    return results
