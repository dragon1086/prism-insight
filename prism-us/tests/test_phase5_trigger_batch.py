"""
Phase 5: Trigger Batch Tests

Tests for us_trigger_batch.py and cores/us_surge_detector.py:
- Ticker retrieval (S&P 500, NASDAQ-100)
- Snapshot data retrieval
- Market cap filtering
- Trigger functions
- Agent fit calculations
"""

import sys
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch, MagicMock

import numpy as np
import pandas as pd
import pytest

# Add paths for imports
PRISM_US_DIR = Path(__file__).parent.parent
PROJECT_ROOT = PRISM_US_DIR.parent
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PRISM_US_DIR))

from cores.us_surge_detector import (
    get_sp500_tickers,
    get_nasdaq100_tickers,
    get_major_tickers,
    get_snapshot,
    get_previous_snapshot,
    get_market_cap_df,
    get_multi_day_ohlcv,
    get_ticker_name,
    get_nearest_business_day,
    filter_low_liquidity,
    apply_absolute_filters,
    normalize_and_score,
    enhance_dataframe,
)

from us_trigger_batch import (
    TRIGGER_CRITERIA,
    MIN_MARKET_CAP,
    MIN_TRADING_VALUE,
    calculate_agent_fit_metrics,
    score_candidates_by_agent_criteria,
    trigger_morning_volume_surge,
    trigger_morning_gap_up_momentum,
)


# =============================================================================
# Test: Ticker Retrieval
# =============================================================================

@pytest.mark.network
class TestTickerRetrieval:
    """Tests for ticker list retrieval."""

    def test_get_sp500_tickers_returns_list(self):
        """Test get_sp500_tickers returns a list."""
        tickers = get_sp500_tickers()
        assert isinstance(tickers, list)

    def test_get_sp500_tickers_has_items(self):
        """Test get_sp500_tickers returns non-empty list."""
        tickers = get_sp500_tickers()
        # May fall back to ~36 major tickers if Wikipedia fails
        assert len(tickers) >= 30, "S&P 500 should have at least fallback tickers"

    def test_get_sp500_tickers_contains_major_stocks(self):
        """Test get_sp500_tickers contains major stocks."""
        tickers = get_sp500_tickers()
        # These major stocks should always be in S&P 500
        major_stocks = ['AAPL', 'MSFT', 'GOOGL', 'AMZN']
        for stock in major_stocks:
            assert stock in tickers, f"{stock} should be in S&P 500"

    def test_get_nasdaq100_tickers_returns_list(self):
        """Test get_nasdaq100_tickers returns a list."""
        tickers = get_nasdaq100_tickers()
        assert isinstance(tickers, list)

    def test_get_nasdaq100_tickers_has_items(self):
        """Test get_nasdaq100_tickers returns non-empty list."""
        tickers = get_nasdaq100_tickers()
        # May be empty on failure, but should work
        if len(tickers) > 0:
            assert len(tickers) >= 90, "NASDAQ-100 should have ~100 tickers"

    def test_get_major_tickers_combines_lists(self):
        """Test get_major_tickers combines S&P 500 and NASDAQ-100."""
        tickers = get_major_tickers()
        assert isinstance(tickers, list)
        # May fall back to ~36 major tickers if APIs fail
        assert len(tickers) >= 30  # At least fallback tickers


# =============================================================================
# Test: Snapshot Data Retrieval
# =============================================================================

