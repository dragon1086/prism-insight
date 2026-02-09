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


def prefetch_stock_info(ticker: str) -> str:
    """Prefetch company info and key statistics via yfinance.

    Replaces yahoo_finance MCP get_stock_info call and
    firecrawl key-statistics/financials page scrapes.

    Args:
        ticker: Stock ticker symbol

    Returns:
        Markdown formatted company info string, or empty string on error
    """
    try:
        client = _get_us_data_client()
        info = client.get_company_info(ticker)

        if not info or not info.get("name"):
            logger.warning(f"No company info for {ticker}")
            return ""

        def _fmt(val, fmt_type="default"):
            if val is None or val == 0:
                return "N/A"
            if fmt_type == "currency":
                if abs(val) >= 1e12:
                    return f"${val/1e12:.2f}T"
                elif abs(val) >= 1e9:
                    return f"${val/1e9:.2f}B"
                elif abs(val) >= 1e6:
                    return f"${val/1e6:.2f}M"
                return f"${val:,.2f}"
            elif fmt_type == "percent":
                return f"{val*100:.2f}%" if abs(val) < 1 else f"{val:.2f}%"
            elif fmt_type == "ratio":
                return f"{val:.2f}"
            elif fmt_type == "number":
                if abs(val) >= 1e9:
                    return f"{val/1e9:.2f}B"
                elif abs(val) >= 1e6:
                    return f"{val/1e6:.2f}M"
                return f"{val:,.0f}"
            return str(val)

        result = f"### Company Info: {info.get('name', ticker)} ({ticker})\n\n"

        result += "#### Valuation Measures\n\n"
        result += "| Metric | Value |\n|--------|-------|\n"
        result += f"| Market Cap | {_fmt(info.get('market_cap'), 'currency')} |\n"
        result += f"| Enterprise Value | {_fmt(info.get('enterprise_value'), 'currency')} |\n"
        result += f"| Trailing P/E | {_fmt(info.get('pe_ratio'), 'ratio')} |\n"
        result += f"| Forward P/E | {_fmt(info.get('forward_pe'), 'ratio')} |\n"
        result += f"| PEG Ratio | {_fmt(info.get('peg_ratio'), 'ratio')} |\n"
        result += f"| Price/Sales | {_fmt(info.get('price_to_sales'), 'ratio')} |\n"
        result += f"| Price/Book | {_fmt(info.get('price_to_book'), 'ratio')} |\n"
        result += "\n"

        result += "#### Financial Highlights\n\n"
        result += "| Metric | Value |\n|--------|-------|\n"
        result += f"| Revenue | {_fmt(info.get('revenue'), 'currency')} |\n"
        result += f"| Gross Profit | {_fmt(info.get('gross_profit'), 'currency')} |\n"
        result += f"| EBITDA | {_fmt(info.get('ebitda'), 'currency')} |\n"
        result += f"| Net Income | {_fmt(info.get('net_income'), 'currency')} |\n"
        result += f"| Diluted EPS | {_fmt(info.get('earnings_per_share'), 'ratio')} |\n"
        result += f"| Profit Margin | {_fmt(info.get('profit_margin'), 'percent')} |\n"
        result += f"| Operating Margin | {_fmt(info.get('operating_margin'), 'percent')} |\n"
        result += f"| ROA | {_fmt(info.get('return_on_assets'), 'percent')} |\n"
        result += f"| ROE | {_fmt(info.get('return_on_equity'), 'percent')} |\n"
        result += "\n"

        result += "#### Trading Information\n\n"
        result += "| Metric | Value |\n|--------|-------|\n"
        result += f"| Current Price | {_fmt(info.get('price'), 'currency')} |\n"
        result += f"| Previous Close | {_fmt(info.get('previous_close'), 'currency')} |\n"
        result += f"| Beta | {_fmt(info.get('beta'), 'ratio')} |\n"
        result += f"| 52-Week High | {_fmt(info.get('fifty_two_week_high'), 'currency')} |\n"
        result += f"| 52-Week Low | {_fmt(info.get('fifty_two_week_low'), 'currency')} |\n"
        result += f"| 50-Day Average | {_fmt(info.get('fifty_day_avg'), 'currency')} |\n"
        result += f"| 200-Day Average | {_fmt(info.get('two_hundred_day_avg'), 'currency')} |\n"
        result += f"| Avg Volume (3mo) | {_fmt(info.get('avg_volume'), 'number')} |\n"
        result += f"| Shares Outstanding | {_fmt(info.get('shares_outstanding'), 'number')} |\n"
        result += f"| Float Shares | {_fmt(info.get('float_shares'), 'number')} |\n"
        result += f"| Short Ratio | {_fmt(info.get('short_ratio'), 'ratio')} |\n"
        result += "\n"

        result += "#### Dividend Info\n\n"
        result += "| Metric | Value |\n|--------|-------|\n"
        result += f"| Dividend Rate | {_fmt(info.get('dividend_rate'), 'currency')} |\n"
        result += f"| Dividend Yield | {_fmt(info.get('dividend_yield'), 'percent')} |\n"
        result += f"| Payout Ratio | {_fmt(info.get('payout_ratio'), 'percent')} |\n"
        result += "\n"

        result += "#### Analyst Targets\n\n"
        result += "| Metric | Value |\n|--------|-------|\n"
        result += f"| Target High | {_fmt(info.get('target_high'), 'currency')} |\n"
        result += f"| Target Low | {_fmt(info.get('target_low'), 'currency')} |\n"
        result += f"| Target Mean | {_fmt(info.get('target_mean'), 'currency')} |\n"
        result += f"| Target Median | {_fmt(info.get('target_median'), 'currency')} |\n"
        result += f"| Recommendation | {info.get('recommendation', 'N/A')} |\n"
        result += f"| Number of Analysts | {info.get('num_analysts', 'N/A')} |\n"
        result += "\n"

        return result
    except Exception as e:
        logger.error(f"Error prefetching stock info for {ticker}: {e}")
        return ""


