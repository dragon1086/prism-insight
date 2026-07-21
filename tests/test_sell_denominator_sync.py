#!/usr/bin/env python3
"""Regression tests: partial (pyramiding) sells must stay in sync with mirroring
subscribers.

Root cause: the main tracking agents sell a *fraction* of a multi-lot
(pyramided) position — e.g. buy SK Hynix twice, then loss-cut only the second
lot → the source sells floor(total / remaining_rows) and keeps the rest. The
published sell signal, however, carried no size hint, so the example subscriber
always called ``async_sell_stock`` with no quantity → a full liquidation of the
subscriber's whole position.

Fix (Approach A): the publishers now attach ``sell_denominator`` (default 1 =
full sell, unchanged) to every SELL payload, and the subscriber sells
``floor(its_holding / sell_denominator)`` — the same 1/N fraction the source
used — so a partial exit is mirrored as a partial exit. A too-small position
(fraction < 1 share) is kept for the source's final full sweep.

Run (root suite):
    .venv/bin/python -m pytest tests/test_sell_denominator_sync.py -q
"""
import importlib
import json
import logging
import sys
import types
from pathlib import Path
from unittest.mock import MagicMock

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

SUB_MOD = "examples.messaging.gcp_pubsub_subscriber_example"
_LOG = logging.getLogger("test_sell_denominator_sync")


# --------------------------------------------------------------------------- #
# Publisher side: SELL payload must carry sell_denominator (default 1)
# --------------------------------------------------------------------------- #
def _make_gcp_publisher():
    from messaging.gcp_pubsub_signal_publisher import SignalPublisher

    pub = SignalPublisher(project_id="test-project", topic_id="test-topic")
    mock_client = MagicMock()
    future = MagicMock()
    future.result = MagicMock(return_value="msg-id")
    mock_client.publish = MagicMock(return_value=future)
    pub._publisher = mock_client
    pub._topic_path = "projects/test/topics/test"
    return pub, mock_client


def _gcp_payload(mock_client) -> dict:
    message_bytes = mock_client.publish.call_args[0][1]
    return json.loads(message_bytes.decode("utf-8"))


def _make_redis_publisher():
    from messaging.redis_signal_publisher import SignalPublisher as RedisSignalPublisher

    pub = RedisSignalPublisher.__new__(RedisSignalPublisher)
    pub.STREAM_NAME = "prism-trading-signals"
    mock_redis = MagicMock()
    mock_redis.xadd = MagicMock(return_value="1-0")
    pub._redis = mock_redis
    pub._is_connected = lambda: True
    return pub, mock_redis


def _redis_payload(mock_redis) -> dict:
    fields = mock_redis.xadd.call_args[0][2]
    return json.loads(fields["data"])


@pytest.mark.asyncio
async def test_gcp_sell_carries_denominator():
    pub, mc = _make_gcp_publisher()
    await pub.publish_sell_signal(
        ticker="000660", company_name="SK hynix", price=170000,
        buy_price=200000, profit_rate=-15.0, sell_reason="stop-loss",
        sell_denominator=2,
    )
    assert _gcp_payload(mc)["sell_denominator"] == 2


@pytest.mark.asyncio
async def test_gcp_sell_denominator_defaults_to_full():
    pub, mc = _make_gcp_publisher()
    await pub.publish_sell_signal(
        ticker="000660", company_name="SK hynix", price=170000,
        buy_price=200000, profit_rate=-15.0, sell_reason="stop-loss",
    )
    assert _gcp_payload(mc)["sell_denominator"] == 1


@pytest.mark.asyncio
async def test_redis_sell_carries_denominator():
    pub, mr = _make_redis_publisher()
    await pub.publish_sell_signal(
        ticker="000660", company_name="SK hynix", price=170000,
        buy_price=200000, profit_rate=-15.0, sell_reason="stop-loss",
        sell_denominator=3,
    )
    assert _redis_payload(mr)["sell_denominator"] == 3


