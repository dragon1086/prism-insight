"""Immediate reduction uses exact children, not crossed synthetic exit orders."""
import json
import sqlite3
from decimal import Decimal

import pytest

from live.scenario_broker import BrokerNotReady, ScenarioDemoBroker
from tests import test_scenario_execution as execution_tests
from tests.test_scenario_execution import persist


@pytest.fixture(name="live")
def execution_fixture(tmp_path):
    return execution_tests.live.__wrapped__(tmp_path)


def held(live):
    broker, exchange = live
    plan = persist(broker, take_profits=[dict(id="t1", price=105, fraction=.5),
                                       dict(id="t2", price=110, fraction=.5)])
    broker.execute(plan, "a1")
    exchange.fill(broker.children()[0]["link_id"], .1)
    broker.reconcile()
    return broker, exchange


def reduction(broker):
    payload = dict(scenario_id="s1", action_id="r1", action="REDUCE",
                   reduce_fraction=.5, expires_at=1800000300,
                   reduce_plan=dict(quantity_before=.1, requested_quantity=.05,
                                    target_remainder=.05),
                   entries=[], take_profits=[], partial_stops=[], cancel_entry_ids=[])
    broker.conn.execute("INSERT INTO llm_scenario_intents VALUES(?,?,?,'PENDING',NULL)",
                        ("r1", "s1", json.dumps(payload)))
    broker.prepare_reduce(payload, "r1")
    broker.conn.commit()
    return payload


def test_reduce_is_durable_before_side_effects_and_keeps_native(live):
    broker, exchange = held(live)
    before = list(exchange.writes)
    payload = reduction(broker)
    assert exchange.writes == before
    original = exchange.place_order

    def fill_market(**request):
        result = original(**request)
        if request.get("orderType") == "Market" and not request.get("triggerPrice"):
            exchange.fill(request["orderLinkId"], float(request["qty"]))
        return result

    exchange.place_order = fill_market
    broker.execute(payload, "r1")
    assert float(exchange.size) == .05
    markets = [r for kind, r in exchange.writes if kind == "place" and r.get("timeInForce") == "IOC"]
    assert len(markets) == 1 and markets[0]["reduceOnly"] is True
    assert not any(kind == "cancel" and r.get("orderId") == "native-id" for kind, r in exchange.writes)
    broker.execute(payload, "r1")
    broker.reconcile()
    assert len([r for kind, r in exchange.writes if kind == "place" and r.get("timeInForce") == "IOC"]) == 1
    targets = [c for c in broker.children() if c["kind"] == "tp" and c["status"] == "LIVE"]
    assert sum(float(c["request"]["qty"]) for c in targets) == .05


def test_reduce_without_atomic_preparation_is_rejected(live):
    broker, exchange = held(live)
    payload = persist(broker, action="REDUCE", action_id="r1", entries=[], reduce_fraction=.5)
    before = list(exchange.writes)
    with pytest.raises(BrokerNotReady, match="reduce_preparation_missing"):
        broker.execute(payload, "r1")
    assert exchange.writes == before


def market_posts(exchange):
    return [r for kind, r in exchange.writes if kind == "place" and r.get("timeInForce") == "IOC"]


def auto_fill(exchange, *, amount=None, lose_ack=False):
    original = exchange.place_order

    def place(**request):
        result = original(**request)
        if request.get("timeInForce") == "IOC":
            quantity = float(request["qty"]) if amount is None else amount
            exchange.fill(request["orderLinkId"], quantity)
            if quantity < float(request["qty"]):
                exchange.orders[request["orderLinkId"]]["orderStatus"] = "PartiallyFilledCanceled"
            if lose_ack:
                raise TimeoutError()
        return result

    exchange.place_order = place


def test_partial_ioc_fill_never_tops_up_and_restores_actual_remainder(live):
    broker, exchange = held(live)
    payload = reduction(broker)
    auto_fill(exchange, amount=.02)
    broker.execute(payload, "r1")
    assert float(exchange.size) == .08
    broker.reconcile()
    broker.execute(payload, "r1")
    assert len(market_posts(exchange)) == 1
    assert sum(float(c["request"]["qty"]) for c in broker.children()
               if c["kind"] == "tp" and c["status"] == "LIVE") == .08


def test_ack_loss_restart_queries_same_child_without_duplicate(live):
    broker, exchange = held(live)
    payload = reduction(broker)
    auto_fill(exchange, lose_ack=True)
    with pytest.raises(BrokerNotReady, match="submission_unknown"):
        broker.execute(payload, "r1")
    restarted = ScenarioDemoBroker(broker.conn, session=exchange, expected_main_uid="123",
                                  clock=broker.clock, execution_enabled=True)
    restarted.reconcile()
    assert len(market_posts(exchange)) == 1
    assert float(exchange.size) == .05
    assert sum(float(c["request"]["qty"]) for c in restarted.children()
               if c["kind"] == "tp" and c["status"] == "LIVE") == .05


def test_tp_cancel_race_reduces_only_to_original_remainder(live):
    broker, exchange = held(live)
    payload = reduction(broker)
    original = exchange.cancel_order
    raced = False

    def cancel(**request):
        nonlocal raced
        if not raced:
            raced = True
            exchange.fill(request["orderLinkId"], .02)
        return original(**request)

    exchange.cancel_order = cancel
    auto_fill(exchange)
    broker.execute(payload, "r1")
    assert float(market_posts(exchange)[0]["qty"]) == .03
    assert float(exchange.size) == .05
    quotas = sorted(float(c["request"]["qty"]) for c in broker.children()
                    if c["kind"] == "tp" and c["status"] == "LIVE")
    assert quotas == [.018, .031]


