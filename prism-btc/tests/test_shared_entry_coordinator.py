from __future__ import annotations

import multiprocessing

import pytest

from live import shared_entry_coordinator as coordinator, tracking
from live.demo import DemoAdapter
from live.swing import ExchangeBackend
from live.entry_reservations import EntryReservationStore


def reply(rows):
    return {"retCode": 0, "result": {"list": rows, "nextPageCursor": ""}}


class Session:
    endpoint = 'https://api-demo.bybit.com'

    def __init__(self, uid):
        self.uid = uid
        self.parents = []
        self.executions = []
        self.positions = []
        self.orders = []
        self.submissions = []
        self.accept_then_timeout = False

    def get_api_key_information(self):
        return {"retCode": 0, "result": {"userID": self.uid}}

    def get_wallet_balance(self, **_):
        return reply([{"totalEquity": "10000"}])

    def get_positions(self, **_):
        return reply(self.positions)

    def get_open_orders(self, **kwargs):
        return reply([o for o in self.orders if not kwargs.get("orderId") or o["orderId"] == kwargs["orderId"]])

    def get_tickers(self, **_):
        return reply([{"symbol": "BTCUSDT", "markPrice": "100", "lastPrice": "100"}])

    def get_order_history(self, **kwargs):
        return reply([o for o in self.parents if (not kwargs.get("orderLinkId") or o["orderLinkId"] == kwargs["orderLinkId"])
                      and (not kwargs.get("orderId") or o["orderId"] == kwargs["orderId"])])

    def get_executions(self, **kwargs):
        return reply([e for e in self.executions if not kwargs.get("orderId") or e["orderId"] == kwargs["orderId"]])

    def set_leverage(self, **_):
        return {"retCode": 0}

    def place_order(self, **kwargs):
        self.submissions.append(kwargs)
        parent = {**kwargs, "orderId": "order-" + str(len(self.parents)), "orderStatus": "New",
                  "cumExecQty": "0", "leavesQty": kwargs["qty"], "reduceOnly": False}
        self.parents.append(parent)
        self.orders.append(parent)
        if self.accept_then_timeout:
            raise TimeoutError("ack lost")
        return {"retCode": 0, "result": {"orderId": parent["orderId"]}}


@pytest.fixture
def setup(tmp_path, monkeypatch):
    conn = tracking.get_connection(tmp_path / "root.sqlite")
    tracking.ensure_schema(conn)
    for key, value in {"ENABLED": "true", "COMBINED_HEAT": ".06", "SLIPPAGE": ".01",
                       "MAIN_UID": "101", "SWING_UID": "202"}.items():
        monkeypatch.setenv(coordinator.PREFIX + key, value)
    sessions = {"main": Session("101"), "swing": Session("202")}
    monkeypatch.setattr(coordinator, "_sessions", lambda adapter, lane: sessions)
    monkeypatch.setattr("core.leadership.leadership_multipliers", lambda: (1., 1., "fixture"))
    monkeypatch.setattr("live.demo.time.sleep", lambda _: None)
    main = DemoAdapter.__new__(DemoAdapter)
    main.conn, main.sess, main.mode = conn, sessions["main"], "demo"
    main._last_execution_capture = {}
    swing = ExchangeBackend(conn, sessions["swing"])
    swing.entry_context = {"decision_bar": "bar-1", "leverage": 1., "logical_capital": 10000., "entry_bar_idx": 1}
    yield conn, sessions, main, swing
    conn.close()


def payload(bar=1):
    return {"bar_idx": bar, "tranche_index": 0, "side": "long", "sizing_qty": 10.,
            "limit_price": 100., "sizing_sl_price": 95.}