@pytest.mark.asyncio
async def test_redis_sell_denominator_defaults_to_full():
    pub, mr = _make_redis_publisher()
    await pub.publish_sell_signal(
        ticker="000660", company_name="SK hynix", price=170000,
        buy_price=200000, profit_rate=-15.0, sell_reason="stop-loss",
    )
    assert _redis_payload(mr)["sell_denominator"] == 1


# --------------------------------------------------------------------------- #
# Subscriber side: mirror the source's 1/N fraction against our own holding
# --------------------------------------------------------------------------- #
class _FakeTrader:
    """Stand-in for the KIS trading client (KR context / US instance)."""

    def __init__(self, holding: int):
        self._holding = holding
        self.sell_calls = []  # captured quantity= per async_sell_stock call

    def get_current_price(self, ticker):
        return {"current_price": 100}

    def get_holding_quantity(self, ticker):
        return self._holding

    async def async_sell_stock(self, stock_code=None, ticker=None,
                               limit_price=None, quantity=None):
        self.sell_calls.append(quantity)
        return {"success": True, "message": f"sold quantity={quantity}"}


class _FakeCtx:
    def __init__(self, trader):
        self._trader = trader

    async def __aenter__(self):
        return self._trader

    async def __aexit__(self, *a):
        return False


@pytest.fixture
def sub():
    return importlib.import_module(SUB_MOD)


def _install_kr_stub(monkeypatch, trader):
    # The subscriber does `from trading.domestic_stock_trading import
    # AsyncTradingContext` inside the function; a stub module in sys.modules
    # keeps this test free of the heavy KIS import chain.
    if "trading" not in sys.modules:
        pkg = types.ModuleType("trading")
        pkg.__path__ = []
        monkeypatch.setitem(sys.modules, "trading", pkg)
    mod = types.ModuleType("trading.domestic_stock_trading")
    mod.AsyncTradingContext = lambda *a, **k: _FakeCtx(trader)
    monkeypatch.setitem(sys.modules, "trading.domestic_stock_trading", mod)


@pytest.mark.asyncio
async def test_kr_partial_sell_mirrors_fraction(sub, monkeypatch):
    trader = _FakeTrader(holding=10)
    _install_kr_stub(monkeypatch, trader)
    res = await sub.execute_sell_trade("000660", "SK hynix", _LOG,
                                       limit_price=170000, sell_denominator=2)
    assert res["success"] is True
    assert trader.sell_calls == [5]  # floor(10 / 2)


@pytest.mark.asyncio
async def test_kr_full_sell_when_denominator_one(sub, monkeypatch):
    trader = _FakeTrader(holding=10)
    _install_kr_stub(monkeypatch, trader)
    await sub.execute_sell_trade("000660", "SK hynix", _LOG,
                                 limit_price=170000, sell_denominator=1)
    assert trader.sell_calls == [None]  # None => sell whole position


@pytest.mark.asyncio
async def test_kr_partial_skips_when_fraction_below_one_share(sub, monkeypatch):
    trader = _FakeTrader(holding=1)
    _install_kr_stub(monkeypatch, trader)
    res = await sub.execute_sell_trade("000660", "SK hynix", _LOG,
                                       limit_price=170000, sell_denominator=2)
    assert res["success"] is True          # not a failure
    assert trader.sell_calls == []         # no order placed; kept for final sweep


@pytest.mark.asyncio
async def test_us_partial_sell_mirrors_fraction(sub, monkeypatch):
    trader = _FakeTrader(holding=9)
    monkeypatch.setattr(sub, "load_us_stock_trading_class", lambda: (lambda: trader))
    res = await sub.execute_us_sell_trade("NVDA", "NVIDIA", _LOG,
                                          limit_price=120.0, sell_denominator=3)
    assert res["success"] is True
    assert trader.sell_calls == [3]  # floor(9 / 3)


@pytest.mark.asyncio
async def test_us_full_sell_when_denominator_one(sub, monkeypatch):
    trader = _FakeTrader(holding=9)
    monkeypatch.setattr(sub, "load_us_stock_trading_class", lambda: (lambda: trader))
    await sub.execute_us_sell_trade("NVDA", "NVIDIA", _LOG,
                                    limit_price=120.0, sell_denominator=1)
    assert trader.sell_calls == [None]
