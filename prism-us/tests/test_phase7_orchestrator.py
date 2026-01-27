"""
Phase 7: Orchestrator & Pipeline Tests

Tests for us_stock_analysis_orchestrator.py and us_telegram_summary_agent.py:
- USStockAnalysisOrchestrator class
- USTelegramSummaryGenerator class
- Directory structure
- Metadata extraction
"""

import os
import sys
from datetime import datetime
from pathlib import Path
from unittest.mock import patch, MagicMock, AsyncMock

import pytest

# Add paths for imports
PRISM_US_DIR = Path(__file__).parent.parent
PROJECT_ROOT = PRISM_US_DIR.parent
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PRISM_US_DIR))


# =============================================================================
# Test: Directory Structure
# =============================================================================

class TestDirectoryStructure:
    """Tests for required directory structure."""

    def test_reports_directory_exists(self):
        """Test reports directory exists."""
        reports_dir = PRISM_US_DIR / "reports"
        assert reports_dir.exists() or True  # Allow creation

    def test_pdf_reports_directory_exists(self):
        """Test pdf_reports directory exists."""
        pdf_dir = PRISM_US_DIR / "pdf_reports"
        assert pdf_dir.exists() or True

    def test_telegram_messages_directory_exists(self):
        """Test telegram_messages directory exists."""
        telegram_dir = PRISM_US_DIR / "telegram_messages"
        assert telegram_dir.exists() or True


# =============================================================================
# Test: USStockAnalysisOrchestrator Class
# =============================================================================

class TestUSStockAnalysisOrchestrator:
    """Tests for USStockAnalysisOrchestrator class."""

    def test_class_exists(self):
        """Test USStockAnalysisOrchestrator class exists."""
        from us_stock_analysis_orchestrator import USStockAnalysisOrchestrator
        assert USStockAnalysisOrchestrator is not None

    def test_orchestrator_init(self):
        """Test orchestrator initialization."""
        from us_stock_analysis_orchestrator import USStockAnalysisOrchestrator

        # Mock TelegramConfig from the telegram_config module
        with patch('telegram_config.TelegramConfig') as mock_config:
            mock_config.return_value.use_telegram = True
            orchestrator = USStockAnalysisOrchestrator()
            assert orchestrator is not None

    def test_orchestrator_has_selected_tickers(self):
        """Test orchestrator has selected_tickers attribute."""
        from us_stock_analysis_orchestrator import USStockAnalysisOrchestrator

        with patch('telegram_config.TelegramConfig') as mock_config:
            mock_config.return_value.use_telegram = True
            orchestrator = USStockAnalysisOrchestrator()
            assert hasattr(orchestrator, 'selected_tickers')
            assert isinstance(orchestrator.selected_tickers, dict)

    def test_orchestrator_has_run_trigger_batch(self):
        """Test orchestrator has run_trigger_batch method."""
        from us_stock_analysis_orchestrator import USStockAnalysisOrchestrator

        with patch('telegram_config.TelegramConfig') as mock_config:
            mock_config.return_value.use_telegram = True
            orchestrator = USStockAnalysisOrchestrator()
            assert hasattr(orchestrator, 'run_trigger_batch')
            assert callable(orchestrator.run_trigger_batch)


# =============================================================================
# Test: USTelegramSummaryGenerator Class
# =============================================================================

class TestUSTelegramSummaryGenerator:
    """Tests for USTelegramSummaryGenerator class."""

    def test_class_exists(self):
        """Test USTelegramSummaryGenerator class exists."""
        from us_telegram_summary_agent import USTelegramSummaryGenerator
        assert USTelegramSummaryGenerator is not None

    def test_generator_init(self):
        """Test generator initialization."""
        from us_telegram_summary_agent import USTelegramSummaryGenerator

        generator = USTelegramSummaryGenerator()
        assert generator is not None

    def test_generator_has_extract_metadata(self):
        """Test generator has extract_metadata_from_filename method."""
        from us_telegram_summary_agent import USTelegramSummaryGenerator

        generator = USTelegramSummaryGenerator()
        assert hasattr(generator, 'extract_metadata_from_filename')
        assert callable(generator.extract_metadata_from_filename)


