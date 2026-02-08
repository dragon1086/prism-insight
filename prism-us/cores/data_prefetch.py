"""
Data Prefetch Module for US Stock Analysis

Pre-fetches US stock data using yfinance (via USDataClient) to inject into agent
instructions, eliminating the need for yahoo_finance MCP server tool calls.

This reduces token usage by avoiding MCP tool call round-trips for predictable,
parameterized data fetches (OHLCV, holder info, market indices).
"""

import logging
from pathlib import Path
import importlib.util

import pandas as pd

logger = logging.getLogger(__name__)


def _df_to_markdown(df: pd.DataFrame, title: str = "") -> str:
    """Convert DataFrame to markdown table string.

    Args:
        df: DataFrame to convert
        title: Optional title to prepend

    Returns:
        Markdown table string
    """
    if df is None or df.empty:
        return f"### {title}\n\n_No data available_\n" if title else "_No data available_\n"

    result = ""
    if title:
        result += f"### {title}\n\n"

    result += df.to_markdown(index=True) + "\n"
    return result


def _get_us_data_client():
    """Get USDataClient instance, importing from the local module.

    Returns:
        USDataClient instance
    """
    # Import USDataClient from the same directory
    _current_dir = Path(__file__).parent
    _client_path = _current_dir / "us_data_client.py"
    spec = importlib.util.spec_from_file_location("us_data_client_local", _client_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.USDataClient()


def prefetch_us_stock_ohlcv(ticker: str, period: str = "1y") -> str:
    """Prefetch US stock OHLCV data using yfinance.

    Args:
        ticker: Stock ticker symbol (e.g., "AAPL") or index symbol (e.g., "^GSPC")
        period: Data period (default: "1y")

    Returns:
        Markdown formatted OHLCV data string, or empty string on error
    """
    try:
        client = _get_us_data_client()
        df = client.get_ohlcv(ticker, period=period, interval="1d")

        if df is None or df.empty:
            logger.warning(f"No OHLCV data for {ticker}")
            return ""

        # Capitalize column names for readability
        df.columns = [col.title().replace("_", " ") for col in df.columns]
        df.index.name = "Date"

        return _df_to_markdown(df, f"OHLCV: {ticker} ({period})")
    except Exception as e:
        logger.error(f"Error prefetching OHLCV for {ticker}: {e}")
        return ""


def prefetch_us_holder_info(ticker: str) -> str:
    """Prefetch US institutional holder data using yfinance.

    Args:
        ticker: Stock ticker symbol

    Returns:
        Markdown formatted holder data string (major + institutional + mutualfund), or empty string on error
    """
    try:
        client = _get_us_data_client()
        holders = client.get_institutional_holders(ticker)

        if not holders:
            logger.warning(f"No holder data for {ticker}")
            return ""

        result = ""

        # Major holders
        major = holders.get("major_holders")
        if major is not None and not major.empty:
            result += _df_to_markdown(major, f"Major Holders: {ticker}")
            result += "\n"

        # Institutional holders
        institutional = holders.get("institutional_holders")
        if institutional is not None and not institutional.empty:
            result += _df_to_markdown(institutional, f"Top Institutional Holders: {ticker}")
            result += "\n"

        # Mutual fund holders
        mutualfund = holders.get("mutualfund_holders")
        if mutualfund is not None and not mutualfund.empty:
            result += _df_to_markdown(mutualfund, f"Top Mutual Fund Holders: {ticker}")
            result += "\n"

        return result if result else ""
    except Exception as e:
        logger.error(f"Error prefetching holder info for {ticker}: {e}")
        return ""


def prefetch_us_market_indices(reference_date: str = None) -> dict:
    """Prefetch US market index data.

    Args:
        reference_date: Reference date (YYYYMMDD) - used for logging only

    Returns:
        Dictionary with index data as markdown strings:
        - "sp500": S&P 500 data
        - "nasdaq": NASDAQ Composite data
        - "dow": Dow Jones data
        - "russell": Russell 2000 data
        - "vix": VIX data
    """
    indices = {
        "sp500": ("^GSPC", "1y"),
        "nasdaq": ("^IXIC", "1y"),
        "dow": ("^DJI", "1y"),
        "russell": ("^RUT", "1y"),
        "vix": ("^VIX", "3mo"),
    }

    result = {}
    for key, (symbol, period) in indices.items():
        data = prefetch_us_stock_ohlcv(symbol, period=period)
        if data:
            result[key] = data

    if result:
        logger.info(f"Prefetched US market indices: {list(result.keys())}")

    return result


def prefetch_us_analysis_data(ticker: str) -> dict:
    """Prefetch all data needed for US stock analysis agents.

    Args:
        ticker: Stock ticker symbol (e.g., "AAPL")

    Returns:
        Dictionary with prefetched data:
        - "stock_ohlcv": OHLCV data as markdown
        - "holder_info": Institutional holder data as markdown
        - "market_indices": Dict of index data
    """
    result = {}

    # 1. Stock OHLCV
    stock_ohlcv = prefetch_us_stock_ohlcv(ticker, period="1y")
    if stock_ohlcv:
        result["stock_ohlcv"] = stock_ohlcv

    # 2. Holder info
    holder_info = prefetch_us_holder_info(ticker)
    if holder_info:
        result["holder_info"] = holder_info

    # 3. Market indices
    market_indices = prefetch_us_market_indices()
    if market_indices:
        result["market_indices"] = market_indices

    if result:
        logger.info(f"Prefetched US data for {ticker}: {list(result.keys())}")
    else:
        logger.warning(f"Failed to prefetch any US data for {ticker}")

    return result
