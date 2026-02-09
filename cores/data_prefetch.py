"""
Data Prefetch Module for Korean Stock Analysis

Pre-fetches stock data by calling kospi_kosdaq MCP server's library functions directly
(not via MCP protocol), eliminating MCP tool call round-trips during analysis.

Architecture:
- Direct call: import kospi_kosdaq_stock_server module → call functions → Dict → markdown
- MCP fallback: if import fails, agents use MCP tool calls as before (no prefetch)

This mirrors the US module's pattern (us_data_client.py direct import).
"""

import logging

import pandas as pd

logger = logging.getLogger(__name__)


def _dict_to_markdown(data: dict, title: str = "") -> str:
    """Convert MCP server's dict response to markdown table string.

    The kospi_kosdaq MCP server functions return Dict[str, Any] with date keys.
    This converts them back to DataFrame for markdown rendering.

    Args:
        data: Date-keyed dict from MCP server functions (e.g., {"2026-02-09": {"Open": ..., ...}})
        title: Optional title to prepend

    Returns:
        Markdown table string, or empty string if data is empty/error
    """
    if not data or "error" in data:
        return ""

    df = pd.DataFrame.from_dict(data, orient='index')
    if df.empty:
        return ""

    df.index.name = "Date"

    result = ""
    if title:
        result += f"### {title}\n\n"

    result += df.to_markdown(index=True) + "\n"
    return result


def _get_mcp_server_module():
    """Import kospi_kosdaq_stock_server module for direct library calls.

    Returns:
        The kospi_kosdaq_stock_server module, or None if import fails
    """
    try:
        import kospi_kosdaq_stock_server as server
        return server
    except ImportError:
        logger.warning("kospi_kosdaq_stock_server module not available, prefetch disabled")
        return None


def prefetch_stock_ohlcv(company_code: str, start_date: str, end_date: str) -> str:
    """Prefetch stock OHLCV data via kospi_kosdaq MCP server library.

    Args:
        company_code: 6-digit stock code (e.g., "005930")
        start_date: Start date (YYYYMMDD)
        end_date: End date (YYYYMMDD)

    Returns:
        Markdown formatted OHLCV data string, or empty string on error
    """
    try:
        server = _get_mcp_server_module()
        if not server:
            return ""

        data = server.get_stock_ohlcv(start_date, end_date, company_code)

        return _dict_to_markdown(data, f"Stock OHLCV: {company_code} ({start_date}~{end_date})")
    except Exception as e:
        logger.error(f"Error prefetching OHLCV for {company_code}: {e}")
        return ""


def prefetch_stock_trading_volume(company_code: str, start_date: str, end_date: str) -> str:
    """Prefetch investor trading volume data via kospi_kosdaq MCP server library.

    Args:
        company_code: 6-digit stock code
        start_date: Start date (YYYYMMDD)
        end_date: End date (YYYYMMDD)

    Returns:
        Markdown formatted trading volume data string, or empty string on error
    """
    try:
        server = _get_mcp_server_module()
        if not server:
            return ""

        data = server.get_stock_trading_volume(start_date, end_date, company_code)

        return _dict_to_markdown(data, f"Investor Trading Volume: {company_code} ({start_date}~{end_date})")
    except Exception as e:
        logger.error(f"Error prefetching trading volume for {company_code}: {e}")
        return ""


def prefetch_index_ohlcv(index_ticker: str, start_date: str, end_date: str) -> str:
    """Prefetch market index OHLCV data via kospi_kosdaq MCP server library.

    Args:
        index_ticker: Index ticker ("1001" for KOSPI, "2001" for KOSDAQ)
        start_date: Start date (YYYYMMDD)
        end_date: End date (YYYYMMDD)

    Returns:
        Markdown formatted index data string, or empty string on error
    """
    try:
        server = _get_mcp_server_module()
        if not server:
            return ""

        index_name = "KOSPI" if index_ticker == "1001" else "KOSDAQ" if index_ticker == "2001" else index_ticker

        data = server.get_index_ohlcv(start_date, end_date, index_ticker)

        return _dict_to_markdown(data, f"{index_name} Index ({start_date}~{end_date})")
    except Exception as e:
        logger.error(f"Error prefetching index OHLCV for {index_ticker}: {e}")
        return ""


def prefetch_kr_analysis_data(company_code: str, reference_date: str, max_years_ago: str) -> dict:
    """Prefetch all data needed for KR stock analysis agents.

    Calls kospi_kosdaq MCP server's library functions directly (not via MCP protocol).
    If the library is unavailable, returns empty dict and agents fall back to MCP tool calls.

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