def prefetch_recommendations(ticker: str) -> str:
    """Prefetch analyst recommendations via yfinance.

    Args:
        ticker: Stock ticker symbol

    Returns:
        Markdown formatted recommendations string, or empty string on error
    """
    try:
        import yfinance as yf
        stock = yf.Ticker(ticker)
        recs = stock.recommendations

        if recs is None or recs.empty:
            logger.warning(f"No recommendations for {ticker}")
            return ""

        return _df_to_markdown(recs, f"Analyst Recommendations: {ticker}")
    except Exception as e:
        logger.error(f"Error prefetching recommendations for {ticker}: {e}")
        return ""


def prefetch_analysis_estimates(ticker: str) -> str:
    """Prefetch earnings/revenue estimates and analyst data via yfinance.

    Replaces firecrawl scrape of Yahoo Finance Analysis page for company_status agent.

    Args:
        ticker: Stock ticker symbol

    Returns:
        Markdown formatted analysis estimates string, or empty string on error
    """
    try:
        import yfinance as yf
        stock = yf.Ticker(ticker)

        result = ""

        # 1. Earnings Estimates
        try:
            earnings_est = stock.earnings_estimate
            if earnings_est is not None and not earnings_est.empty:
                result += _df_to_markdown(earnings_est, f"Earnings Estimates: {ticker}")
                result += "\n"
        except Exception as e:
            logger.debug(f"No earnings estimates for {ticker}: {e}")

        # 2. Revenue Estimates
        try:
            revenue_est = stock.revenue_estimate
            if revenue_est is not None and not revenue_est.empty:
                result += _df_to_markdown(revenue_est, f"Revenue Estimates: {ticker}")
                result += "\n"
        except Exception as e:
            logger.debug(f"No revenue estimates for {ticker}: {e}")

        # 3. EPS Trend
        try:
            eps_trend = stock.eps_trend
            if eps_trend is not None and not eps_trend.empty:
                result += _df_to_markdown(eps_trend, f"EPS Trend: {ticker}")
                result += "\n"
        except Exception as e:
            logger.debug(f"No EPS trend for {ticker}: {e}")

        # 4. EPS Revisions
        try:
            eps_revisions = stock.eps_revisions
            if eps_revisions is not None and not eps_revisions.empty:
                result += _df_to_markdown(eps_revisions, f"EPS Revisions: {ticker}")
                result += "\n"
        except Exception as e:
            logger.debug(f"No EPS revisions for {ticker}: {e}")

        # 5. Growth Estimates
        try:
            growth_est = stock.growth_estimates
            if growth_est is not None and not growth_est.empty:
                result += _df_to_markdown(growth_est, f"Growth Estimates: {ticker}")
                result += "\n"
        except Exception as e:
            logger.debug(f"No growth estimates for {ticker}: {e}")

        # 6. Analyst Price Targets (dict format)
        try:
            targets = stock.analyst_price_targets
            if targets and isinstance(targets, dict):
                result += f"### Analyst Price Targets: {ticker}\n\n"
                result += "| Metric | Value |\n|--------|-------|\n"
                result += f"| Current | ${targets.get('current', 'N/A')} |\n"
                result += f"| High | ${targets.get('high', 'N/A')} |\n"
                result += f"| Low | ${targets.get('low', 'N/A')} |\n"
                result += f"| Mean | ${targets.get('mean', 'N/A')} |\n"
                result += f"| Median | ${targets.get('median', 'N/A')} |\n"
                result += "\n"
        except Exception as e:
            logger.debug(f"No analyst price targets for {ticker}: {e}")

        # 7. Recommendations Summary
        try:
            rec_summary = stock.recommendations_summary
            if rec_summary is not None and not rec_summary.empty:
                result += _df_to_markdown(rec_summary, f"Recommendations Summary: {ticker}")
                result += "\n"
        except Exception as e:
            logger.debug(f"No recommendations summary for {ticker}: {e}")

        if not result:
            logger.warning(f"No analysis estimates data for {ticker}")
            return ""

        return result
    except Exception as e:
        logger.error(f"Error prefetching analysis estimates for {ticker}: {e}")
        return ""


