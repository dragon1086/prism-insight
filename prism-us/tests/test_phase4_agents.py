"""
Phase 4: Core Agents Tests

Tests for cores/agents/ module:
- URL generation
- Agent factory functions
- Agent directory
"""

import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

# Add paths for imports
PRISM_US_DIR = Path(__file__).parent.parent
PROJECT_ROOT = PRISM_US_DIR.parent
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PRISM_US_DIR))

from cores.agents import get_us_data_urls, get_us_agent_directory


# =============================================================================
# Test: URL Generation
# =============================================================================

class TestURLGeneration:
    """Tests for get_us_data_urls function."""

    def test_get_us_data_urls_returns_dict(self, sample_ticker):
        """Test that get_us_data_urls returns a dictionary."""
        urls = get_us_data_urls(sample_ticker)
        assert isinstance(urls, dict)

    def test_get_us_data_urls_has_required_keys(self, sample_ticker):
        """Test that URL dict has all required keys."""
        urls = get_us_data_urls(sample_ticker)

        required_keys = [
            'profile',
            'key_statistics',
            'financials',
            'analysis',
            'holders',
            'news',
            'sec_filings',
        ]
        for key in required_keys:
            assert key in urls, f"Missing URL key: {key}"

    def test_get_us_data_urls_contains_ticker(self, sample_ticker):
        """Test that URLs contain the ticker symbol."""
        urls = get_us_data_urls(sample_ticker)

        for key, url in urls.items():
            if key != 'sec_filings':  # SEC uses CIK which might differ
                assert sample_ticker in url, f"URL for {key} should contain ticker"

    def test_get_us_data_urls_yahoo_finance(self, sample_ticker):
        """Test Yahoo Finance URLs are correct."""
        urls = get_us_data_urls(sample_ticker)

        assert 'finance.yahoo.com' in urls['profile']
        assert 'finance.yahoo.com' in urls['key_statistics']
        assert 'finance.yahoo.com' in urls['financials']
        assert 'finance.yahoo.com' in urls['analysis']
        assert 'finance.yahoo.com' in urls['holders']
        assert 'finance.yahoo.com' in urls['news']

    def test_get_us_data_urls_sec_filings(self, sample_ticker):
        """Test SEC filings URL is correct."""
        urls = get_us_data_urls(sample_ticker)

        assert 'sec.gov' in urls['sec_filings']
        assert 'edgar' in urls['sec_filings'].lower()

    def test_get_us_data_urls_different_tickers(self, sample_tickers):
        """Test URL generation for different tickers."""
        for ticker in sample_tickers:
            urls = get_us_data_urls(ticker)
            assert ticker in urls['profile']


# =============================================================================
# Test: Agent Directory
# =============================================================================

class TestAgentDirectory:
    """Tests for get_us_agent_directory function."""

    def test_returns_dict(self, sample_ticker, sample_reference_date, agent_sections):
        """Test that get_us_agent_directory returns a dictionary."""
        agents = get_us_agent_directory(
            company_name="Apple Inc.",
            ticker=sample_ticker,
            reference_date=sample_reference_date,
            base_sections=agent_sections,
            language="en"
        )
        assert isinstance(agents, dict)

    def test_returns_agents_for_sections(self, sample_ticker, sample_reference_date, agent_sections):
        """Test that agents are created for requested sections."""
        agents = get_us_agent_directory(
            company_name="Apple Inc.",
            ticker=sample_ticker,
            reference_date=sample_reference_date,
            base_sections=agent_sections,
            language="en"
        )

        for section in agent_sections:
            assert section in agents, f"Missing agent for section: {section}"

    def test_agent_has_name(self, sample_ticker, sample_reference_date):
        """Test that created agents have names."""
        agents = get_us_agent_directory(
            company_name="Apple Inc.",
            ticker=sample_ticker,
            reference_date=sample_reference_date,
            base_sections=['price_volume_analysis'],
            language="en"
        )

        agent = agents.get('price_volume_analysis')
        assert agent is not None
        assert hasattr(agent, 'name')
        assert agent.name is not None

    def test_agent_has_server_names(self, sample_ticker, sample_reference_date):
        """Test that agents have MCP server references."""
        agents = get_us_agent_directory(
            company_name="Apple Inc.",
            ticker=sample_ticker,
            reference_date=sample_reference_date,
            base_sections=['price_volume_analysis'],
            language="en"
        )

        agent = agents.get('price_volume_analysis')
        assert agent is not None
        assert hasattr(agent, 'server_names')

    def test_empty_sections_returns_empty_dict(self, sample_ticker, sample_reference_date):
        """Test that empty sections list returns empty dict."""
        agents = get_us_agent_directory(
            company_name="Apple Inc.",
            ticker=sample_ticker,
            reference_date=sample_reference_date,
            base_sections=[],
            language="en"
        )
        assert agents == {}

    def test_unknown_section_ignored(self, sample_ticker, sample_reference_date):
        """Test that unknown sections are ignored."""
        agents = get_us_agent_directory(
            company_name="Apple Inc.",
            ticker=sample_ticker,
            reference_date=sample_reference_date,
            base_sections=['unknown_section', 'price_volume_analysis'],
            language="en"
        )

        assert 'unknown_section' not in agents
        assert 'price_volume_analysis' in agents


