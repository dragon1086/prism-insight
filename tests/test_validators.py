"""
Tests for cores/validators.py input validation functions.
"""

import pytest
from datetime import datetime
from cores.validators import (
    validate_stock_code,
    validate_date_format,
    validate_language,
    validate_analysis_inputs,
    ValidationError,
)


class TestValidateStockCode:
    """Tests for stock code validation"""

    def test_valid_kr_code(self):
        assert validate_stock_code("005930", "kr") == "005930"

    def test_valid_kr_code_short(self):
        """Should zero-pad short codes"""
        assert validate_stock_code("5930", "kr") == "005930"

    def test_valid_kr_code_strips_whitespace(self):
        assert validate_stock_code("  005930  ", "kr") == "005930"

    def test_invalid_kr_code_letters(self):
        with pytest.raises(ValidationError) as exc_info:
            validate_stock_code("ABCDEF", "kr")
        assert exc_info.value.field == "stock_code"

    def test_invalid_kr_code_too_long(self):
        with pytest.raises(ValidationError):
            validate_stock_code("1234567", "kr")

    def test_valid_us_ticker(self):
        assert validate_stock_code("AAPL", "us") == "AAPL"

    def test_valid_us_ticker_lowercase(self):
        """Should uppercase US tickers"""
        assert validate_stock_code("aapl", "us") == "AAPL"

    def test_valid_us_ticker_single_char(self):
        assert validate_stock_code("A", "us") == "A"

    def test_invalid_us_ticker_too_long(self):
        with pytest.raises(ValidationError):
            validate_stock_code("TOOLONG", "us")

    def test_invalid_us_ticker_digits(self):
        with pytest.raises(ValidationError):
            validate_stock_code("123", "us")

    def test_empty_code(self):
        with pytest.raises(ValidationError) as exc_info:
            validate_stock_code("", "kr")
        assert "empty" in str(exc_info.value).lower()

    def test_none_code(self):
        with pytest.raises(ValidationError):
            validate_stock_code(None, "kr")

    def test_unknown_market(self):
        with pytest.raises(ValidationError) as exc_info:
            validate_stock_code("005930", "jp")
        assert exc_info.value.field == "market"

    def test_suggestion_present(self):
        with pytest.raises(ValidationError) as exc_info:
            validate_stock_code("INVALID", "kr")
        assert exc_info.value.suggestion  # Non-empty suggestion


class TestValidateDateFormat:
    """Tests for date format validation"""

    def test_valid_date(self):
        result = validate_date_format("20260307")
        assert result.year == 2026
        assert result.month == 3
        assert result.day == 7

    def test_valid_date_strips_whitespace(self):
        result = validate_date_format("  20260307  ")
        assert result.day == 7

    def test_invalid_format_short(self):
        with pytest.raises(ValidationError):
            validate_date_format("2026030")

    def test_invalid_format_with_dashes(self):
        with pytest.raises(ValidationError):
            validate_date_format("2026-03-07")

    def test_invalid_calendar_date(self):
        with pytest.raises(ValidationError):
            validate_date_format("20261301")  # Month 13

    def test_future_date_rejected(self):
        with pytest.raises(ValidationError):
            validate_date_format("20990101")

    def test_future_date_allowed(self):
        result = validate_date_format("20990101", allow_future=True)
        assert result.year == 2099

    def test_date_before_2000_rejected(self):
        with pytest.raises(ValidationError):
            validate_date_format("19991231")

    def test_empty_date(self):
        with pytest.raises(ValidationError):
            validate_date_format("")

    def test_none_date(self):
        with pytest.raises(ValidationError):
            validate_date_format(None)


class TestValidateLanguage:
    """Tests for language validation"""

    def test_valid_ko(self):
        assert validate_language("ko") == "ko"

    def test_valid_en(self):
        assert validate_language("en") == "en"

    def test_case_insensitive(self):
        assert validate_language("KO") == "ko"

    def test_strips_whitespace(self):
        assert validate_language("  en  ") == "en"

    def test_invalid_language(self):
        with pytest.raises(ValidationError):
            validate_language("fr")


class TestValidateAnalysisInputs:
    """Tests for combined input validation"""

    def test_valid_kr_inputs(self):
        result = validate_analysis_inputs(
            company_code="005930",
            company_name="삼성전자",
            reference_date="20260307",
            language="ko",
            market="kr",
        )
        assert result["company_code"] == "005930"
        assert result["company_name"] == "삼성전자"
        assert result["language"] == "ko"

    def test_valid_us_inputs(self):
        result = validate_analysis_inputs(
            company_code="AAPL",
            company_name="Apple Inc.",
            language="en",
            market="us",
        )
        assert result["company_code"] == "AAPL"

    def test_empty_company_name(self):
        with pytest.raises(ValidationError):
            validate_analysis_inputs(
                company_code="005930",
                company_name="",
                market="kr",
            )

    def test_defaults_to_today_when_no_date(self):
        result = validate_analysis_inputs(
            company_code="005930",
            company_name="삼성전자",
            market="kr",
        )
        assert result["reference_date"].date() == datetime.now().date()
