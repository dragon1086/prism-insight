import pytest
from live.take_profit import allocation_snapshot, settlement


def intent():
    return dict(side="long", order_id="tp", link_id="link", qty=.010,
                allocations=allocation_snapshot([(1, .020), (2, .010)], .010),
                executions={})


def trade(qty=.004, **changes):
    return dict(dict(execId="e1", orderId="tp", orderLinkId="link", symbol="BTCUSDT",
                     side="Sell", execType="Trade", execQty=str(qty), execPrice="110",
                     execFee=".2"), **changes)


def test_allocation_and_monotonic_partial_restart():
    i = intent()
    first = settlement(i, [trade()], .026, [(1, .020), (2, .010)])
    assert first[0] == {1: .004, 2: 0}
    i["executions"] = first[1]
    assert settlement(i, [trade(), trade()], .026, [(1, .016), (2, .010)])[0] == {1: 0, 2: 0}
    final = settlement(i, [trade(), trade(.006, execId="e2")], .020, [(1, .016), (2, .010)])
    assert sum(final[0].values()) == pytest.approx(.006)


@pytest.mark.parametrize("change", [dict(orderId="wrong"), dict(orderLinkId="wrong"),
    dict(execType="Funding"), dict(execQty="NaN"), dict(execFee="inf"), dict(side="Buy")])
def test_invalid_trade_unknown(change):
    assert settlement(intent(), [trade(**change)], .026, [(1, .020), (2, .010)]) is None


def test_overlap_and_conflicts_unknown():
    assert settlement(intent(), [trade()], .036, [(1, .020), (2, .010)]) is None
    assert settlement(intent(), [trade(), trade(execFee=".3")], .026, [(1, .020), (2, .010)]) is None


@pytest.mark.parametrize("extra", [.020, .500])
def test_duplicate_local_ids_unknown(extra):
    assert settlement(intent(), [trade()], .026, [(1, extra), (1, .020), (2, .010)]) is None


def test_no_fill_does_not_reduce():
    assert settlement(intent(), [], .030, [(1, .020), (2, .010)])[0] == {1: 0, 2: 0}


@pytest.mark.parametrize("positions,qty", [([(1, .001)], .002), ([(1, .003)], .0005),
    ([(1, .002), (1, .001)], .001), ([(None, .003)], .001), ([(0, .003)], .001),
    ([(1, -.003)], .001), ([(1, .0035)], .001)])
def test_invalid_snapshot_rejected(positions, qty):
    with pytest.raises(ValueError):
        allocation_snapshot(positions, qty)


def test_exhausted_quota_missing_row_restart():
    i = intent()
    i["qty"] = .001
    i["allocations"] = allocation_snapshot([(1, .001), (2, .001), (3, .001)], .001)
    result = settlement(i, [trade(.001)], .002, [(1, .001), (2, .001), (3, .001)])
    assert result[0] == {1: .001, 2: 0, 3: 0}
    i["executions"] = result[1]
    assert settlement(i, [trade(.001)], .002, [(2, .001), (3, .001)])[0] == {1: 0, 2: 0, 3: 0}


def setup_adapter(monkeypatch, side="long"):
    from tests.test_demo import FakeExchange, _patch_session, _make_adapter, _conn, _seed_open_position
    from live import tracking
    fake = FakeExchange()
    _patch_session(monkeypatch, fake)
    conn = _conn()
    adapter = _make_adapter(conn, fake)
    _seed_open_position(adapter, conn, side=side)
    pos = tracking.load_open_positions(conn, "demo")[0]
    adapter._set_meta("tp_intent", dict(side=side, order_id="tp", link_id="link", qty=.010,
        price=110., generation=1, state="New", executions={},
        allocations=allocation_snapshot([(pos.id, .030)], .010)))
    order = dict(orderId="tp", orderLinkId="link", symbol="BTCUSDT", positionIdx=0,
        side="Sell" if side == "long" else "Buy", reduceOnly=True, orderType="Limit",
        qty=".010", price="110", cumExecQty=".004", orderStatus="PartiallyFilled")
    fake._open_orders = [order]
    fake._executions = [trade(side=order["side"])]
    return adapter, fake, conn, order


