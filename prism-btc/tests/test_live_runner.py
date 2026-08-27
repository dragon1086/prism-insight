from __future__ import annotations

import sqlite3

import pandas as pd

from live.runner import _load_protection_bars


def _market_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.execute(
        "CREATE TABLE klines ("
        "timeframe TEXT, open_time INTEGER, open REAL, high REAL, low REAL, "
        "close REAL, volume REAL, turnover REAL, confirmed INTEGER)"
    )
    rows = [
        ("5m", 1_700_004_000_000, 100, 101, 99, 100.5, 1, 1, 1),
        ("5m", 1_700_004_300_000, 100.5, 102, 100, 101.5, 1, 1, 1),
        ("5m", 1_700_004_600_000, 101.5, 103, 101, 102, 1, 1, 1),
        ("5m", 1_700_004_900_000, 102, 104, 101.5, 103.5, 1, 1, 0),
    ]
    conn.executemany("INSERT INTO klines VALUES (?,?,?,?,?,?,?,?,?)", rows)
    conn.commit()
    return conn


def test_cold_start_only_loads_latest_protection_bucket():
    bars = _load_protection_bars(_market_conn(), None)
    assert len(bars) == 1
    assert bars.index[0] == pd.Timestamp("2023-11-14 23:30:00+00:00")
    assert bars.iloc[0]["high"] == 104


def test_protection_cursor_loads_only_new_buckets():
    bars = _load_protection_bars(_market_conn(), 1_700_004_000_000 * 1_000_000)
    assert len(bars) == 1
    assert bars.iloc[0]["close"] == 103.5
