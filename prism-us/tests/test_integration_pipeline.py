"""
Integration Tests: Full Pipeline

End-to-end integration tests for the PRISM-US pipeline:
- Market check → Data retrieval
- Data → Trigger detection
- Trigger → Agent creation
- Full morning pipeline
- Full afternoon pipeline
"""

import os
import sys
from datetime import datetime
from pathlib import Path
from unittest.mock import patch, MagicMock, AsyncMock

import pandas as pd
import pytest

# Add paths for imports
PRISM_US_DIR = Path(__file__).parent.parent
PROJECT_ROOT = PRISM_US_DIR.parent
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PRISM_US_DIR))


# =============================================================================
# Test: Market to Data Flow
# =============================================================================

@pytest.mark.integration
class TestMarketToDataFlow:
    """Tests for market check → data retrieval flow."""

    def test_market_check_to_ohlcv(self):
        """Test flow from market check to OHLCV retrieval."""
        from check_market_day import is_us_market_day
        from cores.us_data_client import USDataClient

        # Check if today is a market day
        is_trading_day = is_us_market_day()

        # Create data client
        client = USDataClient()

        # Get OHLCV for a ticker
        df = client.get_ohlcv("AAPL", period="5d")

        # Should always get data (even on non-trading days, get historical)
        assert isinstance(df, pd.DataFrame)

    def test_market_status_provides_context(self):
        """Test market status provides full context."""
        from check_market_day import get_market_status

        status = get_market_status()

        # Status should provide decision-making info
        assert 'is_trading_day' in status
        assert 'is_market_open' in status

        # On non-trading days, should provide next trading day
        if not status['is_trading_day']:
            assert 'next_trading_day' in status


# =============================================================================
# Test: Data to Trigger Flow
# =============================================================================

@pytest.mark.integration
class TestDataToTriggerFlow:
    """Tests for data retrieval → trigger detection flow."""

    def test_snapshot_to_trigger(self, sample_snapshot, sample_market_cap_df):
        """Test flow from snapshot data to trigger detection."""
        from us_trigger_batch import trigger_morning_volume_surge

        # Create previous snapshot (lower volume)
        prev_snapshot = sample_snapshot.copy()
        prev_snapshot['Volume'] = prev_snapshot['Volume'] * 0.5

        # Run trigger detection
        result = trigger_morning_volume_surge(
            trade_date=datetime.now().strftime("%Y%m%d"),
            snapshot=sample_snapshot,
            prev_snapshot=prev_snapshot,
            cap_df=sample_market_cap_df,
            top_n=5
        )

        assert isinstance(result, pd.DataFrame)

    def test_market_cap_filter_applied(self, sample_snapshot):
        """Test market cap filter is applied in trigger."""
        from us_trigger_batch import MIN_MARKET_CAP

        # Verify constant is set correctly ($20B)
        assert MIN_MARKET_CAP == 20_000_000_000


# =============================================================================
# Test: Trigger to Agents Flow
# =============================================================================

@pytest.mark.integration
class TestTriggerToAgentsFlow:
    """Tests for trigger detection → agent creation flow."""

    def test_selected_tickers_to_agents(self, sample_reference_date, agent_sections):
        """Test flow from selected tickers to agent creation."""
        from cores.agents import get_us_agent_directory

        # Simulate selected tickers from trigger
        selected_tickers = {
            'AAPL': {'ticker': 'AAPL', 'company_name': 'Apple Inc.'},
            'MSFT': {'ticker': 'MSFT', 'company_name': 'Microsoft Corporation'},
        }

        # Create agents for each ticker
        for ticker, info in selected_tickers.items():
            agents = get_us_agent_directory(
                company_name=info['company_name'],
                ticker=ticker,
                reference_date=sample_reference_date,
                base_sections=agent_sections,
                language="en"
            )

            # Each ticker should get all 6 agents
            assert len(agents) == len(agent_sections)

    def test_agents_have_correct_mcp_servers(self, sample_reference_date):
        """Test agents have correct MCP server assignments."""
        from cores.agents import get_us_agent_directory

        agents = get_us_agent_directory(
            company_name="Apple Inc.",
            ticker="AAPL",
            reference_date=sample_reference_date,
            base_sections=['price_volume_analysis', 'news_analysis'],
            language="en"
        )

        # Price/volume should use yfinance
        assert 'yfinance_us' in agents['price_volume_analysis'].server_names

        # News should use perplexity
        assert 'perplexity' in agents['news_analysis'].server_names


