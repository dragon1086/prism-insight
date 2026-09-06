from copy import deepcopy

import pytest

from live.closed_entry_recovery import closed_entry_proof


def evidence():
    pending = dict(order_id="entry", link_id="entry-link", side="long", submitted_at_ms=1000)
    parent = dict(orderId="entry", orderLinkId="entry-link", orderStatus="Filled", qty=".03", cumExecQty=".03", leavesQty="0")
    child = dict(orderId="sl", parentOrderLinkId="entry-link", symbol="BTCUSDT", positionIdx=0,
                 side="Sell", reduceOnly=True, orderType="Market", stopOrderType="StopLoss",
                 orderStatus="Filled", cumExecQty=".03")
    entry = dict(orderId="entry", execId="entry-exec", symbol="BTCUSDT", side="Buy", execType="Trade",
                 execQty=".03", execPrice="100", execFee=".001", execTime="2000")
    close = dict(orderId="sl", execId="sl-exec", symbol="BTCUSDT", side="Sell", execType="Trade",
                 execQty=".03", closedSize=".03", execPrice="90", execFee=".001", execTime="3000")
    pnl = dict(orderId="sl", symbol="BTCUSDT", qty=".03", avgEntryPrice="100", avgExitPrice="90",
               openFee=".001", closeFee=".001", closedPnl="-.302", closedSize=".03", execType="Trade", side="Sell")
    data = {"get_order_history": [parent, child], "get_executions": [entry, close], "get_closed_pnl": [pnl]}
    return pending, data


def fake_call(data):
    def call(method, **kwargs):
        rows = data[method]
        if kwargs.get("orderId"):
            rows = [row for row in rows if row.get("orderId") == kwargs["orderId"]]
        return {"retCode": 0, "result": {"list": rows}}
    return call


def test_exact_native_close_proof_deduplicates_identical_execution_ids():
    pending, data = evidence()
    data["get_executions"] += deepcopy(data["get_executions"])
    proof = closed_entry_proof(fake_call(data), pending, lambda *a: True, now_ms=4000)
    assert proof["qty"] == .03
    assert proof["execution_ids"] == ["sl-exec"]
    assert proof["net_pnl"] == -.302
    assert proof["funding_paid"] == pytest.approx(0)


@pytest.mark.parametrize("fault", ["entry_fee", "close_fee", "pnl_fee", "history", "link", "duplicate_conflict",
                                    "extra_child", "missing_exec", "partial_close", "wrong_pnl", "future_time"])
def test_missing_or_conflicting_evidence_remains_unknown(fault):
    pending, data = evidence()
    if fault == "entry_fee":
        del data["get_executions"][0]["execFee"]
    elif fault == "close_fee":
        del data["get_executions"][1]["execFee"]
    elif fault == "pnl_fee":
        del data["get_closed_pnl"][0]["openFee"]
    elif fault == "history":
        data["get_order_history"] = []
    elif fault == "link":
        data["get_order_history"][1]["parentOrderLinkId"] = "unrelated"
    elif fault == "duplicate_conflict":
        data["get_executions"].append({**data["get_executions"][1], "execFee": ".002"})
    elif fault == "extra_child":
        data["get_order_history"].append({**data["get_order_history"][1], "orderId": "other"})
    elif fault == "missing_exec":
        data["get_executions"].pop()
    elif fault == "partial_close":
        data["get_executions"][1]["closedSize"] = ".01"
    elif fault == "wrong_pnl":
        data["get_closed_pnl"][0]["qty"] = ".02"
    elif fault == "future_time":
        data["get_executions"][1]["execTime"] = "5000"
    assert closed_entry_proof(fake_call(data), pending, lambda *a: True, now_ms=4000) is None


@pytest.mark.parametrize("failure", ["cycle", "later_page", "exception"])
def test_incomplete_pagination_never_returns_proof(failure):
    pending, data = evidence()
    base = fake_call(data)
    def call(method, **params):
        if method != "get_executions":
            return base(method, **params)
        if failure == "exception":
            raise RuntimeError("untrusted payload")
        if failure == "later_page" and params.get("cursor"):
            return {"retCode": 1, "result": {}}
        reply = base(method, **params)
        reply["result"]["nextPageCursor"] = "repeat"
        return reply
    assert closed_entry_proof(call, pending, lambda *a: True, now_ms=4000) is None


def test_time_bound_is_unknown_not_auto_release():
    pending, data = evidence()
    assert closed_entry_proof(fake_call(data), pending, lambda *a: True,
                              now_ms=1000 + 24 * 3600_000) is None
