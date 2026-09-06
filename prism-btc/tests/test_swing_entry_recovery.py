import pytest

from live import tracking
from live.swing import ExchangeBackend
from live.protection import ProtectionResult
from .test_shared_entry_coordinator import Session


@pytest.fixture
def recovered(tmp_path, monkeypatch):
    monkeypatch.delenv("BTC_SHARED_ENTRY_ENABLED", raising=False)
    conn = tracking.get_connection(tmp_path / "root.sqlite")
    tracking.ensure_schema(conn)
    session = Session("202")
    backend = ExchangeBackend(conn, session)
    pending = {"link_id": "exact-entry", "side": "long", "qty": 2., "stop_price": 95.,
               "entry_context": {"leverage": 1., "logical_capital": 10000., "entry_bar_idx": 3}}
    tracking.set_meta(conn, "swing_entry_pending", pending, "swing")
    session.parents = [{"orderId": "parent", "orderLinkId": "exact-entry", "symbol": "BTCUSDT",
                        "positionIdx": 0, "side": "Buy", "reduceOnly": False, "orderType": "Market",
                        "qty": "2", "cumExecQty": "1", "leavesQty": "0", "orderStatus": "PartiallyFilledCanceled"}]
    session.executions = [{"orderId": "parent", "orderLinkId": "exact-entry", "execId": "fill-1",
                           "symbol": "BTCUSDT", "side": "Buy", "execType": "Trade", "execQty": "1",
                           "execPrice": "100", "execFee": ".05", "execTime": "1700000000000"}]
    session.positions = [{"symbol": "BTCUSDT", "positionIdx": 0, "side": "Buy", "size": "1", "avgPrice": "100"}]
    backend._ensure_entry_stop = lambda *_: ProtectionResult("CONFIRMED", "stop", 95., 1.)
    yield conn, backend, session
    conn.close()


def test_accepted_unrecorded_partial_terminal_recovers_once(recovered):
    conn, backend, _ = recovered
    assert backend.recover_pending_entry()
    positions = tracking.load_open_positions(conn, "swing")
    assert len(positions) == 1 and positions[0].qty == 1.
    assert positions[0].entry_fee == .05
    assert tracking.get_meta(conn, "swing_entry_pending", "swing") is None
    assert backend.recover_pending_entry()
    assert len(tracking.load_open_positions(conn, "swing")) == 1


def test_live_partial_is_protected_but_not_receipted(recovered):
    conn, backend, session = recovered
    session.parents[0].update(orderStatus="PartiallyFilled", leavesQty="1")
    repairs = []
    backend._ensure_entry_stop = lambda *args: repairs.append(args) or ProtectionResult("CONFIRMED")
    assert not backend.recover_pending_entry()
    assert repairs == [("long", 1., 95.)]
    assert tracking.load_open_positions(conn, "swing") == []
    assert tracking.get_meta(conn, "swing_entry_pending", "swing")


@pytest.mark.parametrize("corrupt", ["identity", "fee", "time", "quantity", "flat", "stop"])
def test_ambiguous_recovery_keeps_pending(recovered, corrupt):
    conn, backend, session = recovered
    if corrupt == "identity":
        session.parents[0]["orderLinkId"] = "manual"
    elif corrupt in ("fee", "time"):
        session.executions[0].pop("execFee" if corrupt == "fee" else "execTime")
    elif corrupt == "quantity":
        session.executions[0]["execQty"] = ".5"
    elif corrupt == "flat":
        session.positions = []
    else:
        backend._ensure_entry_stop = lambda *_: ProtectionResult("SUBMISSION_UNKNOWN")
    assert not backend.recover_pending_entry()
    assert tracking.get_meta(conn, "swing_entry_pending", "swing")
    assert tracking.load_open_positions(conn, "swing") == []


def test_terminal_zero_requires_empty_execution_history(recovered):
    conn, backend, session = recovered
    session.parents[0].update(orderStatus="Cancelled", cumExecQty="0")
    session.positions = []
    assert not backend.recover_pending_entry()
    session.executions = []
    assert backend.recover_pending_entry()
    assert tracking.load_open_positions(conn, "swing") == []
    assert tracking.get_meta(conn, "swing_entry_receipt:exact-entry", "swing")["qty"] == 0


def test_crash_after_atomic_receipt_does_not_duplicate(recovered):
    conn, backend, _ = recovered
    pending = tracking.get_meta(conn, "swing_entry_pending", "swing")
    assert backend.recover_pending_entry()
    tracking.set_meta(conn, "swing_entry_pending", pending, "swing")
    assert backend.recover_pending_entry()
    assert len(tracking.load_open_positions(conn, "swing")) == 1


def test_native_sl_closed_before_adoption_records_exact_round_trip_once(recovered):
    import time
    conn, backend, session = recovered
    now = time.time_ns() // 1_000_000
    pending = tracking.get_meta(conn, "swing_entry_pending", "swing")
    pending["submitted_at_ms"] = now - 3000
    tracking.set_meta(conn, "swing_entry_pending", pending, "swing")
    session.positions = []
    session.executions[0]["execTime"] = str(now - 2000)
    session.parents.append({"orderId": "native-child", "parentOrderLinkId": "exact-entry",
                            "symbol": "BTCUSDT", "positionIdx": 0, "side": "Sell", "reduceOnly": True,
                            "orderType": "Market", "stopOrderType": "StopLoss", "orderStatus": "Filled",
                            "cumExecQty": "1"})
    session.executions.append({"orderId": "native-child", "execId": "close-1", "symbol": "BTCUSDT",
                               "side": "Sell", "execType": "Trade", "execQty": "1", "closedSize": "1",
                               "execPrice": "95", "execFee": ".0475", "execTime": str(now - 1000)})
    session.get_order_history = lambda **kwargs: {"retCode": 0, "result": {"list": [
        row for row in session.parents if not kwargs.get("orderId") or row["orderId"] == kwargs["orderId"]]}}
    session.get_closed_pnl = lambda **_: {"retCode": 0, "result": {"list": [
        {"orderId": "native-child", "symbol": "BTCUSDT", "side": "Sell", "execType": "Trade",
         "qty": "1", "closedSize": "1", "avgEntryPrice": "100", "avgExitPrice": "95",
         "openFee": ".05", "closeFee": ".0475", "closedPnl": "-5.0975"}]}}
    # Recover the ACK identity first; exact-link query contains only the parent.
    pending["order_id"] = "parent"
    tracking.set_meta(conn, "swing_entry_pending", pending, "swing")
    assert backend.recover_pending_entry()
    assert tracking.load_open_positions(conn, "swing") == []
    trade = conn.execute("SELECT * FROM btc_trading_history WHERE mode='swing'").fetchone()
    assert trade["net_pnl"] == -5.0975 and trade["qty"] == 1.
    assert tracking.get_meta(conn, "swing_entry_receipt:exact-entry", "swing")["closed"] is True
    tracking.set_meta(conn, "swing_entry_pending", pending, "swing")
    assert backend.recover_pending_entry()
    assert conn.execute("SELECT count(*) FROM btc_trading_history WHERE mode='swing'").fetchone()[0] == 1
