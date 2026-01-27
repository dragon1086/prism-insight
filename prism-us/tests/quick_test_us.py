#!/usr/bin/env python3
"""
Quick Test Script for PRISM-US

Interactive script for rapid testing of US market modules.
Run individual tests or all tests at once.

Usage:
    python prism-us/tests/quick_test_us.py market      # Market status check
    python prism-us/tests/quick_test_us.py data AAPL   # Data client test
    python prism-us/tests/quick_test_us.py trigger     # Trigger batch test
    python prism-us/tests/quick_test_us.py agents      # Agent creation test
    python prism-us/tests/quick_test_us.py database    # Database test
    python prism-us/tests/quick_test_us.py trading     # Trading module test
    python prism-us/tests/quick_test_us.py all         # Run all quick tests
"""

import argparse
import logging
import sys
from datetime import datetime
from pathlib import Path

# Add paths for imports
PRISM_US_DIR = Path(__file__).parent.parent
PROJECT_ROOT = PRISM_US_DIR.parent
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PRISM_US_DIR))

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def test_market():
    """Test market day checker and status."""
    print("\n" + "=" * 60)
    print("MARKET STATUS TEST")
    print("=" * 60)

    from check_market_day import (
        is_us_market_day,
        get_market_status,
        is_market_open,
    )

    print("\n[1] Current Market Status:")
    status = get_market_status()
    for key, value in status.items():
        print(f"    {key}: {value}")

    print(f"\n[2] is_us_market_day(): {is_us_market_day()}")
    print(f"[3] is_market_open(): {is_market_open()}")

    # Test specific dates
    from datetime import date
    print("\n[4] Specific Date Tests:")
    test_dates = [
        (date(2026, 1, 20), "Tuesday (should be trading day)"),
        (date(2026, 1, 18), "Saturday (should be closed)"),
        (date(2026, 1, 19), "MLK Day (should be closed)"),
        (date(2025, 12, 25), "Christmas (should be closed)"),
    ]

    for test_date, desc in test_dates:
        result = is_us_market_day(test_date)
        status = "OPEN" if result else "CLOSED"
        print(f"    {test_date} ({desc}): {status}")

    print("\n✓ Market status test completed")
    return True


def test_data(ticker="AAPL"):
    """Test US data client."""
    print("\n" + "=" * 60)
    print(f"DATA CLIENT TEST: {ticker}")
    print("=" * 60)

    from cores.us_data_client import USDataClient

    client = USDataClient()

    print(f"\n[1] OHLCV Data (5 days):")
    df = client.get_ohlcv(ticker, period="5d")
    if not df.empty:
        print(df.tail(3))
    else:
        print("    (No data retrieved)")

    print(f"\n[2] Company Info:")
    info = client.get_company_info(ticker)
    if info:
        key_info = ['name', 'sector', 'market_cap', 'price', 'pe_ratio']
        for key in key_info:
            value = info.get(key, 'N/A')
            if key == 'market_cap' and isinstance(value, (int, float)):
                value = f"${value:,.0f}"
            elif key == 'price' and isinstance(value, (int, float)):
                value = f"${value:.2f}"
            print(f"    {key}: {value}")
    else:
        print("    (No info retrieved)")

    print(f"\n[3] Institutional Holders:")
    holders = client.get_institutional_holders(ticker)
    if holders.get('institutional_holders') is not None:
        print(holders['institutional_holders'].head(3))
    else:
        print("    (No institutional holders data)")

    print(f"\n[4] Market Indices:")
    indices = client.get_market_indices(period="1d")
    for name, df in indices.items():
        if not df.empty:
            latest = df.iloc[-1]
            close = latest.get('close', 0)
            print(f"    {name}: ${close:,.2f}")

    print(f"\n[5] Convenience Methods:")
    print(f"    get_current_price({ticker}): ${client.get_current_price(ticker):.2f}")
    print(f"    get_market_cap({ticker}): ${client.get_market_cap(ticker):,.0f}")
    print(f"    is_large_cap({ticker}, $20B): {client.is_large_cap(ticker, 20e9)}")

    print("\n✓ Data client test completed")
    return True


