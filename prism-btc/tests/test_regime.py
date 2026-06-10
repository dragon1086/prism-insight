# tests/test_regime.py — Offline tests for regime engine (no network)
import pytest
import pandas as pd
import numpy as np
from datetime import datetime, timezone

from engine.regime import (
    build_tf_state,
    compute_alignment_score,
    build_snapshot,
    TFState,
    _trend,
    _candle_position,
    _candle_aligns_with_trend,
)
from engine.config import TF_WEIGHTS


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------

def make_kline_df(closes, opens=None, highs=None, lows=None) -> pd.DataFrame:
    """Build a minimal OHLCV DataFrame from a list of close prices."""
    n = len(closes)
    closes = [float(c) for c in closes]
    opens = opens if opens is not None else [c * 0.999 for c in closes]
    highs = highs if highs is not None else [c * 1.005 for c in closes]
    lows = lows if lows is not None else [c * 0.995 for c in closes]
    return pd.DataFrame({
        "open": opens,
        "high": highs,
        "low": lows,
        "close": closes,
        "volume": [1.0] * n,
        "turnover": [1.0] * n,
    })


def bullish_df(n: int = 60, base: float = 100.0, final: float = 130.0) -> pd.DataFrame:
    """Trending up: MA10 will be above MA35."""
    closes = list(np.linspace(base, final, n))
    return make_kline_df(closes)


def bearish_df(n: int = 60, base: float = 130.0, final: float = 100.0) -> pd.DataFrame:
    """Trending down: MA10 will be below MA35."""
    closes = list(np.linspace(base, final, n))
    return make_kline_df(closes)


def flat_df(n: int = 60, price: float = 100.0) -> pd.DataFrame:
    """Flat price: MA10 == MA35 → flat trend."""
    return make_kline_df([price] * n)


def all_tfs_bullish() -> dict[str, pd.DataFrame]:
    return {tf: bullish_df() for tf in TF_WEIGHTS}


def all_tfs_bearish() -> dict[str, pd.DataFrame]:
    return {tf: bearish_df() for tf in TF_WEIGHTS}


def all_tfs_flat() -> dict[str, pd.DataFrame]:
    return {tf: flat_df() for tf in TF_WEIGHTS}


# ---------------------------------------------------------------------------
# _trend tests
# ---------------------------------------------------------------------------

class TestTrend:
    def test_up(self):
        assert _trend(ma10=105.0, ma35=100.0, close=104.0) == "up"

    def test_down(self):
        assert _trend(ma10=95.0, ma35=100.0, close=97.0) == "down"

    def test_flat_within_threshold(self):
        # |105 - 104.9| / 105.0 ≈ 0.000952 < 0.0015 → flat
        assert _trend(ma10=105.0, ma35=104.9, close=105.0) == "flat"

    def test_not_flat_outside_threshold(self):
        # |105 - 104.5| / 105 ≈ 0.00476 > 0.0015 → not flat
        result = _trend(ma10=105.0, ma35=104.5, close=105.0)
        assert result in ("up", "down")


# ---------------------------------------------------------------------------
# _candle_position tests
# ---------------------------------------------------------------------------

class TestCandlePosition:
    def test_above_all(self):
        # Candle entirely above both MAs
        pos = _candle_position(open_=110, high=115, low=109, close=112, ma10=105, ma35=100)
        assert pos == "above_all"

    def test_below_all(self):
        pos = _candle_position(open_=90, high=94, low=88, close=91, ma10=100, ma35=105)
        assert pos == "below_all"

    def test_break_ma10_up(self):
        # open below MA10, close above MA10
        pos = _candle_position(open_=98, high=103, low=97, close=102, ma10=100, ma35=95)
        assert pos == "break_ma10_up"

    def test_break_ma10_down(self):
        # open above MA10, close below MA10
        pos = _candle_position(open_=102, high=103, low=97, close=98, ma10=100, ma35=95)
        assert pos == "break_ma10_down"

    def test_break_ma35_up(self):
        pos = _candle_position(open_=98, high=103, low=97, close=102, ma10=110, ma35=100)
        assert pos == "break_ma35_up"

    def test_break_ma35_down(self):
        pos = _candle_position(open_=102, high=103, low=97, close=98, ma10=110, ma35=100)
        assert pos == "break_ma35_down"

    def test_support_ma10(self):
        # Body above MA10, low touches MA10
        # MA10=100, body_low=100.5, low=99.95 (within 0.1% of 100)
        pos = _candle_position(open_=100.5, high=103, low=99.95, close=102, ma10=100, ma35=95)
        assert pos == "support_ma10"

    def test_resist_ma10(self):
        # Body below MA10, high touches MA10
        pos = _candle_position(open_=98, high=100.05, low=96, close=97, ma10=100, ma35=95)
        assert pos == "resist_ma10"

    def test_support_ma35(self):
        # Body above MA35, low touches MA35; MA10 is higher so no MA10 touches
        pos = _candle_position(open_=100.5, high=103, low=99.95, close=102, ma10=110, ma35=100)
        assert pos == "support_ma35"

    def test_resist_ma35(self):
        pos = _candle_position(open_=98, high=100.05, low=96, close=97, ma10=110, ma35=100)
        assert pos == "resist_ma35"

    def test_between(self):
        # Candle body spans both MAs, no clean break (open > both, close > both, but
        # low dips far below both — use a candle where body stays between ma35 and ma10
        # without cleanly breaking either, and no touch within tolerance)
        # ma35=95, ma10=100; open=97, close=98 (body between MAs, no crossover),
        # high=98.5 (too far from ma10=100 to touch), low=96.5 (too far from ma35=95)
        pos = _candle_position(open_=97, high=98.5, low=96.5, close=98, ma10=100, ma35=95)
        assert pos == "between"


