"""Cancel ACK is not terminal evidence, including across process restart."""
import sqlite3

import pytest

from live import tracking
from live.order_retirement import request_stop_retirement, reconcile_stop_retirements


def response(rows):
    return {"retCode": 0, "result": {"list": rows, "nextPageCursor": ""}}


def stop(status="Untriggered", **extra):
    return {"orderId": "owned-stop", "symbol": "BTCUSDT", "positionIdx": 0,
            "side": "Sell", "orderType": "Market", "reduceOnly": True,
            "triggerPrice": "90000", "orderStatus": status, **extra}


class Broker:
    def __init__(self):
        self.row = stop()
        self.size = "0"
        self.calls = []
        self.cancel_unknown = False
        self.entries = []

    def __call__(self, method, **kwargs):
        self.calls.append((method, kwargs))
        if method == "get_positions":
            return response([{"symbol": "BTCUSDT", "positionIdx": 0, "size": self.size}])
        if method == "get_order_history":
            return response([] if self.row is None else [self.row])
        if method == "get_open_orders":
            return response(self.entries)
        if method == "cancel_order":
            if self.cancel_unknown:
                raise TimeoutError("accepted, then connection lost")
            return {"retCode": 0, "result": {"orderId": "owned-stop"}}
        raise AssertionError(method)


@pytest.fixture
def conn(tmp_path):
    c = sqlite3.connect(tmp_path / "tracking.sqlite")
    c.row_factory = sqlite3.Row
    tracking.ensure_schema(c)
    yield c
    c.close()


def test_cancel_ack_keeps_durable_retirement_and_restart(conn):
    broker = Broker()
    assert not request_stop_retirement(conn, "demo", broker, "owned-stop")
    assert tracking.get_meta(conn, "stop_retirements_v1", "demo")
    path = conn.execute("PRAGMA database_list").fetchone()[2]
    with sqlite3.connect(path) as reopened:
        reopened.row_factory = sqlite3.Row
        assert not reconcile_stop_retirements(reopened, "demo", broker)
        broker.row = stop("Cancelled")
        assert reconcile_stop_retirements(reopened, "demo", broker)
        assert not tracking.get_meta(reopened, "stop_retirements_v1", "demo")


@pytest.mark.parametrize("status", ["Cancelled", "Filled", "Rejected", "Deactivated",
                                    "PartiallyFilledCanceled"])
def test_exact_terminal_releases_without_cancel(conn, status):
    broker = Broker()
    broker.row = stop(status)
    assert request_stop_retirement(conn, "swing", broker, "owned-stop")
    assert not any(m == "cancel_order" for m, _ in broker.calls)


@pytest.mark.parametrize("mutation", [None, {"orderId": "other"}, {"symbol": "ETHUSDT"},
                                     {"positionIdx": 1}, {"reduceOnly": False},
                                     {"triggerPrice": "NaN"}, {"orderType": "Limit"}])
def test_missing_or_wrong_identity_never_frees_or_cancels(conn, mutation):
    broker = Broker()
    broker.row = None if mutation is None else stop("Cancelled", **mutation)
    assert not request_stop_retirement(conn, "demo", broker, "owned-stop")
    assert not any(m == "cancel_order" for m, _ in broker.calls)


@pytest.mark.parametrize("size", ["0.1", "NaN", "-1", "garbage"])
def test_nonflat_or_unknown_position_preserves_stop(conn, size):
    broker = Broker()
    broker.size = size
    assert not request_stop_retirement(conn, "demo", broker, "owned-stop")
    assert not any(m == "cancel_order" for m, _ in broker.calls)


def test_cancel_timeout_never_loses_identity(conn):
    broker = Broker()
    broker.cancel_unknown = True
    assert not request_stop_retirement(conn, "demo", broker, "owned-stop")
    assert tracking.get_meta(conn, "stop_retirements_v1", "demo") == ["owned-stop"]


def test_duplicate_request_is_one_queue_entry(conn):
    broker = Broker()
    request_stop_retirement(conn, "demo", broker, "owned-stop")
    request_stop_retirement(conn, "demo", broker, "owned-stop")
    assert tracking.get_meta(conn, "stop_retirements_v1", "demo") == ["owned-stop"]


def test_readback_not_cancel_ack_finishes(conn):
    broker = Broker()
    def call(method, **kwargs):
        result = broker(method, **kwargs)
        if method == "cancel_order":
            broker.row = stop("Cancelled")
        return result
    assert request_stop_retirement(conn, "demo", call, "owned-stop")


def test_empty_queue_is_network_free(conn):
    def forbidden(*args, **kwargs):
        raise AssertionError("no pending cleanup needs a network call")
    assert reconcile_stop_retirements(conn, "demo", forbidden)


def test_corrupt_queue_is_not_silently_erased(conn):
    tracking.set_meta(conn, "stop_retirements_v1", {"bad": "data"}, "demo")
    assert not reconcile_stop_retirements(conn, "demo", Broker())
    assert tracking.get_meta(conn, "stop_retirements_v1", "demo") == {"bad": "data"}