def test_trigger():
    """Test trigger batch system."""
    print("\n" + "=" * 60)
    print("TRIGGER BATCH TEST")
    print("=" * 60)

    from cores.us_surge_detector import (
        get_sp500_tickers,
        get_nasdaq100_tickers,
    )
    from us_trigger_batch import (
        TRIGGER_CRITERIA,
        MIN_MARKET_CAP,
        MIN_TRADING_VALUE,
    )

    print("\n[1] Ticker Lists:")
    sp500 = get_sp500_tickers()
    print(f"    S&P 500: {len(sp500)} tickers")
    print(f"    Sample: {sp500[:5]}")

    nasdaq100 = get_nasdaq100_tickers()
    print(f"    NASDAQ-100: {len(nasdaq100)} tickers")

    print("\n[2] Trigger Criteria:")
    for trigger_type, criteria in TRIGGER_CRITERIA.items():
        print(f"    {trigger_type}: R/R={criteria['rr_target']}, SL={criteria['sl_max']*100}%")

    print("\n[3] Filter Constants:")
    print(f"    MIN_MARKET_CAP: ${MIN_MARKET_CAP:,.0f} (${MIN_MARKET_CAP/1e9:.0f}B)")
    print(f"    MIN_TRADING_VALUE: ${MIN_TRADING_VALUE:,.0f} (${MIN_TRADING_VALUE/1e6:.0f}M)")

    print("\n✓ Trigger batch test completed")
    return True


def test_agents():
    """Test agent creation."""
    print("\n" + "=" * 60)
    print("AGENT CREATION TEST")
    print("=" * 60)

    from cores.agents import get_us_data_urls, get_us_agent_directory

    ticker = "AAPL"
    company_name = "Apple Inc."
    reference_date = datetime.now().strftime("%Y%m%d")

    print(f"\n[1] URL Generation for {ticker}:")
    urls = get_us_data_urls(ticker)
    for key, url in urls.items():
        print(f"    {key}: {url[:60]}...")

    print(f"\n[2] Agent Directory for {company_name}:")
    sections = [
        'price_volume_analysis',
        'institutional_holdings_analysis',
        'company_status',
        'company_overview',
        'news_analysis',
        'market_index_analysis',
    ]

    agents = get_us_agent_directory(company_name, ticker, reference_date, sections, "en")

    for section, agent in agents.items():
        servers = agent.server_names if hasattr(agent, 'server_names') else []
        print(f"    {section}:")
        print(f"        Name: {agent.name}")
        print(f"        MCP Servers: {servers}")

    print("\n✓ Agent creation test completed")
    return True


def test_database():
    """Test database schema."""
    print("\n" + "=" * 60)
    print("DATABASE SCHEMA TEST")
    print("=" * 60)

    import tempfile
    from tracking.db_schema import (
        initialize_us_database,
        get_us_holdings_count,
        is_us_ticker_in_holdings,
    )

    # Create temp database
    temp_file = tempfile.NamedTemporaryFile(suffix='.sqlite', delete=False)
    db_path = temp_file.name
    temp_file.close()

    print(f"\n[1] Initializing database: {db_path}")
    cursor, conn = initialize_us_database(db_path)

    print("\n[2] US Tables:")
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'us_%'")
    tables = cursor.fetchall()
    for table in tables:
        print(f"    - {table[0]}")

    print("\n[3] US Indexes:")
    cursor.execute("SELECT name FROM sqlite_master WHERE type='index' AND name LIKE 'idx_us_%'")
    indexes = cursor.fetchall()
    for index in indexes:
        print(f"    - {index[0]}")

    print(f"\n[4] Holdings count: {get_us_holdings_count(cursor)}")
    print(f"[5] is_us_ticker_in_holdings('AAPL'): {is_us_ticker_in_holdings(cursor, 'AAPL')}")

    # Cleanup
    conn.close()
    import os
    os.unlink(db_path)

    print("\n✓ Database test completed")
    return True


