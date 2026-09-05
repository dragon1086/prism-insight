"""No network: protective stops require confirmed exchange state, not ACKs."""
import pytest

from live.protection import reconcile_stop


def stop(**changes):
    return dict(orderId="sl", symbol="BTCUSDT", positionIdx=0, side="Sell",
                orderType="Market", reduceOnly=True, triggerPrice="95.0",
                qty="0.030", triggerDirection=2, triggerBy="LastPrice",
                orderStatus="Untriggered", **changes)


class Exchange:
    def __init__(self, rows=(), apply=True, reject=False):
        self.rows = list(rows)
        self.apply = apply
        self.reject = reject
        self.calls = []

    def call(self, method, **kw):
        self.calls.append((method, kw))
        if method == "get_open_orders":
            return {"retCode": 0, "result": {"list": self.rows}}
        assert method in ("amend_order", "place_order")
        if self.reject:
            return {"retCode": 1, "result": {}}
        oid = kw.get("orderId", "new-sl")
        if self.apply:
            if method == "amend_order":
                for row in self.rows:
                    if row["orderId"] == oid:
                        row.update(qty=kw["qty"], triggerPrice=kw["triggerPrice"])
            else:
                self.rows.append({**kw, "orderId": oid, "orderStatus": "Untriggered"})
        return {"retCode": 0, "result": {"orderId": oid}}


def reconcile(ex, **kw):
    return reconcile_stop(ex.call, side="long", qty=.03, trigger=95., **kw)


def test_sufficient_tighter_stop_no_churn():
    ex = Exchange([{**stop(), "triggerPrice": "97.0"}])
    result = reconcile(ex, owned_order_id="sl")
    assert result.confirmed and result.order_id == "sl"
    assert [m for m, _ in ex.calls] == ["get_open_orders"]


def test_amends_quantity_and_trigger_without_cancel():
    ex = Exchange([{**stop(), "qty": ".01", "triggerPrice": "97"}])
    result = reconcile(ex, owned_order_id="sl")
    assert result.confirmed
    amend = next(k for m, k in ex.calls if m == "amend_order")
    assert amend["qty"] == "0.030" and amend["triggerPrice"] == "97.0"
    assert not any(m in ("cancel_order", "place_order") for m, _ in ex.calls)


def test_amend_ack_without_readback_not_confirmed():
    ex = Exchange([{**stop(), "qty": ".01"}], apply=False)
    result = reconcile(ex, owned_order_id="sl")
    assert not result.confirmed and result.order_id == "sl"
    assert ex.rows[0]["qty"] == ".01"


def test_create_only_after_complete_absence_and_verify():
    ex = Exchange()
    result = reconcile(ex, create_order_link_id="persisted-stop-intent")
    assert result.confirmed and result.order_id == "new-sl"
    assert [m for m, _ in ex.calls] == ["get_open_orders", "place_order", "get_open_orders"]


def test_creation_requires_persisted_identity():
    ex = Exchange()
    assert not reconcile(ex).confirmed
    assert len(ex.calls) == 1


def test_create_ack_without_readback_remains_unconfirmed():
    ex = Exchange(apply=False)
    result = reconcile(ex, create_order_link_id="intent")
    assert result.status == "ACK_UNCONFIRMED" and result.order_id == "new-sl"
    assert ex.rows == []


def test_failed_read_never_places_or_cancels():
    calls = []
    def call(method, **kw):
        calls.append(method)
        return None
    result = reconcile_stop(call, side="long", qty=.03, trigger=95., create_order_link_id="intent")
    assert not result.confirmed and calls == ["get_open_orders"]


@pytest.mark.parametrize("field,value", [
    ("side", "Buy"), ("orderType", "Limit"), ("reduceOnly", False),
    ("qty", "NaN"), ("triggerPrice", "NaN"), ("positionIdx", 1),
    ("triggerBy", "MarkPrice"), ("triggerDirection", 1),
    ("orderStatus", "Triggered"), ("symbol", "ETHUSDT"),
])
def test_invalid_owned_order_never_counts_as_protection(field, value):
    ex = Exchange([{**stop(), field: value}])
    result = reconcile(ex, owned_order_id="sl", create_order_link_id="intent")
    assert not result.confirmed
    assert len(ex.calls) == 1


def test_failed_amend_keeps_old_stop_and_id():
    ex = Exchange([{**stop(), "qty": ".01"}], reject=True)
    result = reconcile(ex, owned_order_id="sl")
    assert not result.confirmed and result.order_id == "sl"
    assert ex.rows[0]["qty"] == ".01"
    assert not any(m in ("cancel_order", "place_order") for m, _ in ex.calls)


def test_repeat_after_creation_does_not_duplicate():
    ex = Exchange()
    first = reconcile(ex, create_order_link_id="intent")
    second = reconcile(ex, create_order_link_id="intent")
    assert first.confirmed and second.confirmed
    assert sum(m == "place_order" for m, _ in ex.calls) == 1


def test_short_preserves_lower_trigger_while_upsizing():
    ex = Exchange([{**stop(), "side": "Buy", "triggerDirection": 1,
                    "triggerPrice": "103.0", "qty": ".01"}])
    result = reconcile_stop(ex.call, side="short", qty=.03, trigger=105., owned_order_id="sl")
    assert result.confirmed and result.trigger == 103.
    assert float(ex.rows[0]["qty"]) == .03


def test_second_page_stop_prevents_duplicate_creation():
    calls = []
    def call(method, **kw):
        calls.append(method)
        assert method == "get_open_orders"
        return {"retCode": 0, "result": {
            "list": [stop()] if kw.get("cursor") else [],
            "nextPageCursor": "" if kw.get("cursor") else "next"}}
    result = reconcile_stop(call, side="long", qty=.03, trigger=95., create_order_link_id="intent")
    assert result.confirmed and len(calls) == 2


def test_unowned_insufficient_stop_is_not_modified_or_replaced():
    ex = Exchange([{**stop(), "qty": ".01"}])
    result = reconcile(ex, create_order_link_id="intent")
    assert result.status == "UNOWNED_STOP_INSUFFICIENT"
    assert len(ex.calls) == 1


def test_lost_ack_can_be_confirmed_by_readback():
    ex = Exchange()
    def call(method, **kw):
        result = ex.call(method, **kw)
        if method == "place_order":
            raise TimeoutError("response lost after accept")
        return result
    result = reconcile_stop(call, side="long", qty=.03, trigger=95., create_order_link_id="intent")
    assert result.confirmed
