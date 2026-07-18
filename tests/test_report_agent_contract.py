"""Characterization tests for the KR detailed-report agent definitions."""

from cores.agents.company_info_agents import (
    create_company_overview_agent,
    create_company_status_agent,
)
from cores.agents.market_index_agents import create_market_index_analysis_agent
from cores.agents.news_strategy_agents import create_news_analysis_agent
from cores.agents.stock_price_agents import (
    create_investor_trading_analysis_agent,
    create_price_volume_analysis_agent,
)


def _assert_agent(agent, name, servers):
    assert agent.name == name
    assert isinstance(agent.instruction, str)
    assert len(agent.instruction) > 100
    assert tuple(agent.server_names) == tuple(servers)


def test_price_agents_preserve_names_and_tool_servers():
    price = create_price_volume_analysis_agent(
        "SK하이닉스", "000660", "20260718", "20250718", 1
    )
    investor = create_investor_trading_analysis_agent(
        "SK하이닉스", "000660", "20260718", "20250718", 1
    )

    _assert_agent(price, "price_volume_analysis_agent", ["kospi_kosdaq"])
    _assert_agent(investor, "investor_trading_analysis_agent", ["kospi_kosdaq"])


def test_prefetched_price_agents_remove_tool_servers():
    price = create_price_volume_analysis_agent(
        "SK하이닉스",
        "000660",
        "20260718",
        "20250718",
        1,
        prefetched_data="prefetched OHLCV",
    )
    investor = create_investor_trading_analysis_agent(
        "SK하이닉스",
        "000660",
        "20260718",
        "20250718",
        1,
        prefetched_data="prefetched investor flow",
    )

    _assert_agent(price, "price_volume_analysis_agent", [])
    _assert_agent(investor, "investor_trading_analysis_agent", [])


def test_company_and_news_agents_preserve_tool_servers():
    urls = {"기업현황": "https://example.test/status", "기업개요": "https://example.test/overview"}
    status = create_company_status_agent(
        "SK하이닉스", "000660", "20260718", urls
    )
    overview = create_company_overview_agent(
        "SK하이닉스", "000660", "20260718", urls
    )
    news = create_news_analysis_agent("SK하이닉스", "000660", "20260718")

    _assert_agent(status, "company_status_agent", ["firecrawl"])
    _assert_agent(overview, "company_overview_agent", ["firecrawl"])
    _assert_agent(news, "news_analysis_agent", ["perplexity", "firecrawl"])


def test_market_agent_preserves_dynamic_tool_servers():
    live = create_market_index_analysis_agent(
        "20260718", "20250718", 1
    )
    prefetched = create_market_index_analysis_agent(
        "20260718",
        "20250718",
        1,
        prefetched_kospi="kospi data",
        prefetched_kosdaq="kosdaq data",
    )

    _assert_agent(live, "market_index_analysis_agent", ["kospi_kosdaq", "perplexity"])
    _assert_agent(prefetched, "market_index_analysis_agent", ["perplexity"])