def test_filled_tp_not_resurrected_when_race_already_meets_target(live):
    broker, exchange = held(live)
    payload = reduction(broker)
    original = exchange.cancel_order
    raced = False

    def cancel(**request):
        nonlocal raced
        if not raced:
            raced = True
            exchange.fill(request["orderLinkId"], .05)
        return original(**request)

    exchange.cancel_order = cancel
    broker.execute(payload, "r1")
    broker.reconcile()
    assert market_posts(exchange) == []
    targets = [c for c in broker.children() if c["kind"] == "tp" and c["status"] == "LIVE"]
    assert len(targets) == 1 and float(targets[0]["request"]["price"]) == 110
    assert float(targets[0]["request"]["qty"]) == .05


@pytest.mark.parametrize("reason", ["expiry", "failed"])
def test_expiry_or_failure_skips_market_and_restores_protection(live, reason):
    broker, exchange = held(live)
    payload = reduction(broker)
    if reason == "expiry":
        broker.clock = lambda: 1800000301
    else:
        state = json.loads(broker.conn.execute("SELECT body FROM llm_scenario_state").fetchone()[0])
        state["active"]["entry_contract_observation"] = {"status": "FAILED"}
        broker.conn.execute("UPDATE llm_scenario_state SET body=?", (json.dumps(state),))
        broker.conn.commit()
    broker.execute(payload, "r1")
    assert market_posts(exchange) == []
    assert float(exchange.size) == .1
    assert sum(float(c["request"]["qty"]) for c in broker.children()
               if c["kind"] == "tp" and c["status"] == "LIVE") == .1
    if reason == "failed":
        assert not [c for c in broker.children() if c["intent_id"] == "r1"]


def test_native_sl_race_flat_never_rearms_or_reduces(live):
    broker, exchange = held(live)
    payload = reduction(broker)
    original = exchange.cancel_order
    raced = False

    def cancel(**request):
        nonlocal raced
        if not raced:
            raced = True
            exchange.fill("native", .1)
            native = next(c for c in broker.children() if c["kind"] == "native_sl")
            broker._query_child(native)
        return original(**request)

    exchange.cancel_order = cancel
    broker.execute(payload, "r1")
    assert float(exchange.size) == 0
    assert market_posts(exchange) == []
    assert not [c for c in broker.children() if c["kind"] == "tp" and c["status"] == "LIVE"]


def test_corrupt_preparation_fails_before_cancelling(live):
    broker, exchange = held(live)
    payload = reduction(broker)
    broker.conn.execute("UPDATE llm_scenario_reductions SET sha256='corrupt'")
    broker.conn.commit()
    before = list(exchange.writes)
    with pytest.raises(BrokerNotReady, match="reduce_preparation_corrupt"):
        broker.execute(payload, "r1")
    assert exchange.writes == before


def test_short_reduction_buys_only_and_preserves_stop(live):
    broker, exchange = live
    state = json.loads(broker.conn.execute("SELECT body FROM llm_scenario_state").fetchone()[0])
    state["active"].update(side="SHORT", hard_stop=102)
    broker.conn.execute("UPDATE llm_scenario_state SET body=?", (json.dumps(state),))
    broker.conn.commit()
    exchange.stop = "102"
    original_positions, original_fill = exchange.get_positions, exchange.fill

    def positions(**kwargs):
        response = original_positions(**kwargs)
        response["result"]["list"][0]["side"] = "Sell" if float(exchange.size) else ""
        return response

    def fill(link, amount):
        order = exchange.orders[link]
        side = order["side"]
        order["side"] = "Buy" if side == "Sell" else "Sell"
        original_fill(link, amount)
        order["side"] = side
        exchange.fills[-1]["side"] = side
        if "native" in exchange.orders:
            exchange.orders["native"]["side"] = "Buy"

    exchange.get_positions, exchange.fill = positions, fill
    plan = persist(broker, side="SHORT", hard_stop=102,
                   take_profits=[dict(id="t1", price=95, fraction=1)])
    broker.execute(plan, "a1")
    exchange.fill(broker.children()[0]["link_id"], .1)
    broker.reconcile()
    payload = reduction(broker)
    auto_fill(exchange)
    broker.execute(payload, "r1")
    assert market_posts(exchange)[0]["side"] == "Buy"
    assert float(exchange.size) == .05 and exchange.stop == "102"
    assert sum(float(c["request"]["qty"]) for c in broker.children()
               if c["kind"] == "tp" and c["status"] == "LIVE") == .05


def test_unknown_market_absence_never_reposts(live):
    broker, exchange = held(live)
    payload = reduction(broker)
    original = exchange.place_order
    attempts = []

    def absent(**request):
        if request.get("timeInForce") == "IOC":
            attempts.append(request)
            raise TimeoutError()
        return original(**request)

    exchange.place_order = absent
    with pytest.raises(BrokerNotReady, match="submission_unknown"):
        broker.execute(payload, "r1")
    for _ in range(2):
        with pytest.raises(BrokerNotReady, match="exact_order_not_found"):
            broker.execute(payload, "r1")
    assert len(attempts) == 1
    assert float(exchange.size) == .1 and exchange.stop == "98"


@pytest.mark.parametrize("change", ["pending", "unknown"])
def test_preparation_refuses_unconfirmed_children(live, change):
    broker, exchange = held(live)
    entry = next(c for c in broker.children() if c["kind"] == "entry")
    broker.conn.execute("UPDATE llm_scenario_children SET status=? WHERE link_id=?",
                        ("LIVE" if change == "pending" else "UNKNOWN", entry["link_id"]))
    broker.conn.commit()
    before = list(exchange.writes)
    with pytest.raises(BrokerNotReady, match="reduce_unreconciled_children"):
        reduction(broker)
    broker.conn.rollback()
    assert exchange.writes == before