@pytest.mark.parametrize("side", ["long", "short"])
def test_adapter_partial_restart_and_actual_fee(monkeypatch, side):
    from live import tracking
    from tests.test_demo import _make_adapter
    adapter, fake, conn, order = setup_adapter(monkeypatch, side)
    assert adapter._settle_tp(dict(side=side, qty=.026))
    pos = tracking.load_open_positions(conn, "demo")[0]
    assert pos.qty == pytest.approx(.026)
    assert not pos.tp1_hit
    assert pos.acc_exit_fee == pytest.approx(.2)
    adapter = _make_adapter(conn, fake)
    assert adapter._settle_tp(dict(side=side, qty=.026))
    fake._executions.append(trade(.006, execId="e2", side=order["side"], execFee="-.01"))
    order.update(cumExecQty=".010", orderStatus="Filled")
    assert adapter._settle_tp(dict(side=side, qty=.020))
    pos = tracking.load_open_positions(conn, "demo")[0]
    assert pos.qty == pytest.approx(.020)
    assert pos.tp1_hit
    assert pos.acc_exit_fee == pytest.approx(.19)


def test_atomic_cursor_rollback(monkeypatch):
    from live import tracking
    adapter, fake, conn, _ = setup_adapter(monkeypatch)
    original = tracking.set_meta
    def fail(*args, **kwargs):
        if args[1] == "tp_intent":
            raise RuntimeError("crash")
        return original(*args, **kwargs)
    monkeypatch.setattr(tracking, "set_meta", fail)
    with pytest.raises(RuntimeError):
        adapter._settle_tp(dict(side="long", qty=.026))
    assert tracking.load_open_positions(conn, "demo")[0].qty == .030
    assert adapter._get_meta("tp_intent")["executions"] == {}
    monkeypatch.setattr(tracking, "set_meta", original)
    assert adapter._settle_tp(dict(side="long", qty=.026))


def test_exhausted_position_archived_atomically(monkeypatch):
    from live import tracking
    from tests.test_demo import _seed_open_position
    adapter, fake, conn, order = setup_adapter(monkeypatch)
    first = tracking.load_open_positions(conn, "demo")[0]
    first.qty = .001
    tracking.save_position(conn, first)
    for _ in range(2):
        _seed_open_position(adapter, conn, qty=.001)
    rows = tracking.load_open_positions(conn, "demo")
    i = adapter._get_meta("tp_intent")
    i.update(qty=.001, allocations=allocation_snapshot([(p.id, p.qty) for p in rows], .001))
    adapter._set_meta("tp_intent", i)
    order.update(qty=".001", cumExecQty=".001", orderStatus="Filled")
    fake._executions = [trade(.001)]
    assert adapter._settle_tp(dict(side="long", qty=.002))
    assert len(tracking.load_open_positions(conn, "demo")) == 2
    assert adapter._get_meta("tp_closed_position:" + str(first.id))["tp1_hit"]
    assert adapter._settle_tp(dict(side="long", qty=.002))


@pytest.mark.parametrize("filled,previous", [(0., 0.), (.004, 0.), (.004, .004)])
def test_proven_add_and_tp_conserve_qty(monkeypatch, filled, previous):
    from live import tracking
    from tests.test_demo import TestExitReduceOnly, _BASE_TS, _bar, _ex_position
    adapter, fake, conn, order = setup_adapter(monkeypatch)
    if previous:
        assert adapter._settle_tp(dict(side="long", qty=.030 - previous))
    parent = TestExitReduceOnly._native_pending(adapter, order_id="accepted-parent")
    pending = adapter._get_meta("pending_order")
    pending["tranche_index"] = 1
    adapter._set_meta("pending_order", pending)
    adapter._set_meta("tp_order_id", "tp")
    parent.update(orderStatus="Filled", cumExecQty=".030")
    order.update(cumExecQty=str(filled), orderStatus="PartiallyFilled" if filled else "New")
    tp_trades = [trade()] if filled else []
    entry = dict(execId="entry", orderId="accepted-parent", orderLinkId="entry-lost-ack",
                 execType="Trade", symbol="BTCUSDT", side="Buy", execQty=".030", execPrice="100", execFee=".01")
    fake._position = _ex_position(size=str(.060 - filled))
    def history(**kw):
        return fake._ok({"list": [parent if kw.get("orderLinkId") == "entry-lost-ack" else order]})
    def executions(**kw):
        return fake._ok({"list": [entry] if kw.get("orderLinkId") == "entry-lost-ack" else tp_trades})
    monkeypatch.setattr(fake, "get_order_history", history, raising=False)
    monkeypatch.setattr(fake, "get_executions", executions)
    original_cancel = fake.cancel_order
    def cancel(**kw):
        if kw.get("orderId") == "tp":
            order["orderStatus"] = "Cancelled"
        return original_cancel(**kw)
    monkeypatch.setattr(fake, "cancel_order", cancel)
    adapter.process_bar(_BASE_TS, _bar(close=100.), False, None)
    rows = tracking.load_open_positions(conn, "demo")
    assert [p.qty for p in rows] == pytest.approx([.030 - filled, .030])
    assert adapter._get_meta("pending_order") is None
    new_tp = [row for row in fake.placed_orders if row.get("orderType") == "Limit" and row.get("reduceOnly")]
    assert len(new_tp) == 1
    assert float(new_tp[0]["qty"]) == float(f"{(.060 - filled) / 3:.3f}")


