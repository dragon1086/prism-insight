# collector/bybit_public.py — Bybit v5 public REST client (no API key required)
from __future__ import annotations

import time
import logging
from typing import Iterator

import requests

from engine.config import (
    BYBIT_BASE_URL,
    BYBIT_KLINE_ENDPOINT,
    BYBIT_SYMBOL,
    BYBIT_CATEGORY,
    BYBIT_MAX_LIMIT,
    BYBIT_SLEEP_BETWEEN_REQUESTS,
    TF_INTERVAL_MAP,
)

log = logging.getLogger(__name__)

# One row from the Bybit kline API (list[str])
# [startTime, openPrice, highPrice, lowPrice, closePrice, volume, turnover]
KlineRow = list[str]


def _get_klines(
    interval: str,
    end_ms: int | None = None,
    limit: int = BYBIT_MAX_LIMIT,
    retries: int = 5,
) -> list[KlineRow]:
    """Single paginated request. Returns raw rows (newest first)."""
    params: dict[str, str | int] = {
        "category": BYBIT_CATEGORY,
        "symbol": BYBIT_SYMBOL,
        "interval": interval,
        "limit": limit,
    }
    if end_ms is not None:
        params["end"] = end_ms

    for attempt in range(retries):
        try:
            resp = requests.get(
                BYBIT_BASE_URL + BYBIT_KLINE_ENDPOINT,
                params=params,
                timeout=15,
            )
            resp.raise_for_status()
            data = resp.json()
            if data.get("retCode") != 0:
                log.warning("Bybit retCode %s: %s", data.get("retCode"), data.get("retMsg"))
                time.sleep(2 ** attempt)
                continue
            return data["result"]["list"]
        except requests.RequestException as exc:
            log.warning("Request error attempt %d: %s", attempt + 1, exc)
            time.sleep(2 ** attempt)

    raise RuntimeError(f"Failed to fetch klines after {retries} attempts")


def fetch_klines_page(tf: str, end_ms: int | None = None) -> list[KlineRow]:
    """Fetch one page for given timeframe label (e.g. '30m')."""
    interval = TF_INTERVAL_MAP[tf]
    time.sleep(BYBIT_SLEEP_BETWEEN_REQUESTS)
    return _get_klines(interval, end_ms=end_ms)


def iter_klines_backwards(
    tf: str,
    start_ms: int,
    end_ms: int | None = None,
) -> Iterator[list[KlineRow]]:
    """
    Yield pages of kline rows going backwards in time until start_ms is reached.
    Each page is a list of raw rows (newest-first from Bybit).
    """
    cursor_ms = end_ms  # None means "latest"
    while True:
        rows = fetch_klines_page(tf, end_ms=cursor_ms)
        if not rows:
            break
        yield rows
        # rows[-1] is the oldest in this page (newest-first order)
        oldest_in_page = int(rows[-1][0])
        if oldest_in_page <= start_ms:
            break
        # move cursor back one ms to avoid overlap
        cursor_ms = oldest_in_page - 1
