"""Unit tests for prism_core.time_windows (issue #412 Phase 1-b).

Pins the KST domestic order-window classification boundaries so the extraction
from trading/domestic_stock_trading.py is behavior-preserving.
"""
import datetime
from zoneinfo import ZoneInfo

import pytest

from prism_core.time_windows import domestic_order_window, KST


def _at(h, m, s=0):
    """Naive datetime carrying a KST wall-clock time (used via .time())."""
    return datetime.datetime(2026, 7, 20, h, m, s)


@pytest.mark.parametrize(
    "h,m,expected",
    [
        # regular: 09:00 ~ 15:30 inclusive
        (9, 0, "regular"),
        (12, 0, "regular"),
        (15, 30, "regular"),
        # closing: 15:40 ~ 16:00 inclusive
        (15, 40, "closing"),
        (16, 0, "closing"),
        # reserved: 16:00 < t <= 23:40, and 00:10 <= t <= 07:30
        (16, 1, "reserved"),
        (20, 0, "reserved"),
        (23, 40, "reserved"),
        (0, 10, "reserved"),
        (3, 0, "reserved"),
        (7, 30, "reserved"),
        # unavailable gaps
        (8, 59, "unavailable"),   # before regular open
        (8, 15, "unavailable"),   # reserved maintenance gap 07:30~09:00
        (7, 31, "unavailable"),   # just after reserved morning window
        (15, 31, "unavailable"),  # between regular and closing
        (15, 39, "unavailable"),
        (23, 41, "unavailable"),  # 23:40~00:10 blackout
        (0, 0, "unavailable"),
        (0, 9, "unavailable"),
    ],
)
def test_domestic_order_window_boundaries(h, m, expected):
    assert domestic_order_window(_at(h, m)) == expected


def test_tz_aware_input_is_converted_to_kst():
    # 01:00 UTC == 10:00 KST -> regular
    utc_dt = datetime.datetime(2026, 7, 20, 1, 0, tzinfo=ZoneInfo("UTC"))
    assert domestic_order_window(utc_dt) == "regular"
    # 07:00 UTC == 16:00 KST -> closing
    utc_closing = datetime.datetime(2026, 7, 20, 7, 0, tzinfo=ZoneInfo("UTC"))
    assert domestic_order_window(utc_closing) == "closing"


def test_default_now_returns_valid_classification():
    result = domestic_order_window()
    assert result in {"regular", "closing", "reserved", "unavailable"}


def test_delegation_matches_core():
    """The trading adapter wrapper must return the same value as the core fn."""
    from trading import domestic_stock_trading as dom
    for h, m in [(10, 0), (15, 35), (20, 0), (8, 15)]:
        assert dom._domestic_order_window(_at(h, m)) == domestic_order_window(_at(h, m))
