"""Causal OHLCV aggregation helpers shared by live protection and research."""
from __future__ import annotations

import pandas as pd


def aggregate_ohlcv(df: pd.DataFrame, bucket_minutes: int = 10) -> pd.DataFrame:
    """Aggregate timestamp-indexed OHLCV rows into fixed UTC buckets.

    The input index must be UTC timestamps at or before the observation time.
    The output keeps partial latest buckets, which is appropriate for the
    protection path; callers doing historical signal research should pass only
    confirmed source rows or explicitly apply their own close cutoff.
    """
    if df is None or df.empty:
        return pd.DataFrame(columns=["open", "high", "low", "close", "volume", "turnover"])
    work = df.sort_index().copy()
    bucket = f"{int(bucket_minutes)}min"
    grouped = work.resample(bucket, label="left", closed="left").agg(
        open=("open", "first"),
        high=("high", "max"),
        low=("low", "min"),
        close=("close", "last"),
        volume=("volume", "sum"),
        turnover=("turnover", "sum"),
    )
    return grouped.dropna(subset=["open", "high", "low", "close"])
