"""Real offline session accounting, latency and broker compatibility tests."""
import json
import sqlite3
from decimal import Decimal

import pytest

from backtest.scenario_exchange import OfflineBybitSession
from live.scenario_broker import ScenarioDemoBroker


def session(**kw):
    return OfflineBybitSession(slippage_bps=0, spread=0, maker_fee=0, taker_fee=0, **kw)


def order(s, side="Buy", qty="1", price="100", **kw):
    args = dict(symbol="BTCUSDT", positionIdx=0, side=side, qty=qty,
                orderType="Limit", price=price, timeInForce="GTC")
    args.update(kw)
    return s.place_order(**args)["result"]["orderId"]


@pytest.mark.parametrize("side,exit_price", [("Buy", 110), ("Sell", 90)])
def test_long_short_realized_fees_and_marked_equity(side, exit_price):
    s = OfflineBybitSession(slippage_bps=0, spread=0, maker_fee=.001, taker_fee=.002)
    order(s, side=side)
    s.advance(100, 100, 100, 1000)
    assert s.cash == Decimal('9999.800')
    s.advance(200, exit_price, exit_price, 0)
    assert s.equity == Decimal('10009.800')
    order(s, side="Sell" if side == "Buy" else "Buy", orderType="Market", reduceOnly=True)
    s.advance(300, exit_price, exit_price, 1000)
    assert s.position == 0
    assert s.cash == Decimal('10010') - Decimal('.2') - Decimal(exit_price) * Decimal('.002')
    assert sum(Decimal(r["cashFlow"]) for r in s.transactions) == 10


def test_partial_fill_cancel_latency_and_shared_capacity():
    s = session(cancel_latency_ms=200)
    a, b = order(s), order(s)
    assert s.advance(99, 100, 100, 1000) == 0
    assert s.advance(100, 100, 100, 600) == 600
    assert s.orders[a]["cumExecQty"] == '0.600'
    assert s.orders[b]["cumExecQty"] == '0'
    s.cancel_order(orderId=a)
    s.advance(200, 100, 100, 100)
    assert s.orders[a]["cumExecQty"] == '0.700'
    s.advance(300, 100, 100, 100)
    assert s.orders[a]["orderStatus"] == 'Cancelled'
    assert s.orders[b]["cumExecQty"] == '0.100'


def test_no_same_event_or_future_price_fill():
    s = session()
    s.advance(100, 90, 90, 1000)
    ident = order(s, price="95")
    assert not s.executions
    s.get_open_orders()
    assert not s.executions
    s.advance(200, 100, 100, 1000)
    assert not s.executions
    s.advance(300, 94, 94, 1000)
    assert s.orders[ident]["avgPrice"] == '95'
    with pytest.raises(ValueError, match="strictly"):
        s.advance(300, 80, 80, 1000)


def test_stop_gap_mark_trigger_and_reduce_first():
    s = session()
    order(s, stopLoss="98", slTriggerBy="MarkPrice", tpslMode="Full")
    s.advance(100, 100, 100, 1000)
    native = s._native_id
    assert s.orders[native]["triggerPrice"] == '98'
    order(s, side="Sell", price="105", reduceOnly=True)
    s.advance(200, 106, 97, 1000)
    assert s.position == 0
    assert s.executions[-1]["orderId"] == native
    assert s.executions[-1]["execPrice"] == '106'
    assert s.mark_proxy_used is False


def test_stop_gap_slippage_and_capacity_limited_residual():
    s = OfflineBybitSession(slippage_bps=10, spread=0, maker_fee=0, taker_fee=0)
    order(s, stopLoss="98", slTriggerBy="MarkPrice", tpslMode="Full")
    s.advance(100, 100, 100, 1000)
    s.advance(200, 90, 90, 400)
    assert s.position == Decimal('.6')
    assert s.executions[-1]["execPrice"] == '89.9'
    s.advance(300, 100, 100, 600)
    assert s.position == 0  # already-triggered stop does not untrigger on recovery


def test_split_take_profit_resizes_native_protection():
    s = session()
    order(s, stopLoss="95", slTriggerBy="MarkPrice", tpslMode="Full")
    s.advance(100, 100, 100, 1000)
    order(s, side="Sell", qty=".4", price="110", reduceOnly=True)
    order(s, side="Sell", qty=".6", price="120", reduceOnly=True)
    s.advance(200, 110, 110, 1000)
    assert s.position == Decimal('.6')
    assert s.orders[s._native_id]["leavesQty"] == '0.600'
    s.advance(300, 120, 120, 1000)
    assert s.position == 0
    assert s.cash == 10016


