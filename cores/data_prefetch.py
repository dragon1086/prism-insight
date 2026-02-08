"""
Data Prefetch Module for Korean Stock Analysis

Pre-fetches stock data using pykrx Python API to inject into agent instructions,
eliminating the need for kospi_kosdaq MCP server tool calls during analysis.

This reduces token usage by avoiding MCP tool call round-trips for predictable,
parameterized data fetches (OHLCV, index data, trading volume).
"""

import logging
from datetime import datetime, timedelta

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

    # Format the DataFrame as markdown table
    result += df.to_markdown(index=True) + "\n"
    return result


def prefetch_stock_ohlcv(company_code: str, start_date: str, end_date: str) -> str:
    """Prefetch stock OHLCV data using pykrx.

    Args:
        company_code: 6-digit stock code (e.g., "005930")
        start_date: Start date (YYYYMMDD)
        end_date: End date (YYYYMMDD)

    Returns:
        Markdown formatted OHLCV data string, or empty string on error
    """
    try:
        from pykrx import stock as pykrx_stock

        df = pykrx_stock.get_market_ohlcv_by_date(start_date, end_date, company_code)

        if df is None or df.empty:
            logger.warning(f"No OHLCV data for {company_code} ({start_date}~{end_date})")
            return ""

        # Rename columns for clarity
        df.columns = ["Open", "High", "Low", "Close", "Volume", "Change"]
        df.index.name = "Date"

        return _df_to_markdown(df, f"Stock OHLCV: {company_code} ({start_date}~{end_date})")
    except Exception as e:
        logger.error(f"Error prefetching OHLCV for {company_code}: {e}")
        return ""


def prefetch_stock_trading_volume(company_code: str, start_date: str, end_date: str) -> str:
    """Prefetch investor trading volume data using pykrx.

    Args:
        company_code: 6-digit stock code
        start_date: Start date (YYYYMMDD)
        end_date: End date (YYYYMMDD)

    Returns:
        Markdown formatted trading volume data string, or empty string on error
    """
    try:
        from pykrx import stock as pykrx_stock

        df = pykrx_stock.get_market_trading_volume_by_date(start_date, end_date, company_code)

        if df is None or df.empty:
            logger.warning(f"No trading volume data for {company_code} ({start_date}~{end_date})")
            return ""

        df.index.name = "Date"

        return _df_to_markdown(df, f"Investor Trading Volume: {company_code} ({start_date}~{end_date})")
    except Exception as e:
        logger.error(f"Error prefetching trading volume for {company_code}: {e}")
        return ""


def prefetch_index_ohlcv(index_ticker: str, start_date: str, end_date: str) -> str:
    """Prefetch market index OHLCV data using pykrx.

    Args:
        index_ticker: Index ticker ("1001" for KOSPI, "2001" for KOSDAQ)
        start_date: Start date (YYYYMMDD)
        end_date: End date (YYYYMMDD)

    Returns:
        Markdown formatted index data string, or empty string on error
    """
    try:
        from pykrx import stock as pykrx_stock

        index_name = "KOSPI" if index_ticker == "1001" else "KOSDAQ" if index_ticker == "2001" else index_ticker

        # Try to get index data - pykrx may fail on getting index name, so we wrap it
        try:
            df = pykrx_stock.get_index_ohlcv_by_date(start_date, end_date, index_ticker)
        except KeyError as ke:
            # If the error is about '지수명' column, try alternative approach
            if '지수명' in str(ke):
                logger.warning(f"pykrx index name lookup failed for {index_ticker}, trying raw fetch...")
                # Use lower-level API that doesn't fetch index name
                from pykrx.website.krx.market.core import Stock
                stock = Stock()
                df = stock.get_index_ohlcv(start_date, end_date, index_ticker, freq='d')
            else:
                raise

        if df is None or df.empty:
            logger.warning(f"No index data for {index_ticker} ({start_date}~{end_date})")
            return ""

        df.index.name = "Date"

        return _df_to_markdown(df, f"{index_name} Index ({start_date}~{end_date})")
    except Exception as e:
        logger.error(f"Error prefetching index OHLCV for {index_ticker}: {e}")
        return ""


def prefetch_kr_analysis_data(company_code: str, reference_date: str, max_years_ago: str) -> dict:
    """Prefetch all data needed for KR stock analysis agents.

    This function gathers all the data that would normally be fetched via
    kospi_kosdaq MCP server tool calls during analysis.

    Args:
        company_code: 6-digit stock code
        reference_date: Analysis reference date (YYYYMMDD)
        max_years_ago: Start date for data collection (YYYYMMDD)

    Returns:
        Dictionary with prefetched data:
        - "stock_ohlcv": OHLCV data as markdown
        - "trading_volume": Investor trading volume as markdown
        - "kospi_index": KOSPI index data as markdown
        - "kosdaq_index": KOSDAQ index data as markdown
        Returns empty dict on total failure.
    """
    result = {}

    # 1. Stock OHLCV data
    stock_ohlcv = prefetch_stock_ohlcv(company_code, max_years_ago, reference_date)
    if stock_ohlcv:
        result["stock_ohlcv"] = stock_ohlcv

    # 2. Investor trading volume data
    trading_volume = prefetch_stock_trading_volume(company_code, max_years_ago, reference_date)
    if trading_volume:
        result["trading_volume"] = trading_volume

    # 3. KOSPI index data
    kospi_index = prefetch_index_ohlcv("1001", max_years_ago, reference_date)
    if kospi_index:
        result["kospi_index"] = kospi_index

    # 4. KOSDAQ index data
    kosdaq_index = prefetch_index_ohlcv("2001", max_years_ago, reference_date)
    if kosdaq_index:
        result["kosdaq_index"] = kosdaq_index

    if result:
        logger.info(f"Prefetched KR data for {company_code}: {list(result.keys())}")
    else:
        logger.warning(f"Failed to prefetch any KR data for {company_code}")

    return result
