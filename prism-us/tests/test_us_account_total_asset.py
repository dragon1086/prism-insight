"""Unit tests for US get_account_summary settlement-coherent total asset.

Regression guard for the 18:00 portfolio season-return bug: summing a real-time
stock eval against the settlement-lagged USD deposit (frcr_dncl_amt_2) made the
season return see-saw. get_account_summary must instead expose KIS-computed
tot_asst_amt (KRW) as total_asset_usd = tot_asst_amt / exchange_rate.
"""
import os
import sys
from types import SimpleNamespace

import pytest

_HERE = os.path.dirname(__file__)
_ROOT = os.path.abspath(os.path.join(_HERE, "..", ".."))
# kis_auth and shared modules live in the root trading/ dir (US runtime puts it on path).
sys.path.insert(0, os.path.join(_ROOT, "trading"))
sys.path.insert(0, os.path.join(_ROOT, "prism-us", "trading"))
import us_stock_trading as ust  # noqa: E402


class _FakeBody:
    def __init__(self, output2, output3):
        self.output2 = output2
        self.output3 = output3


class _FakeRes:
    def __init__(self, output2, output3, ok=True):
        self._body = _FakeBody(output2, output3)
        self._ok = ok

    def isOK(self):
        return self._ok

    def getBody(self):
        return self._body

    def getErrorCode(self):
        return "ERR"

    def getErrorMessage(self):
        return "boom"


def _make_trader(res, portfolio):
    trader = ust.USStockTrading.__new__(ust.USStockTrading)
    trader.trenv = SimpleNamespace(my_acct="123", my_prod="01")
    trader._request = lambda *a, **k: res
    trader.get_portfolio = lambda: portfolio
    return trader


_USD_ROW = {
    "crcy_cd": "USD",
    "frcr_dncl_amt_2": "2640.910000",   # settlement-lagged deposit
    "frst_bltn_exrt": "1513.50000000",
}
_PORTFOLIO = [
    {"eval_amount": 8709.0, "profit_amount": 342.0, "avg_price": 100.0, "quantity": 83},
]


def test_total_asset_usd_uses_kis_total_not_eval_plus_cash():
    # KIS total asset (KRW) includes stock + USD cash + KRW deposit + unsettled.
    output3 = {"tot_asst_amt": "20925371"}
    trader = _make_trader(_FakeRes([_USD_ROW], output3), _PORTFOLIO)

    summary = trader.get_account_summary()

    assert summary is not None
    # 20,925,371 / 1513.5 ~= 13825.95, NOT eval+cash (8709 + 2640.91 = 11349.91)
    assert summary["total_asset_usd"] == pytest.approx(20925371 / 1513.5, abs=0.01)
    assert summary["total_asset_usd"] > summary["total_eval_amount"] + summary["usd_cash"]
    assert summary["usd_cash"] == pytest.approx(2640.91, abs=0.01)


def test_total_asset_usd_handles_output3_as_list():
    output3 = [{"tot_asst_amt": "20925371"}]
    trader = _make_trader(_FakeRes([_USD_ROW], output3), _PORTFOLIO)

    summary = trader.get_account_summary()

    assert summary["total_asset_usd"] == pytest.approx(20925371 / 1513.5, abs=0.01)


def test_total_asset_usd_falls_back_to_zero_when_missing():
    # Missing tot_asst_amt -> total_asset_usd 0 so callers fall back to eval+cash.
    trader = _make_trader(_FakeRes([_USD_ROW], {}), _PORTFOLIO)

    summary = trader.get_account_summary()

    assert summary["total_asset_usd"] == 0.0


def test_total_asset_usd_zero_when_no_exchange_rate():
    usd_row = {"crcy_cd": "USD", "frcr_dncl_amt_2": "2640.91", "frst_bltn_exrt": "0"}
    trader = _make_trader(_FakeRes([usd_row], {"tot_asst_amt": "20925371"}), _PORTFOLIO)

    summary = trader.get_account_summary()

    assert summary["total_asset_usd"] == 0.0


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
