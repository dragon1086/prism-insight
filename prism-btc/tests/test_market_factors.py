from __future__ import annotations

import math

import numpy as np
import pandas as pd

from research.market_factors import build_factor_snapshot, compute_ohlcv_factors


def _frame(rows: int = 30) -> pd.DataFrame:
    index = pd.date_range("2026-01-01", periods=rows, freq="4h", tz="UTC")
    close = np.exp(np.arange(rows, dtype=float) * 0.01) * 100.0
    return pd.DataFrame(
        {
            "open": close * 0.995,
            "high": close * 1.01,
            "low": close * 0.99,
            "close": close,
            "volume": np.arange(1, rows + 1, dtype=float) * 10.0,
        },
        index=index,
    )


def test_fixed_ohlcv_factor_set_is_finite_and_versioned() -> None:
    result = compute_ohlcv_factors(_frame())

    assert result["schema_version"] == 1
    assert result["status"] == "ok"
    assert result["bars"] == 30
    assert set(result["values"]) == {
        "trend_r2_10", "trend_r2_21",
        "log_return_vol_10", "log_return_vol_21",
        "rsv_10", "rsv_21",
        "price_volume_corr_10", "price_volume_corr_21",
    }
    assert result["values"]["trend_r2_21"] == 1.0
    assert all(
        value is None or math.isfinite(value)
        for value in result["values"].values()
    )


def test_factor_snapshot_excludes_unclosed_higher_timeframe_candle() -> None:
    frame = _frame()
    evaluated_at = frame.index[-1] + pd.Timedelta(hours=4)
    baseline = build_factor_snapshot({"4h": frame}, evaluated_at, ("4h",))

    future = frame.iloc[[-1]].copy()
    future.index = pd.DatetimeIndex([evaluated_at])
    future.loc[:, ["open", "high", "low", "close", "volume"]] = [
        1.0, 1_000_000.0, 0.1, 999_999.0, 999_999_999.0,
    ]
    with_unclosed = build_factor_snapshot(
        {"4h": pd.concat([frame, future])}, evaluated_at, ("4h",)
    )

    assert with_unclosed == baseline
    assert baseline["timeframes"]["4h"]["as_of_open_time"] == str(frame.index[-1])


def test_factor_snapshot_marks_insufficient_history_without_raising() -> None:
    result = build_factor_snapshot(
        {"4h": _frame(5)}, pd.Timestamp("2026-01-02", tz="UTC"), ("4h", "1d")
    )

    assert result["timeframes"]["4h"]["status"] == "insufficient"
    assert result["timeframes"]["1d"]["status"] == "missing"
