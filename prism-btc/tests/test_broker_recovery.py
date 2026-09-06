from types import SimpleNamespace

import pandas as pd
import pytest

from live import broker_recovery, tracking


@pytest.fixture
def conn():
    db = tracking.get_connection(":memory:")
    tracking.ensure_schema(db)
    yield db
    db.close()


def adapter(conn, monkeypatch, position=None, local=()):
    calls = []
    monkeypatch.setattr(tracking, "load_open_positions", lambda *a: list(local))
    obj = SimpleNamespace(
        conn=conn, mode="demo", sess=object(),
        _call=lambda *a, **k: pytest.fail("unexpected broker call"),
        _sync_state=lambda now: {"position": position, "open_orders": []},
        _get_meta=lambda *a: None,
        _recover_entry_identity=lambda *a: calls.append("identity"),
        _protect_tp_exposure=lambda *a: calls.append("tp_protect"),
        _protect_pending_entry=lambda *a: calls.append("entry_protect"),
        _resume_reduce=lambda: calls.append("reduce"),
        _settle_tp=lambda *a: True,
        _ensure_stop=lambda *a: calls.append(("stop", a)) or SimpleNamespace(confirmed=True),
        _record_closed_trades=lambda *a: calls.append("closed_trades"),
    )
    return obj, calls


def test_known_exposure_repairs_stop_without_bar_or_entries(conn, monkeypatch):
    obj, calls = adapter(conn, monkeypatch, {"side": "long", "qty": .3},
                         [SimpleNamespace(side="long", sl_price=90), SimpleNamespace(side="long", sl_price=95)])
    broker_recovery.reconcile_main(obj, pd.Timestamp.now(tz="UTC"))
    assert ("stop", ("long", .3, 95)) in calls
    assert calls.index("tp_protect") < calls.index("closed_trades")
    assert tracking.get_meta(conn, "last_processed_30m_ns", "demo") is None


def test_unknown_snapshot_never_clears_or_repairs_positions(conn, monkeypatch):
    obj, calls = adapter(conn, monkeypatch)
    def fail(*a):
        raise RuntimeError("snapshot unavailable")
    obj._sync_state = fail
    with pytest.raises(RuntimeError, match="snapshot unavailable"):
        broker_recovery.reconcile_main(obj, "now")
    assert calls == []


def test_unconfirmed_stop_is_not_success(conn, monkeypatch):
    obj, _ = adapter(conn, monkeypatch, {"side": "short", "qty": .3},
                     [SimpleNamespace(side="short", sl_price=110)])
    obj._ensure_stop = lambda *a: SimpleNamespace(confirmed=False)
    with pytest.raises(RuntimeError, match="stop not confirmed"):
        broker_recovery.reconcile_main(obj, "now")


def test_orphan_exposure_requires_intervention(conn, monkeypatch):
    obj, _ = adapter(conn, monkeypatch, {"side": "long", "qty": .3})
    with pytest.raises(RuntimeError, match="no owned stop intent"):
        broker_recovery.reconcile_main(obj, "now")


def test_swing_no_exposure_does_not_create_broker(conn, monkeypatch):
    from live import swing
    monkeypatch.setattr(swing, "_make_swing_session", lambda: pytest.fail("unneeded broker"))
    broker_recovery.reconcile_swing(conn, "demo", "now")


def test_partial_entry_stop_failure_fences_even_without_local_fill(conn, monkeypatch):
    obj, calls = adapter(conn, monkeypatch, {"side": "long", "qty": .1})
    obj._get_meta = lambda *a: {"side": "long", "sizing_sl_price": 95}
    obj._ensure_stop = lambda *a: SimpleNamespace(confirmed=False)
    with pytest.raises(RuntimeError, match="stop not confirmed"):
        broker_recovery.reconcile_main(obj, "now")
    assert "entry_protect" in calls


def test_swing_virtual_without_exchange_ownership_is_unchanged(conn, monkeypatch):
    from live import swing
    monkeypatch.setattr(tracking, "load_open_positions", lambda *a: [object()])
    monkeypatch.setattr(swing, "_make_swing_session", lambda: (None, "no keys"))
    broker_recovery.reconcile_swing(conn, "demo", "now")


def test_swing_exchange_ownership_missing_session_fails_closed(conn, monkeypatch):
    from live import swing
    tracking.set_meta(conn, "swing_native_entry", {"link_id": "owned"}, "swing")
    monkeypatch.setattr(swing, "_make_swing_session", lambda: (None, "no keys"))
    with pytest.raises(RuntimeError, match="broker unavailable"):
        broker_recovery.reconcile_swing(conn, "demo", "now")


