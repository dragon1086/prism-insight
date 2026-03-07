"""
Input validators for PRISM-INSIGHT.

Validates stock codes, date formats, and other user inputs
to catch errors early with clear messages instead of deep pipeline failures.
"""

import re
import logging
from datetime import datetime
from typing import Optional

logger = logging.getLogger(__name__)


class ValidationError(Exception):
    """Raised when input validation fails"""

    def __init__(self, message: str, field: str = "", suggestion: str = ""):
        self.field = field
        self.suggestion = suggestion
        super().__init__(message)


# Stock code patterns by market
STOCK_CODE_PATTERNS = {
    "kr": re.compile(r"^\d{6}$"),           # Korean market: 6 digits (e.g., 005930)
    "us": re.compile(r"^[A-Z]{1,5}$"),      # US market: 1-5 uppercase letters (e.g., AAPL)
}


def validate_stock_code(code: str, market: str = "kr") -> str:
    """
    Validate and normalize a stock code.

    Args:
        code: Stock ticker or code to validate.
        market: Market identifier ('kr' or 'us').

    Returns:
        Normalized stock code string.

    Raises:
        ValidationError: If the code format is invalid.
    """
    if not code or not isinstance(code, str):
        raise ValidationError(
            "Stock code cannot be empty",
            field="stock_code",
            suggestion="Provide a valid stock code (e.g., '005930' for KR, 'AAPL' for US)"
        )

    code = code.strip()
    market = market.lower()

    if market not in STOCK_CODE_PATTERNS:
        raise ValidationError(
            f"Unknown market: '{market}'",
            field="market",
            suggestion="Supported markets: 'kr' (Korean), 'us' (US)"
        )

    pattern = STOCK_CODE_PATTERNS[market]

    # Normalize: uppercase for US, zero-pad for KR
    if market == "us":
        code = code.upper()
    elif market == "kr":
        code = code.zfill(6)  # Pad to 6 digits

    if not pattern.match(code):
        examples = {
            "kr": "e.g., '005930', '035720'",
            "us": "e.g., 'AAPL', 'MSFT', 'NVDA'",
        }
        raise ValidationError(
            f"Invalid {market.upper()} stock code: '{code}'",
            field="stock_code",
            suggestion=f"Expected format: {examples.get(market, 'unknown')}"
        )

    logger.debug(f"Validated stock code: {code} (market: {market})")
    return code


def validate_date_format(date_str: str, allow_future: bool = False) -> datetime:
    """
    Validate and parse a date string in YYYYMMDD format.

    Args:
        date_str: Date string to validate (YYYYMMDD format).
        allow_future: Whether to allow future dates.

    Returns:
        Parsed datetime object.

    Raises:
        ValidationError: If the date format is invalid or the date is in the future.
    """
    if not date_str or not isinstance(date_str, str):
        raise ValidationError(
            "Date cannot be empty",
            field="date",
            suggestion="Provide a date in YYYYMMDD format (e.g., '20260307')"
        )

    date_str = date_str.strip()

    # Check format
    if not re.match(r"^\d{8}$", date_str):
        raise ValidationError(
            f"Invalid date format: '{date_str}'",
            field="date",
            suggestion="Expected format: YYYYMMDD (e.g., '20260307')"
        )

    # Parse date
    try:
        parsed = datetime.strptime(date_str, "%Y%m%d")
    except ValueError:
        raise ValidationError(
            f"Invalid date value: '{date_str}' (not a real calendar date)",
            field="date",
            suggestion="Ensure the date is a valid calendar date"
        )

    # Check for future dates
    if not allow_future and parsed.date() > datetime.now().date():
        raise ValidationError(
            f"Date is in the future: '{date_str}'",
            field="date",
            suggestion=f"Use today's date or earlier: {datetime.now().strftime('%Y%m%d')}"
        )

    # Check for unreasonably old dates (before 2000)
    if parsed.year < 2000:
        raise ValidationError(
            f"Date too old: '{date_str}' (before year 2000)",
            field="date",
            suggestion="Stock data is typically available from 2000 onwards"
        )

    logger.debug(f"Validated date: {date_str}")
    return parsed


def validate_language(language: str) -> str:
    """
    Validate language code.

    Args:
        language: Language code to validate.

    Returns:
        Normalized lowercase language code.

    Raises:
        ValidationError: If the language code is not supported.
    """
    supported = {"ko", "en"}
    language = language.strip().lower()

    if language not in supported:
        raise ValidationError(
            f"Unsupported language: '{language}'",
            field="language",
            suggestion=f"Supported languages: {', '.join(sorted(supported))}"
        )

    return language


def validate_analysis_inputs(
    company_code: str,
    company_name: str,
    reference_date: Optional[str] = None,
    language: str = "ko",
    market: str = "kr",
) -> dict:
    """
    Validate all analysis inputs at once.

    Args:
        company_code: Stock code/ticker.
        company_name: Company name.
        reference_date: Analysis date (YYYYMMDD, defaults to today).
        language: Report language code.
        market: Market identifier.

    Returns:
        Dict with validated and normalized values.

    Raises:
        ValidationError: If any input is invalid.
    """
    validated = {}

    # Validate stock code
    validated["company_code"] = validate_stock_code(company_code, market)

    # Validate company name
    if not company_name or not company_name.strip():
        raise ValidationError(
            "Company name cannot be empty",
            field="company_name",
            suggestion="Provide the company name (e.g., '삼성전자', 'Apple Inc.')"
        )
    validated["company_name"] = company_name.strip()

    # Validate reference date
    if reference_date:
        validated["reference_date"] = validate_date_format(reference_date)
    else:
        validated["reference_date"] = datetime.now()

    # Validate language
    validated["language"] = validate_language(language)

    validated["market"] = market.lower()

    return validated
