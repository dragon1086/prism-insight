# engine/indicators.py — Pure pandas indicator calculations
from __future__ import annotations

import pandas as pd


def sma(series: pd.Series, n: int) -> pd.Series:
    """Simple Moving Average of length n."""
    return series.rolling(window=n, min_periods=n).mean()


def atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """
    Average True Range (Wilder's smoothing = EWM with alpha=1/period).
    Expects df with columns: high, low, close (float).
    Returns Series aligned with df.index.
    """
    high = df["high"]
    low = df["low"]
    close = df["close"]
    prev_close = close.shift(1)

    tr = pd.concat(
        [
            high - low,
            (high - prev_close).abs(),
            (low - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)

    # Wilder smoothing: EWM with com = period - 1 (equiv. alpha = 1/period)
    return tr.ewm(com=period - 1, min_periods=period, adjust=False).mean()


def add_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """
    Add MA10, MA35, ATR14 columns to a kline DataFrame in-place.
    df must have columns: open, high, low, close, volume, turnover.
    Returns the same DataFrame with extra columns.
    """
    df = df.copy()
    df["ma10"] = sma(df["close"], 10)
    df["ma35"] = sma(df["close"], 35)
    df["atr14"] = atr(df, 14)
    return df
