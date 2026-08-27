from __future__ import annotations

import pandas as pd

from collector.aggregate import aggregate_ohlcv


def test_aggregate_ohlcv_uses_causal_bucket_ohlcv():
    idx = pd.to_datetime([
        "2024-01-01 00:00:00+00:00",
        "2024-01-01 00:05:00+00:00",
        "2024-01-01 00:10:00+00:00",
    ])
    df = pd.DataFrame({
        "open": [100, 101, 102], "high": [101, 103, 104],
        "low": [99, 100, 101], "close": [100.5, 102.5, 103],
        "volume": [1, 2, 3], "turnover": [10, 20, 30],
    }, index=idx)
    out = aggregate_ohlcv(df, 10)
    assert len(out) == 2
    assert out.iloc[0]["open"] == 100
    assert out.iloc[0]["high"] == 103
    assert out.iloc[0]["low"] == 99
    assert out.iloc[0]["close"] == 102.5
    assert out.iloc[0]["volume"] == 3
