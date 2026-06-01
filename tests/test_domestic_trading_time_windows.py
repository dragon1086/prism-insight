import datetime
import sys
import types
from typing import Any, cast

# domestic_stock_trading imports kis_auth at module import time. These tests only
# exercise pure time-window routing, so stub heavyweight optional dependencies
# that may be absent in lightweight CI/local environments.
sys.modules.setdefault("pandas", types.ModuleType("pandas"))
crypto = sys.modules.setdefault("Crypto", types.ModuleType("Crypto"))
cipher = sys.modules.setdefault("Crypto.Cipher", types.ModuleType("Crypto.Cipher"))
aes = sys.modules.setdefault("Crypto.Cipher.AES", types.ModuleType("Crypto.Cipher.AES"))
util = sys.modules.setdefault("Crypto.Util", types.ModuleType("Crypto.Util"))
padding = sys.modules.setdefault("Crypto.Util.Padding", types.ModuleType("Crypto.Util.Padding"))
setattr(cipher, "AES", aes)
setattr(crypto, "Cipher", cipher)
setattr(crypto, "Util", util)
setattr(util, "Padding", padding)
setattr(padding, "unpad", lambda data, block_size: data)

from trading import domestic_stock_trading as domestic


class _DummyDomesticTrader:
    auto_trading = True

    def __init__(self):
        self.called = None

    def buy_market_price(self, stock_code, buy_amount=None):
        self.called = ("buy_market_price", stock_code, buy_amount)
        return {"success": True, "method": "market"}

    def buy_closing_price(self, stock_code, buy_amount=None):
        self.called = ("buy_closing_price", stock_code, buy_amount)
        return {"success": True, "method": "closing"}

    def buy_reserved_order(self, stock_code, buy_amount=None, limit_price=None):
        self.called = ("buy_reserved_order", stock_code, buy_amount, limit_price)
        return {"success": True, "method": "reserved"}

    def sell_all_market_price(self, stock_code, quantity=None):
        self.called = ("sell_all_market_price", stock_code, quantity)
        return {"success": True, "method": "market"}

    def sell_all_closing_price(self, stock_code, quantity=None):
        self.called = ("sell_all_closing_price", stock_code, quantity)
        return {"success": True, "method": "closing"}

    def sell_all_reserved_order(self, stock_code, limit_price=None, quantity=None):
        self.called = ("sell_all_reserved_order", stock_code, limit_price, quantity)
        return {"success": True, "method": "reserved"}


def _dt(hour, minute):
    return datetime.datetime(2026, 6, 1, hour, minute, tzinfo=domestic.KST)


def test_domestic_order_window_uses_kst_regular_market():
    assert domestic._domestic_order_window(_dt(15, 15)) == "regular"


def test_domestic_order_window_blocks_0730_to_0900_reserved_gap():
    assert domestic._domestic_order_window(_dt(8, 15)) == "unavailable"


def test_domestic_order_window_blocks_reserved_maintenance_gap():
    assert domestic._domestic_order_window(_dt(23, 50)) == "unavailable"


def test_smart_buy_uses_market_order_at_1515_kst(monkeypatch):
    trader = _DummyDomesticTrader()
    monkeypatch.setattr(domestic, "_now_kst", lambda: _dt(15, 15))

    result = domestic.DomesticStockTrading.smart_buy(cast(Any, trader), "005935", buy_amount=100000, limit_price=50000)

    assert result["method"] == "market"
    assert trader.called == ("buy_market_price", "005935", 100000)


def test_smart_buy_does_not_submit_reserved_order_in_0815_kst_gap(monkeypatch):
    trader = _DummyDomesticTrader()
    monkeypatch.setattr(domestic, "_now_kst", lambda: _dt(8, 15))

    result = domestic.DomesticStockTrading.smart_buy(cast(Any, trader), "005935", buy_amount=100000, limit_price=50000)

    assert result["success"] is False
    assert "Order window unavailable" in result["message"]
    assert trader.called is None


def test_smart_sell_uses_market_order_at_1515_kst(monkeypatch):
    trader = _DummyDomesticTrader()
    monkeypatch.setattr(domestic, "_now_kst", lambda: _dt(15, 15))

    result = domestic.DomesticStockTrading.smart_sell_all(cast(Any, trader), "005935", limit_price=50000, quantity=3)

    assert result["method"] == "market"
    assert trader.called == ("sell_all_market_price", "005935", 3)
