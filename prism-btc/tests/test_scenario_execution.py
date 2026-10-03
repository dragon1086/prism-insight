"""No-network executor invariants, including crash/ACK-loss recovery."""
import json
import sqlite3

import pytest

from live.scenario_broker import BrokerNotReady, ScenarioDemoBroker
from tests.test_scenario_broker import Session, reply


class Exchange(Session):
    def __init__(self):
        super().__init__()
        self.orders, self.writes = {}, []
        self.lose_ack = False
        self.fills=[]
        self.stop="98"
        self.now=1800000000

    def get_instruments_info(self, **kw):
        return reply([dict(symbol="BTCUSDT", status="Trading", settleCoin="USDT",fundingInterval="480",
            priceFilter=dict(tickSize="0.1"), lotSizeFilter=dict(qtyStep="0.001",
            minOrderQty="0.001", maxOrderQty="100", maxMktOrderQty="100", minNotionalValue="5"))])

    def get_open_orders(self, **kw):
        return reply([o for o in self.orders.values() if o["orderStatus"] in {"New","PartiallyFilled","Untriggered"}
                      and (not kw.get("orderLinkId") or kw["orderLinkId"] == o.get("orderLinkId"))
                      and (not kw.get("orderId") or kw["orderId"] == o["orderId"])])

    def get_order_history(self, **kw):
        return reply([o for o in self.orders.values() if (kw.get("orderLinkId") and kw["orderLinkId"] == o.get("orderLinkId"))
                      or (kw.get("orderId") and kw["orderId"]==o["orderId"])])

    def get_executions(self, **kw):
        return reply([e for e in self.fills if not kw.get("orderId") or kw["orderId"]==e["orderId"]])

    def get_transaction_log(self, **kw):
        return reply([dict(id="txn"+e["execId"],type="TRADE",symbol="BTCUSDT",currency="USDT",
            tradeId=e["execId"],orderId=e["orderId"],cashFlow="0",fee=e["execFee"],funding="0",transactionTime=e["execTime"])
            for e in self.fills if e["execTime"]>=str(kw.get("startTime",0))])

    def get_funding_rate_history(self,**kw):
        return reply([])

    def get_tickers(self,**kw):
        return reply([dict(symbol="BTCUSDT",markPrice="100",lastPrice="100",nextFundingTime="1800003600000")])

    def get_positions(self,**kw):
        return reply([dict(symbol="BTCUSDT",positionIdx=0,size=self.size,side="Buy" if float(self.size)>0 else "",avgPrice="100",stopLoss=self.stop,leverage="10")])

    def fill(self, link, amount):
        o=self.orders[link]
        total=round(float(o["cumExecQty"])+amount,6)
        o.update(cumExecQty=str(total),leavesQty=str(round(float(o["qty"])-total,6)),orderStatus="Filled" if total==float(o["qty"]) else "PartiallyFilled")
        self.fills.append(dict(execId="fill"+str(len(self.fills)),orderId=o["orderId"],symbol="BTCUSDT",side=o["side"],
            execType="Trade",execQty=str(amount),execPrice="100",execFee="0.01",execTime=str(int(self.now*1000))))
        self.size=str(round(float(self.size)+amount*(1 if o["side"]=="Buy" else -1),6))
        if link=="native":
            if float(self.size)>0:
                o["orderStatus"]="Cancelled"
            return
        if float(self.size)>0 and o.get("reduceOnly") is not True:
            self.orders["native"] = dict(orderId="native-id",orderLinkId="",symbol="BTCUSDT",positionIdx=0,qty=self.size,
                side="Sell",reduceOnly=True,stopOrderType="StopLoss",triggerBy="MarkPrice",triggerPrice=self.stop,
                orderStatus="Untriggered",cumExecQty="0",leavesQty=self.size)
        elif float(self.size)>0 and "native" in self.orders and self.orders["native"]["orderStatus"]=="Untriggered":
            self.orders["native"].update(qty=self.size,leavesQty=self.size)
        elif "native" in self.orders and link!="native":
            self.orders["native"]["orderStatus"]="Cancelled"

    def set_trading_stop(self,**kw):
        self.writes.append(("stop",kw))
        self.stop=kw["stopLoss"]
        self.orders["native"]["triggerPrice"]=self.stop
        return dict(retCode=0,result={})

    def place_order(self, **kw):
        self.writes.append(("place", kw))
        ident = "order" + str(len(self.orders))
        self.orders[kw["orderLinkId"]] = dict(kw, orderId=ident, orderStatus="New",
            cumExecQty="0", leavesQty=kw["qty"])
        if self.lose_ack:
            raise TimeoutError()
        return dict(retCode=0, result=dict(orderId=ident, orderLinkId=kw["orderLinkId"]))

    def cancel_order(self, **kw):
        self.writes.append(("cancel", kw))
        self.orders[kw["orderLinkId"]]["orderStatus"] = "Cancelled"
        return dict(retCode=0, result={})


