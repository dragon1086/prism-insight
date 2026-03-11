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


def prefetch_macro_intelligence_data(reference_date: str) -> dict:
    """Prefetch data for macro intelligence analysis.

    Fetches KOSPI/KOSDAQ index data and sector mapping, then computes market regime
    programmatically from price data (not LLM-based).

    Args:
        reference_date: Analysis date (YYYYMMDD)

    Returns:
        Dictionary with:
        - "kospi_ohlcv_md": KOSPI 20-day OHLCV as markdown
        - "kosdaq_ohlcv_md": KOSDAQ 20-day OHLCV as markdown
        - "sector_map": ticker → sector mapping dict
        - "computed_regime": programmatically computed regime info dict
    """
    from datetime import datetime, timedelta

    result = {}

    server = _get_mcp_server_module()
    if not server:
        return result

    ref_dt = datetime.strptime(reference_date, "%Y%m%d")
    start_date = (ref_dt - timedelta(days=45)).strftime("%Y%m%d")

    # 1. KOSPI index OHLCV
    kospi_md = prefetch_index_ohlcv("1001", start_date, reference_date)
    if kospi_md:
        result["kospi_ohlcv_md"] = kospi_md

    # 2. KOSDAQ index OHLCV
    kosdaq_md = prefetch_index_ohlcv("2001", start_date, reference_date)
    if kosdaq_md:
        result["kosdaq_ohlcv_md"] = kosdaq_md

    # 3. Sector map (ticker → sector name) via get_sector_info
    try:
        import json as _json
        # Fetch KOSPI + KOSDAQ sector classifications
        kospi_sectors = server.get_sector_info("KOSPI")
        kosdaq_sectors = server.get_sector_info("KOSDAQ")
        sector_data = {}
        for raw in [kospi_sectors, kosdaq_sectors]:
            parsed = _json.loads(raw) if isinstance(raw, str) else raw
            if isinstance(parsed, dict) and "error" not in parsed:
                sector_data.update(parsed)
        if sector_data:
            result["sector_map"] = sector_data
            logger.info(f"Prefetched sector_map: {len(sector_data)} tickers")
        else:
            logger.warning("Sector map not available from get_sector_info")
    except Exception as e:
        logger.error(f"Error fetching sector map: {e}")

    # 4. Compute regime from raw KOSPI data
    try:
        kospi_raw = server.get_index_ohlcv(start_date, reference_date, "1001")
        kosdaq_raw = server.get_index_ohlcv(start_date, reference_date, "2001")
        if kospi_raw:
            result["computed_regime"] = _compute_kr_regime(kospi_raw, kosdaq_raw)
    except Exception as e:
        logger.error(f"Error computing regime: {e}")

    if result:
        logger.info(f"Prefetched macro intelligence data: {list(result.keys())}")

    return result


def _compute_kr_regime(kospi_ohlcv: dict, kosdaq_ohlcv: dict = None) -> dict:
    """Compute KR market regime programmatically from KOSPI OHLCV data.

    Uses 20-day MA position and 2-week change rate for classification.

    Returns:
        Dict with regime classification, index summary, and confidence.
    """
    df = pd.DataFrame.from_dict(kospi_ohlcv, orient='index')
    if df.empty or len(df) < 10:
        return {"market_regime": "sideways", "regime_confidence": 0.3, "simple_ma_regime": "sideways"}

    df.index = pd.to_datetime(df.index)
    df = df.sort_index()
    df_20d = df.tail(20)

    # Determine close column name (could be "Close" or "종가")
    close_col = None
    for col_name in ["Close", "종가"]:
        if col_name in df.columns:
            close_col = col_name
            break
    if not close_col:
        close_col = df.columns[3]  # fallback to 4th column (typically Close)

    current_price = float(df_20d[close_col].iloc[-1])
    ma_20d = float(df_20d[close_col].mean())

    # 2-week change (last 10 trading days)
    if len(df_20d) >= 10:
        price_2w_ago = float(df_20d[close_col].iloc[-10])
    else:
        price_2w_ago = float(df_20d[close_col].iloc[0])
    change_2w_pct = ((current_price - price_2w_ago) / price_2w_ago) * 100

    # MA position
    ma_diff_pct = ((current_price - ma_20d) / ma_20d) * 100
    above_ma = current_price > ma_20d

    # simple_ma_regime (pure index-based)
    if abs(ma_diff_pct) <= 0.5:
        simple_ma_regime = "sideways"
    elif above_ma:
        simple_ma_regime = "bull"
    else:
        simple_ma_regime = "bear"

    # KOSPI 20d trend
    if change_2w_pct > 2:
        kospi_trend = "up"
    elif change_2w_pct < -2:
        kospi_trend = "down"
    else:
        kospi_trend = "sideways"

    # Market regime classification (KR uses 2-week / ±5% thresholds)
    if above_ma and change_2w_pct > 5:
        regime = "strong_bull"
        confidence = 0.85
    elif above_ma and change_2w_pct >= 0:
        regime = "moderate_bull"
        confidence = 0.75
    elif abs(ma_diff_pct) <= 1 and abs(change_2w_pct) < 2:
        regime = "sideways"
        confidence = 0.65
    elif not above_ma and change_2w_pct < -5:
        regime = "strong_bear"
        confidence = 0.85
    else:
        regime = "moderate_bear"
        confidence = 0.75

    # KOSDAQ trend (if available)
    kosdaq_trend = "sideways"
    if kosdaq_ohlcv:
        try:
            kd_df = pd.DataFrame.from_dict(kosdaq_ohlcv, orient='index')
            kd_df.index = pd.to_datetime(kd_df.index)
            kd_df = kd_df.sort_index().tail(20)
            kd_close = None
            for col_name in ["Close", "종가"]:
                if col_name in kd_df.columns:
                    kd_close = col_name
                    break
            if kd_close and len(kd_df) >= 10:
                kd_current = float(kd_df[kd_close].iloc[-1])
                kd_prev = float(kd_df[kd_close].iloc[-10])
                kd_change = ((kd_current - kd_prev) / kd_prev) * 100
                if kd_change > 2:
                    kosdaq_trend = "up"
                elif kd_change < -2:
                    kosdaq_trend = "down"
        except Exception:
            pass

    return {
        "market_regime": regime,
        "regime_confidence": confidence,
        "simple_ma_regime": simple_ma_regime,
        "index_summary": {
            "kospi_20d_trend": kospi_trend,
            "kospi_vs_20d_ma": "above" if above_ma else "below",
            "kospi_2w_change_pct": round(change_2w_pct, 2),
            "kospi_current": round(current_price, 2),
            "kospi_20d_ma": round(ma_20d, 2),
            "kosdaq_20d_trend": kosdaq_trend,
        }
    }


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