def test_pending_swing_failure_still_checks_existing_position(conn, monkeypatch):
    pos = object()
    tracking.set_meta(conn, "swing_entry_pending", {"link_id": "pending"}, "swing")
    monkeypatch.setattr(tracking, "load_open_positions", lambda *a: [pos])
    calls = []
    backend = SimpleNamespace(name="exchange", recover_pending_entry=lambda: False,
                              last_protection_confirmed=True,
                              _call=lambda *a, **k: pytest.fail("unexpected broker call"),
                              check_stop=lambda p, bar: calls.append((p, bar)))
    with pytest.raises(RuntimeError, match="entry recovery pending"):
        broker_recovery.reconcile_swing(conn, "demo", "now", backend=backend)
    assert calls == [(pos, None)]


def test_unresolved_retirement_keeps_protection_running(conn, monkeypatch):
    obj, calls = adapter(conn, monkeypatch, {"side": "long", "qty": .3},
                         [SimpleNamespace(side="long", sl_price=95)])
    monkeypatch.setattr(broker_recovery, "reconcile_stop_retirements", lambda *a: False)
    with pytest.raises(RuntimeError, match="stop retirement pending"):
        broker_recovery.reconcile_main(obj, "now")
    assert ("stop", ("long", .3, 95)) in calls


def test_swing_silent_protection_failure_is_not_success(conn, monkeypatch):
    monkeypatch.setattr(tracking, "load_open_positions", lambda *a: [object()])
    backend = SimpleNamespace(name="exchange", recover_pending_entry=lambda: True,
                              _call=lambda *a, **k: pytest.fail("unexpected broker call"),
                              last_protection_confirmed=False, check_stop=lambda *a: None)
    with pytest.raises(RuntimeError, match="protection or settlement unconfirmed"):
        broker_recovery.reconcile_swing(conn, "demo", "now", backend=backend)


def test_swing_exact_close_settlement_without_market_data(conn, monkeypatch):
    from live import swing
    pos = object()
    monkeypatch.setattr(tracking, "load_open_positions", lambda *a: [pos])
    tracking.record_equity(conn, 1000, "swing")
    calls = []
    monkeypatch.setattr(swing, "_close_position", lambda *a: calls.append(a) or (1010, 1))
    backend = SimpleNamespace(name="exchange", recover_pending_entry=lambda: True,
                              _call=lambda *a, **k: pytest.fail("unexpected broker call"),
                              last_protection_confirmed=True, check_stop=lambda *a: 101)
    broker_recovery.reconcile_swing(conn, "demo", "now", backend=backend)
    assert len(calls) == 1
    assert calls[0][2:4] == (pos, 101)
    assert tracking.get_meta(conn, "trade_id_counter", "swing") == 1
    assert tracking.get_meta(conn, "last_processed_30m_ns", "swing") is None


def test_main_recovery_snapshot_and_mutations_hold_shared_lock(conn, monkeypatch):
    from contextlib import contextmanager
    held = []
    @contextmanager
    def lock(db):
        assert db is conn
        held.append(True)
        try:
            yield
        finally:
            held.pop()
    monkeypatch.setattr(broker_recovery, "mutation_lock", lock)
    obj, _ = adapter(conn, monkeypatch)
    def snapshot(*a):
        assert held == [True]
        return {"position": None, "open_orders": []}
    def settlement(*a):
        assert held == [True]
    obj._sync_state = snapshot
    obj._record_closed_trades = settlement
    broker_recovery.reconcile_main(obj, "now")
    assert held == []