def test_disabled_does_not_read_uid_or_reserve(setup, monkeypatch):
    conn, sessions, main, _ = setup
    monkeypatch.delenv(coordinator.PREFIX + "ENABLED")
    monkeypatch.setattr(coordinator, "_sessions", lambda *_: pytest.fail("disabled snapshot"))
    assert main._place_limit_postonly("long", 1., 100., stop_price=95., pending_payload=payload())
    assert not conn.execute("SELECT name FROM sqlite_master WHERE name='entry_reservations'").fetchall()


@pytest.mark.parametrize("key,value", [("ENABLED", ""), ("ENABLED", "garbage"), ("COMBINED_HEAT", "nan"),
                                       ("SLIPPAGE", ""), ("MAIN_UID", "202"), ("SWING_UID", "wrong")])
def test_malformed_enabled_settings_block_new_entry(setup, monkeypatch, key, value):
    _, sessions, main, _ = setup
    monkeypatch.setenv(coordinator.PREFIX + key, value)
    assert main._place_limit_postonly("long", 1., 100., stop_price=95., pending_payload=payload()) is None
    assert sessions["main"].submissions == []


def test_uid_mismatch_or_missing_equity_fail_closed(setup):
    _, sessions, main, _ = setup
    sessions["swing"].uid = "101"
    assert main._place_limit_postonly("long", 1., 100., stop_price=95., pending_payload=payload()) is None
    sessions["swing"].uid = "202"
    sessions["main"].get_wallet_balance = lambda **_: reply([])
    assert main._place_limit_postonly("long", 1., 100., stop_price=95., pending_payload=payload()) is None
    assert sessions["main"].submissions == []


def test_unknown_submit_is_durable_and_never_retried(setup):
    conn, sessions, main, _ = setup
    sessions["main"].accept_then_timeout = True
    assert main._place_limit_postonly("long", 1., 100., stop_price=95., pending_payload=payload()) is None
    store = EntryReservationStore(coordinator.database_path(conn))
    assert store.active()[0].state == "SUBMITTED_UNKNOWN"
    assert main._place_limit_postonly("long", 1., 100., stop_price=95., pending_payload=payload()) is None
    assert len(sessions["main"].submissions) == 1


@pytest.mark.parametrize("first", ["main", "swing"])
def test_both_boundary_orders_share_pending_heat_budget(setup, monkeypatch, first):
    _, sessions, main, swing = setup
    monkeypatch.setenv(coordinator.PREFIX + "COMBINED_HEAT", ".006")
    if first == "main":
        assert main._place_limit_postonly("long", 12., 100., stop_price=95., pending_payload={**payload(), "sizing_qty": 12.})
        assert swing.open("long", 10., 95., 100.) is None
    else:
        # 10 * (101 - 95) = 60 fully consumes the explicit combined budget.
        assert swing.open("long", 10., 95., 100.) is None  # ACK is not a fill
        assert main._place_limit_postonly("long", 10., 100., stop_price=95., pending_payload=payload()) is None
    assert len(sessions[first].submissions) == 1
    assert sessions["swing" if first == "main" else "main"].submissions == []


def test_swing_ioc_limit_has_attached_sl_not_market_slippage(setup):
    _, sessions, _, swing = setup
    assert swing.open("short", 1., 105., 100.) is None
    order = sessions["swing"].submissions[0]
    assert order["orderType"] == "Limit" and order["timeInForce"] == "IOC"
    assert float(order["price"]) == 99. and order["stopLoss"] == "105.0"
    assert "slippageTolerance" not in order


def test_terminal_zero_releases_reservation_not_intent_tombstone(setup):
    conn, sessions, main, _ = setup
    assert main._place_limit_postonly("long", 1., 100., stop_price=95., pending_payload=payload())
    order = sessions["main"].parents[0]
    order.update(orderStatus="Cancelled", leavesQty="0")
    sessions["main"].orders = []
    store = EntryReservationStore(coordinator.database_path(conn))
    coordinator.reconcile_reservations(store, sessions)
    assert store.active() == ()
    tracking.set_meta(conn, "pending_order", None, "demo")
    assert main._place_limit_postonly("long", 1., 100., stop_price=95., pending_payload=payload()) is None
    assert len(sessions["main"].submissions) == 1


