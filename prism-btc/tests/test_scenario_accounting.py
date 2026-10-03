"""Offline accounting evidence and durable daily loss-gate regression tests."""
import copy
import json
import sqlite3
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from live.scenario_accounting import (AccountingPending, authorize_manual_reset,
                                     reconcile_scenario, update_daily_risk)


def trade(eid, oid, side, qty, price, pnl, timestamp):
    return dict(execution_id=eid, order_id=oid, quantity=qty, price=price,
                gross_pnl=pnl, fee="0.1", timestamp=timestamp,
                raw_execution=dict(execId=eid, orderId=oid, side=side, execQty=qty,
                    execPrice=price, execFee="0.1", execTime=timestamp, symbol="BTCUSDT", execType="Trade"),
                raw_transaction=dict(tradeId=eid, orderId=oid, fee="0.1", cashFlow=pnl,
                    funding="0", type="TRADE", symbol="BTCUSDT", currency="USDT"))


def fixture():
    evidence = dict(start_ms=0, end_ms=100, response_pages_complete=True, unmatched_ids=[],
        trades=[trade("a", "entry", "Buy", "2", "100", "0", 10),
                trade("b", "loss", "Sell", "1", "90", "-10", 20),
                trade("c", "win", "Sell", "1", "120", "20", 30)], funding=[])
    orders = [dict(order_id=oid, role=role, side=side, cumulative_qty=qty, terminal=True)
              for oid, role, side, qty in [("entry", "entry", "Buy", 2), ("loss", "exit", "Sell", 1),
                                           ("win", "exit", "Sell", 1)]]
    snapshot = dict(position=dict(symbol="BTCUSDT", positionIdx=0, size="0", side=""), open_orders=[])
    schedule = dict(start_ms=0, end_ms=100, complete=True, events=[])
    return evidence, orders, snapshot, schedule


def run(parts):
    evidence, orders, snapshot, schedule = parts
    return reconcile_scenario("s", evidence, orders, snapshot, funding_schedule=schedule)


def test_profits_do_not_recycle_gross_loss_or_fees():
    result = run(fixture())
    assert result["status"] == "confirmed"
    assert result["realized_loss"] == 10
    assert result["fees_paid"] == .3
    assert result["net_pnl"] == 9.7
    assert result["settlement"]["execution_ids"] == ["a", "b", "c"]


@pytest.mark.parametrize("mutation", [
    lambda e,o,s,f: e["trades"][0]["raw_transaction"].pop("cashFlow"),
    lambda e,o,s,f: e["trades"][0]["raw_transaction"].pop("funding"),
    lambda e,o,s,f: e["trades"][0]["raw_transaction"].update(orderId="foreign"),
    lambda e,o,s,f: e["trades"][0]["raw_transaction"].update(tradeId="foreign"),
    lambda e,o,s,f: e["trades"][0]["raw_transaction"].update(currency="BTC"),
    lambda e,o,s,f: e["trades"].append(copy.deepcopy(e["trades"][0])),
    lambda e,o,s,f: o[0].update(cumulative_qty=3),
    lambda e,o,s,f: s["position"].update(size="1"),
    lambda e,o,s,f: f.update(complete=False),
    lambda e,o,s,f: f.update(end_ms=99),
    lambda e,o,s,f: f.update(events=[dict(timestamp=15,rate=".01")]),
    lambda e,o,s,f: f.update(events=[dict(timestamp=10,rate=".01")]),
])
def test_missing_or_conflicting_evidence_is_pending(mutation):
    parts = fixture()
    mutation(*parts)
    result = run(parts)
    assert result["status"] == "pending"
    assert result["settlement"] is None
    assert result["realized_loss"] is None


def test_actual_funding_crossing_debit():
    parts = fixture()
    parts[3]["events"] = [dict(timestamp=15, rate=".01")]
    parts[0]["funding"] = [dict(transaction_id="f", timestamp=15, funding_net="-2",
        raw=dict(id="f", type="SETTLEMENT", symbol="BTCUSDT", currency="USDT", qty="2",
                 funding="-2", cashFlow="0", fee="0", transactionTime=15))]
    result = run(parts)
    assert result["funding_paid"] == 2
    assert result["funding_net"] == -2
    assert result["net_pnl"] == 7.7
    parts[0]["funding"][0]["raw"]["qty"] = "1"
    assert run(parts)["status"] == "pending"


def test_event_after_flat_does_not_require_funding():
    parts = fixture()
    parts[3]["events"] = [dict(timestamp=40,rate=".01")]
    assert run(parts)["settlement"]["funding_complete"] is True


