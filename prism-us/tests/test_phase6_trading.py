"""
Phase 6: Trading System Tests

Tests for trading/us_stock_trading.py and us_stock_tracking_agent.py:
- Exchange code detection
- USStockTrading class
- AsyncUSTradingContext
- Tracking agent helper functions
"""

import os
import sys
from datetime import datetime, time
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

# Add paths for imports
PRISM_US_DIR = Path(__file__).parent.parent
PROJECT_ROOT = PRISM_US_DIR.parent
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PRISM_US_DIR))


# =============================================================================
# Test: Exchange Code Detection
# =============================================================================

class TestExchangeCodeDetection:
    """Tests for get_exchange_code function."""

    def test_nasdaq_tickers(self):
        """Test NASDAQ tickers return NASD code."""
        from trading.us_stock_trading import get_exchange_code

        nasdaq_tickers = ['AAPL', 'MSFT', 'GOOGL', 'AMZN', 'NVDA', 'META', 'TSLA']
        for ticker in nasdaq_tickers:
            result = get_exchange_code(ticker)
            assert result == 'NASD', f"{ticker} should be NASD, got {result}"

    def test_default_to_nyse(self):
        """Test unknown tickers default to NYSE."""
        from trading.us_stock_trading import get_exchange_code

        # Unknown tickers should default to NYSE
        result = get_exchange_code('UNKNOWN_TICKER')
        assert result == 'NYSE'

    def test_case_insensitive(self):
        """Test exchange code detection is case insensitive."""
        from trading.us_stock_trading import get_exchange_code

        assert get_exchange_code('aapl') == 'NASD'
        assert get_exchange_code('AAPL') == 'NASD'
        assert get_exchange_code('AaPl') == 'NASD'


# =============================================================================
# Test: Exchange Codes Constant
# =============================================================================

class TestExchangeCodesConstant:
    """Tests for EXCHANGE_CODES constant."""

    def test_exchange_codes_defined(self):
        """Test EXCHANGE_CODES dictionary is defined."""
        from trading.us_stock_trading import EXCHANGE_CODES

        assert isinstance(EXCHANGE_CODES, dict)
        assert 'NASDAQ' in EXCHANGE_CODES
        assert 'NYSE' in EXCHANGE_CODES
        assert 'AMEX' in EXCHANGE_CODES

    def test_exchange_codes_values(self):
        """Test EXCHANGE_CODES have correct values."""
        from trading.us_stock_trading import EXCHANGE_CODES

        assert EXCHANGE_CODES['NASDAQ'] == 'NASD'
        assert EXCHANGE_CODES['NYSE'] == 'NYSE'
        assert EXCHANGE_CODES['AMEX'] == 'AMEX'


# =============================================================================
# Test: NASDAQ Tickers Constant
# =============================================================================

class TestNASDAQTickersConstant:
    """Tests for NASDAQ_TICKERS constant."""

    def test_nasdaq_tickers_defined(self):
        """Test NASDAQ_TICKERS set is defined."""
        from trading.us_stock_trading import NASDAQ_TICKERS

        assert isinstance(NASDAQ_TICKERS, set)
        assert len(NASDAQ_TICKERS) > 0

    def test_major_tech_stocks_in_nasdaq(self):
        """Test major tech stocks are in NASDAQ_TICKERS."""
        from trading.us_stock_trading import NASDAQ_TICKERS

        major_tech = ['AAPL', 'MSFT', 'GOOGL', 'AMZN', 'META', 'NVDA', 'TSLA']
        for ticker in major_tech:
            assert ticker in NASDAQ_TICKERS, f"{ticker} should be in NASDAQ_TICKERS"


# =============================================================================
# Test: USStockTrading Class (Initialization)
# =============================================================================