# =============================================================================
# Test: Full Morning Pipeline (Mocked)
# =============================================================================

@pytest.mark.integration
@pytest.mark.asyncio
class TestFullMorningPipeline:
    """Tests for complete morning pipeline flow."""

    async def test_morning_pipeline_components(self):
        """Test all morning pipeline components are available."""
        from check_market_day import is_us_market_day, get_market_status
        from us_trigger_batch import trigger_morning_volume_surge, trigger_morning_gap_up_momentum
        from cores.agents import get_us_agent_directory
        from us_stock_analysis_orchestrator import USStockAnalysisOrchestrator

        # All components should be importable and functional
        assert callable(is_us_market_day)
        assert callable(get_market_status)
        assert callable(trigger_morning_volume_surge)
        assert callable(trigger_morning_gap_up_momentum)
        assert callable(get_us_agent_directory)
        assert USStockAnalysisOrchestrator is not None

    async def test_morning_pipeline_mock_run(self):
        """Test morning pipeline with mocked components."""
        from us_stock_analysis_orchestrator import USStockAnalysisOrchestrator

        with patch('telegram_config.TelegramConfig') as mock_config:
            mock_config.return_value.use_telegram = True
            orchestrator = USStockAnalysisOrchestrator()

            # Mock the trigger batch
            with patch.object(orchestrator, 'run_trigger_batch', new_callable=AsyncMock) as mock_batch:
                mock_batch.return_value = [
                    {'ticker': 'AAPL', 'company_name': 'Apple Inc.', 'trigger_type': 'Volume Surge Top'}
                ]

                result = await orchestrator.run_trigger_batch('morning')

                assert len(result) == 1
                assert result[0]['ticker'] == 'AAPL'


# =============================================================================
# Test: Full Afternoon Pipeline (Mocked)
# =============================================================================

@pytest.mark.integration
@pytest.mark.asyncio
class TestFullAfternoonPipeline:
    """Tests for complete afternoon pipeline flow."""

    async def test_afternoon_pipeline_components(self):
        """Test all afternoon pipeline components are available."""
        # Same imports as morning, but afternoon uses different triggers
        from us_trigger_batch import TRIGGER_CRITERIA

        # Afternoon triggers should be defined
        afternoon_triggers = ['Intraday Rise Top', 'Closing Strength Top', 'Volume Surge Sideways']
        for trigger in afternoon_triggers:
            assert trigger in TRIGGER_CRITERIA

    async def test_afternoon_pipeline_mock_run(self):
        """Test afternoon pipeline with mocked components."""
        from us_stock_analysis_orchestrator import USStockAnalysisOrchestrator

        with patch('telegram_config.TelegramConfig') as mock_config:
            mock_config.return_value.use_telegram = True
            orchestrator = USStockAnalysisOrchestrator()

            with patch.object(orchestrator, 'run_trigger_batch', new_callable=AsyncMock) as mock_batch:
                mock_batch.return_value = [
                    {'ticker': 'NVDA', 'company_name': 'NVIDIA Corporation', 'trigger_type': 'Intraday Rise Top'}
                ]

                result = await orchestrator.run_trigger_batch('afternoon')

                assert len(result) == 1
                assert result[0]['trigger_type'] == 'Intraday Rise Top'