def test_prepare_rolls_back_with_parent_intent(live):
    broker, exchange = held(live)
    payload = dict(scenario_id="s1", action_id="r1", action="REDUCE", reduce_fraction=.5,
                   reduce_plan=dict(quantity_before=.1, requested_quantity=.05, target_remainder=.05))
    broker.conn.execute("INSERT INTO llm_scenario_intents VALUES(?,?,?,'PENDING',NULL)",
                        ("r1", "s1", json.dumps(payload)))
    broker.prepare_reduce(payload, "r1")
    broker.conn.rollback()
    assert not broker.conn.execute("SELECT 1 FROM llm_scenario_intents WHERE id='r1'").fetchone()
    assert not broker.conn.execute("SELECT 1 FROM llm_scenario_reductions WHERE intent_id='r1'").fetchone()


def test_restore_ack_loss_does_not_repeat_target_or_market(live):
    broker, exchange = held(live)
    payload = reduction(broker)
    auto_fill(exchange)
    original = exchange.place_order
    lost = False

    def place(**request):
        nonlocal lost
        result = original(**request)
        if request.get("orderType") == "Limit" and not lost:
            lost = True
            raise TimeoutError()
        return result

    exchange.place_order = place
    with pytest.raises(BrokerNotReady, match="submission_unknown"):
        broker.execute(payload, "r1")
    broker.reconcile()
    assert len(market_posts(exchange)) == 1
    targets = [c for c in broker.children() if c["intent_id"] == "r1" and c["kind"] == "tp"]
    assert len(targets) == 2
    assert sum(float(c["request"]["qty"]) for c in targets) == .05


def test_reduce_notice_is_partial_and_settlement_remains_open(live):
    broker, exchange = held(live)
    payload = reduction(broker)
    auto_fill(exchange)
    broker.execute(payload, "r1")
    result = broker.reconcile()
    events = broker._pending_notices()
    reductions = [n for n in events if n.get("reason_code") == "IMMEDIATE_REDUCTION"]
    assert len(reductions) == 1 and reductions[0]["kind"] == "PARTIAL"
    assert not any(n["kind"] == "CLOSED" for n in events)
    assert not result.get("settlement")


def test_live_reduce_blocks_full_exit_until_exact_terminal(live):
    broker, exchange = held(live)
    payload = reduction(broker)
    with pytest.raises(BrokerNotReady, match="reduce_result_pending"):
        broker.execute(payload, "r1")
    exit_payload = persist(broker, action="EXIT", action_id="exit", entries=[])
    before = len(market_posts(exchange))
    with pytest.raises(BrokerNotReady, match="reduce_result_pending"):
        broker.execute(exit_payload, "exit")
    assert len(market_posts(exchange)) == before


@pytest.mark.parametrize("field,value", [("reduceOnly", False), ("timeInForce", "GTC"),
                                         ("closeOnTrigger", False), ("positionIdx", 1)])
def test_reduce_exact_query_rejects_changed_execution_terms(live, field, value):
    broker, exchange = held(live)
    payload = reduction(broker)
    with pytest.raises(BrokerNotReady, match="reduce_result_pending"):
        broker.execute(payload, "r1")
    child = next(c for c in broker.children() if c["kind"] == "reduce")
    exchange.orders[child["link_id"]][field] = value
    with pytest.raises(BrokerNotReady, match="reduce_order_terms_changed"):
        broker._query_child(child)


@pytest.mark.parametrize("plan", [dict(quantity_before=.1, requested_quantity=.049, target_remainder=.051),
                                  dict(quantity_before=.001, requested_quantity=.0005, target_remainder=.0005)])
def test_bad_reduction_lots_fail_before_any_cancellation(live, plan):
    broker, exchange = held(live)
    payload = dict(scenario_id="s1", action_id="r1", action="REDUCE", reduce_fraction=.5,
                   expires_at=1800000300, reduce_plan=plan)
    broker.conn.execute("INSERT INTO llm_scenario_intents VALUES(?,?,?,'PENDING',NULL)",
                        ("r1", "s1", json.dumps(payload)))
    if plan["quantity_before"] == .001:
        # No source quotas for this synthetic instrument-boundary case.
        broker.conn.execute("UPDATE llm_scenario_children SET status='TERMINAL' WHERE kind='tp'")
    broker.prepare_reduce(payload, "r1")
    broker.conn.commit()
    before = list(exchange.writes)
    with pytest.raises(BrokerNotReady, match="reduce_quantity_invalid"):
        broker.execute(payload, "r1")
    assert exchange.writes == before


@pytest.mark.parametrize("side,stop,tp,reduce_price", [("LONG", 98, 110, 101), ("SHORT", 102, 90, 99)])
def test_real_offline_exchange_reduction_then_target_settles_fees(tmp_path, side, stop, tp, reduce_price):
    from backtest.scenario_exchange import OfflineBybitSession
    from live.scenario_control import _write

    exchange = OfflineBybitSession(start_ms=1_800_000_005_000, slippage_bps=0, spread=0)
    conn = sqlite3.connect(tmp_path / "real-offline.db")
    broker = ScenarioDemoBroker(conn, session=exchange, expected_main_uid=exchange.uid,
                               clock=exchange.now, execution_enabled=True)
    _write(conn, dict(version=1, state="active", main_uid=exchange.uid))
    conn.execute("CREATE TABLE llm_scenario_intents(id TEXT PRIMARY KEY,scenario_id TEXT,payload TEXT,status TEXT,evidence TEXT)")
    conn.execute("CREATE TABLE llm_scenario_state(id INTEGER PRIMARY KEY,body TEXT)")
    active = dict(scenario_id="s1", initial_equity=10000, side=side, hard_stop=stop,
                  created_at=exchange.now(), expires_at=exchange.now()+300)
    conn.execute("INSERT INTO llm_scenario_state VALUES(1,?)", (json.dumps(dict(active=active)),))
    conn.commit()
    plan = persist(broker, side=side, hard_stop=stop, chase=dict(max_bps=0, max_reprices=0),
                   take_profits=[dict(id="t1", price=tp, fraction=1)])
    broker.execute(plan, "a1")
    exchange.advance(exchange.ts_ms + 100, 100, 100, 100)
    broker.reconcile()
    payload = reduction(broker)
    # Cancellation and IOC are asynchronous in the actual replay adapter.
    with pytest.raises(BrokerNotReady, match="cancel_not_confirmed"):
        broker.execute(payload, "r1")
    exchange.elapse(exchange.ts_ms + 100)
    broker.reconcile()
    exchange.advance(exchange.ts_ms + 100, reduce_price, reduce_price, 100)
    broker.reconcile()
    assert abs(exchange.position) == Decimal('.05')
    assert exchange.stop == stop
    assert len([c for c in broker.children() if c["kind"] == "reduce"]) == 1
    exchange.advance(exchange.ts_ms + 100, tp, tp, 100)
    result = broker.reconcile()
    assert exchange.position == 0
    assert result["settlement"]["flat_confirmed"] is True
    assert result["settlement"]["fees"] > 0
    assert result["settlement"]["net_pnl"] == pytest.approx(float(exchange.cash - exchange.initial_equity))


