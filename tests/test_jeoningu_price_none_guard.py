"""
Tests for the BTC/KODEX price None-guard.

Guards against fabricated prices: `get_current_price` must return None (not a
mock) when the price lookup fails, and every trading/dashboard caller must
defer instead of trading/computing on a fabricated price.
"""
import os
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Make the repo root importable (mirrors how the app imports `events.*`).
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import events.jeoningu_price_fetcher as price_fetcher
import events.jeoningu_trading as trading
from events.jeoningu_trading import JeoninguTrading


def test_get_current_price_returns_none_when_lookup_fails():
    """When the underlying price fetch fails, we must NOT fabricate a mock price."""
    with patch.object(price_fetcher, "get_stock_price", return_value=None):
        result = price_fetcher.get_current_price(price_fetcher.KODEX_LEVERAGE)
    assert result is None


def test_get_current_price_returns_close_on_success():
    """Successful path is unchanged: returns the close price."""
    with patch.object(price_fetcher, "get_stock_price", return_value={"close": 12345}):
        result = price_fetcher.get_current_price("122630")
    assert result == 12345


def _make_trader():
    """Build a JeoninguTrading without running __init__ (avoids OpenAI/DB setup)."""
    trader = object.__new__(JeoninguTrading)
    trader.use_telegram = False
    trader.db = AsyncMock()
    return trader


@pytest.mark.asyncio
async def test_buy_deferred_when_price_none_no_trade_executed():
    """
    Bullish sentiment with no current position would BUY. If the price is
    unavailable, the trade-execution boundary (db.insert_trade) must NOT be
    called and the function must not crash.
    """
    trader = _make_trader()
    trader.db.video_id_exists.return_value = False
    trader.db.get_current_position.return_value = None
    trader.db.get_latest_balance.return_value = 1_000_000
    trader.db.calculate_performance_metrics.return_value = {
        "win_rate": 0.0,
        "cumulative_return": 0.0,
    }

    analysis = {
        "video_info": {
            "video_id": "vid1",
            "title": "t",
            "video_date": "2026-07-15",
            "video_url": "http://x",
        },
        "jeon_sentiment": "Bullish",
        "contrarian_action": "buy",
        "target_stock": {"code": "122630", "name": "KODEX Leverage"},
    }

    with patch.object(trading, "get_current_price", return_value=None):
        await trader.execute_trading_strategy(analysis)

    trader.db.insert_trade.assert_not_called()


@pytest.mark.asyncio
async def test_sell_deferred_when_price_none_no_trade_executed():
    """
    Neutral sentiment with a held position would SELL. If the price is
    unavailable, no fabricated SELL trade may be recorded.
    """
    trader = _make_trader()
    trader.db.video_id_exists.return_value = False
    trader.db.get_current_position.return_value = {
        "stock_code": "122630",
        "stock_name": "KODEX Leverage",
        "quantity": 10,
        "buy_amount": 100_000,
        "buy_id": 1,
    }
    trader.db.get_latest_balance.return_value = 1_000_000

    analysis = {
        "video_info": {
            "video_id": "vid2",
            "title": "t",
            "video_date": "2026-07-15",
            "video_url": "http://x",
        },
        "jeon_sentiment": "Neutral",
        "contrarian_action": "sell",
        "target_stock": {},
    }

    with patch.object(trading, "get_current_price", return_value=None):
        await trader.execute_trading_strategy(analysis)

    trader.db.insert_trade.assert_not_called()


def test_dashboard_fallback_to_buy_price_when_price_none():
    """
    Dashboard: get_current_price now RETURNS None (does not raise), so the
    explicit None guard must fall back to buy_price. Replicates the guarded
    expression against the real (failing) fetcher output.
    """
    buy_price = 55_000
    with patch.object(price_fetcher, "get_stock_price", return_value=None):
        current_price = price_fetcher.get_current_price("122630")

    if current_price is None:
        current_price = buy_price

    # Downstream valuation must not crash and must use the fallback.
    quantity = 3
    current_value = quantity * current_price
    assert current_price == buy_price
    assert current_value == quantity * buy_price