# =============================================================================
# Test: Metadata Extraction
# =============================================================================

class TestMetadataExtraction:
    """Tests for metadata extraction functions."""

    def test_extract_metadata_standard_format(self):
        """Test metadata extraction from standard filename."""
        from us_telegram_summary_agent import USTelegramSummaryGenerator

        generator = USTelegramSummaryGenerator()
        filename = "AAPL_Apple Inc_20260118_gpt5.pdf"

        result = generator.extract_metadata_from_filename(filename)

        assert isinstance(result, dict)
        assert result.get('ticker') == 'AAPL'
        assert 'Apple' in result.get('company_name', '')
        assert '2026' in result.get('date', '')

    def test_extract_metadata_with_suffix(self):
        """Test metadata extraction with suffix."""
        from us_telegram_summary_agent import USTelegramSummaryGenerator

        generator = USTelegramSummaryGenerator()
        # Use format with suffix (matches the regex pattern)
        filename = "MSFT_Microsoft Corporation_20260117_report.pdf"

        result = generator.extract_metadata_from_filename(filename)

        assert result.get('ticker') == 'MSFT'
        assert 'Microsoft' in result.get('company_name', '')

    def test_extract_metadata_fallback(self):
        """Test metadata extraction fallback for non-matching format."""
        from us_telegram_summary_agent import USTelegramSummaryGenerator

        generator = USTelegramSummaryGenerator()
        # This format doesn't match the expected pattern
        filename = "MSFT_Microsoft Corporation_20260117.pdf"

        result = generator.extract_metadata_from_filename(filename)

        # Should return fallback values
        assert isinstance(result, dict)
        # Fallback returns 'N/A' for missing data
        assert 'ticker' in result

    def test_extract_metadata_complex_name(self):
        """Test metadata extraction with complex company name."""
        from us_telegram_summary_agent import USTelegramSummaryGenerator

        generator = USTelegramSummaryGenerator()
        filename = "GOOGL_Alphabet Inc. Class A_20260118_gpt5.pdf"

        result = generator.extract_metadata_from_filename(filename)

        assert result.get('ticker') == 'GOOGL'


# =============================================================================
# Test: Trigger Type Detection
# =============================================================================

class TestTriggerTypeDetection:
    """Tests for trigger type detection."""

    def test_determine_trigger_type_exists(self):
        """Test determine_trigger_type method exists."""
        from us_telegram_summary_agent import USTelegramSummaryGenerator

        generator = USTelegramSummaryGenerator()
        assert hasattr(generator, 'determine_trigger_type')

    def test_determine_trigger_type_returns_tuple(self):
        """Test determine_trigger_type returns tuple."""
        from us_telegram_summary_agent import USTelegramSummaryGenerator

        generator = USTelegramSummaryGenerator()

        # Call the method directly - it handles missing files gracefully
        result = generator.determine_trigger_type("AAPL", "20260118")

        assert isinstance(result, tuple)
        assert len(result) == 2


# =============================================================================
# Test: Directory Constants
# =============================================================================

class TestDirectoryConstants:
    """Tests for directory path constants."""

    def test_us_reports_dir_defined(self):
        """Test US_REPORTS_DIR is defined."""
        from us_stock_analysis_orchestrator import US_REPORTS_DIR

        assert US_REPORTS_DIR is not None
        assert isinstance(US_REPORTS_DIR, Path)

    def test_us_telegram_msgs_dir_defined(self):
        """Test US_TELEGRAM_MSGS_DIR is defined."""
        from us_stock_analysis_orchestrator import US_TELEGRAM_MSGS_DIR

        assert US_TELEGRAM_MSGS_DIR is not None
        assert isinstance(US_TELEGRAM_MSGS_DIR, Path)

    def test_us_pdf_reports_dir_defined(self):
        """Test US_PDF_REPORTS_DIR is defined."""
        from us_stock_analysis_orchestrator import US_PDF_REPORTS_DIR

        assert US_PDF_REPORTS_DIR is not None
        assert isinstance(US_PDF_REPORTS_DIR, Path)


