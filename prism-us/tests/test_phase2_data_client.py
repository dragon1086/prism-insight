"""
Phase 2: US Data Client Tests

Tests for cores/us_data_client.py module:
- USDataClient class initialization
- OHLCV data retrieval (yfinance)
- Company information retrieval
- Institutional holders data
- Market indices data
- Finnhub integration (optional)
"""

import os
import sys
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

# Add paths for imports
PRISM_US_DIR = Path(__file__).parent.parent
PROJECT_ROOT = PRISM_US_DIR.parent
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PRISM_US_DIR))

from cores.us_data_client import USDataClient, get_us_data_client


# =============================================================================
# Test: USDataClient Initialization
# =============================================================================

class TestUSDataClientInit:
    """Tests for USDataClient initialization."""

    def test_client_init_without_api_key(self):
        """Test client initialization without Finnhub API key."""
        client = USDataClient()
        assert client is not None
        assert isinstance(client, USDataClient)

    def test_client_init_with_api_key(self):
        """Test client initialization with Finnhub API key."""
        client = USDataClient(finnhub_api_key="test_key")
        assert client is not None
        assert client.finnhub_api_key == "test_key"

    def test_client_init_from_env(self):
        """Test client initialization from environment variable."""
        with patch.dict(os.environ, {"FINNHUB_API_KEY": "env_test_key"}):
            client = USDataClient()
            assert client.finnhub_api_key == "env_test_key"

    def test_get_us_data_client_factory(self):
        """Test get_us_data_client factory function."""
        client = get_us_data_client()
        assert isinstance(client, USDataClient)


# =============================================================================
# Test: OHLCV Data (yfinance)
# =============================================================================

@pytest.mark.network
class TestOHLCVData:
    """Tests for OHLCV data retrieval."""

    def test_get_ohlcv_valid_ticker(self, sample_ticker):
        """Test OHLCV retrieval for valid ticker."""
        client = USDataClient()
        df = client.get_ohlcv(sample_ticker, period="5d")

        assert isinstance(df, pd.DataFrame)
        if not df.empty:
            # Check required columns (lowercase after standardization)
            expected_cols = ['open', 'high', 'low', 'close', 'volume']
            for col in expected_cols:
                assert col in df.columns, f"Missing column: {col}"
            assert len(df) > 0, "Should have at least one row"

    def test_get_ohlcv_invalid_ticker(self):
        """Test OHLCV retrieval for invalid ticker returns empty DataFrame."""
        client = USDataClient()
        df = client.get_ohlcv("INVALID_TICKER_12345XYZ")

        assert isinstance(df, pd.DataFrame)
        # Should be empty for invalid ticker
        assert df.empty, "Invalid ticker should return empty DataFrame"

    def test_get_ohlcv_with_period(self, sample_ticker):
        """Test OHLCV retrieval with different periods."""
        client = USDataClient()

        for period in ["1d", "5d", "1mo"]:
            df = client.get_ohlcv(sample_ticker, period=period)
            assert isinstance(df, pd.DataFrame)

    def test_get_ohlcv_with_dates(self, sample_ticker):
        """Test OHLCV retrieval with start/end dates."""
        client = USDataClient()
        df = client.get_ohlcv(
            sample_ticker,
            start="2025-01-01",
            end="2025-01-15"
        )
        assert isinstance(df, pd.DataFrame)

    def test_get_stock_ohlcv_alias(self, sample_ticker):
        """Test get_stock_ohlcv method (date range version)."""
        client = USDataClient()
        df = client.get_stock_ohlcv(
            sample_ticker,
            start_date="2025-01-01",
            end_date="2025-01-15"
        )
        assert isinstance(df, pd.DataFrame)


# =============================================================================
# Test: Company Information
# =============================================================================

@pytest.mark.network
class TestCompanyInfo:
    """Tests for company information retrieval."""

    def test_get_company_info_valid(self, sample_ticker):
        """Test company info retrieval for valid ticker."""
        client = USDataClient()
        info = client.get_company_info(sample_ticker)

        assert isinstance(info, dict)
        if info:
            assert "ticker" in info
            assert "name" in info
            assert "market_cap" in info
            assert info["ticker"] == sample_ticker

    def test_get_company_info_has_required_fields(self, sample_ticker):
        """Test that company info contains required fields."""
        client = USDataClient()
        info = client.get_company_info(sample_ticker)

        if info:
            required_fields = [
                "ticker", "name", "sector", "industry",
                "market_cap", "price", "volume"
            ]
            for field in required_fields:
                assert field in info, f"Missing field: {field}"

    def test_get_company_info_invalid_ticker(self):
        """Test company info for invalid ticker."""
        client = USDataClient()
        info = client.get_company_info("INVALID_TICKER_XYZ123")

        assert isinstance(info, dict)
        # May be empty or have minimal info

    def test_get_market_cap(self, sample_ticker):
        """Test get_market_cap convenience method."""
        client = USDataClient()
        market_cap = client.get_market_cap(sample_ticker)

        assert isinstance(market_cap, (int, float))
        if market_cap > 0:
            assert market_cap > 1_000_000_000, "AAPL should be > $1B"

    def test_get_current_price(self, sample_ticker):
        """Test get_current_price convenience method."""
        client = USDataClient()
        price = client.get_current_price(sample_ticker)

        assert isinstance(price, (int, float))
        # Price can be 0 if there's an issue, but should be positive for valid ticker

    def test_is_large_cap(self, sample_ticker):
        """Test is_large_cap method."""
        client = USDataClient()
        is_large = client.is_large_cap(sample_ticker, threshold=20e9)

        assert isinstance(is_large, bool)
        # AAPL should definitely be large cap
        assert is_large is True, "AAPL should be large cap (>$20B)"