@pytest.fixture
def live(tmp_path):
    conn = sqlite3.connect(tmp_path / "exec.db")
    exchange = Exchange()
    b = ScenarioDemoBroker(conn, session=exchange, expected_main_uid="123",
        clock=lambda: 1800000000, execution_enabled=True)
    from live.scenario_control import _write
    _write(conn,dict(version=1,state="active",main_uid="123"))
    conn.execute("CREATE TABLE llm_scenario_intents(id TEXT PRIMARY KEY, scenario_id TEXT,payload TEXT,status TEXT,evidence TEXT)")
    conn.execute("CREATE TABLE llm_scenario_state(id INTEGER PRIMARY KEY,body TEXT)")
    conn.execute("INSERT INTO llm_scenario_state VALUES(1,?)", (json.dumps(dict(active=dict(
        scenario_id="s1", initial_equity=10000, side="LONG", hard_stop=98,
        created_at=1800000000, expires_at=1800000300))),))
    conn.commit()
    return b, exchange


def persist(b, **updates):
    p = dict(scenario_id="s1", action="OPEN", action_id="a1", side="LONG", hard_stop=98,
        leverage=10, expires_at=1800000300, entries=[dict(id="e1",price=100,quantity=.1)],
        take_profits=[], partial_stops=[], cancel_entry_ids=[], chase=dict(max_bps=20,max_reprices=1))
    p.update(updates)
    b.conn.execute("INSERT INTO llm_scenario_intents VALUES(?,?,?,'PENDING',NULL)",
                   (p["action_id"],p["scenario_id"],json.dumps(p)))
    b.conn.commit()
    return p


def test_paused_flat_account_still_has_verified_no_exposure(live):
    from live.scenario_control import _write
    b,e=live
    b.conn.execute("UPDATE llm_scenario_state SET body=?",(json.dumps(dict(active=None)),));b.conn.commit()
    _write(b.conn,dict(version=1,state="paused",main_uid="123"))
    result=b.reconcile()
    assert result["protection_confirmed"] is True
    assert result["protection_status"]=="verified_flat_no_exposure"
    assert not e.writes


def test_entry_is_durable_before_submit_and_native_protected(live):
    b, e = live
    original = e.place_order
    def check(**kw):
        row = b.conn.execute("SELECT status FROM llm_scenario_children WHERE link_id=?",(kw["orderLinkId"],)).fetchone()
        assert row == ("UNKNOWN",)
        return original(**kw)
    e.place_order = check
    p = persist(b)
    b.execute(p, "a1")
    req = e.writes[0][1]
    assert req["stopLoss"] == "98" and req["slTriggerBy"] == "MarkPrice"
    assert req["tpslMode"] == "Full" and req["timeInForce"] == "GTC"
    assert len(req["orderLinkId"]) <= 36
    b.execute(p, "a1")
    assert len(e.writes) == 1


def test_ack_loss_is_recovered_exactly_without_resubmit(live):
    b, e = live
    p = persist(b)
    e.lose_ack = True
    with pytest.raises(BrokerNotReady):
        b.execute(p,"a1")
    b.execute(p,"a1")
    assert len(e.writes) == 1
    assert b.conn.execute("SELECT status FROM llm_scenario_children").fetchone()[0] == "LIVE"


def test_missing_ack_and_missing_order_never_resubmits(live):
    b, e = live
    p = persist(b)
    e.lose_ack = True
    with pytest.raises(BrokerNotReady):
        b.execute(p,"a1")
    e.orders.clear()
    with pytest.raises(BrokerNotReady):
        b.execute(p,"a1")
    assert len(e.writes) == 1


@pytest.mark.parametrize("changes", [dict(leverage=20),dict(hard_stop=98.01),
    dict(entries=[dict(id="x",price=100,quantity=.1001)]),dict(expires_at=1799999999)])