def test_market_max_rejects_before_cancel_instead_of_auto_split(live):
    broker, exchange = held(live)
    payload = reduction(broker)
    original = exchange.get_instruments_info

    def instrument(**kwargs):
        result = original(**kwargs)
        result["result"]["list"][0]["lotSizeFilter"]["maxMktOrderQty"] = "0.04"
        return result

    exchange.get_instruments_info = instrument
    before = list(exchange.writes)
    with pytest.raises(BrokerNotReady, match="reduce_quantity_invalid"):
        broker.execute(payload, "r1")
    assert exchange.writes == before


def test_external_order_refuses_before_any_cancel(live):
    broker, exchange = held(live)
    payload = reduction(broker)
    exchange.orders["external"] = dict(orderId="external-id", orderLinkId="external", symbol="BTCUSDT",
        positionIdx=0, side="Buy", orderType="Limit", price="90", qty="0.001", reduceOnly=False,
        orderStatus="New", cumExecQty="0", leavesQty="0.001")
    before = list(exchange.writes)
    with pytest.raises(BrokerNotReady, match="reduce_external_exposure"):
        broker.execute(payload, "r1")
    assert exchange.writes == before


def test_tp_and_partial_stop_groups_scale_independently(live):
    broker, exchange = live
    plan = persist(broker, take_profits=[dict(id="tp", price=105, fraction=1)],
                   partial_stops=[dict(id="ps", price=99, fraction=.5)])
    broker.execute(plan, "a1")
    exchange.fill(broker.children()[0]["link_id"], .1)
    broker.reconcile()
    payload = reduction(broker)
    auto_fill(exchange)
    broker.execute(payload, "r1")
    broker.reconcile()
    for kind, qty in [("tp", .05), ("partial_sl", .025)]:
        targets = [c for c in broker.children() if c["kind"] == kind and c["status"] == "LIVE"]
        assert len(targets) == 1 and float(targets[0]["request"]["qty"]) == qty
    assert exchange.stop == "98"


def test_restore_fill_caps_later_other_group_to_actual_remainder(live):
    broker, exchange = live
    plan = persist(broker, take_profits=[dict(id="tp", price=105, fraction=.5)],
                   partial_stops=[dict(id="ps", price=99, fraction=1)])
    broker.execute(plan, "a1")
    exchange.fill(broker.children()[0]["link_id"], .1)
    broker.reconcile()
    payload = reduction(broker)
    auto_fill(exchange)
    original = exchange.place_order

    def place(**request):
        result = original(**request)
        if request.get("orderType") == "Limit":
            exchange.fill(request["orderLinkId"], float(request["qty"]))
        return result

    exchange.place_order = place
    broker.execute(payload, "r1")
    assert float(exchange.size) == .025
    stops = [c for c in broker.children() if c["kind"] == "partial_sl" and c["status"] == "LIVE"]
    assert len(stops) == 1 and float(stops[0]["request"]["qty"]) == .025
    assert len(market_posts(exchange)) == 1


@pytest.mark.parametrize("first_kind", ["tp", "partial_sl"])
@pytest.mark.parametrize("restart", [False, True])
def test_both_restore_group_races_refresh_before_sibling(live, first_kind, restart):
    broker, exchange = live
    plan = persist(broker)
    broker.execute(plan, "a1")
    exchange.fill(broker.children()[0]["link_id"], .1)
    broker.reconcile()
    second_kind = "partial_sl" if first_kind == "tp" else "tp"
    for kind, qty in [(first_kind, ".05"), (second_kind, ".1")]:
        request = dict(side="Sell", qty=qty, reduceOnly=True, orderType="Limit",
                       timeInForce="GTC", price="105")
        if kind == "partial_sl":
            request.pop("price")
            request.update(orderType="Market", closeOnTrigger=True,
                           triggerPrice="99", triggerBy="MarkPrice", triggerDirection=2)
        broker._submit(plan, "a1", kind, "source-" + kind, request)
    payload = reduction(broker)
    auto_fill(exchange)
    original = exchange.place_order
    raced = False

    def place(**request):
        nonlocal raced
        result = original(**request)
        if request.get("timeInForce") != "IOC" and not raced:
            raced = True
            exchange.fill(request["orderLinkId"], float(request["qty"]))
            if restart:
                raise TimeoutError()
        return result

    exchange.place_order = place
    if restart:
        with pytest.raises(BrokerNotReady, match="submission_unknown"):
            broker.execute(payload, "r1")
        broker = ScenarioDemoBroker(broker.conn, session=exchange, expected_main_uid="123",
                                   clock=broker.clock, execution_enabled=True)
    broker.execute(payload, "r1")
    assert float(exchange.size) == .025
    targets = [c for c in broker.children() if c["kind"] == second_kind and c["status"] == "LIVE"]
    assert len(targets) == 1 and float(targets[0]["request"]["qty"]) == .025
    assert len(market_posts(exchange)) == 1
    assert len([c for c in broker.children() if c["kind"] == first_kind and c["intent_id"] == "r1"]) == 1