@pytest.mark.parametrize("filled", [0, .004])
def test_exact_cancel_replaces_residual_once(monkeypatch, filled):
    adapter, fake, conn, order = setup_adapter(monkeypatch)
    order.update(orderStatus="Cancelled", cumExecQty=str(filled))
    fake._executions = [trade()] if filled else []
    assert not adapter._settle_tp(dict(side="long", qty=.030 - filled))
    assert len(fake.placed_orders) == 1
    assert float(fake.placed_orders[0]["qty"]) == pytest.approx(.010 - filled)
    assert fake.placed_orders[0]["timeInForce"] == "GTC"
    assert adapter._get_meta("tp_intent")["generation"] == 2
    # Incomplete order/execution readback does not submit another generation.
    adapter._settle_tp(dict(side="long", qty=.030 - filled))
    assert len(fake.placed_orders) == 1


@pytest.mark.parametrize("side, high, low", [("long", 115., 99.), ("short", 101., 85.)])
def test_bar_touch_without_trade_never_marks_tp(monkeypatch, side, high, low):
    from live import tracking
    from tests.test_demo import FakeExchange, _patch_session, _make_adapter, _conn, _seed_open_position, _ex_position, _BASE_TS, _bar
    fake = FakeExchange(position=_ex_position(side="Buy" if side == "long" else "Sell"))
    _patch_session(monkeypatch, fake)
    conn = _conn()
    adapter = _make_adapter(conn, fake)
    _seed_open_position(adapter, conn, side=side, tp1=110 if side == "long" else 90,
                        sl=80 if side == "long" else 120)
    bar = _bar(close=100.)
    bar["high"], bar["low"] = high, low
    adapter.process_bar(_BASE_TS, bar, False, None)
    assert not tracking.load_open_positions(conn, "demo")[0].tp1_hit


def test_partial_qty_is_used_by_stop_update(monkeypatch):
    from live import demo
    from tests.test_demo import _BASE_TS, _bar, _ex_position
    adapter, fake, conn, order = setup_adapter(monkeypatch)
    fake._position = _ex_position(size=".026")
    monkeypatch.setattr(demo, "evaluate_exits", lambda *args: [demo.UpdateStop(new_stop=95.)])
    calls = []
    monkeypatch.setattr(adapter, "_ensure_stop", lambda side, qty, price: calls.append((side, qty, price)))
    adapter._process_bar_inner(_BASE_TS, _bar(close=100.), False, None)
    assert calls
    assert all(side == "long" and qty == pytest.approx(.026) for side, qty, _ in calls)
    assert calls[-1][2] == 95.


def test_unexplained_extra_position_unknown(monkeypatch):
    from tests.test_demo import _seed_open_position
    adapter, fake, conn, order = setup_adapter(monkeypatch)
    _seed_open_position(adapter, conn, qty=.030)
    assert not adapter._settle_tp(dict(side="long", qty=.056))
    assert adapter._get_meta("tp_intent")["executions"] == {}