# =============================================================================
# Test: Institutional Holders
# =============================================================================

@pytest.mark.network
class TestInstitutionalHolders:
    """Tests for institutional holders data retrieval."""

    def test_get_institutional_holders_valid(self, sample_ticker):
        """Test institutional holders retrieval for valid ticker."""
        client = USDataClient()
        holders = client.get_institutional_holders(sample_ticker)

        assert isinstance(holders, dict)
        if holders:
            assert "institutional_holders" in holders
            assert "major_holders" in holders

    def test_get_institutional_holders_has_data(self, sample_ticker):
        """Test that institutional holders contains data."""
        client = USDataClient()
        holders = client.get_institutional_holders(sample_ticker)

        if holders.get("institutional_holders") is not None:
            inst = holders["institutional_holders"]
            # Should be a DataFrame
            assert isinstance(inst, pd.DataFrame) or inst is None


# =============================================================================
# Test: Market Index Data
# =============================================================================

@pytest.mark.network
class TestMarketIndices:
    """Tests for market indices data retrieval."""

    def test_get_index_data(self):
        """Test index data retrieval."""
        client = USDataClient()
        df = client.get_index_data("^GSPC", period="5d")  # S&P 500

        assert isinstance(df, pd.DataFrame)
        if not df.empty:
            assert 'close' in df.columns

    def test_get_market_indices(self):
        """Test all major indices retrieval."""
        client = USDataClient()
        indices = client.get_market_indices(period="5d")

        assert isinstance(indices, dict)
        expected_indices = ["sp500", "dow", "nasdaq", "russell2000", "vix"]
        for idx_name in expected_indices:
            assert idx_name in indices, f"Missing index: {idx_name}"
            assert isinstance(indices[idx_name], pd.DataFrame)


# =============================================================================
# Test: Finnhub Integration
# =============================================================================

@pytest.mark.skipif(
    not os.getenv("FINNHUB_API_KEY"),
    reason="Finnhub API key not set"
)
@pytest.mark.network
class TestFinnhubIntegration:
    """Tests for Finnhub integration (requires API key)."""

    def test_finnhub_client_initialized(self):
        """Test that Finnhub client is initialized with API key."""
        client = USDataClient()
        assert client._finnhub_client is not None

    def test_get_company_profile_finnhub(self, sample_ticker):
        """Test Finnhub company profile retrieval."""
        client = USDataClient()
        profile = client.get_company_profile_finnhub(sample_ticker)

        assert isinstance(profile, dict)
        if profile:
            # Finnhub profile may have different fields
            assert "ticker" in profile or "name" in profile or len(profile) > 0

    def test_get_company_news_finnhub(self, sample_ticker):
        """Test Finnhub news retrieval."""
        client = USDataClient()
        news = client.get_company_news_finnhub(sample_ticker)

        assert isinstance(news, list)

    def test_get_sec_filings_finnhub(self, sample_ticker):
        """Test Finnhub SEC filings retrieval."""
        client = USDataClient()
        filings = client.get_sec_filings_finnhub(sample_ticker)

        assert isinstance(filings, list)


# =============================================================================
# Test: Utility Methods
# =============================================================================

@pytest.mark.network
class TestUtilityMethods:
    """Tests for utility methods."""

    def test_get_price_change(self, sample_ticker):
        """Test get_price_change method."""
        client = USDataClient()
        change = client.get_price_change(sample_ticker)

        assert isinstance(change, dict)
        expected_keys = ["price", "previous_close", "change", "change_pct"]
        for key in expected_keys:
            assert key in change, f"Missing key: {key}"


# =============================================================================
# Unit Tests with Mocking
# =============================================================================

class TestUSDataClientMocked:
    """Unit tests with mocked external dependencies."""

    def test_ohlcv_error_handling(self):
        """Test OHLCV error handling returns empty DataFrame."""
        with patch('cores.us_data_client.yf.Ticker') as mock_ticker:
            mock_ticker.return_value.history.side_effect = Exception("API Error")
            client = USDataClient()
            df = client.get_ohlcv("TEST")
            assert isinstance(df, pd.DataFrame)
            assert df.empty

    def test_company_info_error_handling(self):
        """Test company info error handling returns empty dict."""
        with patch('cores.us_data_client.yf.Ticker') as mock_ticker:
            mock_ticker.return_value.info = None
            client = USDataClient()
            info = client.get_company_info("TEST")
            assert isinstance(info, dict)

    def test_finnhub_not_initialized_warning(self):
        """Test warning when Finnhub methods called without client."""
        client = USDataClient(finnhub_api_key=None)
        client._finnhub_client = None

        # These should return empty results, not raise
        profile = client.get_company_profile_finnhub("AAPL")
        news = client.get_company_news_finnhub("AAPL")
        filings = client.get_sec_filings_finnhub("AAPL")

        assert profile == {}
        assert news == []
        assert filings == []