@pytest.mark.parametrize("status", ["Rejected", "Cancelled", "Deactivated", "PartiallyFilledCanceled"])
def test_unfilled_restore_terminal_is_explicit_failure_not_success(live, status):
    from live.scenario_immediate_reduce import load
    broker, exchange = held(live)
    payload = reduction(broker)
    auto_fill(exchange)
    original = exchange.place_order

    def reject(**request):
        result = original(**request)
        if request.get("orderType") == "Limit":
            if status == "PartiallyFilledCanceled":
                exchange.fill(request["orderLinkId"], .01)
            exchange.orders[request["orderLinkId"]]["orderStatus"] = status
        return result

    exchange.place_order = reject
    broker.execute(payload, "r1")
    result = broker.reconcile()
    receipt = load(broker, "r1")
    assert receipt["status"] == "RESTORE_FAILED"
    assert len(receipt["failed_source_ids"]) == 2
    item = next(i for i in result["intents"] if i["intent_id"] == "r1")
    assert item["terminal"] is True  # Known failed operation, not an UNKNOWN fence.
    assert item["reduction_result"]["status"] == "RESTORE_FAILED"
    context = broker.context()
    assert context["immediate_reduce_available"] is False
    assert context["immediate_reduce_result"]["status"] == "RESTORE_FAILED"
    writes = list(exchange.writes)
    broker.execute(payload, "r1")
    assert exchange.writes == writes
    assert float(exchange.size) == (.03 if status == "PartiallyFilledCanceled" else .05)
    assert exchange.stop == "98"
    exit_plan = persist(broker, action="EXIT", action_id="exit", entries=[])
    broker.execute(exit_plan, "exit")
    assert any(c["kind"] == "exit" for c in broker.children())


@pytest.mark.parametrize("phase", ["PREPARED", "CANCELLED", "RESTORING"])
def test_failed_at_each_phase_does_not_create_restoration_unknown(live, monkeypatch, phase):
    from live import scenario_immediate_reduce as reduction_module
    broker, exchange = held(live)
    payload = reduction(broker)
    auto_fill(exchange)
    if phase != "PREPARED":
        original_save = reduction_module._save

        def interrupt(b, ident, body, **kwargs):
            original_save(b, ident, body, **kwargs)
            if body["status"] == phase:
                raise RuntimeError("owned simulated crash")

        with monkeypatch.context() as patch:
            patch.setattr(reduction_module, "_save", interrupt)
            with pytest.raises(RuntimeError, match="owned simulated crash"):
                broker.execute(payload, "r1")
    state = json.loads(broker.conn.execute("SELECT body FROM llm_scenario_state").fetchone()[0])
    state["active"]["entry_contract_observation"] = {"status": "FAILED"}
    broker.conn.execute("UPDATE llm_scenario_state SET body=?", (json.dumps(state),))
    broker.conn.commit()
    before = list(exchange.writes)
    broker.execute(payload, "r1")
    assert exchange.writes == before
    assert reduction_module.load(broker, "r1")["status"] == "SKIPPED_FAILED"
    item = next(i for i in broker.reconcile()["intents"] if i["intent_id"] == "r1")
    assert item["terminal"] is True and item["reduction_result"]["status"] == "SKIPPED_FAILED"
    full_exit = persist(broker, action="EXIT", action_id="exit", entries=[])
    broker.execute(full_exit, "exit")
    child = next(c for c in broker.children() if c["kind"] == "exit")
    exchange.fill(child["link_id"], float(child["request"]["qty"]))
    assert broker.reconcile()["settlement"]["flat_confirmed"] is True


def test_failed_during_unresolved_reduce_waits_then_skips_restoration(live):
    from live.scenario_immediate_reduce import load
    broker, exchange = held(live)
    payload = reduction(broker)
    with pytest.raises(BrokerNotReady, match="reduce_result_pending"):
        broker.execute(payload, "r1")
    state = json.loads(broker.conn.execute("SELECT body FROM llm_scenario_state").fetchone()[0])
    state["active"]["entry_contract_observation"] = {"status": "FAILED"}
    broker.conn.execute("UPDATE llm_scenario_state SET body=?", (json.dumps(state),))
    broker.conn.commit()
    before = list(exchange.writes)
    with pytest.raises(BrokerNotReady, match="reduce_result_pending"):
        broker.execute(payload, "r1")
    assert load(broker, "r1")["status"] == "CANCELLED" and exchange.writes == before
    child = next(c for c in broker.children() if c["kind"] == "reduce")
    exchange.fill(child["link_id"], float(child["request"]["qty"]))
    broker.execute(payload, "r1")
    assert load(broker, "r1")["status"] == "SKIPPED_FAILED"
    assert exchange.writes == before


def test_fully_filled_restoration_is_complete_not_unfilled_cancel_failure(live):
    from live.scenario_immediate_reduce import load
    broker, exchange = held(live)
    payload = reduction(broker)
    auto_fill(exchange)
    original = exchange.place_order

    def fill_target(**request):
        result = original(**request)
        if request.get("orderType") == "Limit":
            exchange.fill(request["orderLinkId"], float(request["qty"]))
            exchange.orders[request["orderLinkId"]]["orderStatus"] = "Cancelled"
        return result

    exchange.place_order = fill_target
    broker.execute(payload, "r1")
    assert load(broker, "r1")["status"] == "COMPLETE"
    assert load(broker, "r1")["failed_source_ids"] == []
    assert float(exchange.size) == 0