# ---------------------------------------------------------------------------
# _candle_aligns_with_trend tests
# ---------------------------------------------------------------------------

class TestCandleAlign:
    def test_bullish_position_up_trend(self):
        assert _candle_aligns_with_trend("above_all", "up") == 1
        assert _candle_aligns_with_trend("support_ma10", "up") == 1
        assert _candle_aligns_with_trend("break_ma35_up", "up") == 1

    def test_bearish_position_down_trend(self):
        assert _candle_aligns_with_trend("below_all", "down") == 1
        assert _candle_aligns_with_trend("resist_ma10", "down") == 1

    def test_conflict(self):
        assert _candle_aligns_with_trend("above_all", "down") == -1
        assert _candle_aligns_with_trend("below_all", "up") == -1

    def test_flat_always_zero(self):
        for pos in ["above_all", "below_all", "between", "support_ma10"]:
            assert _candle_aligns_with_trend(pos, "flat") == 0


# ---------------------------------------------------------------------------
# build_tf_state / alignment score / snapshot integration
# ---------------------------------------------------------------------------

class TestBuildTFState:
    def test_bullish_state(self):
        df = bullish_df()
        state = build_tf_state(df)
        assert state.trend == "up"
        assert state.ma10 > state.ma35

    def test_bearish_state(self):
        df = bearish_df()
        state = build_tf_state(df)
        assert state.trend == "down"
        assert state.ma10 < state.ma35

    def test_flat_state(self):
        df = flat_df()
        state = build_tf_state(df)
        assert state.trend == "flat"

    def test_insufficient_data_raises(self):
        df = make_kline_df([100.0] * 10)
        with pytest.raises(ValueError, match="Insufficient"):
            build_tf_state(df)


class TestAlignmentScore:
    def test_full_bullish_score_positive(self):
        tf_states = {}
        for tf in TF_WEIGHTS:
            tf_states[tf] = TFState(
                trend="up", candle_position="above_all",
                ma10=105.0, ma35=100.0, close=106.0, atr14=1.0
            )
        score = compute_alignment_score(tf_states)
        assert score > 80.0

    def test_full_bearish_score_negative(self):
        tf_states = {}
        for tf in TF_WEIGHTS:
            tf_states[tf] = TFState(
                trend="down", candle_position="below_all",
                ma10=95.0, ma35=100.0, close=94.0, atr14=1.0
            )
        score = compute_alignment_score(tf_states)
        assert score < -80.0

    def test_flat_score_near_zero(self):
        tf_states = {}
        for tf in TF_WEIGHTS:
            tf_states[tf] = TFState(
                trend="flat", candle_position="between",
                ma10=100.0, ma35=100.0, close=100.0, atr14=1.0
            )
        score = compute_alignment_score(tf_states)
        assert abs(score) < 5.0

    def test_mixed_score_bounded(self):
        # Half bullish, half bearish
        tfs = list(TF_WEIGHTS.keys())
        tf_states = {}
        for i, tf in enumerate(tfs):
            if i % 2 == 0:
                tf_states[tf] = TFState("up", "above_all", 105.0, 100.0, 106.0, 1.0)
            else:
                tf_states[tf] = TFState("down", "below_all", 95.0, 100.0, 94.0, 1.0)
        score = compute_alignment_score(tf_states)
        assert -100.0 <= score <= 100.0


class TestBuildSnapshot:
    def test_all_bullish_snapshot(self):
        snap = build_snapshot(all_tfs_bullish())
        assert snap.alignment_score > 0
        assert all(s.trend == "up" for s in snap.tf_states.values())

    def test_all_bearish_snapshot(self):
        snap = build_snapshot(all_tfs_bearish())
        assert snap.alignment_score < 0

    def test_flat_snapshot_near_zero(self):
        snap = build_snapshot(all_tfs_flat())
        assert abs(snap.alignment_score) < 5.0

    def test_snapshot_serializable(self):
        snap = build_snapshot(all_tfs_bullish())
        j = snap.to_json()
        import json
        d = json.loads(j)
        assert "alignment_score" in d
        assert "tf_states" in d
        assert "evaluated_at" in d

    def test_snapshot_evaluated_at(self):
        t = datetime(2024, 6, 1, 12, 0, 0, tzinfo=timezone.utc)
        snap = build_snapshot(all_tfs_flat(), evaluated_at=t)
        assert snap.evaluated_at == "2024-06-01T12:00:00Z"
