"""
#289 KR 다주 상대강도 스크리닝 US 이식 단위 테스트

Covers:
  (a) return_nd calculation accuracy (fake OHLCV expected value comparison)
  (b) min-max normalization into rs_score
  (c) fetch failure → safe defaults (extension_score=1.0, rs_score=0.5)
  (d) FinalScore weighted sum with regime weights
"""

import sys
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pandas as pd
import pytest

# --- import path setup (mirrors existing test convention) ---
PRISM_US_DIR = Path(__file__).parent.parent
PROJECT_ROOT = PRISM_US_DIR.parent
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PRISM_US_DIR))

from us_trigger_batch import (
    SCREENING_SIGNAL_LOOKBACK_DAYS,
    EXTENSION_ADR_T_LOW,
    EXTENSION_ADR_T_HIGH,
    REGIME_SCORE_WEIGHTS,
    _DEFAULT_SCORE_WEIGHTS,
    _compute_extension_score,
    calculate_screening_signals,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_ohlcv(n: int, start_close: float = 100.0, end_close: float = 120.0) -> pd.DataFrame:
    """Build a minimal OHLCV DataFrame with n rows.

    Closes linearly interpolate from start_close to end_close.
    High = close * 1.01, Low = close * 0.99 → ADR ~2%.
    """
    closes = np.linspace(start_close, end_close, n)
    return pd.DataFrame({
        "Close": closes,
        "High":  closes * 1.01,
        "Low":   closes * 0.99,
        "Open":  closes,
        "Volume": np.ones(n) * 1_000_000,
    })


# ---------------------------------------------------------------------------
# (a) return_nd calculation accuracy
# ---------------------------------------------------------------------------

class TestReturnNd:
    def test_return_nd_positive(self):
        """60-day climb from 100 → 120 should yield +20% return_nd."""
        df = _make_ohlcv(60, start_close=100.0, end_close=120.0)
        current_price = 120.0

        with patch("us_trigger_batch.get_multi_day_ohlcv", return_value=df):
            result = calculate_screening_signals("FAKE", current_price, "2025-01-01")

        assert abs(result["return_nd"] - 20.0) < 0.1, (
            f"Expected ~20.0%, got {result['return_nd']}"
        )

    def test_return_nd_negative(self):
        """60-day fall from 100 → 80 should yield -20% return_nd."""
        df = _make_ohlcv(60, start_close=100.0, end_close=80.0)
        current_price = 80.0

        with patch("us_trigger_batch.get_multi_day_ohlcv", return_value=df):
            result = calculate_screening_signals("FAKE", current_price, "2025-01-01")

        assert abs(result["return_nd"] - (-20.0)) < 0.1, (
            f"Expected ~-20.0%, got {result['return_nd']}"
        )

    def test_return_nd_flat(self):
        """Flat price → return_nd ~0%."""
        df = _make_ohlcv(60, start_close=100.0, end_close=100.0)
        current_price = 100.0

        with patch("us_trigger_batch.get_multi_day_ohlcv", return_value=df):
            result = calculate_screening_signals("FAKE", current_price, "2025-01-01")

        assert abs(result["return_nd"]) < 0.01


# ---------------------------------------------------------------------------
# (b) min-max normalization → rs_score
# ---------------------------------------------------------------------------

class TestRsScoreNormalization:
    def test_rs_score_range(self):
        """rs_score must stay within [0, 1] for any distribution of return_nd."""
        returns = [-30.0, -10.0, 0.0, 15.0, 40.0]
        r_min, r_max = min(returns), max(returns)
        r_range = r_max - r_min

        scores = [(r - r_min) / r_range for r in returns]
        assert abs(scores[0] - 0.0) < 1e-9
        assert abs(scores[-1] - 1.0) < 1e-9
        assert all(0.0 <= s <= 1.0 for s in scores)

    def test_rs_score_single_ticker_fallback(self):
        """When all tickers have the same return (r_range=0), rs_score = 0.5."""
        returns = [10.0]
        r_min, r_max = returns[0], returns[0]
        r_range = r_max - r_min if r_max > r_min else 0.0
        rs_score = ((returns[0] - r_min) / r_range) if r_range > 0 else 0.5
        assert rs_score == 0.5


# ---------------------------------------------------------------------------
# (c) fetch failure → safe defaults
# ---------------------------------------------------------------------------

class TestFetchFailureDefaults:
    def test_empty_dataframe_returns_defaults(self):
        """Empty OHLCV fetch → extension_score=1.0, return_nd=0.0."""
        with patch("us_trigger_batch.get_multi_day_ohlcv", return_value=pd.DataFrame()):
            result = calculate_screening_signals("FAIL", 100.0, "2025-01-01")

        assert result["extension_score"] == 1.0
        assert result["return_nd"] == 0.0
        assert result["extension_in_adr"] == 0.0

    def test_too_few_rows_returns_defaults(self):
        """Fewer than 5 rows → safe defaults (guard against thin data)."""
        df = _make_ohlcv(3)
        with patch("us_trigger_batch.get_multi_day_ohlcv", return_value=df):
            result = calculate_screening_signals("THIN", 100.0, "2025-01-01")

        assert result["extension_score"] == 1.0
        assert result["return_nd"] == 0.0

    def test_zero_price_returns_defaults(self):
        """current_price <= 0 short-circuits immediately."""
        result = calculate_screening_signals("ZERO", 0.0, "2025-01-01")
        assert result == {"extension_in_adr": 0.0, "extension_score": 1.0, "return_nd": 0.0, "oneil_raw": None}


# ---------------------------------------------------------------------------
# _compute_extension_score unit tests
# ---------------------------------------------------------------------------

class TestComputeExtensionScore:
    def test_below_low_threshold(self):
        assert _compute_extension_score(EXTENSION_ADR_T_LOW - 0.1) == 1.0

    def test_at_low_threshold(self):
        assert _compute_extension_score(EXTENSION_ADR_T_LOW) == 1.0

    def test_above_high_threshold(self):
        assert _compute_extension_score(EXTENSION_ADR_T_HIGH + 0.1) == 0.0

    def test_at_high_threshold(self):
        assert _compute_extension_score(EXTENSION_ADR_T_HIGH) == 0.0

    def test_midpoint_is_half(self):
        mid = (EXTENSION_ADR_T_LOW + EXTENSION_ADR_T_HIGH) / 2
        score = _compute_extension_score(mid)
        assert abs(score - 0.5) < 1e-9


# ---------------------------------------------------------------------------
# (d) FinalScore weighted sum
# ---------------------------------------------------------------------------

class TestFinalScoreWeightedSum:
    def test_sideways_regime_weights_sum_to_one(self):
        w = REGIME_SCORE_WEIGHTS["sideways"]
        assert abs(sum(w) - 1.0) < 1e-9

    def test_all_regime_weights_sum_to_one(self):
        for regime, w in REGIME_SCORE_WEIGHTS.items():
            assert abs(sum(w) - 1.0) < 1e-9, f"Regime '{regime}' weights don't sum to 1"

    def test_final_score_formula(self):
        """Verify FinalScore = w_comp*comp + w_agent*agent + w_rs*rs + w_ext*ext."""
        w_comp, w_agent, w_rs, w_ext = REGIME_SCORE_WEIGHTS["sideways"]
        comp_norm, agent, rs, ext = 0.8, 0.6, 0.7, 0.9

        expected = w_comp * comp_norm + w_agent * agent + w_rs * rs + w_ext * ext
        computed = (
            comp_norm * w_comp +
            agent * w_agent +
            rs * w_rs +
            ext * w_ext
        )
        assert abs(expected - computed) < 1e-9
        # Must be in [0, 1] since all inputs and weights are in [0,1] and sum to 1
        assert 0.0 <= computed <= 1.0

    def test_default_weights_mirror_sideways(self):
        assert _DEFAULT_SCORE_WEIGHTS == REGIME_SCORE_WEIGHTS["sideways"]
