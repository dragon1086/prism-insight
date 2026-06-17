"""Unit tests for US get_account_summary USD-denominated total asset.

Regression guard for the 18:00 portfolio season-return bug. The season return is
measured as USD-denominated assets minus start capital, so total_asset_usd must be:

    total_eval + usd_cash + (unsettled_sell - unsettled_buy)

- It must EXCLUDE KRW cash (dollar-basis metric).
- The net-unsettled term puts cash on the same trade-date basis as the real-time
  eval, removing the see-saw caused by the settlement-lagged USD deposit
  (frcr_dncl_amt_2).
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


def _usd_row(cash="2640.91", sell="1023.69", buy="595.39", rate="1513.50"):
    return {
        "crcy_cd": "USD",
        "frcr_dncl_amt_2": cash,
        "frcr_sll_amt_smtl": sell,
        "frcr_buy_amt_smtl": buy,
        "frst_bltn_exrt": rate,
    }


_PORTFOLIO = [
    {"eval_amount": 8709.0, "profit_amount": 342.0, "avg_price": 100.0, "quantity": 83},
]
# KRW deposit in output3 must NOT leak into total_asset_usd.
_OUTPUT3_WITH_KRW = {"tot_asst_amt": "20925371", "tot_dncl_amt": "3098556"}


def test_total_asset_usd_is_eval_plus_cash_plus_net_unsettled():
    trader = _make_trader(_FakeRes([_usd_row()], _OUTPUT3_WITH_KRW), _PORTFOLIO)

    summary = trader.get_account_summary()

    expected = 8709.0 + 2640.91 + (1023.69 - 595.39)  # = 11778.21
    assert summary is not None
    assert summary["total_asset_usd"] == pytest.approx(expected, abs=0.01)
    assert summary["net_unsettled_usd"] == pytest.approx(428.30, abs=0.01)


def test_total_asset_usd_excludes_krw_cash():
    # KRW deposit (~$2,047) is large; if it leaked in, total would jump well above
    # eval+cash+unsettled. Guard that the dollar metric stays KRW-free.
    trader = _make_trader(_FakeRes([_usd_row()], _OUTPUT3_WITH_KRW), _PORTFOLIO)

    summary = trader.get_account_summary()

    assert summary["total_asset_usd"] < 12000  # not the ~$13,800 KRW-inclusive total


def test_no_unsettled_reduces_to_eval_plus_cash():
    trader = _make_trader(_FakeRes([_usd_row(sell="0", buy="0")], {}), _PORTFOLIO)

    summary = trader.get_account_summary()

    assert summary["net_unsettled_usd"] == pytest.approx(0.0, abs=0.01)
    assert summary["total_asset_usd"] == pytest.approx(8709.0 + 2640.91, abs=0.01)


def test_net_unsettled_can_be_negative():
    # More buys than sells in transit -> cash is overstated, net unsettled negative.
    trader = _make_trader(_FakeRes([_usd_row(sell="100", buy="700")], {}), _PORTFOLIO)

    summary = trader.get_account_summary()

    assert summary["net_unsettled_usd"] == pytest.approx(-600.0, abs=0.01)
    assert summary["total_asset_usd"] == pytest.approx(8709.0 + 2640.91 - 600.0, abs=0.01)


def test_missing_usd_row_degrades_to_eval_only():
    trader = _make_trader(_FakeRes([], {}), _PORTFOLIO)

    summary = trader.get_account_summary()

    assert summary["usd_cash"] == 0.0
    assert summary["total_asset_usd"] == pytest.approx(8709.0, abs=0.01)


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