def test_manual_order_blocks_both_lanes(setup):
    _, sessions, main, swing = setup
    sessions["swing"].orders = [{"orderId": "manual", "reduceOnly": False}]
    assert main._place_limit_postonly("long", 1., 100., stop_price=95., pending_payload=payload()) is None
    assert swing.open("long", 1., 95., 100.) is None
    assert not sessions["main"].submissions and not sessions["swing"].submissions


def test_wrong_uid_does_not_mutate_existing_reservation(setup):
    conn, sessions, main, swing = setup
    assert main._place_limit_postonly("long", 1., 100., stop_price=95., pending_payload=payload())
    store = EntryReservationStore(coordinator.database_path(conn))
    before = store.active()
    sessions["main"].parents[0].update(orderStatus="Cancelled", leavesQty="0")
    sessions["main"].orders = []
    sessions["swing"].uid = "101"
    assert swing.open("long", 1., 95., 100.) is None
    assert store.active() == before


def test_legacy_unknown_pending_blocks_other_lane(setup):
    conn, sessions, _, swing = setup
    tracking.set_meta(conn, "pending_order", {"link_id": "legacy-unknown", "order_id": None}, "demo")
    assert swing.open("long", 1., 95., 100.) is None
    assert not sessions["swing"].submissions


@pytest.mark.parametrize("field,value", [("qty", "1000"), ("leavesQty", "1000"), ("side", "Sell"),
                                        ("orderId", "other"), ("positionIdx", 1)])
def test_known_link_does_not_authorize_conflicting_open_order(setup, field, value):
    conn, sessions, main, swing = setup
    assert main._place_limit_postonly("long", 1., 100., stop_price=95., pending_payload=payload())
    sessions["main"].orders = [{**sessions["main"].parents[0], field: value}]
    store = EntryReservationStore(coordinator.database_path(conn))
    before = store.active()
    assert swing.open("long", 1., 95., 100.) is None
    assert store.active() == before
    assert not sessions["swing"].submissions


def test_opposite_sides_do_not_net_snapshot_gross(setup, monkeypatch):
    _, sessions, main, swing = setup
    monkeypatch.setenv(coordinator.PREFIX + "SLIPPAGE", ".0001")
    assert main._place_limit_postonly("long", 410., 100., stop_price=99.9, pending_payload=payload())
    assert swing.open("short", 410., 100.1, 100.) is None
    assert not sessions["swing"].submissions


def test_partial_fill_and_remaining_reservation_are_counted_once(setup, monkeypatch):
    conn, sessions, main, swing = setup
    monkeypatch.setenv(coordinator.PREFIX + "COMBINED_HEAT", ".0065")
    assert main._place_limit_postonly("long", 10., 100., stop_price=95., pending_payload=payload())
    parent = sessions["main"].parents[0]
    parent.update(orderStatus="PartiallyFilled", cumExecQty="5", leavesQty="5")
    sessions["main"].executions = [{"orderId": parent["orderId"], "orderLinkId": parent["orderLinkId"],
                                   "execId": "fill-1", "symbol": "BTCUSDT", "side": "Buy", "execType": "Trade",
                                   "execQty": "5", "execPrice": "100"}]
    sessions["main"].positions = [{"symbol": "BTCUSDT", "positionIdx": 0, "side": "Buy", "size": "5", "avgPrice": "100"}]
    sessions["main"].orders.append({"orderId": "stop", "symbol": "BTCUSDT", "positionIdx": 0,
                                    "reduceOnly": True, "side": "Sell", "orderType": "Market", "qty": "5",
                                    "orderStatus": "Untriggered", "stopOrderType": "StopLoss",
                                    "triggerBy": "LastPrice", "triggerDirection": 2, "triggerPrice": "95"})
    position = tracking.PositionRow(side="long", entry_price=100., qty=5., leverage=1., sl_price=95.,
                                    tp1_price=0., tp2_price=0., tp3_price=0., liq_price=0., entry_time="t",
                                    tranche_index=0, entry_bar_idx=1, initial_risk=25., mode="demo")
    tracking.save_position(conn, position)
    pending = tracking.get_meta(conn, "pending_order", "demo")
    pending["fill_receipt"] = {"order_id": parent["orderId"], "position_id": position.id}
    tracking.set_meta(conn, "pending_order", pending, "demo")
    assert swing.open("long", 2., 95., 100.) is None  # ACK remains unconfirmed.
    assert len(sessions["swing"].submissions) == 1  # 25 filled +25 reserved +12 proposed <=65.
    main_reservation = next(r for r in EntryReservationStore(coordinator.database_path(conn)).active() if r.lane == "main")
    assert main_reservation.confirmed_filled_qty == 5. and main_reservation.remaining_qty == 5.