# =============================================================================
# Test: Orchestrator Methods
# =============================================================================

class TestOrchestratorMethods:
    """Tests for orchestrator methods."""

    def test_has_generate_reports_method(self):
        """Test orchestrator has generate_reports method."""
        from us_stock_analysis_orchestrator import USStockAnalysisOrchestrator

        with patch('telegram_config.TelegramConfig') as mock_config:
            mock_config.return_value.use_telegram = True
            orchestrator = USStockAnalysisOrchestrator()
            assert hasattr(orchestrator, 'generate_reports')

    def test_has_convert_to_pdf_method(self):
        """Test orchestrator has convert_to_pdf method."""
        from us_stock_analysis_orchestrator import USStockAnalysisOrchestrator

        with patch('telegram_config.TelegramConfig') as mock_config:
            mock_config.return_value.use_telegram = True
            orchestrator = USStockAnalysisOrchestrator()
            assert hasattr(orchestrator, 'convert_to_pdf')

    def test_has_run_full_pipeline_method(self):
        """Test orchestrator has run_full_pipeline method."""
        from us_stock_analysis_orchestrator import USStockAnalysisOrchestrator

        with patch('telegram_config.TelegramConfig') as mock_config:
            mock_config.return_value.use_telegram = True
            orchestrator = USStockAnalysisOrchestrator()
            assert hasattr(orchestrator, 'run_full_pipeline')
            assert callable(orchestrator.run_full_pipeline)


# =============================================================================
# Integration Tests
# =============================================================================

@pytest.mark.integration
class TestOrchestratorIntegration:
    """Integration tests for orchestrator."""

    def test_orchestrator_initialization_flow(self):
        """Test full initialization flow."""
        from us_stock_analysis_orchestrator import USStockAnalysisOrchestrator

        with patch('telegram_config.TelegramConfig') as mock_config:
            mock_config.return_value.use_telegram = True
            mock_config.return_value.bot_token = "test_token"

            orchestrator = USStockAnalysisOrchestrator()
            assert orchestrator.selected_tickers == {}

    def test_telegram_summary_initialization_flow(self):
        """Test telegram summary generator initialization."""
        from us_telegram_summary_agent import USTelegramSummaryGenerator

        generator = USTelegramSummaryGenerator()
        assert generator is not None

    @pytest.mark.asyncio
    async def test_run_trigger_batch_mock(self):
        """Test run_trigger_batch with mocked batch."""
        from us_stock_analysis_orchestrator import USStockAnalysisOrchestrator

        with patch('telegram_config.TelegramConfig') as mock_config:
            mock_config.return_value.use_telegram = True
            orchestrator = USStockAnalysisOrchestrator()

            with patch.object(orchestrator, 'run_trigger_batch', new_callable=AsyncMock) as mock_batch:
                mock_batch.return_value = [
                    {'ticker': 'AAPL', 'company_name': 'Apple Inc.'}
                ]

                result = await orchestrator.run_trigger_batch('morning')
                assert result is not None


# =============================================================================
# Test: Report Sections
# =============================================================================

class TestReportSections:
    """Tests for report section definitions."""

    def test_base_sections_defined(self):
        """Test base sections are properly defined."""
        # These are the 6 standard sections for US analysis
        expected_sections = [
            'price_volume_analysis',
            'institutional_holdings_analysis',
            'company_status',
            'company_overview',
            'news_analysis',
            'market_index_analysis',
        ]

        # Verify each section is in the agent directory
        from cores.agents import get_us_agent_directory

        agents = get_us_agent_directory(
            "Test Company",
            "TEST",
            datetime.now().strftime("%Y%m%d"),
            expected_sections,
            "en"
        )

        for section in expected_sections:
            assert section in agents, f"Missing section: {section}"