def test_invalid_plan_has_no_order(live, changes):
    b,e = live
    with pytest.raises(BrokerNotReady):
        b.execute(persist(b,**changes),"a1")
    assert not e.writes


def test_unpersisted_intent_cannot_submit(live):
    b,e = live
    with pytest.raises(BrokerNotReady):
        b.execute({},"missing")
    assert not e.writes


def test_uid_change_prevents_order(live):
    b,e = live
    p = persist(b)
    e.uid = "456"
    with pytest.raises(BrokerNotReady):
        b.execute(p,"a1")
    assert not e.writes


def test_partial_fill_reconcile_installs_tp_with_full_stop_retained(live):
    b,e=live
    p=persist(b,take_profits=[dict(id="tp1",price=105,fraction=.5)])
    b.execute(p,"a1")
    link=next(iter(e.orders))
    e.fill(link,.05)
    result=b.reconcile()
    assert result["protection_confirmed"] is True
    assert any(c["kind"]=="native_sl" for c in b.children())
    tp=next(c for c in b.children() if c["kind"]=="tp")
    assert float(tp["request"]["qty"])==.025
    assert e.orders["native"]["orderStatus"]=="Untriggered"
    b.reconcile()
    assert len([w for w in e.writes if w[0]=="place"])==2


def test_target_context_separates_intent_and_logical_id_from_execution_generation(live):
    b,e=live
    p=persist(b,take_profits=[dict(id="tp:near",price=105,fraction=.5)])
    b.execute(p,"a1")
    e.fill(next(iter(e.orders)),.05)
    b.reconcile()
    before=len(e.writes)
    target=b.context()["target_status"][0]
    assert target["intent_id"] == "a1"
    assert target["logical_target_id"] == "tp:near"
    assert target["target_id"].endswith(":tp:near")
    assert target["target_id"] != target["logical_target_id"]
    assert len(e.writes) == before


@pytest.mark.parametrize("field,kind,prices", [
    ("take_profits", "tp", (105,106)),
    ("partial_stops", "partial_sl", (99,99.5)),
])
def test_partial_fill_quota_matches_exact_logical_target_not_suffix(live,field,kind,prices):
    b,e=live
    p=persist(b,**{field:[dict(id="near",price=prices[0],fraction=.25),
                            dict(id="tp:near",price=prices[1],fraction=.25)]})
    b.execute(p,"a1")
    entry=next(iter(e.orders))
    e.fill(entry,.05)
    b.reconcile()
    filled=next(c for c in b.children() if c["kind"]==kind
                and c["local_id"].partition(":")[2]=="tp:near")
    e.fill(filled["link_id"],.012)
    e.fill(entry,.05)
    evidence=b.reconcile()
    targets={c["local_id"].partition(":")[2]:float(c["request"]["qty"])
             for c in b.children() if c["kind"]==kind and c["status"]=="LIVE"}
    assert targets == {"near":.025,"tp:near":.013}
    assert evidence["protection_confirmed"] is True


def test_completed_tp_does_not_resurrect(live):
    b,e=live
    p=persist(b,take_profits=[dict(id="tp1",price=105,fraction=.5)])
    b.execute(p,"a1")
    e.fill(next(iter(e.orders)),.1)
    b.reconcile()
    tp=next(c for c in b.children() if c["kind"]=="tp")
    e.fill(tp["link_id"],.05)
    b.reconcile()
    b.reconcile()
    assert float(e.size)==.05
    assert len([c for c in b.children() if c["kind"]=="tp"])==1


def test_halt_cancels_entry_without_llm(live):
    b,e=live
    b.execute(persist(b),"a1")
    from live.scenario_control import _write
    _write(b.conn,dict(version=1,state="paused",main_uid="123"))
    b.reconcile()
    assert all(o["orderStatus"]=="Cancelled" for o in e.orders.values())
    with pytest.raises(BrokerNotReady):
        b.chase("a1","e1",100.1)
    assert len([w for w in e.writes if w[0]=="place"])==1


