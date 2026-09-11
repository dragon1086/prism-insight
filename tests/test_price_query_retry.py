"""KIS-first analysis prices; persisted fallback is not a fresh BUY quote."""
from unittest.mock import AsyncMock

import pytest
import tracking.helpers as helpers


class Cursor:
    def __init__(self, price=None):
        self.price = price
        self.query = None

    def execute(self, sql, args):
        self.query = (sql, args)

    def fetchone(self):
        return (self.price,) if self.price is not None else None


@pytest.mark.asyncio
async def test_kis_quote_precedes_persisted_price(monkeypatch):
    quote = AsyncMock(return_value=72000)
    monkeypatch.setattr(helpers, "_get_price_from_kis", quote)
    cursor = Cursor(70000)
    assert await helpers.get_current_stock_price(cursor, "005930") == 72000
    assert cursor.query is None
    quote.assert_awaited_once_with("005930")


@pytest.mark.asyncio
async def test_missing_kis_quote_preserves_scoped_analysis_fallback(monkeypatch):
    monkeypatch.setattr(helpers, "_get_price_from_kis", AsyncMock(return_value=0))
    cursor = Cursor(70000)
    assert await helpers.get_current_stock_price(cursor, "005930", account_key="scope") == 70000
    assert cursor.query[1] == ("005930", "scope")


@pytest.mark.asyncio
async def test_new_candidate_without_quote_remains_unavailable(monkeypatch):
    monkeypatch.setattr(helpers, "_get_price_from_kis", AsyncMock(return_value=0))
    assert await helpers.get_current_stock_price(Cursor(), "005930") == 0


@pytest.mark.asyncio
async def test_rank_change_is_unknown_without_expensive_current_scan(monkeypatch):
    from cores import market_data
    def forbidden(*args, **kwargs):
        raise AssertionError("current whole-market scan cannot establish historical rank")
    monkeypatch.setattr(market_data, "get_market_ohlcv_by_ticker", forbidden)
    percentage, message = await helpers.get_trading_value_rank_change("005930")
    assert percentage is None
    assert "UNKNOWN" in message