@pytest.mark.parametrize("side,expected", [("Buy", '-.1'), ("Sell", '.1')])
def test_funding_sign_and_boundary_before_fills(side, expected):
    s = session(funding_schedule=[(1000, .001)], funding_interval_ms=60000)
    order(s, side=side)
    s.advance(100, 100, 100, 1000)
    order(s, side="Sell" if side == "Buy" else "Buy", reduceOnly=True, orderType="Market")
    s.advance(1000, 100, 100, 1000)
    assert abs(s.position) == 1
    assert s.transactions[-1]["type"] == 'SETTLEMENT'
    assert Decimal(s.transactions[-1]["funding"]) == Decimal(expected)
    s.advance(1001, 100, 100, 1000)
    assert s.position == 0
    assert s.executions[-1]["execTime"] == '1001'


def test_ioc_partial_terminal_and_amend_latency():
    s = session(amend_latency_ms=200)
    a = order(s, qty="1", orderType="Market")
    s.advance(100, 100, 100, 200)
    assert s.orders[a]["orderStatus"] == 'PartiallyFilledCanceled'
    b = order(s, price="90")
    s.amend_order(orderId=b, price="101")
    s.advance(200, 100, 100, 1000)
    assert s.orders[b]["cumExecQty"] == '0'
    s.advance(300, 100, 100, 1000)
    assert s.orders[b]["orderStatus"] == 'Filled'


def test_broker_capture_and_financial_evidence_use_real_session(tmp_path):
    s = session(start_ms=1_800_000_005_000)
    conn = sqlite3.connect(tmp_path / 'broker.db')
    broker = ScenarioDemoBroker(conn, session=s, expected_main_uid=s.uid, clock=s.now)
    assert broker.capture_account()["equity"] == 10000
    start = s.ts_ms
    order(s)
    s.advance(start + 100, 100, 100, 1000)
    order(s, side="Sell", orderType="Market", reduceOnly=True)
    s.advance(start + 200, 110, 110, 1000)
    evidence = broker.capture_financial_evidence(start, s.ts_ms)
    assert sum(t["gross_pnl"] for t in evidence["trades"]) == 10
    assert evidence["unmatched_ids"] == []
    assert broker.capture_account()["equity"] == 10010


def test_reproducible_digest_no_future_funding_and_defensive_reads():
    a, b = session(funding_schedule=[(1000, .1)]), session(funding_schedule=[(1000, .1)])
    for s in (a, b):
        order(s)
        s.advance(100, 100, capacity_lots=1000)
    assert a.digest() == b.digest()
    assert a.snapshot()["mark_proxy_used"] is True
    assert a.get_funding_rate_history(endTime=2000)["result"]["list"] == []
    rows = a.get_open_orders()["result"]["list"]
    if rows:
        rows[0]["qty"] = '999'
    assert a.digest() == b.digest()
    json.dumps(a.snapshot(), allow_nan=False)


def test_actual_broker_entry_protection_tp_and_settlement(tmp_path):
    from live.scenario_control import _write

    s = session(start_ms=1_800_000_005_000)
    conn = sqlite3.connect(tmp_path / 'execution.db')
    broker = ScenarioDemoBroker(conn, session=s, expected_main_uid=s.uid, clock=s.now, execution_enabled=True)
    _write(conn, dict(version=1, state="active", main_uid=s.uid))
    conn.execute("CREATE TABLE llm_scenario_intents(id TEXT PRIMARY KEY,scenario_id TEXT,payload TEXT,status TEXT,evidence TEXT)")
    conn.execute("CREATE TABLE llm_scenario_state(id INTEGER PRIMARY KEY,body TEXT)")
    active = dict(scenario_id="s1", initial_equity=10000, side="LONG", hard_stop=98,
                  created_at=s.now(), expires_at=s.now()+300)
    conn.execute("INSERT INTO llm_scenario_state VALUES(1,?)", (json.dumps(dict(active=active)),))
    plan = dict(scenario_id="s1", action="OPEN", action_id="a1", side="LONG", hard_stop=98,
                leverage=10, expires_at=s.now()+300, entries=[dict(id="e1",price=100,quantity=.1)],
                take_profits=[dict(id="tp1",price=110,fraction=1)], partial_stops=[],
                cancel_entry_ids=[], chase=dict(max_bps=0,max_reprices=0))
    conn.execute("INSERT INTO llm_scenario_intents VALUES(?,?,?,'PENDING',NULL)", ('a1','s1',json.dumps(plan)))
    conn.commit()
    broker.execute(plan, 'a1')
    assert len(s.orders) == 1
    assert not s.executions
    s.advance(s.ts_ms+100, 100, 100, 100)
    result = broker.reconcile()
    assert result["protection_confirmed"] is True
    assert {c["kind"] for c in broker.children()} == {'entry','native_sl','tp'}
    s.advance(s.ts_ms+100, 110, 110, 100)
    result = broker.reconcile()
    assert result["settlement"] is not None, conn.execute("SELECT body FROM llm_scenario_broker_evidence WHERE kind='scenario_accounting' ORDER BY rowid DESC LIMIT 1").fetchone()
    assert result["settlement"]["net_pnl"] == 1
    assert result["settlement"]["flat_confirmed"] is True
    assert len(result["settlement"]["execution_ids"]) == 2