def test_tighter_sl_cancels_old_attached_sl_before_native_update(live):
    b,e=live
    b.execute(persist(b),"a1")
    e.fill(next(iter(e.orders)),.05)
    b.reconcile()
    p=persist(b,action_id="a2",action="ADJUST",hard_stop=99,entries=[])
    b.execute(p,"a2")
    assert [w[0] for w in e.writes][-2:]==["cancel","stop"]
    result=b.reconcile()
    assert result["confirmed_hard_stop"]==99
    assert next(i for i in result["intents"] if i["intent_id"]=="a2")["terminal"] is True
    with pytest.raises(BrokerNotReady):
        b.chase("a1","e1",100.1)


def test_native_stop_hit_can_reconcile_exact_order_id(live):
    b,e=live
    b.execute(persist(b),"a1")
    e.fill(next(iter(e.orders)),.1)
    b.reconcile()
    e.fill("native",.1)
    result=b.reconcile()
    native=next(c for c in b.children() if c["kind"]=="native_sl")
    assert native["status"]=="TERMINAL"
    assert native["evidence"]["executions"][0]["orderId"]=="native-id"
    assert result["observation"]["legacy_fenced"] is False


def test_unsent_rejected_open_abandons_without_fake_fill(live):
    b,e=live
    with pytest.raises(BrokerNotReady):
        b.execute(persist(b,hard_stop=98.01),"a1")
    result=b.reconcile()
    assert not e.writes and not b.children()
    assert result["intents"][0]["terminal"] is True
    assert result["settlement"]["no_fills_confirmed"] is True
    assert result["settlement"]["execution_ids"]==[]
    assert result["notices"][0]["resolution"]=="CANCELLED_UNFILLED"


def test_partially_submitted_batch_abandons_unsent_children(live):
    b,e=live
    original=b._preflight
    calls=[]
    def check(*args):
        calls.append(1)
        if len(calls)==2:
            raise BrokerNotReady("margin_changed")
        return original(*args)
    b._preflight=check
    p=persist(b,entries=[dict(id="e1",price=100,quantity=.1),dict(id="e2",price=99.5,quantity=.1)])
    with pytest.raises(BrokerNotReady):
        b.execute(p,"a1")
    result=b.reconcile()
    assert result["intents"][0]["orders_reconciled"] is True
    assert result["intents"][0]["terminal"] is False
    assert len(b.children())==1
    with pytest.raises(BrokerNotReady):
        b.execute(p,"a1")
    assert len(e.writes)==1


def test_decimal_clock_daily_does_not_block_second_capture(live):
    b,e=live
    b.clock=lambda:1800000000.123456
    b._daily(b.capture_account())
    b.clock=lambda:1800000001.876543
    assert b._daily(b.capture_account())["new_risk_allowed"] is True


def test_changed_exchange_entry_price_is_not_owned_risk(live):
    b,e=live
    b.execute(persist(b),"a1")
    child=b.children()[0]
    e.orders[child["link_id"]]["price"]="150"
    with pytest.raises(BrokerNotReady):
        b._query_child(child)


def test_funding_history_missing_expected_event_never_zero(live):
    b,e=live
    with pytest.raises(BrokerNotReady,match="funding_schedule_event_missing"):
        b._funding_schedule(1799950000000,1800000000000,b.capture_account())


def test_adjust_tp_allocates_remaining_not_original_position(live):
    b,e=live
    b.execute(persist(b,take_profits=[dict(id="tp1",price=105,fraction=.5)]),"a1")
    e.fill(next(iter(e.orders)),.1)
    b.reconcile()
    tp=next(c for c in b.children() if c["kind"]=="tp")
    e.fill(tp["link_id"],.05)
    b.reconcile()
    b.execute(persist(b,action_id="a2",action="ADJUST",entries=[],take_profits=[dict(id="tp2",price=106,fraction=.5)]),"a2")
    new_tp=next(c for c in b.children() if c["kind"]=="tp" and c["intent_id"]=="a2")
    assert float(new_tp["request"]["qty"])==.025


def test_automatic_chase_cancels_exactly_and_preserves_bound(live):
    b,e=live
    b.clock=lambda:e.now
    original=e.get_tickers
    def ticker(**kw):
        out=original(**kw)
        out["result"]["list"][0]["ask1Price"]="100.1"
        return out
    e.get_tickers=ticker
    b.execute(persist(b),"a1")
    e.now+=61
    b.reconcile()
    assert [w[0] for w in e.writes]==["place","cancel","place"]
    assert e.writes[-1][1]["price"]=="100.1"
    e.now+=61
    b.reconcile()
    assert len(e.writes)==3