def test_existing_drawdown_risk_constants_are_preserved(setup):
    conn, sessions, main, _ = setup
    sessions["main"].get_wallet_balance = lambda **_: reply([{"totalEquity": "9500"}])
    tracking.record_equity(conn, 10000., "demo")
    assert main._place_limit_postonly("long", 80., 100., stop_price=95.,
                                      pending_payload={**payload(), "sizing_qty": 80.})


@pytest.mark.parametrize("side,stop", [("long", 95.), ("short", 105.)])
def test_normally_sized_swing_tightens_ioc_price_instead_of_increasing_risk(setup, side, stop):
    from core.swing import compute_swing_sizing
    _, sessions, _, swing = setup
    sizing = compute_swing_sizing(10000., 100., stop)
    assert not sizing.rejected and sizing.qty == 30.
    assert swing.open(side, sizing.qty, stop, 100.) is None
    assert len(sessions["swing"].submissions) == 1
    assert float(sessions["swing"].submissions[0]["price"]) == 100.


def _filled_main(setup):
    conn, sessions, main, _ = setup
    pending = {**payload(), "sizing_qty": 1.}
    assert main._place_limit_postonly("long", 1., 100., stop_price=95., pending_payload=pending)
    parent = sessions["main"].parents[0]
    parent.update(orderStatus="Filled", cumExecQty="1", leavesQty="0")
    sessions["main"].orders = []
    sessions["main"].executions = [{"orderId": parent["orderId"], "orderLinkId": parent["orderLinkId"],
                                   "execId": "entry-fill", "symbol": "BTCUSDT", "side": "Buy", "execType": "Trade",
                                   "execQty": "1", "execPrice": "100"}]
    position = tracking.PositionRow(side="long", entry_price=100., qty=1., leverage=1., sl_price=95.,
                                    tp1_price=105., tp2_price=110., tp3_price=115., liq_price=0., entry_time="t",
                                    tranche_index=0, entry_bar_idx=1, initial_risk=5., mode="demo")
    return pending, position


def test_main_fill_receipt_and_reservation_are_one_commit_even_after_close_restart(setup):
    conn, sessions, main, _ = setup
    pending, position = _filled_main(setup)
    # Receipt writer already owns its verified evidence. A second optional API
    # outage must not split that receipt from its pending-risk finalization.
    sessions["main"].get_api_key_information = lambda: pytest.fail("no second UID query in receipt finalization")
    sessions["main"].get_order_history = lambda **_: pytest.fail("no second history query in receipt finalization")
    main._save_native_entry_position(pending, position, 1.)
    store = EntryReservationStore(coordinator.database_path(conn))
    assert store.active() == ()
    tracking.remove_position(conn, position.id)  # Later close does not resurrect pending risk.
    assert EntryReservationStore(store.path).active() == ()