def test_submit_fence_crash_never_retries(monkeypatch):
    adapter, fake, conn, order = setup_adapter(monkeypatch)
    fake._open_orders = []
    monkeypatch.setattr(fake, "get_order_history", lambda **kw: fake._ok({"list": []}), raising=False)
    pending = dict(side="long", fill_receipt=dict(tp_link_id="link", tp_order_id=None,
        prior_tp_order_id=None, tp_qty=.010, tp_price=110., tp_state="PREPARED"))
    adapter._set_meta("pending_order", pending)
    assert not adapter._complete_native_postfill(pending)
    assert not fake.placed_orders


def test_immediate_filled_tp_receipt_waits_for_coherent_trade(monkeypatch):
    from live import tracking
    adapter, fake, conn, order = setup_adapter(monkeypatch)
    order.update(orderStatus="Filled", cumExecQty=".010")
    fake._executions = [trade(.010)]
    pending = dict(side="long", fill_receipt=dict(tp_link_id="link", tp_order_id="tp",
        prior_tp_order_id=None, tp_qty=.010, tp_price=110., tp_state="ACK_UNCONFIRMED"))
    assert not adapter._complete_native_postfill(pending, dict(side="long", qty=.030))
    assert tracking.load_open_positions(conn, "demo")[0].qty == .030
    assert adapter._complete_native_postfill(pending, dict(side="long", qty=.020))
    assert tracking.load_open_positions(conn, "demo")[0].qty == pytest.approx(.020)
    assert tracking.load_open_positions(conn, "demo")[0].tp1_hit


def test_partial_receipt_missing_cumulative_is_unknown(monkeypatch):
    from live import tracking
    adapter, fake, conn, order = setup_adapter(monkeypatch)
    order.pop("cumExecQty")
    pending = dict(side="long", fill_receipt=dict(tp_link_id="link", tp_order_id="tp",
        prior_tp_order_id=None, tp_qty=.010, tp_price=110., tp_state="ACK_UNCONFIRMED"))
    assert not adapter._complete_native_postfill(pending, dict(side="long", qty=.026))
    assert tracking.load_open_positions(conn, "demo")[0].qty == .030


@pytest.mark.parametrize("cadence", ["30m", "10m"])
def test_unknown_tp_does_not_skip_stop_protection(monkeypatch, cadence):
    from tests.test_demo import _BASE_TS, _bar, _ex_position
    adapter, fake, conn, order = setup_adapter(monkeypatch)
    fake._position = _ex_position(size=".030")
    order.update(orderStatus="New", cumExecQty="0")
    monkeypatch.setattr(fake, "get_executions", lambda **kw: None)
    calls = []
    monkeypatch.setattr(adapter, "_ensure_stop", lambda *args: calls.append(args))
    for _ in range(2):
        if cadence == "30m":
            adapter._process_bar_inner(_BASE_TS, _bar(100.), False, None)
        else:
            adapter.process_protection_bar(_BASE_TS, _bar(100.))
    assert len(calls) >= 2
    assert all(args[:2] == ("long", .030) for args in calls)
    assert not fake.placed_orders


@pytest.mark.parametrize("cadence", ["30m", "10m"])
def test_flat_terminal_tp_archives_unpriced_remainder(monkeypatch, cadence):
    from live import tracking
    from tests.test_demo import _BASE_TS, _bar
    adapter, fake, conn, order = setup_adapter(monkeypatch)
    order.update(orderStatus="Cancelled", cumExecQty="0")
    fake._executions = []
    fake._position = None
    if cadence == "30m":
        adapter._process_bar_inner(_BASE_TS, _bar(100.), False, None)
    else:
        adapter.process_protection_bar(_BASE_TS, _bar(100.))
    assert tracking.load_open_positions(conn, "demo") == []
    assert adapter._get_meta("tp_intent")["state"] == "FLAT_TERMINAL"
    assert not fake.placed_orders


@pytest.mark.parametrize("status,cum,leaves", [("New", ".010", "0"),
    ("PartiallyFilled", ".004", ".008"), ("PartiallyFilled", ".004", "NaN")])
def test_tp_order_quantity_contradictions_unknown(monkeypatch, status, cum, leaves):
    adapter, fake, conn, order = setup_adapter(monkeypatch)
    order.update(orderStatus=status, cumExecQty=cum, leavesQty=leaves)
    fake._executions = [trade(float(cum))]
    assert not adapter._settle_tp(dict(side="long", qty=.030 - float(cum)))


