"""Behavior-fixing tests for prism_core.parsing (issue #412 Phase 1).

These lock the exact current behavior of the pure functions extracted from the
KR/US tracking agents so later refactors cannot silently change them. The KR and
US decision normalizers are intentionally distinct.
"""
import pytest

from prism_core.parsing import (
    normalize_decision_kr,
    normalize_decision_us,
    safe_number_conversion,
)


# --- normalize_decision_kr: {Enter, Watch, Skip}, passthrough otherwise ---

@pytest.mark.parametrize("raw", ["진입", "Entry", "enter", "entry", "Enter", "매수", "Buy", "buy"])
def test_kr_enter_variants(raw):
    assert normalize_decision_kr(raw) == "Enter"


@pytest.mark.parametrize("raw", ["관망", "Watch", "watch", "Hold", "hold", "보류"])
def test_kr_watch_variants(raw):
    assert normalize_decision_kr(raw) == "Watch"


@pytest.mark.parametrize("raw", ["미진입", "Skip", "skip", "No entry", "no entry", "패스", "Pass", "pass"])
def test_kr_skip_variants(raw):
    assert normalize_decision_kr(raw) == "Skip"


def test_kr_empty_returns_skip():
    assert normalize_decision_kr("") == "Skip"
    assert normalize_decision_kr(None) == "Skip"


def test_kr_strips_whitespace():
    assert normalize_decision_kr("  진입  ") == "Enter"


def test_kr_unknown_passthrough_stripped_not_lowercased():
    # KR passthrough returns the stripped (NOT lowercased) original
    assert normalize_decision_kr("  Unknown  ") == "Unknown"


# --- normalize_decision_us: {entry, no_entry}, lowercased passthrough otherwise ---

@pytest.mark.parametrize("raw", ["enter", "entry", "ENTER", "Entry", "진입", "yes", "buy", "  BUY  "])
def test_us_entry_variants(raw):
    assert normalize_decision_us(raw) == "entry"


@pytest.mark.parametrize("raw", ["no entry", "no_entry", "no-entry", "미진입", "no", "skip", "pass", "SKIP"])
def test_us_no_entry_variants(raw):
    assert normalize_decision_us(raw) == "no_entry"


def test_us_empty_returns_no_entry():
    assert normalize_decision_us("") == "no_entry"
    assert normalize_decision_us(None) == "no_entry"


def test_us_unknown_passthrough_lowercased_stripped():
    # US passthrough returns lowercased + stripped original
    assert normalize_decision_us("  Maybe  ") == "maybe"


def test_kr_and_us_are_distinct():
    # Same input, different vocab — confirms they must stay separate
    assert normalize_decision_kr("진입") == "Enter"
    assert normalize_decision_us("진입") == "entry"


# --- safe_number_conversion ---

def test_safe_number_numeric_passthrough():
    assert safe_number_conversion(1000) == 1000.0
    assert safe_number_conversion(12.5) == 12.5
    assert safe_number_conversion(0) == 0.0


def test_safe_number_strips_separators_and_currency():
    assert safe_number_conversion("1,000") == 1000.0
    assert safe_number_conversion("1 000") == 1000.0
    assert safe_number_conversion("1,000 KRW") == 1000.0
    assert safe_number_conversion("1000원") == 1000.0
    assert safe_number_conversion("1,234,567") == 1234567.0


def test_safe_number_empty_and_none_and_invalid_return_zero():
    assert safe_number_conversion("") == 0.0
    assert safe_number_conversion("   ") == 0.0
    assert safe_number_conversion(None) == 0.0
    assert safe_number_conversion("abc") == 0.0
    assert safe_number_conversion(["not", "a", "number"]) == 0.0


def test_safe_number_float_string():
    assert safe_number_conversion("12.75") == 12.75


def test_delegation_wiring_kr_staticmethod():
    # The KR static method must still work and produce identical output.
    from stock_tracking_agent import StockTrackingAgent
    assert StockTrackingAgent._normalize_decision("진입") == "Enter"
    assert StockTrackingAgent._normalize_decision("관망") == "Watch"