def test_crash_during_atomic_receipt_rolls_back_reservation_and_position(setup, monkeypatch):
    conn, _, main, _ = setup
    pending, position = _filled_main(setup)
    original = coordinator.commit_entry_receipt
    def crash(update, connection):
        original(update, connection)
        raise RuntimeError("receipt crash")
    monkeypatch.setattr(coordinator, "commit_entry_receipt", crash)
    with pytest.raises(RuntimeError, match="receipt crash"):
        main._save_native_entry_position(pending, position, 1.)
    assert not tracking.load_open_positions(conn, "demo")
    assert EntryReservationStore(coordinator.database_path(conn)).active()[0].state == "SUBMITTED_UNKNOWN"


def test_same_lane_reverse_direct_call_is_rejected(setup):
    conn, sessions, main, _ = setup
    pending, position = _filled_main(setup)
    main._save_native_entry_position(pending, position, 1.)
    tracking.set_meta(conn, "pending_order", None, "demo")
    sessions["main"].positions = [{"symbol": "BTCUSDT", "positionIdx": 0, "side": "Buy", "size": "1", "avgPrice": "100"}]
    sessions["main"].orders = [{"orderId": "stop", "symbol": "BTCUSDT", "positionIdx": 0,
                                "reduceOnly": True, "side": "Sell", "orderType": "Market", "qty": "1",
                                "orderStatus": "Untriggered", "stopOrderType": "StopLoss",
                                "triggerBy": "LastPrice", "triggerDirection": 2, "triggerPrice": "95"}]
    assert main._place_limit_postonly("short", 1., 100., stop_price=105., pending_payload=payload(2)) is None
    assert len(sessions["main"].submissions) == 1


def test_ack_or_wrong_execution_identity_never_reaches_receipt_finalization(setup):
    conn, sessions, main, _ = setup
    pending = {**payload(), "sizing_qty": 1.}
    assert main._place_limit_postonly("long", 1., 100., stop_price=95., pending_payload=pending)
    assert not main._entry_fill_confirmed(pending, 1.)  # New parent/ACK only.
    parent = sessions["main"].parents[0]
    parent.update(orderStatus="Filled", cumExecQty="1", leavesQty="0")
    sessions["main"].executions = [{"orderId": parent["orderId"], "orderLinkId": "wrong-link",
                                   "execId": "forged", "symbol": "BTCUSDT", "side": "Buy", "execType": "Trade",
                                   "execQty": "1", "execPrice": "100"}]
    assert not main._entry_fill_confirmed(pending, 1.)
    assert EntryReservationStore(coordinator.database_path(conn)).active()[0].state == "SUBMITTED_UNKNOWN"


@pytest.mark.parametrize("outcome", ["risk_denied", "retirement_denied", "accepted_unknown"])
def test_real_outer_entry_does_not_persist_unsent_payload(monkeypatch, outcome):
    from .test_demo import FakeExchange, _patch_session, _conn, _make_adapter, _force_signal, _BASE_TS, _bar
    from live import demo, order_retirement
    monkeypatch.delenv(coordinator.PREFIX + "ENABLED", raising=False)
    fake = FakeExchange()
    _patch_session(monkeypatch, fake)
    conn = _conn()
    adapter = _make_adapter(conn, fake)
    _force_signal(monkeypatch)
    seen = []
    if outcome == "risk_denied":
        monkeypatch.setattr(demo, "authorize", lambda *_: seen.append("risk") or (False, None))
    elif outcome == "retirement_denied":
        monkeypatch.setattr(order_retirement, "reconcile_stop_retirements", lambda *_: seen.append("retirement") or False)
    else:
        original = fake.place_order
        def accepted(**params):
            original(**params)
            seen.append("accepted")
            raise TimeoutError("accepted but ACK lost")
        fake.place_order = accepted
    adapter._process_bar_inner(_BASE_TS, _bar(close=100.), True, 12345)
    assert seen  # Actual entry boundary reached, not an upstream no-signal pass.
    pending = tracking.get_meta(conn, "pending_order", "demo")
    if outcome == "accepted_unknown":
        assert pending["link_id"] and pending["order_id"] is None
        assert len(fake.placed_orders) == 1
    else:
        assert pending is None
        assert not fake.placed_orders