@pytest.mark.parametrize("action,second,race", [("force", False, 0), ("close", False, 0),
    ("close", True, 0), ("force", False, .004)])
def test_own_reduction_retires_tp_then_rebases_surviving_quota(monkeypatch, action, second, race):
    from live import tracking, demo
    from tests.test_demo import _BASE_TS, _bar, _ex_position, _seed_open_position
    adapter, fake, conn, order = setup_adapter(monkeypatch)
    order.update(orderStatus="New", cumExecQty="0")
    fake._executions = []
    if second:
        _seed_open_position(adapter, conn, entry=101., qty=.030)
        i = adapter._get_meta("tp_intent")
        i.update(qty=.020, allocations=allocation_snapshot(
            [(p.id, p.qty) for p in tracking.load_open_positions(conn, "demo")], .020))
        adapter._set_meta("tp_intent", i)
        order["qty"] = ".020"
    fake._position = _ex_position(size=".060" if second else ".030")
    def exits(pos, *args):
        if pos.entry_price != 100 or pos.liq_breach_flagged:
            return []
        return ([demo.ForceReduce(fraction=.5, price=100., gross=0., first_breach=True)]
                if action == "force" else [demo.ClosePosition(reason="sl", price=100.)])
    monkeypatch.setattr(demo, "evaluate_exits", exits)
    monkeypatch.setattr(adapter, "_ensure_stop", lambda *args: None)
    executions = []
    def cancel(**kw):
        if race and order["orderStatus"] != "Cancelled":
            executions.append(trade(race))
            fake._position["size"] = str(float(fake._position["size"]) - race)
        order.update(orderStatus="Cancelled", cumExecQty=str(race))
        return fake._ok({"orderId": kw["orderId"]})
    monkeypatch.setattr(fake, "cancel_order", cancel)
    original = fake.place_order
    def place(**kw):
        result = original(**kw)
        if kw.get("orderType") == "Market" and kw.get("timeInForce") == "IOC":
            executions.append(dict(execId="reduce-fill", orderId=result["result"]["orderId"],
                orderLinkId=kw["orderLinkId"], execType="Trade", symbol="BTCUSDT", side="Sell", execQty=kw["qty"]))
            size = float(fake._position["size"]) - float(kw["qty"])
            fake._position = _ex_position(size=str(size)) if size > 0 else None
        return result
    monkeypatch.setattr(fake, "place_order", place)
    monkeypatch.setattr(fake, "get_executions", lambda **kw: fake._ok({"list":
        [r for r in executions if r["orderLinkId"] == kw.get("orderLinkId")]}))
    for _ in range(4):
        adapter._process_bar_inner(_BASE_TS, _bar(100.), False, None)
    markets = [r for r in fake.placed_orders if r.get("timeInForce") == "IOC"]
    assert len(markets) == 1
    tps = [r for r in fake.placed_orders if r.get("orderType") == "Limit"]
    if action == "close" and not second:
        assert not tps
        assert tracking.load_open_positions(conn, "demo") == []
        assert adapter._get_meta("tp_intent")["state"] == "FLAT_TERMINAL"
    else:
        assert len(tps) == 1
        assert float(tps[0]["qty"]) == pytest.approx(.010 - race)
        assert float(tps[0]["price"]) == 110.


def test_flat_active_tp_keeps_cancel_fence_until_terminal(monkeypatch):
    from live import tracking
    adapter, fake, conn, order = setup_adapter(monkeypatch)
    order.update(orderStatus="New", cumExecQty="0")
    fake._executions = []
    assert not adapter._settle_tp(None)
    assert adapter._get_meta("tp_intent")["cancel_requested"]
    assert tracking.load_open_positions(conn, "demo")
    assert not fake.placed_orders
    order["orderStatus"] = "Cancelled"
    assert adapter._settle_tp(None)
    assert not tracking.load_open_positions(conn, "demo")
    assert adapter._get_meta("tp_intent")["state"] == "FLAT_TERMINAL"


