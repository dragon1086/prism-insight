"""Public Bybit 1h open-interest incremental collector (observer data only)."""
from __future__ import annotations

import logging
import sqlite3
import time
from pathlib import Path

import requests

from engine.config import BYBIT_BASE_URL, BYBIT_CATEGORY, BYBIT_SYMBOL

log = logging.getLogger(__name__)
ENDPOINT = "/v5/market/open-interest"
DEFAULT_DB = Path(__file__).resolve().parents[1] / "state" / "btc_research_5m.db"
OpenInterestRow = tuple[int, float, float | None]


def fetch_open_interest_page(
    *,
    retries: int = 2,
    timeout: float = 10.0,
) -> list[OpenInterestRow]:
    """Fetch the latest official 1h page, normalized newest first."""
    params = {
        "category": BYBIT_CATEGORY,
        "symbol": BYBIT_SYMBOL,
        "intervalTime": "1h",
        "limit": 200,
    }
    for attempt in range(retries):
        try:
            response = requests.get(
                BYBIT_BASE_URL + ENDPOINT,
                params=params,
                timeout=timeout,
            )
            response.raise_for_status()
            payload = response.json()
            if payload.get("retCode") != 0:
                raise RuntimeError(
                    f"Bybit OI retCode={payload.get('retCode')} "
                    f"msg={payload.get('retMsg')}"
                )
            rows: list[OpenInterestRow] = []
            for item in payload.get("result", {}).get("list", []):
                single = item.get("singleOpenInterest")
                rows.append((
                    int(item["timestamp"]),
                    float(item["openInterest"]),
                    float(single) if single not in (None, "") else None,
                ))
            return rows
        except Exception as exc:
            if attempt + 1 >= retries:
                raise RuntimeError("failed to fetch Bybit open interest") from exc
            time.sleep(2 ** attempt)
    return []


def _ensure_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        "CREATE TABLE IF NOT EXISTS open_interest ("
        "timestamp INTEGER, open_interest REAL NOT NULL, "
        "single_open_interest REAL)"
    )
    # Older research DBs predate a timestamp constraint.  Deduplicate once so
    # the incremental collector can use a real idempotent upsert.
    conn.execute(
        "DELETE FROM open_interest WHERE rowid NOT IN "
        "(SELECT MAX(rowid) FROM open_interest GROUP BY timestamp)"
    )
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_open_interest_timestamp "
        "ON open_interest(timestamp)"
    )
    conn.commit()


def upsert_open_interest_rows(
    db_path: str | Path,
    rows: list[OpenInterestRow],
) -> int:
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    try:
        _ensure_schema(conn)
        conn.executemany(
            "INSERT INTO open_interest "
            "(timestamp, open_interest, single_open_interest) VALUES (?, ?, ?) "
            "ON CONFLICT(timestamp) DO UPDATE SET "
            "open_interest=excluded.open_interest, "
            "single_open_interest=excluded.single_open_interest",
            rows,
        )
        conn.commit()
        return len(rows)
    finally:
        conn.close()


def update_open_interest(db_path: str | Path | None = None) -> int:
    rows = fetch_open_interest_page()
    if not rows:
        return 0
    count = upsert_open_interest_rows(db_path or DEFAULT_DB, rows)
    log.debug("open interest: upserted %d hourly rows", count)
    return count