# =============================================================================
# Test: Individual Agent Imports
# =============================================================================

class TestAgentModuleImports:
    """Tests for agent module imports."""

    def test_stock_price_agents_import(self):
        """Test stock_price_agents module imports."""
        from cores.agents.stock_price_agents import (
            create_us_price_volume_analysis_agent,
            create_us_institutional_holdings_analysis_agent
        )
        assert callable(create_us_price_volume_analysis_agent)
        assert callable(create_us_institutional_holdings_analysis_agent)

    def test_company_info_agents_import(self):
        """Test company_info_agents module imports."""
        from cores.agents.company_info_agents import (
            create_us_company_status_agent,
            create_us_company_overview_agent
        )
        assert callable(create_us_company_status_agent)
        assert callable(create_us_company_overview_agent)

    def test_news_strategy_agents_import(self):
        """Test news_strategy_agents module imports."""
        from cores.agents.news_strategy_agents import (
            create_us_news_analysis_agent
        )
        assert callable(create_us_news_analysis_agent)

    def test_market_index_agents_import(self):
        """Test market_index_agents module imports."""
        from cores.agents.market_index_agents import (
            create_us_market_index_analysis_agent
        )
        assert callable(create_us_market_index_analysis_agent)

    def test_trading_agents_import(self):
        """Test trading_agents module imports."""
        from cores.agents.trading_agents import (
            create_us_trading_scenario_agent,
            create_us_sell_decision_agent
        )
        assert callable(create_us_trading_scenario_agent)
        assert callable(create_us_sell_decision_agent)


# =============================================================================
# Test: Agent Creation
# =============================================================================