def test_expired_partial_add_cancels_despite_unknown_tp(monkeypatch):
    from tests.test_demo import TestExitReduceOnly, _BASE_TS, _bar, _ex_position
    adapter, fake, conn, order = setup_adapter(monkeypatch)
    order.update(orderStatus="New", cumExecQty="0")
    parent = TestExitReduceOnly._native_pending(adapter, order_id="accepted-parent")
    pending = adapter._get_meta("pending_order")
    pending.update(tranche_index=1, bar_idx=pending["bar_idx"] - 100)
    adapter._set_meta("pending_order", pending)
    fake._position = _ex_position(size=".040")
    fake._open_orders = [order, parent]
    fake._executions = []
    monkeypatch.setattr(adapter, "_protect_pending_entry", lambda *args: True)
    monkeypatch.setattr(adapter, "_ensure_stop", lambda *args: None)
    monkeypatch.setattr(fake, "get_order_history", lambda **kw: fake._ok({"list": [parent]}), raising=False)
    adapter._process_bar_inner(_BASE_TS, _bar(100.), False, None)
    assert adapter._get_meta("pending_order")["cancel_requested"]
    assert [r["orderId"] for r in fake.calls_to("cancel_order")] == ["accepted-parent"]
    assert not fake.placed_orders


def test_unknown_owned_reduction_keeps_retired_tp_and_repairs_stop(monkeypatch):
    from live import tracking
    from tests.test_demo import _BASE_TS, _bar, _ex_position
    adapter, fake, conn, order = setup_adapter(monkeypatch)
    order.update(orderStatus="New", cumExecQty="0")
    fake._executions = []
    fake._position = _ex_position(size=".030")
    positions = tracking.load_open_positions(conn, "demo")
    ex = dict(side="long", qty=.030)
    assert not adapter._confirmed_market_reduce(positions, "long", .015, "force_reduce", 1, ex)
    order["orderStatus"] = "Cancelled"
    assert adapter._settle_tp(ex)
    monkeypatch.setattr(fake, "get_executions", lambda **kw: None)
    assert not adapter._confirmed_market_reduce(positions, "long", .015, "force_reduce", 1, ex)
    calls = []
    monkeypatch.setattr(adapter, "_ensure_stop", lambda *args: calls.append(args))
    for _ in range(2):
        adapter._process_bar_inner(_BASE_TS, _bar(100.), False, None)
    assert len([r for r in fake.placed_orders if r.get("timeInForce") == "IOC"]) == 1
    assert not [r for r in fake.placed_orders if r.get("orderType") == "Limit"]
    assert adapter._get_meta("pending_reduce") is not None
    assert adapter._get_meta("tp_intent")["state"] == "RETIRED"
    assert len(calls) >= 2


def test_disappeared_exit_restores_tp_without_market(monkeypatch):
    from live import tracking, demo
    from tests.test_demo import _BASE_TS, _bar, _ex_position
    adapter, fake, conn, order = setup_adapter(monkeypatch)
    order.update(orderStatus="New", cumExecQty="0")
    fake._executions = []
    fake._position = _ex_position(size=".030")
    positions = tracking.load_open_positions(conn, "demo")
    assert not adapter._confirmed_market_reduce(positions, "long", .015, "force_reduce", 1,
                                                dict(side="long", qty=.030))
    order["orderStatus"] = "Cancelled"
    monkeypatch.setattr(demo, "evaluate_exits", lambda *args: [])
    monkeypatch.setattr(adapter, "_ensure_stop", lambda *args: None)
    adapter._process_bar_inner(_BASE_TS, _bar(100.), False, None)
    assert len(fake.placed_orders) == 1
    assert fake.placed_orders[0]["orderType"] == "Limit"
    assert float(fake.placed_orders[0]["qty"]) == .010


def test_resumed_lot_exhaustion_removes_zero_row_before_rebase(monkeypatch):
    from live import tracking
    adapter, fake, conn, order = setup_adapter(monkeypatch)
    pos = tracking.load_open_positions(conn, "demo")[0]
    pos.qty = .001
    tracking.save_position(conn, pos)
    i = adapter._get_meta("tp_intent")
    i.update(state="RETIRED", retire_reason="force_reduce", qty=.001,
             allocations=allocation_snapshot([(pos.id, .001)], .001))
    adapter._set_meta("tp_intent", i)
    adapter._set_meta("pending_reduce", dict(link_id="reduce", order_id="reduce-id", side="long",
        qty=.001, original_qty=.001, position_ids=[pos.id], reason="force_reduce", bar_idx=1))
    fake._executions = [trade(.001, orderLinkId="reduce", orderId="reduce-id")]
    assert adapter._resume_reduce()
    assert tracking.load_open_positions(conn, "demo") == []
    assert adapter._settle_tp(None)
    assert adapter._get_meta("tp_intent")["state"] == "FLAT_TERMINAL"
    assert not fake.placed_orders