def prefetch_company_profile(ticker: str) -> str:
    """Prefetch company profile data via yfinance.

    Replaces firecrawl profile page scrape for company_overview agent.

    Args:
        ticker: Stock ticker symbol

    Returns:
        Markdown formatted company profile string, or empty string on error
    """
    try:
        import yfinance as yf
        stock = yf.Ticker(ticker)
        info = stock.info

        if not info:
            logger.warning(f"No profile info for {ticker}")
            return ""

        result = f"### Company Profile: {info.get('longName', ticker)}\n\n"

        result += "#### Basic Information\n\n"
        result += "| Field | Value |\n|-------|-------|\n"
        result += f"| Company Name | {info.get('longName', 'N/A')} |\n"
        result += f"| Sector | {info.get('sector', 'N/A')} |\n"
        result += f"| Industry | {info.get('industry', 'N/A')} |\n"
        result += f"| Website | {info.get('website', 'N/A')} |\n"
        employees = info.get('fullTimeEmployees')
        result += f"| Full-Time Employees | {employees:,} |\n" if employees else "| Full-Time Employees | N/A |\n"
        city = info.get('city', '')
        state = info.get('state', '')
        country = info.get('country', '')
        address = ", ".join(filter(None, [city, state, country]))
        result += f"| Headquarters | {address or 'N/A'} |\n"
        result += "\n"

        description = info.get('longBusinessSummary', '')
        if description:
            result += "#### Business Description\n\n"
            result += f"{description}\n\n"

        officers = info.get('companyOfficers', [])
        if officers:
            result += "#### Key Executives\n\n"
            result += "| Name | Title | Total Pay |\n|------|-------|-----------|\n"
            for officer in officers[:10]:
                name = officer.get('name', 'N/A')
                title = officer.get('title', 'N/A')
                pay = officer.get('totalPay', 0)
                pay_str = f"${pay:,.0f}" if pay else "N/A"
                result += f"| {name} | {title} | {pay_str} |\n"
            result += "\n"

        return result
    except Exception as e:
        logger.error(f"Error prefetching company profile for {ticker}: {e}")
        return ""