def test_flat_pending_entry_and_stop_queue_recovers_without_strategy(conn, monkeypatch):
    obj, calls = adapter(conn, monkeypatch)
    now = pd.Timestamp.now(tz="UTC")
    pending = {"order_id": "entry", "link_id": "owned", "side": "long",
               "submitted_at_ms": now.value // 1_000_000 - 2 * 3600_000}
    tracking.set_meta(conn, "pending_order", pending, "demo")
    obj._get_meta = lambda key, default=None: tracking.get_meta(conn, key, "demo") or default
    obj._set_meta = lambda key, value: tracking.set_meta(conn, key, value, "demo")
    obj._entry_receipt_recorded = lambda p: False
    obj._entry_terminal_zero = lambda p: p.get("cancel_requested", False)
    def cancel(p):
        calls.append("cancel_entry")
        p["cancel_requested"] = True
        obj._set_meta("pending_order", p)
    obj._request_entry_cancel = cancel
    monkeypatch.setattr(broker_recovery, "reconcile_stop_retirements",
                        lambda *a: tracking.get_meta(conn, "pending_order", "demo") is None)
    with pytest.raises(RuntimeError):
        broker_recovery.reconcile_main(obj, now)
    assert calls.count("cancel_entry") == 1
    broker_recovery.reconcile_main(obj, now + pd.Timedelta(minutes=10))
    assert tracking.get_meta(conn, "pending_order", "demo") is None
    assert tracking.get_meta(conn, "last_processed_30m_ns", "demo") is None


@pytest.mark.parametrize("history_unknown", [False, True])
def test_real_adapter_expired_entry_cancel_then_zero_fill_release(conn, monkeypatch, history_unknown):
    from tests.test_demo import FakeExchange, _patch_session, _make_adapter, TestExitReduceOnly
    fake = FakeExchange()
    _patch_session(monkeypatch, fake)
    obj = _make_adapter(conn, fake)
    parent = TestExitReduceOnly._native_pending(obj, order_id="accepted-parent")
    now = pd.Timestamp.now(tz="UTC")
    pending = obj._get_meta("pending_order")
    pending["submitted_at_ms"] = now.value // 1_000_000 - 2 * 3600_000
    obj._set_meta("pending_order", pending)
    parent.update(orderStatus="New", cumExecQty="0")
    fake._open_orders = [parent]
    monkeypatch.setattr(fake, "get_order_history", lambda **kw: fake._ok({"list": [parent]}), raising=False)
    with pytest.raises(RuntimeError, match="entry recovery pending"):
        broker_recovery.reconcile_main(obj, now)
    assert fake.calls_to("cancel_order") == [{"category": "linear", "symbol": "BTCUSDT", "orderId": "accepted-parent"}]
    assert obj._get_meta("pending_order") is not None  # ACK alone is not terminal.
    parent["orderStatus"] = "Cancelled"
    fake._open_orders = []
    if history_unknown:
        monkeypatch.setattr(fake, "get_order_history", lambda **kw: fake._ok({"list": []}))
        with pytest.raises(RuntimeError, match="entry recovery pending"):
            broker_recovery.reconcile_main(obj, now + pd.Timedelta(minutes=10))
        assert obj._get_meta("pending_order") is not None
    else:
        broker_recovery.reconcile_main(obj, now + pd.Timedelta(minutes=10))
        assert obj._get_meta("pending_order") is None
    assert tracking.load_open_positions(conn, "demo") == []
    assert not fake.placed_orders


@pytest.mark.parametrize("qty,status", [(".03", "Filled"), (".01", "Cancelled")])
def test_real_adapter_terminal_native_fill_recovers_receipt_once(conn, monkeypatch, qty, status):
    from tests.test_demo import FakeExchange, _patch_session, _make_adapter, TestExitReduceOnly, _ex_position
    fake = FakeExchange(position=_ex_position(size=qty))
    _patch_session(monkeypatch, fake)
    obj = _make_adapter(conn, fake)
    parent = TestExitReduceOnly._native_pending(obj, order_id="accepted-parent")
    parent.update(orderStatus=status, cumExecQty=qty, leavesQty="0")
    fake._executions = [{"orderId": "accepted-parent", "orderLinkId": parent["orderLinkId"],
                         "symbol": "BTCUSDT", "side": "Buy", "execType": "Trade",
                         "execQty": qty, "execPrice": "100", "execId": "entry-fill",
                         "execTime": "1700000000000"}]
    def history(**kw):
        rows = [parent] + list(fake._open_orders)
        return fake._ok({"list": [row for row in rows
            if (not kw.get("orderId") or row.get("orderId") == kw["orderId"])
            and (not kw.get("orderLinkId") or row.get("orderLinkId") == kw["orderLinkId"])]})
    monkeypatch.setattr(fake, "get_order_history", history, raising=False)
    monkeypatch.setattr(fake, "get_executions", lambda **kw: fake._ok({"list": [row for row in fake._executions
        if (not kw.get("orderId") or row.get("orderId") == kw["orderId"])
        and (not kw.get("orderLinkId") or row.get("orderLinkId") == kw["orderLinkId"])]}))
    now = pd.Timestamp.now(tz="UTC")
    broker_recovery.reconcile_main(obj, now)
    assert obj._get_meta("pending_order") is None
    assert len(tracking.load_open_positions(conn, "demo")) == 1
    before = list(fake.placed_orders)
    broker_recovery.reconcile_main(_make_adapter(conn, fake), now + pd.Timedelta(minutes=10))
    assert len(tracking.load_open_positions(conn, "demo")) == 1
    assert fake.placed_orders == before
    assert all(order["reduceOnly"] for order in fake.placed_orders)


@pytest.mark.parametrize("fault", [None, "missing_fee", "missing_child_link", "missing_history", "conflicting_exec", "receipt_write"])
def test_real_adapter_attached_sl_closes_before_adoption(conn, monkeypatch, fault):
    from tests.test_demo import FakeExchange, _patch_session, _make_adapter, TestExitReduceOnly
    from tests.test_closed_entry_recovery import evidence, fake_call
    fake = FakeExchange()
    _patch_session(monkeypatch, fake)
    obj = _make_adapter(conn, fake)
    parent = TestExitReduceOnly._native_pending(obj, order_id="accepted-parent")
    now = pd.Timestamp.now(tz="UTC")
    submitted = int(now.value // 1_000_000) - 10_000
    pending = obj._get_meta("pending_order")
    pending["submitted_at_ms"] = submitted
    obj._set_meta("pending_order", pending)
    _, data = evidence()
    parent.update(orderStatus="Filled", cumExecQty=".03", leavesQty="0")
    data["get_order_history"][0] = parent
    data["get_order_history"][1]["parentOrderLinkId"] = pending["link_id"]
    data["get_executions"][0].update(orderId=pending["order_id"], execTime=str(submitted + 2000))
    data["get_executions"][1]["execTime"] = str(submitted + 5000)
    data["get_executions"].append(dict(data["get_executions"][1]))  # identical page duplicate
    if fault == "missing_fee":
        del data["get_executions"][0]["execFee"]
    elif fault == "missing_child_link":
        data["get_order_history"][1].pop("parentOrderLinkId")
    elif fault == "missing_history":
        data["get_order_history"] = []
    elif fault == "conflicting_exec":
        data["get_executions"][-1]["execPrice"] = "91"
    call = fake_call(data)
    for method in data:
        monkeypatch.setattr(fake, method, lambda _method=method, **kw: call(_method, **kw), raising=False)
    if fault == "receipt_write":
        original = tracking.set_meta
        def fail_receipt(db, key, *args, **kwargs):
            if key.startswith("main_entry_receipt:"):
                raise RuntimeError("receipt write interrupted")
            return original(db, key, *args, **kwargs)
        monkeypatch.setattr(tracking, "set_meta", fail_receipt)
        with pytest.raises(RuntimeError, match="receipt write interrupted"):
            broker_recovery.reconcile_main(obj, now)
        assert conn.execute("SELECT count(*) FROM btc_trading_history").fetchone()[0] == 0
        assert obj._get_meta("pending_order") is not None
        monkeypatch.setattr(tracking, "set_meta", original)
        fault = None  # Restart from the intact pending intent; exactly one trade.
    if fault:
        with pytest.raises(RuntimeError, match="entry recovery pending"):
            broker_recovery.reconcile_main(obj, now)
        assert obj._get_meta("pending_order") is not None
        assert obj._get_meta("last_exec_ns") is None
        assert conn.execute("SELECT count(*) FROM btc_trading_history").fetchone()[0] == 0
    else:
        broker_recovery.reconcile_main(obj, now)
        broker_recovery.reconcile_main(_make_adapter(conn, fake), now + pd.Timedelta(minutes=10))
        assert obj._get_meta("pending_order") is None
        assert conn.execute("SELECT count(*) FROM btc_trading_history").fetchone()[0] == 1
        trade = conn.execute("SELECT * FROM btc_trading_history").fetchone()
        assert trade["net_pnl"] == -.302
        assert trade["fee_paid"] == .002
        assert trade["exit_reason"] == "native_sl_before_adoption"
        receipt = obj._get_meta("main_entry_receipt:" + pending["link_id"])
        assert receipt["closed"] is True
        assert receipt["funding_source"] == "settlement_residual"
        from live.shadow import bar_index_for
        assert obj._get_meta("last_close_bar")["long"] == bar_index_for(submitted + 5000)
    assert tracking.load_open_positions(conn, "demo") == []
    assert obj._get_meta("last_processed_30m_ns") is None
    assert not fake.placed_orders
