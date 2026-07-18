"""Pure parsing / normalization helpers (issue #412 Phase 1).

Logic-preserving extractions. Each function is a byte-for-byte behavioral copy of
the inline agent code it replaces; the original methods now delegate here. The KR
and US decision normalizers are DELIBERATELY separate — their mappings and return
vocabularies differ (KR: Enter/Watch/Skip 3-state; US: entry/no_entry 2-state) and
must NOT be unified until the fork is absorbed (Phase 6).
"""
import logging
from typing import Any

logger = logging.getLogger(__name__)


def normalize_decision_kr(decision: str) -> str:
    """Normalize a KR AI decision string to canonical English form.

    Extracted from stock_tracking_agent.StockTrackingAgent._normalize_decision.
    Maps Korean/English variants to {'Enter', 'Watch', 'Skip'}; unknown input is
    returned stripped but otherwise unchanged.
    """
    if not decision:
        return "Skip"
    normalized = decision.strip()
    enter_variants = {"진입", "Entry", "enter", "entry", "Enter", "매수", "Buy", "buy"}
    watch_variants = {"관망", "Watch", "watch", "Hold", "hold", "보류"}
    skip_variants = {"미진입", "Skip", "skip", "No entry", "no entry", "패스", "Pass", "pass"}
    if normalized in enter_variants:
        return "Enter"
    if normalized in watch_variants:
        return "Watch"
    if normalized in skip_variants:
        return "Skip"
    return normalized


def normalize_decision_us(decision: str) -> str:
    """Normalize a US AI decision string for comparison.

    Extracted from prism-us/us_stock_tracking_agent.USStockTrackingAgent
    ._normalize_decision. Maps variants to {'entry', 'no_entry'}; unknown input is
    returned lowercased and stripped. NOTE: distinct vocabulary/logic from the KR
    normalizer above — keep separate.
    """
    if not decision:
        return "no_entry"
    d = decision.lower().strip()
    # Handle various entry formats
    if d in ("enter", "entry", "진입", "yes", "buy"):
        return "entry"
    # Handle various no-entry formats
    elif d in ("no entry", "no_entry", "no-entry", "미진입", "no", "skip", "pass"):
        return "no_entry"
    return d


def safe_number_conversion(value: Any) -> float:
    """Safely convert various value types to a float.

    Extracted from stock_tracking_enhanced_agent
    .StockTrackingEnhancedAgent._safe_number_conversion. Strips thousands
    separators, spaces and KRW/원 currency markers from strings; returns 0.0 for
    empty/None/other types and on conversion failure.
    """
    try:
        # If already a numeric type
        if isinstance(value, (int, float)):
            return float(value)

        # If string
        if isinstance(value, str):
            # Remove commas and spaces
            cleaned_value = value.replace(',', '').replace(' ', '')
            # Remove currency symbols (KRW, 원)
            cleaned_value = cleaned_value.replace('KRW', '').replace('원', '')

            # Check for empty string
            if not cleaned_value:
                return 0.0

            # Convert to number
            return float(cleaned_value)

        # If null or other type
        return 0.0

    except (ValueError, TypeError) as e:
        logger.warning(f"Number conversion failed: {value} -> {str(e)}")
        return 0.0