def prefetch_financial_statements(ticker: str) -> str:
    """Prefetch financial statements (income statement, balance sheet, cash flow) via yfinance.

    Replaces SEC EDGAR get_financials/get_key_metrics calls.

    Args:
        ticker: Stock ticker symbol

    Returns:
        Markdown formatted financial statements string, or empty string on error
    """
    try:
        import yfinance as yf
        stock = yf.Ticker(ticker)

        result = ""

        # Annual income statement
        try:
            income = stock.income_stmt
            if income is not None and not income.empty:
                result += _df_to_markdown(income, f"Annual Income Statement: {ticker}")
                result += "\n"
        except Exception as e:
            logger.debug(f"No annual income statement for {ticker}: {e}")

        # Annual balance sheet
        try:
            balance = stock.balance_sheet
            if balance is not None and not balance.empty:
                result += _df_to_markdown(balance, f"Annual Balance Sheet: {ticker}")
                result += "\n"
        except Exception as e:
            logger.debug(f"No annual balance sheet for {ticker}: {e}")

        # Annual cash flow
        try:
            cashflow = stock.cashflow
            if cashflow is not None and not cashflow.empty:
                result += _df_to_markdown(cashflow, f"Annual Cash Flow: {ticker}")
                result += "\n"
        except Exception as e:
            logger.debug(f"No annual cash flow for {ticker}: {e}")

        # Quarterly income statement (latest 4 quarters)
        try:
            q_income = stock.quarterly_income_stmt
            if q_income is not None and not q_income.empty:
                result += _df_to_markdown(q_income, f"Quarterly Income Statement: {ticker}")
                result += "\n"
        except Exception as e:
            logger.debug(f"No quarterly income statement for {ticker}: {e}")

        # Quarterly balance sheet
        try:
            q_balance = stock.quarterly_balance_sheet
            if q_balance is not None and not q_balance.empty:
                result += _df_to_markdown(q_balance, f"Quarterly Balance Sheet: {ticker}")
                result += "\n"
        except Exception as e:
            logger.debug(f"No quarterly balance sheet for {ticker}: {e}")

        # Quarterly cash flow
        try:
            q_cashflow = stock.quarterly_cashflow
            if q_cashflow is not None and not q_cashflow.empty:
                result += _df_to_markdown(q_cashflow, f"Quarterly Cash Flow: {ticker}")
                result += "\n"
        except Exception as e:
            logger.debug(f"No quarterly cash flow for {ticker}: {e}")

        if not result:
            logger.warning(f"No financial statements for {ticker}")
            return ""

        return result
    except Exception as e:
        logger.error(f"Error prefetching financial statements for {ticker}: {e}")
        return ""


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

    # 4. Stock info (for company_status - replaces key-statistics/financials firecrawl + yahoo_finance MCP)
    stock_info = prefetch_stock_info(ticker)
    if stock_info:
        result["stock_info"] = stock_info

    # 5. Recommendations (for company_status - replaces yahoo_finance MCP get_recommendations)
    recommendations = prefetch_recommendations(ticker)
    if recommendations:
        result["recommendations"] = recommendations

    # 6. Company profile (for company_overview - replaces firecrawl profile page)
    company_profile = prefetch_company_profile(ticker)
    if company_profile:
        result["company_profile"] = company_profile

    # 7. Analysis estimates (for company_status - replaces firecrawl Analysis page)
    analysis_estimates = prefetch_analysis_estimates(ticker)
    if analysis_estimates:
        result["analysis_estimates"] = analysis_estimates

    # 8. Financial statements (for company_status - replaces SEC EDGAR financials)
    financial_statements = prefetch_financial_statements(ticker)
    if financial_statements:
        result["financial_statements"] = financial_statements

    if result:
        logger.info(f"Prefetched US data for {ticker}: {list(result.keys())}")
    else:
        logger.warning(f"Failed to prefetch any US data for {ticker}")

    return result
