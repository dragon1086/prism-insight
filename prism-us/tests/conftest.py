"""
Pytest Fixtures and Configuration for PRISM-US Tests

Provides shared fixtures for all test modules:
- Temporary databases
- Mock data (OHLCV, market cap)
- Sample tickers and snapshots
"""

import os
import sys
import tempfile
from datetime import date, datetime
from pathlib import Path

import pandas as pd
import pytest

# Add paths for imports
PRISM_US_DIR = Path(__file__).parent.parent
PROJECT_ROOT = PRISM_US_DIR.parent
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PRISM_US_DIR))

# Ensure we can import from prism-us
os.chdir(str(PRISM_US_DIR))


# =============================================================================
# Fixtures - Database
# =============================================================================

@pytest.fixture
def temp_database():
    """Create a temporary SQLite database for testing."""
    temp_file = tempfile.NamedTemporaryFile(suffix='.sqlite', delete=False)
    db_path = temp_file.name
    temp_file.close()
    yield db_path
    # Cleanup
    try:
        os.unlink(db_path)
    except Exception:
        pass


@pytest.fixture
def initialized_temp_database(temp_database):
    """Create and initialize a temporary US database."""
    import sqlite3
    import sys
    from pathlib import Path

    # Ensure path is set for import
    prism_us_dir = Path(__file__).parent.parent
    if str(prism_us_dir) not in sys.path:
        sys.path.insert(0, str(prism_us_dir))

    from tracking.db_schema import initialize_us_database
    cursor, conn = initialize_us_database(temp_database)
    yield cursor, conn, temp_database
    conn.close()


# =============================================================================
# Fixtures - Sample Data
# =============================================================================

@pytest.fixture
def sample_ticker():
    """Default test ticker symbol."""
    return "AAPL"


@pytest.fixture
def sample_tickers():
    """List of test ticker symbols."""
    return ["AAPL", "MSFT", "GOOGL", "AMZN", "NVDA"]


@pytest.fixture
def sample_reference_date():
    """Sample reference date in YYYYMMDD format."""
    return datetime.now().strftime("%Y%m%d")


@pytest.fixture
def sample_company_info():
    """Sample company information."""
    return {
        "ticker": "AAPL",
        "name": "Apple Inc.",
        "sector": "Technology",
        "industry": "Consumer Electronics",
        "market_cap": 3_000_000_000_000,  # $3T
        "price": 185.50,
    }


@pytest.fixture
def sample_ohlcv_data():
    """Sample OHLCV DataFrame."""
    dates = pd.date_range(end=datetime.now(), periods=10, freq='B')
    data = {
        'Open': [180.0, 181.0, 182.0, 183.0, 184.0, 185.0, 186.0, 187.0, 188.0, 189.0],
        'High': [182.0, 183.0, 184.0, 185.0, 186.0, 187.0, 188.0, 189.0, 190.0, 191.0],
        'Low': [179.0, 180.0, 181.0, 182.0, 183.0, 184.0, 185.0, 186.0, 187.0, 188.0],
        'Close': [181.0, 182.0, 183.0, 184.0, 185.0, 186.0, 187.0, 188.0, 189.0, 190.0],
        'Volume': [50000000, 52000000, 48000000, 55000000, 60000000,
                   58000000, 62000000, 45000000, 70000000, 65000000],
    }
    df = pd.DataFrame(data, index=dates)
    df['Amount'] = df['Close'] * df['Volume']
    return df


@pytest.fixture
def sample_snapshot(sample_tickers):
    """Sample market snapshot DataFrame."""
    data = {
        'Open': [180.0, 350.0, 140.0, 170.0, 450.0],
        'High': [185.0, 355.0, 145.0, 175.0, 460.0],
        'Low': [178.0, 348.0, 138.0, 168.0, 445.0],
        'Close': [184.0, 353.0, 143.0, 173.0, 455.0],
        'Volume': [50000000, 30000000, 25000000, 40000000, 35000000],
    }
    df = pd.DataFrame(data, index=sample_tickers)
    df['Amount'] = df['Close'] * df['Volume']
    return df


@pytest.fixture
def sample_market_cap_df(sample_tickers):
    """Sample market cap DataFrame."""
    data = {
        'MarketCap': [3_000_000_000_000, 2_800_000_000_000, 1_800_000_000_000,
                      1_700_000_000_000, 1_500_000_000_000],
    }
    return pd.DataFrame(data, index=sample_tickers)


# =============================================================================
# Fixtures - Dates
# =============================================================================

@pytest.fixture
def weekday_date():
    """Get a known weekday date (Tuesday)."""
    # January 20, 2026 is a Tuesday (and not a holiday)
    return date(2026, 1, 20)


@pytest.fixture
def weekend_date():
    """Get a known weekend date (Saturday)."""
    # January 18, 2026 is a Saturday
    return date(2026, 1, 18)


@pytest.fixture
def mlk_day():
    """Get MLK Day date (US holiday)."""
    # January 19, 2026 is MLK Day (3rd Monday of January)
    return date(2026, 1, 19)


@pytest.fixture
def christmas_date():
    """Get Christmas date (US holiday)."""
    return date(2025, 12, 25)


# =============================================================================
# Fixtures - Trading
# =============================================================================

@pytest.fixture
def sample_holding():
    """Sample stock holding record."""
    return {
        'ticker': 'AAPL',
        'company_name': 'Apple Inc.',
        'buy_price': 180.50,
        'buy_date': '2026-01-15',
        'current_price': 185.50,
        'target_price': 198.00,
        'stop_loss': 171.50,
        'trigger_type': 'volume_surge',
        'trigger_mode': 'morning',
        'sector': 'Technology',
    }


@pytest.fixture
def sample_scenario():
    """Sample trading scenario JSON."""
    return {
        "decision": "entry",
        "target_price": 198.00,
        "stop_loss": 171.50,
        "target_percentage": "+10%",
        "stop_loss_percentage": "-5%",
        "risk_reward_ratio": 2.0,
        "confidence_score": 75,
        "entry_signals": ["Volume surge", "Above 50-day MA"],
    }


# =============================================================================
# Fixtures - Agent Testing
# =============================================================================

@pytest.fixture
def agent_sections():
    """List of standard agent sections for US analysis."""
    return [
        'price_volume_analysis',
        'institutional_holdings_analysis',
        'company_status',
        'company_overview',
        'news_analysis',
        'market_index_analysis',
    ]


# =============================================================================
# Markers
# =============================================================================

def pytest_configure(config):
    """Register custom markers."""
    config.addinivalue_line(
        "markers", "integration: marks tests as integration tests (may be slow)"
    )
    config.addinivalue_line(
        "markers", "slow: marks tests as slow (deselect with '-m \"not slow\"')"
    )
    config.addinivalue_line(
        "markers", "network: marks tests that require network access"
    )


# =============================================================================
# Hooks
# =============================================================================

def pytest_collection_modifyitems(config, items):
    """Modify test collection to skip certain tests based on conditions."""
    skip_network = pytest.mark.skip(reason="Network tests disabled")

    for item in items:
        # Skip network tests if SKIP_NETWORK_TESTS is set
        if "network" in item.keywords and os.getenv("SKIP_NETWORK_TESTS"):
            item.add_marker(skip_network)