class TestUSStockTradingInit:
    """Tests for USStockTrading class initialization."""

    @pytest.mark.skipif(
        not os.path.exists(PROJECT_ROOT / "trading" / "config" / "kis_devlp.yaml"),
        reason="KIS config file not found"
    )
    def test_init_demo_mode(self):
        """Test initialization in demo mode."""
        from trading.us_stock_trading import USStockTrading

        try:
            trader = USStockTrading(mode="demo")
            assert trader is not None
            assert trader.mode == "demo"
            assert trader.env == "vps"
        except RuntimeError:
            pytest.skip("KIS API authentication failed - config may be invalid")

    def test_class_constants_defined(self):
        """Test class constants are defined."""
        from trading.us_stock_trading import USStockTrading

        assert hasattr(USStockTrading, 'DEFAULT_BUY_AMOUNT')
        assert hasattr(USStockTrading, 'AUTO_TRADING')
        assert hasattr(USStockTrading, 'DEFAULT_MODE')


# =============================================================================
# Test: AsyncUSTradingContext
# =============================================================================

class TestAsyncUSTradingContext:
    """Tests for AsyncUSTradingContext class."""

    def test_context_class_exists(self):
        """Test AsyncUSTradingContext class exists."""
        from trading.us_stock_trading import AsyncUSTradingContext

        assert AsyncUSTradingContext is not None

    def test_context_has_defaults(self):
        """Test AsyncUSTradingContext has default values."""
        from trading.us_stock_trading import AsyncUSTradingContext

        assert hasattr(AsyncUSTradingContext, 'DEFAULT_BUY_AMOUNT')
        assert hasattr(AsyncUSTradingContext, 'AUTO_TRADING')
        assert hasattr(AsyncUSTradingContext, 'DEFAULT_MODE')

    def test_context_init(self):
        """Test AsyncUSTradingContext initialization."""
        from trading.us_stock_trading import AsyncUSTradingContext

        ctx = AsyncUSTradingContext(mode="demo", buy_amount=100)
        assert ctx.mode == "demo"
        assert ctx.buy_amount == 100


# =============================================================================
# Test: Market Hours Check
# =============================================================================

class TestMarketHoursCheck:
    """Tests for market hours checking."""

    @pytest.mark.skipif(
        not os.path.exists(PROJECT_ROOT / "trading" / "config" / "kis_devlp.yaml"),
        reason="KIS config file not found"
    )
    def test_is_market_open_returns_bool(self):
        """Test is_market_open returns boolean."""
        from trading.us_stock_trading import USStockTrading

        try:
            trader = USStockTrading(mode="demo")
            result = trader.is_market_open()
            assert isinstance(result, bool)
        except RuntimeError:
            pytest.skip("KIS API authentication failed")

    def test_us_timezone_defined(self):
        """Test US Eastern timezone is defined."""
        from trading.us_stock_trading import US_EASTERN

        assert US_EASTERN is not None
        assert US_EASTERN.zone == 'US/Eastern'


# =============================================================================
# Test: Tracking Agent Helper Functions
# =============================================================================

class TestTrackingAgentHelpers:
    """Tests for us_stock_tracking_agent helper functions."""

    def test_extract_ticker_info(self):
        """Test extract_ticker_info function."""
        from us_stock_tracking_agent import extract_ticker_info

        # Test with standard format
        ticker, name = extract_ticker_info("AAPL_Apple Inc_20260117.pdf")
        assert ticker == "AAPL"
        assert name == "Apple Inc"

    def test_extract_ticker_info_with_suffix(self):
        """Test extract_ticker_info with gpt5 suffix."""
        from us_stock_tracking_agent import extract_ticker_info

        ticker, name = extract_ticker_info("MSFT_Microsoft Corporation_20260117_gpt5.pdf")
        assert ticker == "MSFT"
        assert name == "Microsoft Corporation"

    def test_parse_price_value_dollar(self):
        """Test parse_price_value with dollar sign."""
        from us_stock_tracking_agent import parse_price_value

        result = parse_price_value("$185.50")
        assert result == 185.50

    def test_parse_price_value_with_comma(self):
        """Test parse_price_value with comma separator."""
        from us_stock_tracking_agent import parse_price_value

        result = parse_price_value("1,234.56")
        assert result == 1234.56

    def test_parse_price_value_plain(self):
        """Test parse_price_value with plain number."""
        from us_stock_tracking_agent import parse_price_value

        result = parse_price_value("185.50")
        assert result == 185.50

    def test_parse_price_value_invalid(self):
        """Test parse_price_value with invalid input."""
        from us_stock_tracking_agent import parse_price_value

        result = parse_price_value("N/A")
        assert result == 0.0

    def test_default_scenario(self):
        """Test default_scenario function."""
        from us_stock_tracking_agent import default_scenario

        result = default_scenario()
        assert isinstance(result, dict)
        assert 'decision' in result
        assert result['decision'] == 'no_entry'

    def test_default_scenario_keys(self):
        """Test default_scenario has all required keys."""
        from us_stock_tracking_agent import default_scenario

        result = default_scenario()
        # Actual keys from implementation
        required_keys = [
            'decision',
            'target_price',
            'stop_loss',
            'portfolio_analysis',
            'buy_score',
            'investment_period',
            'rationale',
            'sector',
            'considerations',
        ]
        for key in required_keys:
            assert key in result, f"Missing key: {key}"


