"""Tests for the sell-quantity <=0 guard (real-money safety, PR-C).

Covers both `_resolve_sell_quantity` helpers (KR + US) and a focused
entry-point check that an explicit quantity=0 sell is REJECTED and never
reaches the broker order API (`self._request`) — i.e. it does NOT silently
fall back to full liquidation.

Pure-unit: loads the module files directly (no live DB / network / broker).

Run:
    .venv/bin/python -m pytest tests/test_sell_quantity_guard.py -q
"""

import importlib.util
import os
import sys
from unittest.mock import MagicMock

import pytest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)


def _load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_kr = _load_module(
    "kr_trading_for_test",
    os.path.join(PROJECT_ROOT, "trading", "domestic_stock_trading.py"),
)
_us = _load_module(
    "us_trading_for_test",
    os.path.join(PROJECT_ROOT, "prism-us", "trading", "us_stock_trading.py"),
)

kr_resolve = _kr._resolve_sell_quantity
us_resolve = _us._resolve_sell_quantity


# ── Helper unit tests ───────────────────────────────────────────────────────

@pytest.mark.parametrize("resolve", [kr_resolve, us_resolve])
def test_none_returns_full_holding(resolve):
    # Unchanged legitimate behavior: None => sell entire holding.
    assert resolve(100, None) == 100
    assert resolve(1, None) == 1


@pytest.mark.parametrize("resolve", [kr_resolve, us_resolve])
def test_valid_partial_clamped(resolve):
    assert resolve(100, 30) == 30          # partial within range
    assert resolve(100, 100) == 100        # full explicit
    assert resolve(100, 250) == 100        # clamped down to holding (no over-sell)
    assert resolve(100, 1) == 1            # min valid
    assert resolve(100, "40") == 40        # numeric string coerced


@pytest.mark.parametrize("resolve", [kr_resolve, us_resolve])
def test_zero_returns_zero_not_holding(resolve):
    # CRITICAL: explicit 0 must NOT liquidate the full holding.
    assert resolve(100, 0) == 0


@pytest.mark.parametrize("resolve", [kr_resolve, us_resolve])
def test_negative_returns_zero_not_holding(resolve):
    assert resolve(100, -5) == 0


@pytest.mark.parametrize("resolve", [kr_resolve, us_resolve])
def test_non_numeric_returns_zero_not_holding(resolve):
    assert resolve(100, "abc") == 0
    assert resolve(100, object()) == 0
    assert resolve(100, float("nan")) == 0  # int(nan) raises ValueError -> 0


# ── Entry-point guard tests (no broker call on quantity=0) ──────────────────

def test_kr_sell_all_market_price_rejects_zero_without_broker_call():
    self_mock = MagicMock()
    self_mock.auto_trading = True
    self_mock.get_holding_quantity.return_value = 10

    result = _kr.DomesticStockTrading.sell_all_market_price(
        self_mock, stock_code="005930", quantity=0
    )

    assert result["success"] is False
    assert result["quantity"] == 0
    assert result["stock_code"] == "005930"
    self_mock._request.assert_not_called()


def test_us_sell_all_market_price_rejects_zero_without_broker_call():
    self_mock = MagicMock()
    self_mock.auto_trading = True
    self_mock.get_holding_quantity.return_value = 10

    result = _us.USStockTrading.sell_all_market_price(
        self_mock, ticker="AAPL", exchange="NASDAQ", quantity=0
    )

    assert result["success"] is False
    assert result["quantity"] == 0
    assert result["ticker"] == "AAPL"
    self_mock._request.assert_not_called()


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
