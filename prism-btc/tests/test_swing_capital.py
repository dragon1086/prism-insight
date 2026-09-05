from unittest.mock import Mock

import pytest

from live import demo, swing


def response(rows):
    return {"retCode": 0, "result": {"list": rows}}


def session(monkeypatch):
    sess = Mock()
    sess.get_wallet_balance.return_value = response([{"totalEquity": "9616"}])
    sess.get_positions.return_value = response([])
    sess.get_open_orders.return_value = response([])
    monkeypatch.setattr(demo, "_make_session", lambda: (sess, None))
    return sess


def test_uses_main_wallet(monkeypatch):
    session(monkeypatch)
    assert swing._main_capital_snapshot() == {"equity": 9616, "gross": 0}


@pytest.mark.parametrize("value", ["nan", "inf", "0", "-1"])
def test_invalid_capital_blocks(monkeypatch, value):
    sess = session(monkeypatch)
    sess.get_wallet_balance.return_value = response([{"totalEquity": value}])
    assert swing._main_capital_snapshot() is None


def test_api_failure_blocks(monkeypatch):
    sess = session(monkeypatch)
    sess.get_positions.return_value = {"retCode": 10001}
    assert swing._main_capital_snapshot() is None


def test_reserves_pending_entries_not_reduce_orders(monkeypatch):
    sess = session(monkeypatch)
    sess.get_positions.return_value = response([{"size": "0.1", "markPrice": "80000"}])
    sess.get_open_orders.return_value = response([
        {"orderId": "entry", "reduceOnly": False, "leavesQty": "0.2", "price": "80000"},
        {"orderId": "stop", "reduceOnly": True},
    ])
    assert swing._main_capital_snapshot()["gross"] == 24000


def test_incomplete_order_page_blocks(monkeypatch):
    sess = session(monkeypatch)
    sess.get_open_orders.return_value["result"]["nextPageCursor"] = "next"
    assert swing._main_capital_snapshot() is None