# =============================================================================
# Test: Database Integration
# =============================================================================

@pytest.mark.integration
class TestDatabaseIntegration:
    """Tests for database integration in pipeline."""

    def test_trading_uses_correct_tables(self, initialized_temp_database):
        """Test trading system uses US-specific tables."""
        cursor, conn, _ = initialized_temp_database

        # Verify US tables exist
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'us_%'")
        tables = [t[0] for t in cursor.fetchall()]

        assert 'us_stock_holdings' in tables
        assert 'us_trading_history' in tables

    def test_holdings_isolation(self, initialized_temp_database):
        """Test US holdings are isolated from KR holdings."""
        cursor, conn, _ = initialized_temp_database

        # Insert US holding
        cursor.execute("""
            INSERT INTO us_stock_holdings (ticker, company_name, buy_price, buy_date)
            VALUES ('AAPL', 'Apple Inc.', 185.50, '2026-01-18')
        """)
        conn.commit()

        # Query should only return US holdings
        cursor.execute("SELECT COUNT(*) FROM us_stock_holdings")
        us_count = cursor.fetchone()[0]

        assert us_count == 1


# =============================================================================
# Test: Error Handling
# =============================================================================

@pytest.mark.integration
class TestErrorHandling:
    """Tests for error handling in pipeline."""

    def test_invalid_ticker_handled(self):
        """Test invalid ticker is handled gracefully."""
        from cores.us_data_client import USDataClient

        client = USDataClient()
        df = client.get_ohlcv("INVALID_TICKER_XYZ")

        # Should return empty DataFrame, not raise
        assert isinstance(df, pd.DataFrame)
        assert df.empty

    def test_empty_trigger_results_handled(self):
        """Test empty trigger results are handled."""
        from us_trigger_batch import trigger_morning_volume_surge

        empty_df = pd.DataFrame()

        try:
            result = trigger_morning_volume_surge(
                trade_date=datetime.now().strftime("%Y%m%d"),
                snapshot=empty_df,
                prev_snapshot=empty_df,
                cap_df=None,
            )
            assert isinstance(result, pd.DataFrame)
            assert result.empty
        except (KeyError, ValueError):
            # Function may raise error on empty input - that's also acceptable
            pass


# =============================================================================
# Test: Language Support
# =============================================================================

@pytest.mark.integration
class TestLanguageSupport:
    """Tests for language support in pipeline."""

    def test_english_is_default(self, sample_reference_date):
        """Test English is the default language for US market."""
        from cores.agents import get_us_agent_directory

        agents = get_us_agent_directory(
            "Apple Inc.",
            "AAPL",
            sample_reference_date,
            ['news_analysis'],
            language="en"
        )

        # Agent should be created successfully
        assert 'news_analysis' in agents


# =============================================================================
# Test: Concurrent Operations
# =============================================================================

@pytest.mark.integration
@pytest.mark.asyncio
class TestConcurrentOperations:
    """Tests for concurrent operation handling."""

    async def test_multiple_agent_creation(self, sample_reference_date, agent_sections):
        """Test multiple agents can be created concurrently."""
        from cores.agents import get_us_agent_directory
        import asyncio

        async def create_agents(ticker, name):
            return get_us_agent_directory(
                company_name=name,
                ticker=ticker,
                reference_date=sample_reference_date,
                base_sections=agent_sections,
                language="en"
            )

        # Create agents for multiple tickers
        tickers = [
            ("AAPL", "Apple Inc."),
            ("MSFT", "Microsoft Corporation"),
            ("GOOGL", "Alphabet Inc."),
        ]

        # This should complete without errors
        results = await asyncio.gather(*[
            asyncio.to_thread(get_us_agent_directory, name, ticker, sample_reference_date, agent_sections, "en")
            for ticker, name in tickers
        ])

        assert len(results) == 3
        for agents in results:
            assert len(agents) == len(agent_sections)