@pytest.mark.parametrize("financial", ["pending", "raising"])
@pytest.mark.parametrize("failed", [False, True])
def test_failed_no_post_handoff_does_not_wait_for_financial_finality(live, monkeypatch, financial, failed):
    from live.scenario_immediate_reduce import load
    broker, exchange = held(live)
    payload = reduction(broker)
    if failed:
        state = json.loads(broker.conn.execute("SELECT body FROM llm_scenario_state").fetchone()[0])
        state["active"]["entry_contract_observation"] = {"status": "FAILED"}
        broker.conn.execute("UPDATE llm_scenario_state SET body=?", (json.dumps(state),))
        broker.conn.commit()

    def unavailable(*args):
        if financial == "raising":
            raise BrokerNotReady("financial_fixture_unavailable")
        return {"status": "pending"}

    monkeypatch.setattr(broker, "_risk_accounting", unavailable)
    before = list(exchange.writes)
    if not failed:
        with pytest.raises(BrokerNotReady):
            broker.execute(payload, "r1")
        assert load(broker, "r1")["status"] == "PREPARED"
        assert exchange.writes == before
        return
    broker.execute(payload, "r1")
    assert exchange.writes == before
    assert load(broker, "r1")["status"] == "SKIPPED_FAILED"
    full_exit = persist(broker, action="EXIT", action_id="exit", entries=[])
    broker.execute(full_exit, "exit")
    assert any(c["kind"] == "exit" for c in broker.children())


@pytest.mark.parametrize("tables", [[], ["llm_scenario_intents"], ["llm_scenario_reductions"]])
def test_optional_reduction_context_missing_runtime_schema_is_read_only_unavailable(tables):
    from types import SimpleNamespace
    from live.scenario_immediate_reduce import context
    conn = sqlite3.connect(":memory:")
    for table in tables:
        conn.execute(f"CREATE TABLE {table}(id TEXT)")
    before = conn.execute("SELECT name,sql FROM sqlite_master").fetchall()
    changes = conn.total_changes
    result = context(SimpleNamespace(conn=conn), {"scenario_id": "active"})
    assert result == dict(immediate_reduce_available=False,
                         immediate_reduce_result=dict(status="UNAVAILABLE", reason="runtime_schema_missing"))
    assert conn.total_changes == changes
    assert conn.execute("SELECT name,sql FROM sqlite_master").fetchall() == before


def test_reduction_context_complete_schema_does_not_hide_missing_registered_intent(live):
    from live.scenario_immediate_reduce import context
    broker, _ = held(live)
    persist(broker, action="REDUCE", action_id="missing-reduction-record", entries=[])
    with pytest.raises(BrokerNotReady, match="reduce_preparation_missing"):
        context(broker, broker._active())


def after_partial_stop(live):
    broker, exchange = live
    plan = persist(broker, take_profits=[dict(id='tp1',price=105,fraction=.5),
                                         dict(id='tp2',price=110,fraction=.5)],
                   partial_stops=[dict(id='ps',price=99,fraction=.5)])
    broker.execute(plan,'a1')
    exchange.fill(next(c['link_id'] for c in broker.children() if c['kind']=='entry'), .1)
    broker.reconcile()
    stop=next(c for c in broker.children() if c['kind']=='partial_sl' and c['status']=='LIVE')
    exchange.fill(stop['link_id'],.05)
    broker.reconcile()
    assert float(exchange.size)==.05
    tps=[c for c in broker.children() if c['kind']=='tp' and c['status']=='LIVE']
    assert sum(float(c['evidence']['order']['leavesQty']) for c in tps)==pytest.approx(.1)
    payload=dict(scenario_id='s1',action_id='r1',action='REDUCE',reduce_fraction=.5,
                 expires_at=1800000300,reduce_plan=dict(quantity_before=.05,requested_quantity=.025,
                 target_remainder=.025),entries=[],take_profits=[],partial_stops=[],cancel_entry_ids=[])
    broker.conn.execute("INSERT INTO llm_scenario_intents VALUES(?,?,?,'PENDING',NULL)",
                        ('r1','s1',json.dumps(payload)))
    return broker,exchange,payload


def test_reduce_after_partial_stop_cancels_and_scales_without_refilling(live):
    broker,exchange,payload=after_partial_stop(live)
    before=list(exchange.writes)
    broker.prepare_reduce(payload,'r1')
    assert exchange.writes==before
    broker.conn.commit();auto_fill(exchange)
    broker.execute(payload,'r1');broker.reconcile()
    assert float(exchange.size)==pytest.approx(.025)
    targets=[c for c in broker.children() if c['kind']=='tp' and c['status']=='LIVE']
    assert sorted(float(c['request']['qty']) for c in targets) == [.012, .012]
    assert len(market_posts(exchange))==1
    assert not any(kind=='cancel' and r.get('orderId')=='native-id' for kind,r in exchange.writes)
    restarted=ScenarioDemoBroker(broker.conn,session=exchange,expected_main_uid='123',
                                 clock=broker.clock,execution_enabled=True)
    restarted.execute(payload,'r1')
    assert len(market_posts(exchange))==1