# =============================================================================
# Test: Sector Diversity Check
# =============================================================================

class TestSectorDiversityCheck:
    """Tests for sector diversity checking."""

    def test_check_sector_diversity_import(self):
        """Test check_sector_diversity function can be imported."""
        from us_stock_tracking_agent import check_sector_diversity
        assert callable(check_sector_diversity)

    def test_check_sector_diversity_empty_db(self, initialized_temp_database):
        """Test sector diversity with empty database."""
        from us_stock_tracking_agent import check_sector_diversity

        cursor, conn, _ = initialized_temp_database
        result = check_sector_diversity(cursor, "Technology", 3, 0.3)

        assert isinstance(result, bool)
        # Empty database should allow any sector
        assert result is True


# =============================================================================
# Test: Constants
# =============================================================================

class TestTradingConstants:
    """Tests for trading system constants."""

    def test_max_slots_defined(self):
        """Test MAX_SLOTS constant is defined."""
        from us_stock_tracking_agent import USStockTrackingAgent

        assert USStockTrackingAgent.MAX_SLOTS == 10

    def test_max_same_sector_defined(self):
        """Test MAX_SAME_SECTOR constant is defined."""
        from us_stock_tracking_agent import USStockTrackingAgent

        assert USStockTrackingAgent.MAX_SAME_SECTOR == 3

    def test_sector_concentration_defined(self):
        """Test SECTOR_CONCENTRATION_RATIO constant is defined."""
        from us_stock_tracking_agent import USStockTrackingAgent

        assert USStockTrackingAgent.SECTOR_CONCENTRATION_RATIO == 0.3


# =============================================================================
# Test: USStockTrackingAgent Class
# =============================================================================

class TestUSStockTrackingAgent:
    """Tests for USStockTrackingAgent class."""

    def test_class_exists(self):
        """Test USStockTrackingAgent class exists."""
        from us_stock_tracking_agent import USStockTrackingAgent

        assert USStockTrackingAgent is not None

    def test_class_instantiation(self):
        """Test USStockTrackingAgent can be instantiated."""
        from us_stock_tracking_agent import USStockTrackingAgent

        agent = USStockTrackingAgent()
        assert agent is not None

    def test_has_process_reports_method(self):
        """Test agent has process_reports method."""
        from us_stock_tracking_agent import USStockTrackingAgent

        agent = USStockTrackingAgent()
        assert hasattr(agent, 'process_reports')
        assert callable(agent.process_reports)


# =============================================================================
# Integration Tests (with mocking)
# =============================================================================

@pytest.mark.integration
class TestTradingIntegration:
    """Integration tests for trading system."""

    def test_exchange_code_in_buy_flow(self):
        """Test exchange code is used correctly in buy flow."""
        from trading.us_stock_trading import get_exchange_code

        # Simulate getting exchange for buy order
        ticker = "AAPL"
        exchange = get_exchange_code(ticker)

        assert exchange == "NASD"
        # Exchange code should be used in API params

    def test_extract_and_parse_flow(self):
        """Test extract ticker info and parse price flow."""
        from us_stock_tracking_agent import extract_ticker_info, parse_price_value

        # Simulate extracting from filename
        filename = "NVDA_NVIDIA Corporation_20260117_gpt5.pdf"
        ticker, name = extract_ticker_info(filename)

        assert ticker == "NVDA"
        assert name == "NVIDIA Corporation"

        # Simulate parsing price from report
        price_str = "$485.25"
        price = parse_price_value(price_str)

        assert price == 485.25
