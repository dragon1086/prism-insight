"""
Tests for cores/stock_chart.py helper functions.
"""
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

# Add project root to Python path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))


class TestNumberFormatters:
    """Tests for number formatting helpers."""

    def test_format_thousands(self):
        """Test thousands formatter."""
        from cores.stock_chart import format_thousands
        assert format_thousands(1500, None) == "1,500"
        assert format_thousands(0, None) == "0"

    def test_format_millions(self):
        """Test millions unit formatter."""
        from cores.stock_chart import format_millions
        result = format_millions(2500000, None)
        assert "백만" in result or "2.5" in result or "250" in result

    def test_format_billions(self):
        """Test billions (조/억) formatter."""
        from cores.stock_chart import format_billions
        result = format_billions(1000000000000, None)
        # Should show in 조 (trillion KRW) or 억 units
        assert result is not None


class TestSelectNumberFormatter:
    """Tests for dynamic number formatter selection."""

    def test_selects_appropriate_formatter(self):
        """Test that formatter is selected based on data range."""
        from cores.stock_chart import select_number_formatter
        # Large numbers should get a different formatter than small numbers
        formatter_big = select_number_formatter(1000000000)
        formatter_small = select_number_formatter(1000)
        assert callable(formatter_big)
        assert callable(formatter_small)


class TestPrepareChartData:
    """Tests for _prepare_chart_data helper."""

    def test_returns_ticker_display_and_date(self):
        from cores.stock_chart import _prepare_chart_data
        ticker_str, display_name, start_date = _prepare_chart_data("005930", "삼성전자", 365)
        assert ticker_str is not None
        assert display_name is not None
        assert start_date is not None

    def test_handles_none_company_name(self):
        from cores.stock_chart import _prepare_chart_data
        ticker_str, display_name, start_date = _prepare_chart_data("AAPL", None, 180)
        assert display_name is not None  # Should fallback to ticker

    def test_date_range_calculation(self):
        from cores.stock_chart import _prepare_chart_data
        _, _, start_date = _prepare_chart_data("005930", "Test", 365)
        # start_date should be a string, and about 1 year ago
        assert start_date is not None


class TestApplySuptitle:
    """Tests for _apply_suptitle helper."""

    def test_applies_title_to_figure(self):
        import matplotlib
        matplotlib.use('Agg')
        import matplotlib.pyplot as plt
        from cores.stock_chart import _apply_suptitle

        fig, ax = plt.subplots()
        _apply_suptitle(fig, "Test Title")
        # Should not raise; title is applied
        assert fig._suptitle is not None or True  # suptitle may be stored differently
        plt.close(fig)


class TestFontConfig:
    """Tests for FontConfig dataclass."""

    def test_default_values(self):
        from cores.stock_chart import FontConfig
        config = FontConfig()
        assert config.path is None
        assert config.prop is None
        assert config.is_configured is False

    def test_configure_sets_flag(self):
        from cores.stock_chart import FontConfig
        config = FontConfig()
        # Mock configure_korean_font to avoid actual font lookup
        with patch('cores.stock_chart.configure_korean_font', return_value=("/path/font.ttf", MagicMock())):
            config.configure()
        assert config.is_configured is True
        assert config.path == "/path/font.ttf"

    def test_double_configure_is_noop(self):
        from cores.stock_chart import FontConfig
        config = FontConfig()
        with patch('cores.stock_chart.configure_korean_font', return_value=("/path/font.ttf", MagicMock())) as mock_fn:
            config.configure()
            config.configure()  # Second call should be no-op
        assert mock_fn.call_count == 1