@pytest.mark.parametrize("cadence", ["30m", "10m"])
def test_flat_clears_handles_and_preserves_cooldown(monkeypatch, cadence):
    from tests.test_demo import _BASE_TS, _bar, _bar_idx_for, _ex_position, TestExitReduceOnly
    adapter, fake, conn, order = setup_adapter(monkeypatch)
    order.update(orderStatus="Cancelled", cumExecQty="0")
    fake._executions = []
    adapter._set_meta("tp_order_id", "tp")
    adapter._set_meta("sl_order_id", "owned-sl")
    adapter._set_meta("last_close_bar", {"long": -1, "short": -2})
    stop = dict(orderId="owned-sl", orderLinkId="owned-stop-link", symbol="BTCUSDT",
                positionIdx=0, side="Sell", reduceOnly=True, orderType="Market",
                triggerPrice="90", orderStatus="Untriggered")
    def history(**kwargs):
        rows = [order, stop]
        if kwargs.get("orderId"):
            rows = [row for row in rows if row["orderId"] == kwargs["orderId"]]
        return fake._ok({"list": rows})
    original_cancel = fake.cancel_order
    def cancel(**kwargs):
        result = original_cancel(**kwargs)
        if kwargs.get("orderId") == "owned-sl":
            stop["orderStatus"] = "Cancelled"  # subsequent exact readback, not ACK alone
        return result
    monkeypatch.setattr(fake, "get_order_history", history, raising=False)
    monkeypatch.setattr(fake, "cancel_order", cancel)
    for _ in range(2):
        if cadence == "30m":
            adapter._process_bar_inner(_BASE_TS, _bar(100.), False, None)
        else:
            adapter.process_protection_bar(_BASE_TS, _bar(100.))
    assert adapter._get_meta("tp_order_id") is None
    assert adapter._get_meta("sl_order_id") is None
    assert adapter._get_meta("last_close_bar") == {"long": _bar_idx_for(_BASE_TS), "short": -2}
    assert [r["orderId"] for r in fake.calls_to("cancel_order")] == ["owned-sl"]
    # A subsequent legitimate native entry must not inherit the retired TP ID.
    parent = TestExitReduceOnly._native_pending(adapter, order_id="accepted-parent")
    parent.update(orderStatus="Filled", cumExecQty=".030", leavesQty="0")
    fake._position = _ex_position(size=".030")
    fake._executions = [trade(.030, orderId="accepted-parent", orderLinkId="entry-lost-ack", side="Buy")]
    monkeypatch.setattr(fake, "get_order_history", lambda **kw: fake._ok({"list": [parent]}), raising=False)
    adapter._process_bar_inner(_BASE_TS, _bar(100.), False, None)
    assert adapter._get_meta("pending_order") is None
    assert adapter._get_meta("tp_order_id") not in (None, "tp")


def test_failed_rebase_cannot_requeue_or_inflate_owned_reduction(monkeypatch):
    from live import tracking
    adapter, fake, conn, order = setup_adapter(monkeypatch)
    pos = tracking.load_open_positions(conn, "demo")[0]
    pos.qty = .014  # An unexplained additional .001 reduction is not ours to undo.
    tracking.save_position(conn, pos)
    i = adapter._get_meta("tp_intent")
    i.update(state="RETIRED", retire_reason="force_reduce", own_reduction=dict(
        link_id="reduce", order_id="r", side="long", qty=.015, original_qty=.030,
        position_ids=[pos.id], reason="force_reduce", bar_idx=1))
    adapter._set_meta("tp_intent", i)
    fake._executions = [trade(.015, orderLinkId="reduce", orderId="r")]
    assert not adapter._settle_tp(dict(side="long", qty=.014))
    assert adapter._get_meta("pending_reduce") is None
    assert not adapter._resume_reduce()
    assert tracking.load_open_positions(conn, "demo")[0].qty == .014
