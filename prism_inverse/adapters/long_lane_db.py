"""Tickers the long lane currently holds, read-only from the trading DB.

The single-stock inverse lane never bets against a stock the long lane holds (K-SHORT-15).
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from prism_inverse.ports import Unavailable

DEFAULT_PATH = Path(__file__).resolve().parents[2] / "stock_tracking_db.sqlite"
_QUERY = {
    "KR": "SELECT DISTINCT ticker FROM stock_holdings",
    "US": "SELECT DISTINCT ticker FROM us_stock_holdings",
}


class LongLaneDb:
    def __init__(self, path: Path = DEFAULT_PATH):
        self.path = Path(path)

    def tickers(self, market: str) -> frozenset:
        if not self.path.exists():
            raise Unavailable(f"trading DB not found: {self.path.name}")
        uri = f"file:{self.path}?mode=ro"
        try:
            with sqlite3.connect(uri, uri=True) as db:
                rows = db.execute(_QUERY[market]).fetchall()
        except sqlite3.Error as exc:
            raise Unavailable(f"holdings read failed: {type(exc).__name__}") from exc
        return frozenset(str(r[0]).strip().upper() for r in rows if r[0])