def test_unknown_cancel_does_not_chase(live):
    b,e=live
    b.execute(persist(b),"a1")
    e.cancel_order=lambda **kw:dict(retCode=0,result={})
    with pytest.raises(BrokerNotReady,match="cancel_not_confirmed"):
        b.chase("a1","e1",100.1)
    assert len(e.writes)==1


def aborted_replacement(live):
    b, e = live
    b.execute(persist(b, chase=dict(max_bps=0, max_reprices=0)), "a1")
    old = next(iter(e.orders))
    def delayed_cancel(**kw):
        e.writes.append(("cancel", kw))
        return dict(retCode=0, result={})
    e.cancel_order = delayed_cancel
    replacement = persist(b, action_id="a2", action="ADJUST",
        cancel_entry_ids=["e1"], entries=[dict(id="e2", price=99, quantity=.1)],
        chase=dict(max_bps=0, max_reprices=0))
    with pytest.raises(BrokerNotReady, match="cancel_not_confirmed"):
        b.execute(replacement, "a2")
    assert b.conn.execute("SELECT status FROM llm_scenario_execution_batches WHERE intent_id='a2'").fetchone() == ("ABORTED",)
    assert not [c for c in b.children() if c["intent_id"] == "a2"]
    return b, e, old


def test_aborted_zero_child_adjust_resolves_only_after_exact_cancel(live):
    b, e, old = aborted_replacement(live)
    assert not any(i["intent_id"] == "a2" for i in b.reconcile()["intents"])
    e.orders[old]["orderStatus"] = "Cancelled"
    writes = list(e.writes)
    result = b.reconcile()
    recovered = next(i for i in result["intents"] if i["intent_id"] == "a2")
    assert recovered["terminal"] and recovered["orders_reconciled"]
    assert recovered["executions_complete"] and recovered["protection_ok"]
    assert recovered["filled_quantity"] == 0
    assert recovered["execution_ids"] == recovered["exchange_order_ids"] == recovered["open_entries"] == []
    assert result["settlement"]["no_fills_confirmed"] is True
    assert e.writes == writes  # No retry, replacement, cancellation or fabricated fill.


@pytest.mark.parametrize("blocker", ["crashed", "missing_batch", "complete_batch", "unknown_old",
                                      "unknown_own", "nonflat", "external_order"])
def test_aborted_adjust_uncertainty_never_becomes_terminal(live, blocker):
    b, e, old = aborted_replacement(live)
    e.orders[old]["orderStatus"] = "Cancelled"
    if blocker == "crashed":
        b.conn.execute("UPDATE llm_scenario_execution_batches SET status='EXECUTING' WHERE intent_id='a2'")
    elif blocker == "missing_batch":
        b.conn.execute("DELETE FROM llm_scenario_execution_batches WHERE intent_id='a2'")
    elif blocker == "complete_batch":
        b.conn.execute("UPDATE llm_scenario_execution_batches SET status='COMPLETE' WHERE intent_id='a2'")
    elif blocker in {"unknown_old", "unknown_own"}:
        if blocker == "unknown_own":
            b.conn.execute("UPDATE llm_scenario_children SET intent_id='a2' WHERE link_id=?", (old,))
        b.conn.execute("UPDATE llm_scenario_children SET status='UNKNOWN',evidence=NULL WHERE link_id=?", (old,))
        def unknown(child):
            raise BrokerNotReady("unknown_child_evidence")
        b._query_child = unknown
    elif blocker == "nonflat":
        e.fill(old, .1)
    else:
        e.orders["external"] = dict(e.orders[old], orderId="external-id", orderLinkId="external",
                                     orderStatus="New", leavesQty=".1")
    b.conn.commit()
    places = len([w for w in e.writes if w[0] == "place"])
    for _ in range(3):
        result = b.reconcile()
        assert not any(i["intent_id"] == "a2" and i["terminal"] for i in result["intents"])
    assert len([w for w in e.writes if w[0] == "place"]) == places
    if blocker == "crashed":
        assert b.conn.execute("SELECT status FROM llm_scenario_execution_batches WHERE intent_id='a2'").fetchone() == ("INTERRUPTED",)
        proposal = json.loads(b.conn.execute("SELECT payload FROM llm_scenario_intents WHERE id='a2'").fetchone()[0])
        writes = list(e.writes)
        with pytest.raises(BrokerNotReady, match="interrupted_execution_requires_reconciliation"):
            b.execute(proposal, "a2")
        assert e.writes == writes
        assert not any(c["intent_id"] == "a2" for c in b.children())
        assert b.conn.execute("SELECT status FROM llm_scenario_execution_batches WHERE intent_id='a2'").fetchone() == ("INTERRUPTED",)


