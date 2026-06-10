# collector/backfill.py — Backfill all 6 timeframes from BACKFILL_START_MS to now
from __future__ import annotations

import logging
import time

from collector.bybit_public import iter_klines_backwards
from collector.store import get_connection, upsert_rows, get_row_count
from engine.config import TF_INTERVAL_MAP, BACKFILL_START_MS

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger(__name__)


def backfill_tf(tf: str, db_path=None) -> int:
    """Backfill a single timeframe. Returns total rows in DB after."""
    conn = get_connection(db_path)
    log.info("Backfilling %s ...", tf)
    total_fetched = 0
    for page in iter_klines_backwards(tf, start_ms=BACKFILL_START_MS):
        n = upsert_rows(conn, tf, page)
        total_fetched += n
        oldest = int(page[-1][0])
        log.info(
            "  %s: fetched %d rows, oldest=%s, db_total=%d",
            tf, n, time.strftime("%Y-%m-%d", time.gmtime(oldest / 1000)),
            get_row_count(conn, tf),
        )
    total = get_row_count(conn, tf)
    log.info("  %s: DONE — %d rows total in DB", tf, total)
    conn.close()
    return total


def backfill_all(db_path=None) -> dict[str, int]:
    results: dict[str, int] = {}
    for tf in TF_INTERVAL_MAP:
        results[tf] = backfill_tf(tf, db_path)
    return results


if __name__ == "__main__":
    results = backfill_all()
    print("\n=== Backfill Summary ===")
    for tf, count in results.items():
        print(f"  {tf:>4s}: {count:>8,} rows")
