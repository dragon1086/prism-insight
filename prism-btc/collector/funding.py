"""Public Bybit funding-history incremental collector for current shadow data."""
from __future__ import annotations

import sqlite3
import time
from pathlib import Path

import requests

from collector.store import _get_db_path
from engine.config import BYBIT_BASE_URL, BYBIT_CATEGORY, BYBIT_SYMBOL

ENDPOINT = "/v5/market/funding/history"
FundingRow = tuple[int, float]


def fetch_funding_page(
    *,
    retries: int = 2,
    timeout: float = 10.0,
) -> list[FundingRow]:
    params = {
        "category": BYBIT_CATEGORY,
        "symbol": BYBIT_SYMBOL,
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
                    f"Bybit funding retCode={payload.get('retCode')} "
                    f"msg={payload.get('retMsg')}"
                )
            return [
                (int(item["fundingRateTimestamp"]), float(item["fundingRate"]))
                for item in payload.get("result", {}).get("list", [])
            ]
        except Exception as exc:
            if attempt + 1 >= retries:
                raise RuntimeError("failed to fetch Bybit funding history") from exc
            time.sleep(2 ** attempt)
    return []


def upsert_funding_rows(
    db_path: str | Path,
    rows: list[FundingRow],
) -> int:
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    try:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS funding ("
            "funding_time INTEGER PRIMARY KEY, rate REAL NOT NULL)"
        )
        conn.executemany(
            "INSERT INTO funding (funding_time, rate) VALUES (?, ?) "
            "ON CONFLICT(funding_time) DO UPDATE SET rate=excluded.rate",
            rows,
        )
        conn.commit()
        return len(rows)
    finally:
        conn.close()


def update_funding(db_path: str | Path | None = None) -> int:
    rows = fetch_funding_page()
    if not rows:
        return 0
    return upsert_funding_rows(_get_db_path(db_path), rows)
