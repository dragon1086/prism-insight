"""
PRISM-US AI Agents Module

Specialized AI agents for US stock market analysis:
- stock_price_agents: Technical analysis (yfinance-based)
- company_info_agents: Fundamental analysis (SEC filings)
- news_strategy_agents: News and investment strategy
- market_index_agents: S&P500, NASDAQ, Dow analysis
- trading_agents: Buy/Sell decision agents
"""

from typing import Dict, List
from pathlib import Path
import importlib.util

# Get the directory containing this file
_AGENTS_DIR = Path(__file__).parent


def _load_local_module(module_name: str):
    """Load a module from the local agents directory."""
    module_path = _AGENTS_DIR / f"{module_name}.py"
    spec = importlib.util.spec_from_file_location(f"us_{module_name}", module_path)
    if spec and spec.loader:
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    raise ImportError(f"Could not load {module_name} from {module_path}")


# Pre-load all agent modules from local directory
_stock_price_agents = _load_local_module("stock_price_agents")
_company_info_agents = _load_local_module("company_info_agents")
_news_strategy_agents = _load_local_module("news_strategy_agents")
_market_index_agents = _load_local_module("market_index_agents")


def get_us_data_urls(ticker: str) -> Dict[str, str]:
    """
    Generate URLs for US stock data sources.

    Args:
        ticker: Stock ticker symbol (e.g., "AAPL", "MSFT")

    Returns:
        Dictionary of data source URLs
    """
    return {
        "profile": f"https://finance.yahoo.com/quote/{ticker}/profile",
        "key_statistics": f"https://finance.yahoo.com/quote/{ticker}/key-statistics",
        "financials": f"https://finance.yahoo.com/quote/{ticker}/financials",
        "analysis": f"https://finance.yahoo.com/quote/{ticker}/analysis",
        "holders": f"https://finance.yahoo.com/quote/{ticker}/holders",
        "news": f"https://finance.yahoo.com/quote/{ticker}/news",
        "sec_filings": f"https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK={ticker}&type=10-&dateb=&owner=include&count=40",
    }


def get_us_agent_directory(
    company_name: str,
    ticker: str,
    reference_date: str,
    base_sections: List[str],
    language: str = "en"
) -> Dict:
    """
    Returns a directory of agents for each section.

    Args:
        company_name: Company name (e.g., "Apple Inc.")
        ticker: Stock ticker symbol (e.g., "AAPL")
        reference_date: Analysis reference date (YYYYMMDD)
        base_sections: List of sections to generate agents for
        language: Language code (default: "en" for US market)

    Returns:
        Dict[str, Agent]: Dictionary of agents keyed by section name
    """
    # Import agent creators from pre-loaded local modules
    create_us_price_volume_analysis_agent = _stock_price_agents.create_us_price_volume_analysis_agent
    create_us_institutional_holdings_analysis_agent = _stock_price_agents.create_us_institutional_holdings_analysis_agent
    create_us_company_status_agent = _company_info_agents.create_us_company_status_agent
    create_us_company_overview_agent = _company_info_agents.create_us_company_overview_agent
    create_us_news_analysis_agent = _news_strategy_agents.create_us_news_analysis_agent
    create_us_market_index_analysis_agent = _market_index_agents.create_us_market_index_analysis_agent

    # Generate URLs for US data sources
    urls = get_us_data_urls(ticker)

    # Calculate date range (1 year for trading analysis - sufficient for trading decisions)
    from datetime import datetime, timedelta
    ref_date = datetime.strptime(reference_date, "%Y%m%d")
    max_years = 1
    max_years_ago = (ref_date - timedelta(days=365 * max_years)).strftime("%Y%m%d")

    agent_creators = {
        "price_volume_analysis": lambda: create_us_price_volume_analysis_agent(
            company_name, ticker, reference_date, max_years_ago, max_years, language
        ),
        "institutional_holdings_analysis": lambda: create_us_institutional_holdings_analysis_agent(
            company_name, ticker, reference_date, max_years_ago, max_years, language
        ),
        "company_status": lambda: create_us_company_status_agent(
            company_name, ticker, reference_date, urls, language
        ),
        "company_overview": lambda: create_us_company_overview_agent(
            company_name, ticker, reference_date, urls, language
        ),
        "news_analysis": lambda: create_us_news_analysis_agent(
            company_name, ticker, reference_date, language
        ),
        "market_index_analysis": lambda: create_us_market_index_analysis_agent(
            reference_date, max_years_ago, max_years, language
        )
    }

    agents = {}
    for section in base_sections:
        if section in agent_creators:
            agents[section] = agent_creators[section]()

    return agents
