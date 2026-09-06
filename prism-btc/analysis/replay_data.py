"""Strict read-only historical inputs, not evidence of executable or live fills."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
from pathlib import Path
import sqlite3


TIMEFRAME_MS = {
    "5m": 300_000, "30m": 1_800_000, "1h": 3_600_000,
    "4h": 14_400_000, "12h": 43_200_000, "1d": 86_400_000,
    "1w": 604_800_000,
}
MONDAY_ANCHOR_MS = 4 * 86_400_000  # 1970-01-05 UTC.
MAX_UTC_MS = 253_402_300_799_999


@dataclass(frozen=True)
class LoadedBars:
    rows: tuple[dict, ...]
    manifest: dict


@dataclass(frozen=True)
class LoadedFunding:
    rows: tuple[dict, ...]
    manifest: dict


def _integer(value, label: str, *, positive: bool = False) -> int:
    if type(value) is not int or not (int(positive) <= value <= MAX_UTC_MS):
        raise ValueError(f"{label} must be an integer UTC millisecond value")
    return value


def _bounds(start: int, end: int, interval: int, anchor: int = 0) -> int:
    _integer(start, "start_ms")
    _integer(end, "end_ms")
    _integer(interval, "interval_ms", positive=True)
    if start >= end:
        raise ValueError("start_ms must precede end_ms")
    if (start - anchor) % interval or (end - anchor) % interval:
        raise ValueError("requested bounds must be interval-aligned; no clipping")
    return (end - start) // interval


def _read(path, sql: str, parameters: tuple) -> list[dict]:
    # mode=ro refuses missing files; query_only additionally rejects write SQL.
    uri = Path(path).expanduser().resolve().as_uri() + "?mode=ro"
    connection = sqlite3.connect(uri, uri=True)
    try:
        connection.execute("PRAGMA query_only=ON")
        connection.execute("BEGIN")  # One consistent SQLite read snapshot.
        connection.row_factory = sqlite3.Row
        return [dict(row) for row in connection.execute(sql, parameters)]
    finally:
        connection.close()


def _number(value, label: str, *, positive: bool = False) -> float:
    if type(value) not in (int, float) or not math.isfinite(value):
        raise ValueError(f"{label} must be finite numeric data")
    if positive and value <= 0:
        raise ValueError(f"{label} must be positive")
    return float(value)


def _coverage(rows: list[dict], key: str, start: int, end: int,
              interval: int, expected: int) -> None:
    previous = None
    for row in rows:
        timestamp = _integer(row[key], key)
        if not start <= timestamp < end or (timestamp - start) % interval:
            raise ValueError(f"{key} is outside the requested timestamp grid")
        if previous is not None and timestamp <= previous:
            raise ValueError(f"duplicate or unordered {key}: {timestamp}")
        previous = timestamp
    missing = expected - len(rows)
    if missing:
        raise ValueError(f"incomplete {key} coverage: missing_count={missing}")


def _manifest(rows: list[dict], key: str, start: int, end: int,
              interval: int, source: str, assumptions: dict) -> dict:
    identity = {
        "schema_version": 1, "source_kind": source,
        "start_ms": start, "end_ms_exclusive": end,
        "interval_ms": interval, "assumptions": assumptions,
    }
    digest = hashlib.sha256()
    # Streaming canonical JSON lines avoids another full copy of a large range.
    digest.update(json.dumps(identity, sort_keys=True, separators=(",", ":"),
                             allow_nan=False).encode() + b"\n")
    for row in rows:
        digest.update(json.dumps(row, sort_keys=True, separators=(",", ":"),
                                 allow_nan=False).encode() + b"\n")
    return {
        **identity, "count": len(rows), "expected_count": (end - start) // interval,
        "first_timestamp_ms": rows[0][key], "last_timestamp_ms": rows[-1][key],
        "coverage": 1.0, "gap_count": 0, "missing_count": 0,
        "content_sha256": digest.hexdigest(),
        "hash_format": "canonical-json-lines: identity then selected normalized rows",
        "execution_observed": False,
    }


def load_bars(path, timeframe: str, start_ms: int, end_ms: int) -> LoadedBars:
    """Require every confirmed bar in [start, end); zero volume is valid data."""
    if not isinstance(timeframe, str) or timeframe not in TIMEFRAME_MS:
        raise ValueError("unsupported timeframe")
    interval = TIMEFRAME_MS[timeframe]
    anchor = MONDAY_ANCHOR_MS if timeframe == "1w" else 0
    expected = _bounds(start_ms, end_ms, interval, anchor)
    rows = _read(path, "SELECT timeframe, open_time, open, high, low, close, "
                 "volume, turnover, confirmed FROM klines WHERE timeframe = ? "
                 "AND open_time >= ? AND open_time < ? ORDER BY open_time",
                 (timeframe, start_ms, end_ms))
    _coverage(rows, "open_time", start_ms, end_ms, interval, expected)
    for row in rows:
        if type(row["confirmed"]) is not int or row["confirmed"] != 1:
            raise ValueError(f"unconfirmed or invalid bar: {row['open_time']}")
        for field in ("open", "high", "low", "close"):
            row[field] = _number(row[field], field, positive=True)
        if not (row["low"] <= row["open"] <= row["high"]
                and row["low"] <= row["close"] <= row["high"]):
            raise ValueError(f"invalid OHLC range: {row['open_time']}")
        for field in ("volume", "turnover"):
            row[field] = _number(row[field], field)
            if row[field] < 0:
                raise ValueError(f"{field} must be nonnegative")
    manifest = _manifest(rows, "open_time", start_ms, end_ms, interval, "OHLCV", {
        "timeframe": timeframe, "grid_anchor_ms": anchor,
        "price_source": "LAST_TRADE_OHLC", "confirmed_only": True,
        "availability": "open_time + interval_ms; never at bar open",
        "intrabar_order": "UNKNOWN", "mark_price_available": False,
        "orderbook_available": False, "individual_trades_available": False,
        "missing_policy": "REJECT; no interpolation", "zero_volume_is_missing": False,
    })
    return LoadedBars(tuple(rows), manifest)


def load_funding(path, start_ms: int, end_ms: int, interval_ms: int) -> LoadedFunding:
    """Explicit UTC-epoch grid contract; do not assume every market uses 8h."""
    expected = _bounds(start_ms, end_ms, interval_ms)
    rows = _read(path, "SELECT funding_time, rate FROM funding "
                 "WHERE funding_time >= ? AND funding_time < ? ORDER BY funding_time",
                 (start_ms, end_ms))
    _coverage(rows, "funding_time", start_ms, end_ms, interval_ms, expected)
    for row in rows:
        row["rate"] = _number(row["rate"], "rate")
        if abs(row["rate"]) >= 1:
            raise ValueError("funding rate must satisfy abs(rate) < 1")
    manifest = _manifest(rows, "funding_time", start_ms, end_ms, interval_ms,
                         "ACTUAL_FUNDING_TIMESTAMP_RATE", {
        "grid_anchor_ms": 0, "interval_contract": "CALLER_SUPPLIED",
        "missing_policy": "REJECT; no zero-funding fallback",
        "mark_price_available": False, "rate_unit": "SIGNED_FRACTION",
    })
    return LoadedFunding(tuple(rows), manifest)