def test_clock_only_latency_does_not_consume_market_ioc():
    s = session()
    ident = order(s, orderType="Market")
    s.elapse(500)
    assert s.orders[ident]["orderStatus"] == 'New'
    assert not s.executions
    s.advance(501, 103, 103, 1000)
    assert s.orders[ident]["avgPrice"] == '103'


def test_spread_and_marketable_amend_charge_taker():
    s = OfflineBybitSession(slippage_bps=0, spread=.002, maker_fee=.001, taker_fee=.002)
    ticker = s.get_tickers()["result"]["list"][0]
    assert Decimal(ticker["ask1Price"]) == Decimal('100.1')
    assert Decimal(ticker["bid1Price"]) == Decimal('99.9')
    ident = order(s, price="100")
    s.advance(100, 100, 100, 1000)
    assert not s.executions  # Last touch does not cross ask spread
    s.amend_order(orderId=ident, price="101")
    s.advance(200, 100, 100, 1000)
    fill = s.executions[0]
    assert fill["isMaker"] is False
    assert Decimal(fill["execPrice"]) == Decimal('100.1')
    assert Decimal(fill["execFee"]) == Decimal('.2002')


def test_stop_amendment_effect_is_delayed_and_old_stop_stays():
    s = session(amend_latency_ms=200)
    order(s, stopLoss="95", slTriggerBy="MarkPrice", tpslMode="Full")
    s.advance(100, 100, 100, 1000)
    s.set_trading_stop(tpslMode="Full", slTriggerBy="MarkPrice", stopLoss="99")
    s.advance(200, 98, 98, 1000)
    assert s.position == 1 and s.stop == 95
    s.advance(300, 98, 98, 1000)
    assert s.position == 0
    assert s.executions[-1]["execPrice"] == '98'


@pytest.mark.parametrize("capacity", [1000, 2000])
def test_gap_through_entry_latches_attached_stop(capacity):
    s = session()
    order(s, stopLoss="98", slTriggerBy="MarkPrice", tpslMode="Full")
    s.advance(100, 90, 90, capacity)
    if capacity == 2000:
        assert s.position == 0
        assert len(s.executions) == 2
    else:
        assert s.orders[s._native_id]["orderStatus"] == 'Triggered'
        s.advance(200, 110, 110, 1000)
        assert s.position == 0  # rebound cannot erase the triggered stop
    assert s.executions[-1]["side"] == 'Sell'


def test_unobserved_native_stop_stays_unowned_not_fake_settled(tmp_path):
    s = session(start_ms=1_800_000_005_000)
    conn = sqlite3.connect(tmp_path / 'unowned.db')
    broker = ScenarioDemoBroker(conn, session=s, expected_main_uid=s.uid, clock=s.now)
    order(s, stopLoss="98", slTriggerBy="MarkPrice", tpslMode="Full")
    start = s.ts_ms
    s.advance(start+100, 100, 100, 1000)
    s.advance(start+200, 90, 90, 1000)
    assert s.position == 0
    assert broker.capture_account()["legacy_fenced"] is False
    evidence = broker.capture_financial_evidence(start, s.ts_ms)
    assert evidence["settlement_ready"] is False  # no owned order coverage inferred


def test_fractional_average_pnl_reconciles_to_real_accounting(tmp_path):
    from live.scenario_accounting import reconcile_scenario

    s = OfflineBybitSession(start_ms=1_800_000_005_000, spread=0, slippage_bps=0)
    conn = sqlite3.connect(tmp_path / 'fractional.db')
    broker = ScenarioDemoBroker(conn, session=s, expected_main_uid=s.uid, clock=s.now)
    start = s.ts_ms
    order(s, qty=".1")
    s.advance(start+100, 100, 100, 100)
    order(s, price="99", qty=".2")
    s.advance(start+200, 99, 99, 200)
    order(s, side="Sell", orderType="Market", qty=".3", reduceOnly=True)
    s.advance(start+300, 110, 110, 300)
    owned = [dict(order_id=o["orderId"], role="exit" if o["reduceOnly"] else "entry",
                  side=o["side"], cumulative_qty=float(o["cumExecQty"]), terminal=True) for o in s.orders.values()]
    result = reconcile_scenario('s1', broker.capture_financial_evidence(start,s.ts_ms), owned,
        broker.capture_account(), funding_schedule=dict(complete=True,start_ms=start,end_ms=s.ts_ms,events=[]))
    assert result["status"] == 'confirmed', result
    assert result["gross_pnl"] == 3.2


@pytest.mark.parametrize("kwargs", [dict(latency_ms=0), dict(entry_latency_ms=0), dict(initial_equity=-1), dict(slippage_bps=10000)])
def test_invalid_configuration(kwargs):
    with pytest.raises(ValueError):
        OfflineBybitSession(**kwargs)