def test_partial_stop_then_tp_cancel_race_keeps_original_target(live):
    broker,exchange,payload=after_partial_stop(live)
    broker.prepare_reduce(payload,'r1');broker.conn.commit()
    original=exchange.cancel_order;raced=False
    def cancel(**request):
        nonlocal raced
        if not raced:
            raced=True;exchange.fill(request['orderLinkId'],.01)
        return original(**request)
    exchange.cancel_order=cancel;auto_fill(exchange)
    broker.execute(payload,'r1');broker.reconcile()
    assert float(exchange.size)==pytest.approx(.025)
    assert len(market_posts(exchange))==1 and float(market_posts(exchange)[0]['qty'])==pytest.approx(.015)
    targets=[c for c in broker.children() if c['kind']=='tp' and c['status']=='LIVE']
    assert sorted(float(c['request']['qty']) for c in targets)==[.011, .013]
    assert not any(kind=='cancel' and r.get('orderId')=='native-id' for kind,r in exchange.writes)


def test_impossible_exit_quota_still_fails_closed(live):
    broker,exchange,payload=after_partial_stop(live)
    row=broker.conn.execute("SELECT link_id,evidence FROM llm_scenario_children WHERE kind='tp' AND status='LIVE' LIMIT 1").fetchone()
    evidence=json.loads(row[1]);evidence['order']['leavesQty']='0.2'
    broker.conn.execute('UPDATE llm_scenario_children SET evidence=? WHERE link_id=?',(json.dumps(evidence),row[0]))
    before=list(exchange.writes)
    with pytest.raises(BrokerNotReady,match='reduce_source_quota_invalid'):
        broker.prepare_reduce(payload,'r1')
    assert exchange.writes==before


def test_reduce_caps_each_group_after_partially_filled_partial_stop(live):
    broker,exchange=live
    plan=persist(broker,take_profits=[dict(id='tp1',price=105,fraction=.5),
                                     dict(id='tp2',price=110,fraction=.5)],
                 partial_stops=[dict(id='ps',price=99,fraction=.5)])
    broker.execute(plan,'a1')
    exchange.fill(next(c['link_id'] for c in broker.children() if c['kind']=='entry'),.1)
    broker.reconcile()
    stop=next(c for c in broker.children() if c['kind']=='partial_sl' and c['status']=='LIVE')
    exchange.fill(stop['link_id'],.02);broker.reconcile()
    assert float(exchange.size)==pytest.approx(.08)
    payload=dict(scenario_id='s1',action_id='r1',action='REDUCE',reduce_fraction=.5,
                 expires_at=1800000300,reduce_plan=dict(quantity_before=.08,requested_quantity=.04,
                 target_remainder=.04),entries=[],take_profits=[],partial_stops=[],cancel_entry_ids=[])
    broker.conn.execute("INSERT INTO llm_scenario_intents VALUES(?,?,?,'PENDING',NULL)",
                        ('r1','s1',json.dumps(payload)))
    broker.prepare_reduce(payload,'r1');broker.conn.commit();auto_fill(exchange)
    broker.execute(payload,'r1');broker.reconcile()
    assert float(exchange.size)==pytest.approx(.04)
    for kind in ('tp','partial_sl'):
        sources=[c for c in broker.children() if c['kind']==kind and c['status']=='LIVE']
        assert 0 < sum(float(c['request']['qty']) for c in sources) <= .04
    assert len(market_posts(exchange))==1
    assert not any(kind=='cancel' and r.get('orderId')=='native-id' for kind,r in exchange.writes)


def test_reduce_caps_partial_stops_after_partial_tp_fill(live):
    broker,exchange=live
    plan=persist(broker,take_profits=[dict(id='tp1',price=105,fraction=.5),
                                     dict(id='tp2',price=110,fraction=.5)],
                 partial_stops=[dict(id='ps1',price=99,fraction=.5),
                                dict(id='ps2',price=99.5,fraction=.5)])
    broker.execute(plan,'a1')
    exchange.fill(next(c['link_id'] for c in broker.children() if c['kind']=='entry'),.1)
    broker.reconcile()
    tp=next(c for c in broker.children() if c['kind']=='tp' and c['status']=='LIVE')
    exchange.fill(tp['link_id'],.05);broker.reconcile()
    assert float(exchange.size)==pytest.approx(.05)
    payload=dict(scenario_id='s1',action_id='r1',action='REDUCE',reduce_fraction=.5,
                 expires_at=1800000300,reduce_plan=dict(quantity_before=.05,requested_quantity=.025,
                 target_remainder=.025),entries=[],take_profits=[],partial_stops=[],cancel_entry_ids=[])
    broker.conn.execute("INSERT INTO llm_scenario_intents VALUES(?,?,?,'PENDING',NULL)",
                        ('r1','s1',json.dumps(payload)))
    broker.prepare_reduce(payload,'r1');broker.conn.commit();auto_fill(exchange)
    broker.execute(payload,'r1');broker.reconcile()
    assert float(exchange.size)==pytest.approx(.025)
    for kind in ('tp','partial_sl'):
        sources=[c for c in broker.children() if c['kind']==kind and c['status']=='LIVE']
        assert 0 < sum(float(c['request']['qty']) for c in sources) <= .025
    assert len(market_posts(exchange))==1
    assert not any(kind=='cancel' and r.get('orderId')=='native-id' for kind,r in exchange.writes)


@pytest.mark.parametrize('fault',['missing_id','wrong_order','wrong_side','duplicate'])
def test_overlap_requires_verified_entry_fill_identity(live,fault):
    broker,exchange,payload=after_partial_stop(live)
    row=broker.conn.execute("SELECT link_id,evidence FROM llm_scenario_children WHERE kind='entry' LIMIT 1").fetchone()
    evidence=json.loads(row[1]);fill=evidence['executions'][0]
    if fault=='missing_id':fill['execId']=''
    elif fault=='wrong_order':fill['orderId']='other'
    elif fault=='wrong_side':fill['side']='Sell'
    else:evidence['executions'].append(dict(fill))
    broker.conn.execute('UPDATE llm_scenario_children SET evidence=? WHERE link_id=?',(json.dumps(evidence),row[0]))
    before=list(exchange.writes)
    with pytest.raises(BrokerNotReady,match='reduce_source_quota_invalid'):
        broker.prepare_reduce(payload,'r1')
    assert exchange.writes==before