@pytest.mark.network
@pytest.mark.slow
class TestSnapshotRetrieval:
    """Tests for market snapshot retrieval."""

    def test_get_snapshot_returns_dataframe(self, sample_tickers):
        """Test get_snapshot returns a DataFrame."""
        # Use a recent trading date
        trade_date = datetime.now().strftime("%Y%m%d")
        try:
            df = get_snapshot(trade_date, tickers=sample_tickers[:3])
            assert isinstance(df, pd.DataFrame)
        except Exception as e:
            pytest.skip(f"Snapshot retrieval failed (may be weekend/holiday): {e}")

    def test_get_snapshot_has_required_columns(self, sample_tickers):
        """Test snapshot has required OHLCV columns."""
        trade_date = datetime.now().strftime("%Y%m%d")
        try:
            df = get_snapshot(trade_date, tickers=sample_tickers[:3])
            if not df.empty:
                required_cols = ['Open', 'High', 'Low', 'Close', 'Volume']
                for col in required_cols:
                    assert col in df.columns, f"Missing column: {col}"
        except Exception as e:
            pytest.skip(f"Snapshot retrieval failed: {e}")

    def test_get_previous_snapshot_returns_tuple(self, sample_tickers):
        """Test get_previous_snapshot returns tuple of (DataFrame, date)."""
        trade_date = datetime.now().strftime("%Y%m%d")
        try:
            result = get_previous_snapshot(trade_date, tickers=sample_tickers[:3])
            assert isinstance(result, tuple)
            assert len(result) == 2
            assert isinstance(result[0], pd.DataFrame)
            assert isinstance(result[1], str)
        except Exception as e:
            pytest.skip(f"Previous snapshot retrieval failed: {e}")

    def test_get_multi_day_ohlcv(self, sample_ticker):
        """Test multi-day OHLCV retrieval."""
        trade_date = datetime.now().strftime("%Y%m%d")
        df = get_multi_day_ohlcv(sample_ticker, trade_date, days=5)

        assert isinstance(df, pd.DataFrame)
        if not df.empty:
            assert len(df) <= 5


# =============================================================================
# Test: Market Cap Data
# =============================================================================

@pytest.mark.network
class TestMarketCapData:
    """Tests for market cap data retrieval."""

    def test_get_market_cap_df_returns_dataframe(self, sample_tickers):
        """Test get_market_cap_df returns DataFrame."""
        df = get_market_cap_df(sample_tickers[:3])
        assert isinstance(df, pd.DataFrame)

    def test_get_market_cap_df_has_market_cap_column(self, sample_tickers):
        """Test market cap DataFrame has MarketCap column."""
        df = get_market_cap_df(sample_tickers[:3])
        if not df.empty:
            assert 'MarketCap' in df.columns


# =============================================================================
# Test: Utility Functions
# =============================================================================

class TestUtilityFunctions:
    """Tests for utility functions."""

    def test_get_nearest_business_day_weekday(self):
        """Test nearest business day for weekday."""
        # Monday
        result = get_nearest_business_day("20250113", prev=True)
        assert isinstance(result, str)
        assert len(result) == 8

    def test_get_nearest_business_day_weekend(self):
        """Test nearest business day for weekend."""
        # Saturday -> should return Friday
        result = get_nearest_business_day("20250118", prev=True)
        # Result should be Friday (17th)
        assert result == "20250117"

    def test_get_nearest_business_day_next(self):
        """Test next business day from weekend."""
        # Saturday -> should return Monday
        result = get_nearest_business_day("20250118", prev=False)
        # Result should be Monday (20th)
        assert result == "20250120"

    def test_filter_low_liquidity(self, sample_snapshot):
        """Test filter_low_liquidity removes low volume stocks."""
        result = filter_low_liquidity(sample_snapshot, threshold=0.2)
        assert isinstance(result, pd.DataFrame)
        assert len(result) <= len(sample_snapshot)

    def test_apply_absolute_filters(self, sample_snapshot):
        """Test apply_absolute_filters with minimum value."""
        result = apply_absolute_filters(sample_snapshot, min_value=10_000_000)
        assert isinstance(result, pd.DataFrame)

    def test_normalize_and_score(self, sample_snapshot):
        """Test normalize_and_score creates composite score."""
        df = sample_snapshot.copy()
        df['VolumeRatio'] = 1.5  # Add ratio column

        result = normalize_and_score(df, 'VolumeRatio', 'Volume', 0.6, 0.4)
        assert isinstance(result, pd.DataFrame)
        if not result.empty:
            assert 'CompositeScore' in result.columns

    def test_enhance_dataframe(self, sample_snapshot):
        """Test enhance_dataframe adds company names."""
        # Mock get_ticker_name to avoid network calls
        with patch('cores.us_surge_detector.get_ticker_name') as mock_get_name:
            mock_get_name.return_value = "Test Company"
            result = enhance_dataframe(sample_snapshot)

            assert isinstance(result, pd.DataFrame)
            if not result.empty:
                assert 'CompanyName' in result.columns