def test_open_position_confirms_accounting_not_settlement():
    parts = fixture()
    parts[0]["trades"] = parts[0]["trades"][:1]
    parts[1][:] = parts[1][:1]
    parts[2]["position"].update(size="2", side="Buy")
    result = run(parts)
    assert result["status"] == "confirmed"
    assert result["settlement"] is None
    assert result["positions"][0]["quantity"] == 2


def test_remaining_lots_and_losing_lot_debits_do_not_net():
    parts = fixture()
    parts[0]["trades"] = [trade("a","entry","Buy","1","100","0",10),
        trade("d","entry","Buy","1","120","0",15),
        trade("b","loss","Sell","1","110","0",20)]
    parts[1][:] = parts[1][:2]
    parts[2]["position"].update(size="1",side="Buy")
    result = run(parts)
    assert result["status"] == "confirmed"
    assert result["realized_loss"] == 5
    assert result["positions"] == [dict(price=100,quantity=.5),dict(price=120,quantity=.5)]


def ts(text):
    return datetime.fromisoformat(text).replace(tzinfo=ZoneInfo("Asia/Seoul")).timestamp()


def daily(conn, when, equity, previous=None, flows=None, complete=True):
    return update_daily_risk(conn, dict(captured_at=when, equity=equity),
        dict(complete=complete, start_ms=(previous or when)*1000, end_ms=when*1000, flows=flows or []))


@pytest.mark.parametrize("last", ["2026-10-04T00:00:00", "2026-10-04T00:05:00"])
def test_midnight_latches_outgoing_loss_before_rollover(last):
    conn = sqlite3.connect(":memory:")
    t0,t1,t2 = map(ts,["2026-10-03T12:00:00", "2026-10-03T23:55:00", last])
    assert daily(conn,t0,10000)["baseline_kind"] == "activation"
    assert daily(conn,t1,9700,t0)["new_risk_allowed"]
    assert not daily(conn,t2,9500,t1)["new_risk_allowed"]
    assert not daily(conn,t2+300,10000,t2)["new_risk_allowed"]


def test_deposit_not_trading_profit_and_missing_flows_block():
    conn = sqlite3.connect(":memory:")
    t=ts("2026-10-03T12:00:00")
    daily(conn,t,10000)
    assert not daily(conn,t+60,15000,t,complete=False)["daily_proof_complete"]
    result=daily(conn,t+60,14900,t,flows=[dict(id="deposit",timestamp=(t+30)*1000,amount=5000,verified=True)])
    assert result["daily_net_pnl"] == -100


def test_manual_reset_requires_authorization_and_preserves_baseline():
    conn = sqlite3.connect(":memory:")
    t=ts("2026-10-03T12:00:00")
    daily(conn,t,10000)
    kwargs=dict(authorization_id="review1",operator="operator",reason="strategy reviewed",reviewed_at=t)
    with pytest.raises(AccountingPending):
        authorize_manual_reset(conn,**kwargs)
    assert authorize_manual_reset(conn,authorized=True,**kwargs)["authorized"]
    with pytest.raises(AccountingPending):
        authorize_manual_reset(conn,authorized=True,**kwargs)
    assert daily(conn,t+60,9900,t)["daily_net_pnl"] == -100


@pytest.mark.parametrize("additional_loss", [0, 200])
def test_late_rollover_carries_only_new_interval_and_audits_old_day(additional_loss):
    conn = sqlite3.connect(":memory:")
    t0,t1,t2 = map(ts,["2026-10-03T12:00:00", "2026-10-03T23:55:00", "2026-10-04T00:00:04"])
    daily(conn,t0,10000)
    assert not daily(conn,t1,9500,t0)["new_risk_allowed"]
    result=daily(conn,t2,9500-additional_loss,t1)
    assert result["daily_net_pnl"] == -additional_loss
    assert not result["new_risk_allowed"]
    outgoing=json.loads(conn.execute("SELECT body FROM llm_scenario_daily_accounting_history WHERE day='2026-10-03'").fetchone()[0])
    assert float(outgoing["outgoing_net_pnl"]) == -500-additional_loss
    assert float(outgoing["daily_net_pnl"]) == -500
    authorize_manual_reset(conn,authorization_id="review",operator="operator",reason="review complete",
                           reviewed_at=t2,authorized=True)
    resumed=daily(conn,t2+300,9500-additional_loss,t2)
    assert resumed["new_risk_allowed"]
    assert resumed["daily_net_pnl"] == -additional_loss