def test_overlap_reduce_ack_loss_recovers_without_duplicate(live):
    broker,exchange,payload=after_partial_stop(live)
    broker.prepare_reduce(payload,'r1');broker.conn.commit();auto_fill(exchange,lose_ack=True)
    with pytest.raises(BrokerNotReady,match='submission_unknown'):
        broker.execute(payload,'r1')
    restarted=ScenarioDemoBroker(broker.conn,session=exchange,expected_main_uid='123',
                                 clock=broker.clock,execution_enabled=True)
    restarted.reconcile()
    assert len(market_posts(exchange))==1
    assert float(exchange.size)==pytest.approx(.025)
    targets=[c for c in restarted.children() if c['kind']=='tp' and c['status']=='LIVE']
    assert 0 < sum(float(c['request']['qty']) for c in targets) <= .025
    assert not any(kind=='cancel' and r.get('orderId')=='native-id' for kind,r in exchange.writes)


def test_overlap_reduce_native_stop_race_never_reopens(live):
    broker,exchange,payload=after_partial_stop(live)
    broker.prepare_reduce(payload,'r1');broker.conn.commit()
    original=exchange.cancel_order;raced=False
    def cancel(**request):
        nonlocal raced
        if not raced:
            raced=True;exchange.fill('native',.05)
            native=next(c for c in broker.children() if c['kind']=='native_sl')
            broker._query_child(native)
        return original(**request)
    exchange.cancel_order=cancel
    broker.execute(payload,'r1')
    assert float(exchange.size)==0
    assert market_posts(exchange)==[]
    assert not [c for c in broker.children() if c['kind']=='tp' and c['status']=='LIVE']


def test_overlap_expired_reduce_never_posts_market_and_restores_capped_targets(live):
    broker,exchange,payload=after_partial_stop(live)
    broker.prepare_reduce(payload,'r1');broker.conn.commit()
    broker.clock=lambda:1800000301
    broker.execute(payload,'r1');broker.reconcile()
    assert float(exchange.size)==pytest.approx(.05)
    assert market_posts(exchange)==[]
    targets=[c for c in broker.children() if c['kind']=='tp' and c['status']=='LIVE']
    assert sorted(float(c['request']['qty']) for c in targets)==[.025,.025]
    assert not any(kind=='cancel' and r.get('orderId')=='native-id' for kind,r in exchange.writes)


def test_overlap_reduction_with_asynchronous_offline_exchange(tmp_path):
    from decimal import Decimal
    from backtest.scenario_exchange import OfflineBybitSession
    from live.scenario_control import _write
    import sqlite3

    exchange=OfflineBybitSession(start_ms=1_800_000_005_000,slippage_bps=0,spread=0)
    conn=sqlite3.connect(tmp_path/'offline.sqlite')
    broker=ScenarioDemoBroker(conn,session=exchange,expected_main_uid=exchange.uid,
                              clock=exchange.now,execution_enabled=True)
    _write(conn,dict(version=1,state='active',main_uid=exchange.uid))
    conn.execute('CREATE TABLE llm_scenario_intents(id TEXT PRIMARY KEY,scenario_id TEXT,payload TEXT,status TEXT,evidence TEXT)')
    conn.execute('CREATE TABLE llm_scenario_state(id INTEGER PRIMARY KEY,body TEXT)')
    active=dict(scenario_id='s1',initial_equity=10000,side='LONG',hard_stop=98,
                created_at=exchange.now(),expires_at=exchange.now()+300)
    conn.execute('INSERT INTO llm_scenario_state VALUES(1,?)',(json.dumps(dict(active=active)),));conn.commit()
    plan=persist(broker,side='LONG',hard_stop=98,chase=dict(max_bps=0,max_reprices=0),
                 take_profits=[dict(id='tp1',price=105,fraction=.5),dict(id='tp2',price=110,fraction=.5)],
                 partial_stops=[dict(id='ps',price=99,fraction=.5)])
    broker.execute(plan,'a1');exchange.advance(exchange.ts_ms+100,100,100,100);broker.reconcile()
    exchange.elapse(exchange.ts_ms+100);broker.reconcile()
    exchange.advance(exchange.ts_ms+100,99,99,100);exchange.elapse(exchange.ts_ms+100);broker.reconcile()
    assert exchange.position==Decimal('.05')
    payload=dict(scenario_id='s1',action_id='r1',action='REDUCE',reduce_fraction=.5,
                 expires_at=1800000300,reduce_plan=dict(quantity_before=.05,requested_quantity=.025,
                 target_remainder=.025),entries=[],take_profits=[],partial_stops=[],cancel_entry_ids=[])
    conn.execute("INSERT INTO llm_scenario_intents VALUES(?,?,?,'PENDING',NULL)",('r1','s1',json.dumps(payload)))
    broker.prepare_reduce(payload,'r1');conn.commit()
    with pytest.raises(BrokerNotReady,match='cancel_not_confirmed'):
        broker.execute(payload,'r1')
    exchange.elapse(exchange.ts_ms+100);broker.reconcile()
    exchange.advance(exchange.ts_ms+100,99,99,100);broker.reconcile()
    exchange.advance(exchange.ts_ms+100,99,99,100);broker.reconcile()
    assert exchange.position==Decimal('.025') and exchange.stop==98
    exchange.elapse(exchange.ts_ms+100);broker.reconcile()
    live=[c for c in broker.children() if c['kind']=='tp' and c['status']=='LIVE']
    assert len(live)==2
    assert sum(Decimal(c['request']['qty']) for c in live)<=Decimal('.025')
    assert len([c for c in broker.children() if c['kind']=='reduce'])==1