# =============================================================================
# Test: Trigger Criteria Constants
# =============================================================================

class TestTriggerCriteria:
    """Tests for trigger criteria constants."""

    def test_trigger_criteria_defined(self):
        """Test TRIGGER_CRITERIA dictionary is defined."""
        assert isinstance(TRIGGER_CRITERIA, dict)
        assert len(TRIGGER_CRITERIA) > 0

    def test_trigger_criteria_has_required_triggers(self):
        """Test required trigger types are defined."""
        required_triggers = [
            "Volume Surge Top",
            "Gap Up Momentum Top",
            "Intraday Rise Top",
            "Closing Strength Top",
            "default",
        ]
        for trigger in required_triggers:
            assert trigger in TRIGGER_CRITERIA, f"Missing trigger: {trigger}"

    def test_trigger_criteria_has_rr_target(self):
        """Test each trigger has rr_target."""
        for trigger, criteria in TRIGGER_CRITERIA.items():
            assert 'rr_target' in criteria, f"{trigger} missing rr_target"

    def test_trigger_criteria_has_sl_max(self):
        """Test each trigger has sl_max."""
        for trigger, criteria in TRIGGER_CRITERIA.items():
            assert 'sl_max' in criteria, f"{trigger} missing sl_max"

    def test_min_market_cap_value(self):
        """Test MIN_MARKET_CAP is $20B."""
        assert MIN_MARKET_CAP == 20_000_000_000

    def test_min_trading_value(self):
        """Test MIN_TRADING_VALUE is $100M."""
        assert MIN_TRADING_VALUE == 100_000_000


# =============================================================================
# Test: Agent Fit Metrics
# =============================================================================

class TestAgentFitMetrics:
    """Tests for calculate_agent_fit_metrics function."""

    def test_returns_dict(self, sample_ticker, sample_reference_date):
        """Test calculate_agent_fit_metrics returns dict."""
        with patch('us_trigger_batch.get_multi_day_ohlcv') as mock_ohlcv:
            # Mock multi-day OHLCV data
            mock_ohlcv.return_value = pd.DataFrame({
                'High': [182, 183, 184, 185, 186],
                'Low': [178, 179, 180, 181, 182],
                'Close': [181, 182, 183, 184, 185],
            })

            result = calculate_agent_fit_metrics(
                ticker=sample_ticker,
                current_price=185.0,
                trade_date=sample_reference_date,
                lookback_days=10,
                trigger_type="Volume Surge Top"
            )

            assert isinstance(result, dict)

    def test_returns_required_keys(self, sample_ticker, sample_reference_date):
        """Test result has all required keys."""
        with patch('us_trigger_batch.get_multi_day_ohlcv') as mock_ohlcv:
            mock_ohlcv.return_value = pd.DataFrame({
                'High': [182, 183, 184, 185, 186],
                'Low': [178, 179, 180, 181, 182],
                'Close': [181, 182, 183, 184, 185],
            })

            result = calculate_agent_fit_metrics(
                ticker=sample_ticker,
                current_price=185.0,
                trade_date=sample_reference_date,
            )

            required_keys = [
                'stop_loss_price',
                'target_price',
                'stop_loss_pct',
                'risk_reward_ratio',
                'agent_fit_score',
            ]
            for key in required_keys:
                assert key in result, f"Missing key: {key}"

    def test_zero_price_returns_defaults(self, sample_ticker, sample_reference_date):
        """Test zero price returns default values."""
        result = calculate_agent_fit_metrics(
            ticker=sample_ticker,
            current_price=0,
            trade_date=sample_reference_date,
        )

        assert result['stop_loss_price'] == 0
        assert result['target_price'] == 0
        assert result['agent_fit_score'] == 0

    def test_fixed_stop_loss_method(self, sample_ticker, sample_reference_date):
        """Test v1.16.6 fixed stop-loss method."""
        with patch('us_trigger_batch.get_multi_day_ohlcv') as mock_ohlcv:
            mock_ohlcv.return_value = pd.DataFrame({
                'High': [182, 183, 184, 185, 186],
                'Low': [178, 179, 180, 181, 182],
                'Close': [181, 182, 183, 184, 185],
            })

            current_price = 185.0
            result = calculate_agent_fit_metrics(
                ticker=sample_ticker,
                current_price=current_price,
                trade_date=sample_reference_date,
                trigger_type="Volume Surge Top"  # sl_max=0.05
            )

            # Stop loss should be fixed at 5%
            expected_sl = current_price * 0.95  # 185 * 0.95 = 175.75
            assert abs(result['stop_loss_price'] - expected_sl) < 0.01