def test_trading():
    """Test trading module."""
    print("\n" + "=" * 60)
    print("TRADING MODULE TEST")
    print("=" * 60)

    from trading.us_stock_trading import (
        get_exchange_code,
        EXCHANGE_CODES,
        NASDAQ_TICKERS,
    )
    from us_stock_tracking_agent import (
        extract_ticker_info,
        parse_price_value,
        default_scenario,
        USStockTrackingAgent,
    )

    print("\n[1] Exchange Code Detection:")
    test_tickers = ['AAPL', 'MSFT', 'GOOGL', 'JPM', 'GS', 'UNKNOWN']
    for ticker in test_tickers:
        code = get_exchange_code(ticker)
        print(f"    {ticker}: {code}")

    print("\n[2] Exchange Codes Constant:")
    for key, value in EXCHANGE_CODES.items():
        print(f"    {key}: {value}")

    print(f"\n[3] NASDAQ Tickers: {len(NASDAQ_TICKERS)} defined")
    print(f"    Sample: {list(NASDAQ_TICKERS)[:5]}")

    print("\n[4] Tracking Agent Helpers:")
    test_files = [
        "AAPL_Apple Inc_20260118_gpt5.pdf",
        "NVDA_NVIDIA Corporation_20260117.pdf",
    ]
    for filename in test_files:
        ticker, name = extract_ticker_info(filename)
        print(f"    {filename}")
        print(f"        -> ticker: {ticker}, name: {name}")

    print("\n[5] Price Parsing:")
    test_prices = ["$185.50", "1,234.56", "185.50", "N/A"]
    for price_str in test_prices:
        value = parse_price_value(price_str)
        print(f"    '{price_str}' -> {value}")

    print("\n[6] Default Scenario:")
    scenario = default_scenario()
    for key, value in scenario.items():
        print(f"    {key}: {value}")

    print(f"\n[7] Trading Constants (from USStockTrackingAgent):")
    print(f"    MAX_SLOTS: {USStockTrackingAgent.MAX_SLOTS}")
    print(f"    MAX_SAME_SECTOR: {USStockTrackingAgent.MAX_SAME_SECTOR}")
    print(f"    SECTOR_CONCENTRATION_RATIO: {USStockTrackingAgent.SECTOR_CONCENTRATION_RATIO}")

    print("\n✓ Trading module test completed")
    return True


def test_all():
    """Run all quick tests."""
    print("\n" + "=" * 60)
    print("RUNNING ALL QUICK TESTS")
    print("=" * 60)

    tests = [
        ("Market", test_market),
        ("Data", lambda: test_data("AAPL")),
        ("Trigger", test_trigger),
        ("Agents", test_agents),
        ("Database", test_database),
        ("Trading", test_trading),
    ]

    results = []
    for name, test_func in tests:
        try:
            success = test_func()
            results.append((name, "PASS" if success else "FAIL"))
        except Exception as e:
            logger.error(f"Error in {name} test: {e}")
            results.append((name, f"ERROR: {e}"))

    print("\n" + "=" * 60)
    print("TEST SUMMARY")
    print("=" * 60)

    for name, status in results:
        icon = "✓" if status == "PASS" else "✗"
        print(f"    {icon} {name}: {status}")

    passed = sum(1 for _, status in results if status == "PASS")
    print(f"\nTotal: {passed}/{len(results)} passed")

    return all(status == "PASS" for _, status in results)


def main():
    parser = argparse.ArgumentParser(
        description="Quick Test Script for PRISM-US",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__
    )
    parser.add_argument(
        "test",
        choices=["market", "data", "trigger", "agents", "database", "trading", "all"],
        help="Test to run"
    )
    parser.add_argument(
        "ticker",
        nargs="?",
        default="AAPL",
        help="Ticker symbol for data test (default: AAPL)"
    )

    args = parser.parse_args()

    test_map = {
        "market": test_market,
        "data": lambda: test_data(args.ticker),
        "trigger": test_trigger,
        "agents": test_agents,
        "database": test_database,
        "trading": test_trading,
        "all": test_all,
    }

    try:
        success = test_map[args.test]()
        sys.exit(0 if success else 1)
    except Exception as e:
        logger.error(f"Test failed with error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