def test_native_stop_hit_cancels_unfilled_entry_remainder(live):
    b,e=live
    b.execute(persist(b),"a1")
    entry=next(iter(e.orders))
    e.fill(entry,.05)
    b.reconcile()
    e.fill("native",.05)
    b.reconcile()
    assert e.orders[entry]["orderStatus"]=="Cancelled"


def test_emergency_ioc_partial_cancel_reduces_remaining_only_after_terminal_proof(live):
    b,e=live
    b.execute(persist(b),"a1")
    e.fill(next(iter(e.orders)),.1)
    b.reconcile()
    e.fill("native",.05)
    def failed_protection(**kw):
        raise TimeoutError()
    e.set_trading_stop=failed_protection
    b.reconcile()
    emergency=next(c for c in b.children() if c["local_id"]=="hard-stop-race:0")
    e.fill(emergency["link_id"],.02)
    e.orders[emergency["link_id"]]["orderStatus"]="Cancelled"
    b.reconcile()
    second=next(c for c in b.children() if c["local_id"]=="hard-stop-race:1")
    assert float(second["request"]["qty"])==.03
    assert second["request"]["reduceOnly"] is True
    b.reconcile()
    assert len([c for c in b.children() if c["local_id"].startswith("hard-stop-race:")])==2


def test_unknown_financial_context_retains_live_position_and_unknown_costs(live):
    b,e=live
    b.execute(persist(b),"a1")
    e.fill(next(iter(e.orders)),.1)
    b.reconcile()
    def pending(*args):
        raise BrokerNotReady("financial_pending")
    b._risk_accounting=pending
    context=b.context()
    assert context["accounting_status"]=="pending"
    assert context["positions"]==[dict(price=100.,quantity=.1)]
    assert context["fees_paid"] is None and context["realized_loss"] is None
    assert context["new_risk_blocked"] is True


def test_independent_pending_and_recovery_notices_are_stable(live):
    b,e=live
    b.execute(persist(b),"a1")
    e.fill(next(iter(e.orders)),.1)
    normal=b._accounting
    b._accounting=lambda *args:dict(status="pending",settlement=None)
    first=b.reconcile()
    second=b.reconcile()
    assert [n for n in first["notices"] if n["kind"]=="PENDING"]==[n for n in second["notices"] if n["kind"]=="PENDING"]
    assert len([n for n in second["notices"] if n["kind"]=="PENDING"])==1
    b._accounting=normal
    recovered=b.reconcile()
    assert any(n.get("resolution")=="FILLED_PROTECTED" for n in recovered["notices"])


def test_notice_backlog_drains_after_scenario_clears(live):
    from live.scenario_outbox import enqueue
    b,e=live
    b.conn.execute("UPDATE llm_scenario_state SET body=?",(json.dumps(dict(active=None)),))
    for index in range(101):
        event=dict(event_id="backlog"+str(index),kind="PENDING",timestamp=e.now)
        b.conn.execute("INSERT INTO llm_scenario_broker_notices VALUES(?,?,?)",(event["event_id"],"old",json.dumps(event)))
    b.conn.commit()
    batch=b.reconcile()["notices"]
    assert len(batch)==100
    for event in batch:
        enqueue(b.conn,event["event_id"],event)
    b.conn.commit()
    final=b.reconcile()["notices"]
    assert [n["event_id"] for n in final]==["backlog100"]