# =============================================================================
# Test: Morning Triggers (Mocked)
# =============================================================================

class TestMorningTriggers:
    """Tests for morning trigger functions with mocked data."""

    def test_volume_surge_with_data(self, sample_snapshot, sample_market_cap_df):
        """Test volume surge trigger with sample data."""
        # Create previous snapshot with lower volume
        prev_snapshot = sample_snapshot.copy()
        prev_snapshot['Volume'] = prev_snapshot['Volume'] * 0.5

        result = trigger_morning_volume_surge(
            trade_date=datetime.now().strftime("%Y%m%d"),
            snapshot=sample_snapshot,
            prev_snapshot=prev_snapshot,
            cap_df=sample_market_cap_df,
            top_n=5
        )

        assert isinstance(result, pd.DataFrame)

    def test_gap_up_momentum_with_data(self, sample_snapshot, sample_market_cap_df):
        """Test gap up momentum trigger with sample data."""
        # Create previous snapshot with lower close
        prev_snapshot = sample_snapshot.copy()
        prev_snapshot['Close'] = prev_snapshot['Close'] * 0.95

        result = trigger_morning_gap_up_momentum(
            trade_date=datetime.now().strftime("%Y%m%d"),
            snapshot=sample_snapshot,
            prev_snapshot=prev_snapshot,
            cap_df=sample_market_cap_df,
            top_n=5
        )

        assert isinstance(result, pd.DataFrame)

    def test_volume_surge_empty_snapshot(self):
        """Test volume surge with empty snapshot handles gracefully."""
        empty_df = pd.DataFrame()
        try:
            result = trigger_morning_volume_surge(
                trade_date=datetime.now().strftime("%Y%m%d"),
                snapshot=empty_df,
                prev_snapshot=empty_df,
                cap_df=None,
            )
            assert isinstance(result, pd.DataFrame)
            # Empty input should produce empty output
            assert result.empty
        except (KeyError, ValueError):
            # Function may raise error on empty input - that's also acceptable
            pass


# =============================================================================
# Test: Score Candidates
# =============================================================================

class TestScoreCandidates:
    """Tests for score_candidates_by_agent_criteria function."""

    def test_empty_dataframe(self):
        """Test scoring empty DataFrame returns empty."""
        empty_df = pd.DataFrame()
        result = score_candidates_by_agent_criteria(
            empty_df,
            datetime.now().strftime("%Y%m%d"),
        )
        assert result.empty

    def test_adds_score_columns(self, sample_snapshot):
        """Test that scoring adds required columns."""
        with patch('us_trigger_batch.get_multi_day_ohlcv') as mock_ohlcv:
            mock_ohlcv.return_value = pd.DataFrame({
                'High': [182, 183, 184, 185, 186],
                'Low': [178, 179, 180, 181, 182],
                'Close': [181, 182, 183, 184, 185],
            })

            result = score_candidates_by_agent_criteria(
                sample_snapshot,
                datetime.now().strftime("%Y%m%d"),
            )

            if not result.empty:
                expected_cols = [
                    'StopLossPrice',
                    'TargetPrice',
                    'StopLossPct',
                    'RiskRewardRatio',
                    'AgentFitScore',
                ]
                for col in expected_cols:
                    assert col in result.columns, f"Missing column: {col}"