class TestAgentCreation:
    """Tests for individual agent creation."""

    def test_price_volume_agent_creation(self, sample_ticker, sample_reference_date):
        """Test price/volume analysis agent creation."""
        from cores.agents.stock_price_agents import create_us_price_volume_analysis_agent

        ref_date = datetime.strptime(sample_reference_date, "%Y%m%d")
        max_years_ago = (ref_date - timedelta(days=365)).strftime("%Y%m%d")

        agent = create_us_price_volume_analysis_agent(
            company_name="Apple Inc.",
            ticker=sample_ticker,
            reference_date=sample_reference_date,
            max_years_ago=max_years_ago,
            max_years=1,
            language="en"
        )

        assert agent is not None
        assert hasattr(agent, 'name')
        assert 'yfinance_us' in agent.server_names

    def test_institutional_agent_creation(self, sample_ticker, sample_reference_date):
        """Test institutional holdings analysis agent creation."""
        from cores.agents.stock_price_agents import create_us_institutional_holdings_analysis_agent

        ref_date = datetime.strptime(sample_reference_date, "%Y%m%d")
        max_years_ago = (ref_date - timedelta(days=365)).strftime("%Y%m%d")

        agent = create_us_institutional_holdings_analysis_agent(
            company_name="Apple Inc.",
            ticker=sample_ticker,
            reference_date=sample_reference_date,
            max_years_ago=max_years_ago,
            max_years=1,
            language="en"
        )

        assert agent is not None
        assert 'yfinance_us' in agent.server_names

    def test_company_status_agent_creation(self, sample_ticker, sample_reference_date):
        """Test company status agent creation."""
        from cores.agents.company_info_agents import create_us_company_status_agent

        urls = get_us_data_urls(sample_ticker)
        agent = create_us_company_status_agent(
            company_name="Apple Inc.",
            ticker=sample_ticker,
            reference_date=sample_reference_date,
            urls=urls,
            language="en"
        )

        assert agent is not None
        assert 'firecrawl' in agent.server_names

    def test_company_overview_agent_creation(self, sample_ticker, sample_reference_date):
        """Test company overview agent creation."""
        from cores.agents.company_info_agents import create_us_company_overview_agent

        urls = get_us_data_urls(sample_ticker)
        agent = create_us_company_overview_agent(
            company_name="Apple Inc.",
            ticker=sample_ticker,
            reference_date=sample_reference_date,
            urls=urls,
            language="en"
        )

        assert agent is not None
        assert 'firecrawl' in agent.server_names

    def test_news_agent_creation(self, sample_ticker, sample_reference_date):
        """Test news analysis agent creation."""
        from cores.agents.news_strategy_agents import create_us_news_analysis_agent

        agent = create_us_news_analysis_agent(
            company_name="Apple Inc.",
            ticker=sample_ticker,
            reference_date=sample_reference_date,
            language="en"
        )

        assert agent is not None
        assert 'perplexity' in agent.server_names

    def test_market_index_agent_creation(self, sample_reference_date):
        """Test market index analysis agent creation."""
        from cores.agents.market_index_agents import create_us_market_index_analysis_agent

        ref_date = datetime.strptime(sample_reference_date, "%Y%m%d")
        max_years_ago = (ref_date - timedelta(days=365)).strftime("%Y%m%d")

        agent = create_us_market_index_analysis_agent(
            reference_date=sample_reference_date,
            max_years_ago=max_years_ago,
            max_years=1,
            language="en"
        )

        assert agent is not None

    def test_trading_scenario_agent_creation(self):
        """Test trading scenario agent creation."""
        from cores.agents.trading_agents import create_us_trading_scenario_agent

        agent = create_us_trading_scenario_agent()
        assert agent is not None
        assert 'sqlite' in agent.server_names

    def test_sell_decision_agent_creation(self):
        """Test sell decision agent creation."""
        from cores.agents.trading_agents import create_us_sell_decision_agent

        agent = create_us_sell_decision_agent()
        assert agent is not None


# =============================================================================
# Test: Agent MCP Server Assignments
# =============================================================================

class TestAgentServerAssignments:
    """Tests for correct MCP server assignments."""

    def test_price_volume_uses_yfinance(self, sample_ticker, sample_reference_date, agent_sections):
        """Test price/volume agent uses yfinance_us server."""
        agents = get_us_agent_directory(
            "Apple Inc.", sample_ticker, sample_reference_date,
            ['price_volume_analysis'], "en"
        )
        agent = agents['price_volume_analysis']
        assert 'yfinance_us' in agent.server_names

    def test_institutional_uses_yfinance(self, sample_ticker, sample_reference_date):
        """Test institutional holdings agent uses yfinance_us server."""
        agents = get_us_agent_directory(
            "Apple Inc.", sample_ticker, sample_reference_date,
            ['institutional_holdings_analysis'], "en"
        )
        agent = agents['institutional_holdings_analysis']
        assert 'yfinance_us' in agent.server_names

    def test_company_status_uses_firecrawl(self, sample_ticker, sample_reference_date):
        """Test company status agent uses firecrawl server."""
        agents = get_us_agent_directory(
            "Apple Inc.", sample_ticker, sample_reference_date,
            ['company_status'], "en"
        )
        agent = agents['company_status']
        assert 'firecrawl' in agent.server_names

    def test_news_uses_perplexity(self, sample_ticker, sample_reference_date):
        """Test news agent uses perplexity server."""
        agents = get_us_agent_directory(
            "Apple Inc.", sample_ticker, sample_reference_date,
            ['news_analysis'], "en"
        )
        agent = agents['news_analysis']
        assert 'perplexity' in agent.server_names