@pytest.mark.parametrize("extra,allowed", [(1e-16, True), (1e-6, False)])
def test_receipt_float_tolerance_does_not_allow_material_overfill(setup, extra, allowed):
    conn, _, main, _ = setup
    pending, position = _filled_main(setup)
    # 1e-16 is below ulp at1; use next representable quantity for realroundoff.
    import math
    position.qty = math.nextafter(1., 2.) if allowed else 1. + extra
    if allowed:
        main._save_native_entry_position(pending, position, position.qty)
        assert EntryReservationStore(coordinator.database_path(conn)).active() == ()
    else:
        with pytest.raises(ValueError, match="quantity_conflict"):
            main._save_native_entry_position(pending, position, position.qty)
        assert not tracking.load_open_positions(conn, "demo")


def _contender(path, queue):
    conn = tracking.get_connection(path)
    try:
        with coordinator.mutation_lock(conn):
            queue.put("unexpected")
    except coordinator.LockBusy:
        queue.put("busy")
    finally:
        conn.close()


@pytest.mark.parametrize("enabled", [True, False])
def test_canonical_db_reentrant_process_lock(setup, monkeypatch, enabled):
    conn, _, _, _ = setup
    if not enabled:
        monkeypatch.delenv(coordinator.PREFIX + "ENABLED")
    ctx = multiprocessing.get_context("spawn")
    queue = ctx.Queue()
    with coordinator.mutation_lock(conn):
        with coordinator.mutation_lock(conn):
            process = ctx.Process(target=_contender, args=(str(coordinator.database_path(conn)), queue))
            process.start()
            assert queue.get(timeout=5) == "busy"
            process.join(5)
            assert process.exitcode == 0
    with coordinator.mutation_lock(conn):
        pass


def _boundary_contender(path, lane, queue):
    conn = tracking.get_connection(path)
    session = Session("101" if lane == "main" else "202")
    if lane == "main":
        adapter = DemoAdapter.__new__(DemoAdapter)
        adapter.conn, adapter.sess, adapter.mode = conn, session, "demo"
        adapter._last_execution_capture = {}
        outcome = adapter._place_limit_postonly("long", 1., 100., stop_price=95., pending_payload=payload())
    else:
        outcome = ExchangeBackend(conn, session).open("long", 1., 95., 100.)
    queue.put((outcome, len(session.submissions)))
    conn.close()


@pytest.mark.parametrize("lane", ["main", "swing"])
def test_actual_boundary_cannot_mutate_while_outer_demo_has_stale_state(setup, monkeypatch, lane):
    import pandas as pd
    conn, _, main, _ = setup
    monkeypatch.delenv(coordinator.PREFIX + "ENABLED")
    ctx = multiprocessing.get_context("spawn")
    queue = ctx.Queue()
    marker = {"link_id": "outer-owned-intent"}
    def paused_snapshot(_):
        tracking.set_meta(conn, "pending_order", marker, "demo")
        process = ctx.Process(target=_boundary_contender, args=(str(coordinator.database_path(conn)), lane, queue))
        process.start()
        assert queue.get(timeout=10) == (None, 0)
        process.join(10)
        assert process.exitcode == 0
        assert tracking.get_meta(conn, "pending_order", "demo") == marker
        raise RuntimeError("stop after serialization assertion")
    main._sync_state = paused_snapshot
    with pytest.raises(RuntimeError, match="serialization assertion"):
        main._process_bar_inner(pd.Timestamp("2026-09-07", tz="UTC"),
                                pd.Series({"close": 100, "high": 101, "low": 99}), False, None)