def test_runtime_broker_partial_adjust_exit_settlement_pipeline(live):
    from live.scenario_runtime import ScenarioRuntime, REQUIRED_CAPABILITIES
    b,e=live
    b.conn.execute("DELETE FROM llm_scenario_state")
    b.conn.execute("DROP TABLE llm_scenario_intents")
    b.conn.commit()
    b.clock=lambda:e.now
    b.capabilities=REQUIRED_CAPABILITIES  # Isolated test only, not deployment enablement.
    action=["OPEN"]
    def proposal(snapshot,context):
        p=dict(schema_version=1,scenario_id=context.get("scenario_id") or "s1",revision=context["revision"]+1,
            input_id=context["input_id"],action_id="decision"+str(e.now),action=action[0],confidence=.8,
            expires_at=e.now+900,rationale="isolated test",leverage=10)
        if action[0] in {"OPEN","ADJUST"}:
            p.update(side="LONG",hard_stop=98 if action[0]=="OPEN" else 99,
                entries=[dict(id="entry1",price=100,quantity=.1)] if action[0]=="OPEN" else [],
                take_profits=[dict(id="tp1",price=105,fraction=.5)],partial_stops=[],chase=dict(max_bps=20,max_reprices=1))
        return p
    rt=ScenarioRuntime(b.conn,b,proposal,lambda:dict(valid=True,as_of_ms=e.now*1000,oldest_captured_at_ms=e.now*1000),clock=lambda:e.now)
    first=rt.tick()
    assert first["status"]=="intent_pending",first
    e.fill(next(iter(e.orders)),.05)
    e.now+=301
    action[0]="ADJUST"
    adjusted=rt.tick()
    assert adjusted["status"]=="intent_pending",adjusted
    assert e.stop=="99",(adjusted,rt.state(),b.children(),e.writes)
    b.reconcile()
    tp=next(c for c in reversed(b.children()) if c["kind"]=="tp" and c["status"]=="LIVE")
    e.fill(tp["link_id"],float(tp["request"]["qty"]))
    e.now+=301
    action[0]="EXIT"
    exited=rt.tick()
    assert exited["status"]=="intent_pending",exited
    exit_child=next(c for c in b.children() if c["kind"]=="exit")
    e.fill(exit_child["link_id"],float(exit_child["request"]["qty"]))
    e.now+=301
    action[0]="WAIT"
    final=rt.tick()
    assert rt.state()["active"] is None,(final,b.reconcile())
    assert rt.state()["breaker"]["consecutive_losses"]==1
    notice_bodies=[json.loads(r[0]) for r in b.conn.execute("SELECT body FROM llm_scenario_broker_notices")]
    assert {"FILLED","PARTIAL","PROTECTION","CLOSED"} <= {n["kind"] for n in notice_bodies}


def test_runtime_aborted_adjust_settles_no_fills_then_requests_fresh_model(live):
    from live.scenario_runtime import ScenarioRuntime, REQUIRED_CAPABILITIES
    b, e = live
    b.conn.execute("DELETE FROM llm_scenario_state")
    b.conn.execute("DROP TABLE llm_scenario_intents")
    b.conn.commit()
    b.clock = lambda: e.now
    b.capabilities = REQUIRED_CAPABILITIES
    calls = []
    def proposal(snapshot, context):
        action = ("OPEN", "ADJUST", "WAIT")[len(calls)]
        calls.append(context)
        p = dict(schema_version=1, scenario_id=context.get("scenario_id") or "s1",
                 revision=context["revision"]+1, input_id=context["input_id"],
                 action_id="decision"+str(e.now), action=action, confidence=.8,
                 expires_at=e.now+900, rationale="isolated cancellation regression", leverage=10)
        if action != "WAIT":
            p.update(side="LONG", hard_stop=98,
                     entries=[dict(id="entry1" if action == "OPEN" else "entry2",
                                   price=100 if action == "OPEN" else 99, quantity=.1)],
                     cancel_entry_ids=[] if action == "OPEN" else ["entry1"],
                     take_profits=[], partial_stops=[], chase=dict(max_bps=0, max_reprices=0))
        return p
    rt = ScenarioRuntime(b.conn, b, proposal,
        lambda: dict(valid=True, as_of_ms=e.now*1000), clock=lambda: e.now)
    assert rt.tick()["status"] == "intent_pending"
    old = next(iter(e.orders))
    e.cancel_order = lambda **kw: dict(retCode=0, result={})
    e.now += 301
    assert rt.tick()["status"] == "intent_pending"
    assert len(calls) == 2
    e.orders[old]["orderStatus"] = "Cancelled"
    writes = list(e.writes)
    e.now += 301
    assert rt.tick()["status"] == "wait"
    assert len(calls) == 3 and calls[-1]["scenario_id"] is None
    assert rt.state()["active"] is None
    assert rt.state()["breaker"].get("consecutive_losses", 0) == 0
    assert e.writes == writes
