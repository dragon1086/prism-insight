# tests/test_indicators.py — Offline tests for SMA and ATR
import pytest
import pandas as pd
import numpy as np

from engine.indicators import sma, atr, add_indicators


def make_df(closes, highs=None, lows=None):
    n = len(closes)
    closes = pd.Series(closes, dtype=float)
    highs = pd.Series(highs if highs is not None else closes * 1.01, dtype=float)
    lows = pd.Series(lows if lows is not None else closes * 0.99, dtype=float)
    return pd.DataFrame({
        "open": closes * 0.995,
        "high": highs,
        "low": lows,
        "close": closes,
        "volume": [1.0] * n,
        "turnover": [1.0] * n,
    })


class TestSMA:
    def test_basic_value(self):
        s = pd.Series([1.0, 2.0, 3.0, 4.0, 5.0])
        result = sma(s, 3)
        assert result.iloc[2] == pytest.approx(2.0)
        assert result.iloc[3] == pytest.approx(3.0)
        assert result.iloc[4] == pytest.approx(4.0)

    def test_nan_before_window(self):
        s = pd.Series([1.0, 2.0, 3.0, 4.0])
        result = sma(s, 3)
        assert pd.isna(result.iloc[0])
        assert pd.isna(result.iloc[1])
        assert not pd.isna(result.iloc[2])

    def test_sma_10_35_known_values(self):
        # Constant price → SMA == price
        closes = [100.0] * 50
        df = make_df(closes)
        df2 = add_indicators(df)
        assert df2["ma10"].iloc[-1] == pytest.approx(100.0)
        assert df2["ma35"].iloc[-1] == pytest.approx(100.0)

    def test_rising_sma10_gt_sma35_after_pump(self):
        # Start flat then pump: MA10 should exceed MA35
        base = [100.0] * 40
        pump = [200.0] * 10
        closes = base + pump
        df = make_df(closes)
        df2 = add_indicators(df)
        last = df2.iloc[-1]
        assert last["ma10"] > last["ma35"]


class TestATR:
    def test_constant_price(self):
        # Constant price → TR = high-low each bar; with TOUCH_TOL=1% → ATR ~2
        n = 50
        closes = [100.0] * n
        highs = [101.0] * n
        lows = [99.0] * n
        df = make_df(closes, highs=highs, lows=lows)
        df2 = add_indicators(df)
        # ATR should converge to ~2.0 (high-low range)
        assert df2["atr14"].iloc[-1] == pytest.approx(2.0, rel=0.01)

    def test_atr_nan_before_period(self):
        closes = [100.0] * 30
        df = make_df(closes)
        result = atr(df, period=14)
        # First 13 values should be NaN (min_periods=14)
        assert pd.isna(result.iloc[0])
        assert not pd.isna(result.iloc[13])

    def test_add_indicators_columns(self):
        df = make_df([100.0] * 50)
        df2 = add_indicators(df)
        assert "ma10" in df2.columns
        assert "ma35" in df2.columns
        assert "atr14" in df2.columns
        # Original df not mutated
        assert "ma10" not in df.columns
