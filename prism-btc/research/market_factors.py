"""Small, causal OHLCV factor snapshot for BTC decision observability.

The values are research inputs only.  They never gate, resize, or place an order.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from typing import Any

import numpy as np
import pandas as pd

from backtest.engine import _get_tf_slice

FACTOR_SCHEMA_VERSION = 1
DEFAULT_WINDOWS = (10, 21)
DEFAULT_TIMEFRAMES = ("1h", "4h", "1d")
_REQUIRED_COLUMNS = ("high", "low", "close", "volume")


def _finite_or_none(value: float) -> float | None:
    return round(float(value), 8) if math.isfinite(float(value)) else None


def _trend_r2(close: np.ndarray) -> float | None:
    if len(close) < 2 or np.any(close <= 0):
        return None
    y = np.log(close)
    if float(np.std(y)) == 0.0:
        return 0.0
    x = np.arange(len(y), dtype=float)
    correlation = float(np.corrcoef(x, y)[0, 1])
    return _finite_or_none(correlation * correlation)


def _correlation(left: np.ndarray, right: np.ndarray) -> float | None:
    if len(left) < 2 or float(np.std(left)) == 0.0 or float(np.std(right)) == 0.0:
        return None
    return _finite_or_none(float(np.corrcoef(left, right)[0, 1]))


def compute_ohlcv_factors(
    frame: pd.DataFrame,
    windows: Sequence[int] = DEFAULT_WINDOWS,
) -> dict[str, Any]:
    """Return a fixed factor set from the supplied, already-confirmed bars."""
    clean_windows = tuple(sorted({int(window) for window in windows if int(window) > 1}))
    required_rows = max(clean_windows, default=0)
    if frame is None or frame.empty:
        return {
            "schema_version": FACTOR_SCHEMA_VERSION,
            "status": "missing",
            "bars": 0,
            "values": {},
        }
    missing = [column for column in _REQUIRED_COLUMNS if column not in frame.columns]
    if missing:
        return {
            "schema_version": FACTOR_SCHEMA_VERSION,
            "status": "missing_columns",
            "bars": len(frame),
            "missing_columns": missing,
            "values": {},
        }
    if len(frame) < required_rows:
        return {
            "schema_version": FACTOR_SCHEMA_VERSION,
            "status": "insufficient",
            "bars": len(frame),
            "required_bars": required_rows,
            "values": {},
        }

    numeric = frame.loc[:, _REQUIRED_COLUMNS].apply(pd.to_numeric, errors="coerce")
    values: dict[str, float | None] = {}
    for window in clean_windows:
        sample = numeric.tail(window)
        close = sample["close"].to_numpy(dtype=float)
        high = sample["high"].to_numpy(dtype=float)
        low = sample["low"].to_numpy(dtype=float)
        volume = sample["volume"].to_numpy(dtype=float)
        if not all(np.isfinite(array).all() for array in (close, high, low, volume)):
            for name in (
                "trend_r2", "log_return_vol", "rsv", "price_volume_corr"
            ):
                values[f"{name}_{window}"] = None
            continue
        values[f"trend_r2_{window}"] = _trend_r2(close)
        log_returns = np.diff(np.log(close)) if np.all(close > 0) else np.array([])
        values[f"log_return_vol_{window}"] = (
            _finite_or_none(float(np.std(log_returns)))
            if len(log_returns) else None
        )
        range_low = float(np.min(low))
        range_width = float(np.max(high)) - range_low
        values[f"rsv_{window}"] = (
            _finite_or_none((float(close[-1]) - range_low) / range_width)
            if range_width > 0 else None
        )
        values[f"price_volume_corr_{window}"] = _correlation(close, volume)
    return {
        "schema_version": FACTOR_SCHEMA_VERSION,
        "status": "ok",
        "bars": len(frame),
        "values": values,
    }


def build_factor_snapshot(
    tf_data: Mapping[str, pd.DataFrame],
    evaluated_at: pd.Timestamp,
    timeframes: Sequence[str] = DEFAULT_TIMEFRAMES,
) -> dict[str, Any]:
    """Build factors only from candles confirmed at ``evaluated_at``."""
    evaluated = pd.Timestamp(evaluated_at)
    result: dict[str, Any] = {
        "schema_version": FACTOR_SCHEMA_VERSION,
        "status": "observational_only",
        "evaluated_at": str(evaluated),
        "timeframes": {},
    }
    for timeframe in timeframes:
        frame = tf_data.get(timeframe)
        if frame is None or frame.empty:
            result["timeframes"][timeframe] = {
                "schema_version": FACTOR_SCHEMA_VERSION,
                "status": "missing",
                "bars": 0,
                "values": {},
            }
            continue
        confirmed = _get_tf_slice(dict(tf_data), evaluated, timeframe)
        factors = compute_ohlcv_factors(confirmed)
        if not confirmed.empty:
            factors["as_of_open_time"] = str(confirmed.index[-1])
        result["timeframes"][timeframe] = factors
    return result


__all__ = [
    "DEFAULT_TIMEFRAMES",
    "DEFAULT_WINDOWS",
    "FACTOR_SCHEMA_VERSION",
    "build_factor_snapshot",
    "compute_ohlcv_factors",
]