def test_history_cycle_or_contradiction_is_unknown(conn):
    broker = Broker()
    def call(method, **kwargs):
        if method == "get_order_history":
            result = response([stop("Cancelled")])
            result["result"]["nextPageCursor"] = "loop"
            return result
        return broker(method, **kwargs)
    assert not request_stop_retirement(conn, "demo", call, "owned-stop")
    assert tracking.get_meta(conn, "stop_retirements_v1", "demo") == ["owned-stop"]


def test_no_network_inside_sqlite_transaction(conn):
    broker = Broker()
    def call(method, **kwargs):
        assert not conn.in_transaction
        return broker(method, **kwargs)
    assert not request_stop_retirement(conn, "demo", call, "owned-stop")
    assert any(m == "cancel_order" for m, _ in broker.calls)


def test_terminal_removal_keeps_concurrently_added_identity(conn):
    broker = Broker()
    def call(method, **kwargs):
        if method == "get_order_history":
            tracking.set_meta(conn, "stop_retirements_v1", ["owned-stop", "new-stop"], "demo")
            return response([stop("Cancelled")])
        return broker(method, **kwargs)
    assert not request_stop_retirement(conn, "demo", call, "owned-stop")
    assert tracking.get_meta(conn, "stop_retirements_v1", "demo") == ["new-stop"]


@pytest.mark.parametrize("lane", ["demo", "swing"])
def test_real_entry_boundary_blocks_unconfirmed_cleanup(conn, monkeypatch, lane):
    from live import demo, swing
    monkeypatch.setattr(demo, "_make_session", lambda: (object(), None))
    broker = Broker()
    tracking.set_meta(conn, "stop_retirements_v1", ["owned-stop"], lane)
    if lane == "demo":
        adapter = demo.DemoAdapter(conn, {}, [], [])
        adapter._call = broker
        result = adapter._place_limit_postonly("long", .1, 50000., stop_price=49000.)
        assert tracking.get_meta(conn, "native_entry_intent", lane) is None
    else:
        adapter = swing.ExchangeBackend(conn, object())
        adapter._call = broker
        result = adapter.open("long", .1, 49000., 50000.)
        assert tracking.get_meta(conn, "swing_entry_pending", lane) is None
    assert result is None
    assert not any(m == "place_order" for m, _ in broker.calls)


@pytest.mark.parametrize("source", ["local", "broker"])
def test_flat_with_unresolved_entry_never_cancels_stop(conn, source):
    broker = Broker()
    if source == "local":
        tracking.set_meta(conn, "pending_order", {"link_id": "still-working"}, "demo")
    else:
        broker.entries = [dict(orderId="entry", symbol="BTCUSDT", positionIdx=0,
                               reduceOnly=False, leavesQty=".1")]
    assert not request_stop_retirement(conn, "demo", broker, "owned-stop")
    assert not any(m == "cancel_order" for m, _ in broker.calls)


@pytest.mark.parametrize("lane, key", [("demo", "stop_submission_intent"),
                                      ("swing", "swing_stop_intent")])
@pytest.mark.parametrize("same_link", [True, False])
def test_terminal_only_settles_exact_submission_link(conn, lane, key, same_link):
    broker = Broker()
    broker.row = stop("Cancelled", orderLinkId="known-link")
    intent = {"submitted": True, "status": "ACK_UNCONFIRMED", "order_id": "owned-stop",
              "link_id": "known-link" if same_link else "other-link"}
    tracking.set_meta(conn, key, intent, lane)
    if lane == "demo":
        tracking.set_meta(conn, "stop_creation_link", intent["link_id"], lane)
    # Existing exposure prevents auto-discovery; terminal history itself is sufficient.
    broker.size = ".1"
    assert request_stop_retirement(conn, lane, broker, "owned-stop")
    actual = tracking.get_meta(conn, key, lane)
    assert actual["status"] == ("SETTLED" if same_link else "ACK_UNCONFIRMED")
    if lane == "demo":
        assert tracking.get_meta(conn, "stop_creation_link", lane) == (None if same_link else "other-link")


def test_lost_ack_exact_link_recovers_terminal_without_order_handle(conn):
    broker = Broker()
    broker.row = stop("Cancelled", orderLinkId="lost-ack-link")
    tracking.set_meta(conn, "stop_submission_intent", {"submitted": True,
                      "status": "SUBMISSION_UNKNOWN", "link_id": "lost-ack-link"}, "demo")
    assert reconcile_stop_retirements(conn, "demo", broker)
    assert tracking.get_meta(conn, "stop_submission_intent", "demo")["status"] == "SETTLED"
    assert not any(m == "cancel_order" for m, _ in broker.calls)


def test_lost_ack_conflicting_order_id_never_cancels_or_adopts(conn):
    broker = Broker()
    broker.row = stop(orderLinkId="known-link")
    tracking.set_meta(conn, "stop_submission_intent", {"submitted": True,
                      "status": "SUBMISSION_UNKNOWN", "link_id": "known-link",
                      "order_id": "different-id"}, "demo")
    assert not reconcile_stop_retirements(conn, "demo", broker)
    assert not tracking.get_meta(conn, "stop_retirements_v1", "demo")
    assert not any(m == "cancel_order" for m, _ in broker.calls)
