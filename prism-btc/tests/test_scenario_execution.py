"""No-network executor invariants, including crash/ACK-loss recovery."""
import json
import sqlite3
import copy

import pytest

from live.scenario_broker import BrokerNotReady, ScenarioDemoBroker
from tests.test_scenario_broker import Session, reply


def notice_fixture():
    active=dict(scenario_id="notice",side="LONG",initial_equity=10000,hard_stop=98)
    observed=dict(captured_at=1800000000,equity=10000,exchange_flat=False,legacy_fenced=False,
        position=dict(size="1",avgPrice="101",stopLoss="98",leverage="10",side="Buy",positionIM="10.1"))
    children=[dict(kind="tp",status="LIVE",order_id="tp1",evidence=dict(executions=[],
        order=dict(price="105",leavesQty="0.5")))]
    return active,children,observed


def flat_notice_fixture(broker, *, leverage="10", known_entry=True):
    active, _, observed = notice_fixture()
    observed.update(exchange_flat=True)
    observed["position"].update(size="0", avgPrice="", stopLoss="", leverage=leverage, side="")
    children = [dict(kind=kind, status="TERMINAL", evidence=dict(order={}, executions=[dict(
        execId=eid, execQty="1", execPrice=price, execTime=when)]))
        for kind, eid, price, when in (("entry", "old-entry", "100", "1799999000000"),
                                      ("exit", "final-exit", "99", "1799999999000"))]
    if known_entry:
        event = dict(event_id="scenario-fill-old-entry", kind="FILLED")
        broker.conn.execute("INSERT INTO llm_scenario_broker_notices VALUES(?,?,?)",
                            (event["event_id"], active["scenario_id"], json.dumps(event)))
        broker.conn.execute("CREATE TABLE llm_scenario_outbox(event_id TEXT PRIMARY KEY)")
        broker.conn.execute("INSERT INTO llm_scenario_outbox VALUES(?)", (event["event_id"],))
    settlement = dict(execution_ids=["old-entry", "final-exit"], net_pnl=-1.1, fees=.1, funding_net=0)
    return active, children, observed, settlement


@pytest.mark.parametrize("leverage", ["10", ""])
def test_flat_blank_optional_fields_close_once_without_duplicate_partial(live, leverage):
    broker, exchange = live
    active, children, observed, settlement = flat_notice_fixture(broker, leverage=leverage)
    events = broker._notices(active, children, observed, True, settlement)
    assert [event["kind"] for event in events] == ["CLOSED"]
    assert events[0]["net_pnl"] == -1.1
    assert not broker.conn.execute("SELECT 1 FROM llm_scenario_broker_notices WHERE event_id='scenario-fill-final-exit'").fetchone()
    broker.conn.execute("INSERT INTO llm_scenario_outbox VALUES(?)", (events[0]["event_id"],))
    broker.conn.commit()
    restarted = ScenarioDemoBroker(broker.conn, session=exchange, expected_main_uid="123")
    assert restarted._notices(active, children, observed, True, settlement) == []
    assert exchange.writes == []


def test_flat_known_historical_fill_skipped_before_optional_enrichment(live):
    broker, _ = live
    active, children, observed, _ = flat_notice_fixture(broker, leverage="")
    assert broker._notices(active, children[:1], observed, True, None) == []


def test_flat_unsettled_exit_keeps_generic_notice_without_old_stop(live):
    broker, _ = live
    active, children, observed, _ = flat_notice_fixture(broker, leverage="")
    events = broker._notices(active, children, observed, True, None, pending=True)
    exit_event = next(event for event in events if event["kind"] == "PARTIAL")
    assert exit_event["hard_stop"] is None
    assert exit_event["exchange_leverage"] is None
    assert exit_event["settlement_confirmed"] is False
    assert any(event["kind"] == "PENDING" for event in events)


def test_prior_partial_notice_preserved_when_final_settlement_arrives(live):
    broker, _ = live
    active, children, observed, settlement = flat_notice_fixture(broker)
    before = broker._notices(active, children, observed, True, None, pending=True)
    partial = next(event for event in before if event["kind"] == "PARTIAL")
    for event in before:
        broker.conn.execute("INSERT INTO llm_scenario_outbox VALUES(?)", (event["event_id"],))
    after = broker._notices(active, children, observed, True, settlement)
    assert [event["kind"] for event in after if event["kind"] != "RESOLVED"] == ["CLOSED"]
    stored = json.loads(broker.conn.execute("SELECT body FROM llm_scenario_broker_notices WHERE event_id=?",
                                           (partial["event_id"],)).fetchone()[0])
    assert stored == partial


def test_notice_economic_changes_durable_and_no_exchange_writes(live):
    b,e=live
    active,children,observed=notice_fixture()
    first=b._notices(active,children,observed,True,None)
    assert first==[]
    assert b.conn.execute("SELECT revision FROM llm_scenario_notice_positions").fetchone()[0]==1
    children[0]["evidence"]["order"]["price"]="106"
    observed["captured_at"]+=300
    second=b._notices(active,children,observed,True,None)[-1]
    assert second["position_before"]["take_profits"][0]["price"]==105
    assert second["position_after"]["take_profits"][0]["price"]==106
    observed["position"].update(size="2",avgPrice="102",stopLoss="99")
    third=b._notices(active,children,observed,True,None)[-1]
    assert third["position_before"]["hard_stop"]==98
    assert third["position_after"]["hard_stop"]==99
    assert third["position_after"]["average_entry_price"]==102
    assert third["position_after"]["quantity"]==2
    children[0]["order_id"]="replacement-id"
    observed.update(equity=9999,captured_at=observed["captured_at"]+300)
    restarted=ScenarioDemoBroker(b.conn,session=e,expected_main_uid="123")
    assert len(restarted._notices(active,children,observed,True,None))==2
    assert e.writes==[]


def test_initial_fill_seeds_snapshot_without_duplicate_protection(live):
    b,e=live
    active,children,observed=notice_fixture()
    children.append(dict(kind="entry",status="TERMINAL",intent_id="none",evidence=dict(order={},
        executions=[dict(execId="new-fill",execQty="1",execPrice="100",execTime="1800000000000")])))
    events=b._notices(active,children,observed,True,None)
    assert [n["kind"] for n in events]==["FILLED"]
    assert events[0]["price"]==100
    assert events[0]["position_after"]["average_entry_price"]==101
    assert len(b._notices(active,children,observed,True,None))==1
    assert e.writes==[]


def test_notice_entry_stages_follow_exact_fill_history_not_child_order(live):
    b,e=live
    active,children,observed=notice_fixture()
    children.append(dict(kind="entry",status="TERMINAL",intent_id="none",evidence=dict(order={},
        executions=[dict(execId="second",execQty="0.4",execPrice="101",execTime="1800000000000"),
                    dict(execId="first",execQty="0.6",execPrice="100",execTime="1799999999000")])))
    events=b._notices(active,children,observed,True,None)
    assert [n["event_id"] for n in events]==["scenario-fill-first","scenario-fill-second"]
    by_id={n["event_id"]:n for n in events}
    assert by_id["scenario-fill-first"]["entry_stage"]=="initial"
    assert by_id["scenario-fill-second"]["entry_stage"]=="additional"
    assert by_id["scenario-fill-second"]["entry_timestamp"]==1799999999
    # Stable labels through restart/repeated reconciliation; no new order.
    assert b._notices(active,list(reversed(children)),observed,True,None)==events
    assert e.writes==[]


def test_pending_evidence_does_not_claim_first_entry(live):
    b,e=live
    active,children,observed=notice_fixture()
    children.append(dict(kind="entry",status="TERMINAL",intent_id="none",evidence=dict(order={},
        executions=[dict(execId="only-known",execQty="1",execPrice="100",execTime="1800000000000")])))
    events=b._notices(active,children,observed,True,None,pending=True)
    assert "entry_stage" not in next(n for n in events if n["kind"]=="FILLED")
    assert e.writes==[]


def test_existing_position_baseline_never_reannounces_old_entry(live):
    b,e=live
    active,children,observed=notice_fixture()
    children.append(dict(kind="entry",status="TERMINAL",intent_id="none",evidence=dict(order={},
        executions=[dict(execId="old-entry",execQty="1",execPrice="100",execTime="1799999000000")])))
    # The original fill was already announced before snapshot tracking existed.
    old_fill=dict(event_id="scenario-fill-old-entry",kind="FILLED")
    b.conn.execute("INSERT INTO llm_scenario_broker_notices VALUES(?,?,?)",
        (old_fill["event_id"],active["scenario_id"],json.dumps(old_fill)))
    b.conn.execute("CREATE TABLE llm_scenario_outbox(event_id TEXT PRIMARY KEY)")
    b.conn.execute("INSERT INTO llm_scenario_outbox VALUES(?)",(old_fill["event_id"],))
    assert b._notices(active,children,observed,True,None)==[]
    baseline=json.loads(b.conn.execute("SELECT body FROM llm_scenario_notice_positions").fetchone()[0])
    assert baseline["quantity"]==1
    b.conn.commit()
    restarted=ScenarioDemoBroker(b.conn,session=e,expected_main_uid="123")
    observed["captured_at"]+=300
    observed["equity"]+=10
    observed["position"]["positionIM"]="11"
    children[0]["order_id"]="replacement-id"
    assert restarted._notices(active,children,observed,True,None)==[]
    assert b.conn.execute("SELECT count(*) FROM llm_scenario_broker_notices").fetchone()[0]==1
    # A genuine subsequent TP change still produces one before/after event.
    children[0]["evidence"]["order"]["price"]="106"
    changed=restarted._notices(active,children,observed,True,None)
    assert len(changed)==1 and changed[0]["kind"]=="PROTECTION"
    assert changed[0]["position_before"]["take_profits"][0]["price"]==105
    assert changed[0]["position_after"]["take_profits"][0]["price"]==106
    b.conn.execute("INSERT INTO llm_scenario_outbox VALUES(?)",(changed[0]["event_id"],))
    assert restarted._notices(active,children,observed,True,None)==[]
    assert e.writes==[]


def test_flat_baseline_then_first_fill_has_one_rich_notice(live):
    b,e=live
    b.execute(persist(b),"a1")
    b.reconcile()
    baseline=json.loads(b.conn.execute("SELECT body FROM llm_scenario_notice_positions").fetchone()[0])
    assert baseline["quantity"]==0
    e.fill(next(iter(e.orders)),.1)
    events=b.reconcile()["notices"]
    assert [n["kind"] for n in events]==["FILLED"]
    assert events[0]["position_before"]["quantity"]==0
    assert events[0]["position_after"]["quantity"]==.1


@pytest.mark.parametrize("kind,quantity,notice_kind",[("entry","2","FILLED"),("tp","0.5","PARTIAL")])
def test_new_fill_compares_valid_baseline_without_duplicate_protection(live,kind,quantity,notice_kind):
    b,e=live
    active,children,observed=notice_fixture()
    b._notices(active,children,observed,True,None)
    observed["captured_at"]+=60
    observed["position"].update(size=quantity,avgPrice="102")
    children.append(dict(kind=kind,status="TERMINAL",intent_id="none",evidence=dict(order={},
        executions=[dict(execId="added-fill",execQty="1",execPrice="103",execTime="1800000060000")])))
    events=b._notices(active,children,observed,True,None)
    assert [n["kind"] for n in events]==[notice_kind]
    assert events[-1]["position_before"]["quantity"]==1
    assert events[-1]["position_after"]["quantity"]==float(quantity)


def test_fill_before_baseline_timestamp_cannot_claim_baseline_as_before(live):
    b,e=live
    active,children,observed=notice_fixture()
    b._notices(active,children,observed,True,None)
    observed["captured_at"]+=30
    observed["position"].update(size="2",avgPrice="102")
    children.append(dict(kind="entry",status="TERMINAL",intent_id="none",evidence=dict(order={},
        executions=[dict(execId="late-evidence",execQty="1",execPrice="103",execTime="1799999990000")])))
    events=b._notices(active,children,observed,True,None)
    fill=next(n for n in events if n["kind"]=="FILLED")
    assert "position_before" not in fill
    assert events[-1]["kind"]=="PROTECTION"


def test_notice_unknown_evidence_does_not_invent_targets_or_costs(live):
    from live.scenario_notice_evidence import position_snapshot
    b,e=live
    active,children,observed=notice_fixture()
    snap=position_snapshot(active,children,observed,True,False,None)
    assert "scenario_risk" not in snap
    observed["position"].pop("positionIM")
    assert position_snapshot(active,children,observed,True,False,None)["account_snapshot"]["position_margin"] is None
    bad=copy.deepcopy(children)
    bad[0]["evidence"]=None
    assert position_snapshot(active,bad,observed,True,False,None) is None
    assert position_snapshot(active,children,observed,True,True,None) is None
    assert not b._notices(active,children,observed,False,None)


def test_recovered_old_fill_has_no_current_account_snapshot(live):
    b,e=live
    active,children,observed=notice_fixture()
    children.append(dict(kind="entry",status="TERMINAL",intent_id="none",evidence=dict(order={},
        executions=[dict(execId="old-fill",execQty="1",execPrice="100",execTime="1799999000000")])))
    events=b._notices(active,children,observed,True,None)
    fill=next(n for n in events if n["kind"]=="FILLED")
    assert "position_after" not in fill and "account_snapshot" not in fill
    assert [n["kind"] for n in events]==["FILLED"]


def test_notice_risk_uses_confirmed_costs_and_pending_entries():
    from live.scenario_notice_evidence import position_snapshot
    from core.llm_scenario import risk_snapshot
    active,children,observed=notice_fixture()
    children.append(dict(kind="entry",status="LIVE",evidence=dict(executions=[],order=dict(price="100",leavesQty="0.2"))))
    accounting=dict(status="confirmed",positions=[dict(price=101,quantity=1)],realized_loss=3,fees_paid=2,funding_paid=1)
    snap=position_snapshot(active,children,observed,True,False,accounting)
    expected=risk_snapshot(initial_equity=10000,side="LONG",hard_stop=98,positions=accounting["positions"],
        pending_entries=[dict(price=100,quantity=.2)],entries=[],realized_loss=3,fees_paid=2,funding_paid=1,estimated_cost_rate=.002,slippage_bps=20)
    assert snap["scenario_risk"]==expected["total_risk"]
    assert snap["scenario_risk_includes_pending"] is True


@pytest.mark.parametrize("side,tp,stops",[("LONG",[105,110],[99,98]),("SHORT",[95,90],[103,104])])
def test_notice_targets_display_nearest_first(side,tp,stops):
    from live.scenario_notice_evidence import position_snapshot
    active,_,observed=notice_fixture()
    active["side"]=side
    observed["position"]["side"]="Buy" if side=="LONG" else "Sell"
    children=[]
    for kind,prices,field in [("tp",tp,"price"),("partial_sl",stops,"triggerPrice")]:
        for price in reversed(prices):
            children.append(dict(kind=kind,status="LIVE",evidence=dict(executions=[],
                order={field:str(price),"leavesQty":"0.1"})))
    snap=position_snapshot(active,children,observed,True,False,None)
    assert [r["price"] for r in snap["take_profits"]]==tp
    assert [r["price"] for r in snap["partial_stops"]]==stops


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
                side="Sell",reduceOnly=True,stopOrderType="StopLoss",orderType="Market",triggerBy="MarkPrice",triggerPrice=self.stop,
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


def probe_permit(b, payload):
    from live.scenario_recovery import ensure_schema
    ensure_schema(b.conn)
    state = json.loads(b.conn.execute("SELECT body FROM llm_scenario_state WHERE id=1").fetchone()[0])
    state["breaker"] = dict(blocked=True, reasons=["three_losses"], consecutive_losses=3)
    state["recovery"] = dict(phase="CONSUMED", scenario_id="s1", action_id=payload["action_id"],
        side="LONG", initial_equity=10000, budget=50, entry_deadline=1800000300,
        initial_entries=payload["entries"], permit_id="test-permit")
    b.conn.execute("UPDATE llm_scenario_state SET body=? WHERE id=1", (json.dumps(state),))
    b.conn.commit()
    return state


def test_probe_initial_batch_retained_and_original_equity_not_scaled(live):
    b, e = live
    p = persist(b, chase=dict(max_bps=0, max_reprices=0), entries=[
        dict(id="e1", price=100, quantity=.1), dict(id="e2", price=99, quantity=.1)])
    probe_permit(b, p)
    b.execute(p, "a1")
    b.reconcile()
    assert len([w for w in e.writes if w[0] == "place"]) == 2
    assert not any(w[0] == "cancel" for w in e.writes)
    ctx = b.context()
    assert ctx["initial_equity"] == 10000
    assert ctx["scenario_risk_fraction"] == .005


@pytest.mark.parametrize("reason", ["expiry", "paused", "daily_loss", "wrong_action", "wrong_side"])
def test_probe_pending_hard_guards_cancel_original(live, reason):
    b, e = live
    p = persist(b, chase=dict(max_bps=0, max_reprices=0))
    state = probe_permit(b, p)
    b.execute(p, "a1")
    if reason == "expiry":
        b.clock = lambda: 1800000301
    elif reason == "paused":
        from live.scenario_control import _write
        _write(b.conn, dict(version=1, state="paused", main_uid="123"))
    elif reason == "daily_loss":
        state["breaker"]["reasons"].append("daily_loss")
    elif reason == "wrong_action":
        state["recovery"]["action_id"] = "other"
    else:
        state["recovery"]["side"] = "SHORT"
    b.conn.execute("UPDATE llm_scenario_state SET body=? WHERE id=1", (json.dumps(state),))
    b.conn.commit()
    b.reconcile()
    assert any(w[0] == "cancel" for w in e.writes)


def test_probe_fresh_preflight_rejects_2percent_sized_order(live):
    b, e = live
    p = persist(b, chase=dict(max_bps=0, max_reprices=0),
                entries=[dict(id="e1", price=100, quantity=25)])
    probe_permit(b, p)
    with pytest.raises(BrokerNotReady, match="fresh_risk_budget_exceeded"):
        b.execute(p, "a1")
    assert not e.writes


def test_probe_new_adjust_entry_and_chase_forbidden(live):
    b, e = live
    p = persist(b, chase=dict(max_bps=0, max_reprices=0))
    probe_permit(b, p)
    b.execute(p, "a1")
    with pytest.raises(BrokerNotReady, match="recovery_probe_chase_forbidden"):
        b.chase("a1", "e1", 100.1)
    adjust = persist(b, action="ADJUST", action_id="a2", entries=[dict(id="extra", price=99, quantity=.1)])
    with pytest.raises(BrokerNotReady, match="new_risk_halted"):
        b.execute(adjust, "a2")
    assert len([w for w in e.writes if w[0] == "place"]) == 1


def test_probe_expired_permit_preserves_management(live):
    b, e = live
    p = persist(b, chase=dict(max_bps=0, max_reprices=0))
    state = probe_permit(b, p)
    b.execute(p, "a1")
    e.fill(next(iter(e.orders)), .1)
    b.reconcile()
    state["recovery"]["entry_deadline"] = 1799999999
    b.conn.execute("UPDATE llm_scenario_state SET body=? WHERE id=1", (json.dumps(state),))
    b.conn.commit()
    adjust = persist(b, action="ADJUST", action_id="a2", hard_stop=99, entries=[])
    b.execute(adjust, "a2")
    assert e.stop == "99"
    exit_plan = persist(b, action="EXIT", action_id="a3", entries=[])
    b.execute(exit_plan, "a3")
    assert any(w[0] == "place" and w[1].get("reduceOnly") is True for w in e.writes)


def test_probe_short_initial_reservation_is_symmetric(live):
    b, e = live
    p = persist(b, side="SHORT", hard_stop=102, chase=dict(max_bps=0, max_reprices=0))
    state = probe_permit(b, p)
    state["active"].update(side="SHORT", hard_stop=102)
    state["recovery"]["side"] = "SHORT"
    b.conn.execute("UPDATE llm_scenario_state SET body=? WHERE id=1", (json.dumps(state),))
    b.conn.commit()
    b.execute(p, "a1")
    b.reconcile()
    assert any(w[0] == "place" and w[1]["side"] == "Sell" for w in e.writes)
    assert not any(w[0] == "cancel" for w in e.writes)


def test_probe_cost_overrun_cancels_remaining_reservation_not_protection(live):
    b, e = live
    p = persist(b, chase=dict(max_bps=0, max_reprices=0))
    probe_permit(b, p)
    b.execute(p, "a1")
    e.fill(next(iter(e.orders)), .05)
    e.fills[-1]["execFee"] = "60"
    result = b.reconcile()
    assert any(w[0] == "cancel" for w in e.writes)
    assert result["protection_confirmed"] is True
    assert e.orders["native"]["orderStatus"] == "Untriggered"


@pytest.mark.parametrize("probe,budget", [(False, 200), (True, 50)])
def test_fill_notice_uses_host_budget_and_unscaled_initial_equity(live, probe, budget):
    b, e = live
    p = persist(b, chase=dict(max_bps=0, max_reprices=0))
    if probe:
        probe_permit(b, p)
    b.execute(p, "a1")
    e.fill(next(iter(e.orders)), .1)
    result = b.reconcile()
    notice = next(n for n in result["notices"] if n["kind"] == "FILLED")
    assert notice["scenario_budget"] == budget
    assert notice["position_after"]["scenario_budget"] == budget
    assert notice["position_after"]["scenario_initial_equity"] == 10000
    assert notice["position_after"]["account_snapshot"]["equity"] == 10000


@pytest.mark.parametrize("fraction", [True, 0, -.005, .03, float("nan")])
def test_notice_rejects_invalid_host_budget_fraction(fraction):
    from live.scenario_notice_evidence import position_snapshot
    active, children, observed = notice_fixture()
    assert position_snapshot(active, children, observed, True, False, None,
                             scenario_risk_fraction=fraction) is None


def test_probe_required_request_journal_failure_denies_entry(live, monkeypatch):
    from live import scenario_recovery
    b, e = live
    p = persist(b, chase=dict(max_bps=0, max_reprices=0))
    probe_permit(b, p)
    def fail(*args, **kwargs):
        raise ValueError("write failure")
    monkeypatch.setattr(scenario_recovery, "journal", fail)
    with pytest.raises(BrokerNotReady, match="recovery_entry_audit_unavailable"):
        b.execute(p, "a1")
    assert not e.writes
    assert b._recovery_state()["recovery"]["phase"] == "CONSUMED"


def test_probe_response_journal_failure_never_reposts(live, monkeypatch, caplog):
    from live import scenario_recovery
    b, e = live
    p = persist(b, chase=dict(max_bps=0, max_reprices=0))
    probe_permit(b, p)
    original = scenario_recovery.journal
    def response_failure(conn, kind, *args, **kwargs):
        if kind == "EXCHANGE_RESPONSE":
            raise ValueError("response audit unavailable")
        return original(conn, kind, *args, **kwargs)
    monkeypatch.setattr(scenario_recovery, "journal", response_failure)
    b.execute(p, "a1")
    b.execute(p, "a1")
    assert len([w for w in e.writes if w[0] == "place"]) == 1
    assert "AUDIT_GAP" in caplog.text
    assert b.conn.execute("SELECT count(*) FROM llm_scenario_recovery_events WHERE kind='EXCHANGE_REQUEST'").fetchone()[0] == 1


def test_probe_audit_failure_does_not_block_protection(live, monkeypatch, caplog):
    from live import scenario_recovery
    b, e = live
    p = persist(b, chase=dict(max_bps=0, max_reprices=0))
    probe_permit(b, p)
    b.execute(p, "a1")
    e.fill(next(iter(e.orders)), .05)
    b.reconcile()
    def fail(*args, **kwargs):
        raise ValueError("audit unavailable")
    monkeypatch.setattr(scenario_recovery, "journal", fail)
    adjust = persist(b, action="ADJUST", action_id="a2", hard_stop=99, entries=[])
    b.execute(adjust, "a2")
    assert e.stop == "99"
    assert any(w[0] == "cancel" for w in e.writes)
    assert "AUDIT_GAP" in caplog.text


@pytest.mark.parametrize("lost_ack", [False, True])
def test_probe_exchange_audit_links_request_and_result(live, lost_ack):
    b, e = live
    p = persist(b, chase=dict(max_bps=0, max_reprices=0))
    probe_permit(b, p)
    e.lose_ack = lost_ack
    if lost_ack:
        with pytest.raises(BrokerNotReady, match="submission_unknown"):
            b.execute(p, "a1")
    else:
        b.execute(p, "a1")
    rows = [(kind, json.loads(body)) for kind, body in b.conn.execute(
        "SELECT kind,body FROM llm_scenario_recovery_events ORDER BY rowid")]
    assert [kind for kind, _ in rows] == ["EXCHANGE_REQUEST", "EXCHANGE_ERROR" if lost_ack else "EXCHANGE_RESPONSE"]
    assert rows[0][1]["call_id"] == rows[1][1]["call_id"]
    assert rows[0][1]["params"] == e.writes[0][1]
    assert rows[0][1]["permit_id"] == "test-permit"
    if lost_ack:
        assert rows[1][1]["error"] == "submission_unknown"
    else:
        assert rows[1][1]["response"]["retCode"] == 0


def test_exchange_audit_state_read_failure_blocks_entry_only(live, monkeypatch, caplog):
    b, e = live
    p = persist(b, chase=dict(max_bps=0, max_reprices=0))
    probe_permit(b, p)
    b.execute(p, "a1")
    link = next(iter(e.orders))
    e.fill(link, .05)
    def unavailable():
        raise sqlite3.OperationalError("read failed")
    monkeypatch.setattr(b, "_recovery_state", unavailable)
    before = len(e.writes)
    with pytest.raises(BrokerNotReady, match="recovery_entry_audit_unavailable"):
        b._write("place_order", reduceOnly=False)
    assert len(e.writes) == before
    b._write("set_trading_stop", stopLoss="99")
    assert e.stop == "99"
    b._write("cancel_order", orderLinkId=link)
    assert e.orders[link]["orderStatus"] == "Cancelled"
    b._write("place_order", reduceOnly=True, orderLinkId="protective-exit", qty=".05")
    assert "protective-exit" in e.orders
    assert "AUDIT_GAP recovery_exchange_state_unavailable" in caplog.text


@pytest.mark.parametrize("failure",["snapshot","render","state_sql"])
def test_optional_notice_failure_rolls_back_only_notices_and_retries(live,monkeypatch,failure):
    import live.scenario_notice as notice
    import live.scenario_notice_evidence as evidence
    b,e=live
    b.execute(persist(b),"a1")
    link=next(iter(e.orders))
    e.fill(link,.05)
    e.fill(link,.05)
    writes=list(e.writes)
    with monkeypatch.context() as patch:
        if failure=="snapshot":
            def broken(*args,**kwargs):
                raise ValueError("sensitive internal details")
            patch.setattr(evidence,"position_snapshot",broken)
        elif failure=="render":
            original=notice.render_notice
            calls=[]
            def broken(event):
                calls.append(event)
                if len(calls)==2:
                    raise ValueError("sensitive internal details")
                return original(event)
            patch.setattr(notice,"render_notice",broken)
        else:
            b.conn.execute("CREATE TEMP TRIGGER fail_notice_state BEFORE INSERT ON llm_scenario_notice_positions BEGIN SELECT RAISE(ABORT,'notice write unavailable'); END")
            b.conn.commit()
        result=b.reconcile()
    assert result["protection_confirmed"] is True
    assert result["notice_error"]=="optional_notice_capture_failed"
    assert result["notices"]==[]
    assert result["intents"][0]["filled_quantity"]==.1
    assert b.conn.execute("SELECT count(*) FROM llm_scenario_broker_notices").fetchone()[0]==0
    assert b.conn.execute("SELECT count(*) FROM llm_scenario_notice_positions").fetchone()[0]==0
    assert b.conn.execute("SELECT count(*) FROM llm_scenario_broker_evidence WHERE kind='scenario_accounting'").fetchone()[0]>0
    assert e.writes==writes
    if failure=="state_sql":
        b.conn.execute("DROP TRIGGER fail_notice_state")
        b.conn.commit()
    retried=b.reconcile()
    assert retried["notice_error"] is None
    assert len([n for n in retried["notices"] if n["kind"]=="FILLED"])==2
    assert b.conn.execute("SELECT count(*) FROM llm_scenario_notice_positions").fetchone()[0]==1
    assert e.writes==writes


def test_paused_flat_account_still_has_verified_no_exposure(live):
    from live.scenario_control import _write
    b,e=live
    b.conn.execute("UPDATE llm_scenario_state SET body=?",(json.dumps(dict(active=None)),));b.conn.commit()
    _write(b.conn,dict(version=1,state="paused",main_uid="123"))
    result=b.reconcile()
    assert result["protection_confirmed"] is True
    assert result["protection_status"]=="verified_flat_no_exposure"
    assert not e.writes


def test_notice_savepoint_failure_falls_back_to_optional_transaction_rollback(live,monkeypatch):
    import live.scenario_notice as notice
    b,e=live
    b.execute(persist(b),"a1")
    link=next(iter(e.orders));e.fill(link,.05);e.fill(link,.05)
    original=b.conn
    class Connection:
        def __getattr__(self,name):return getattr(original,name)
        def execute(self,sql,*args):
            if sql.startswith("ROLLBACK TO SAVEPOINT scenario_notice_capture"):
                raise sqlite3.OperationalError("savepoint unavailable")
            return original.execute(sql,*args)
    b.conn=Connection()
    render=notice.render_notice;calls=[]
    def fail_second(event):
        calls.append(event)
        if len(calls)==2:raise ValueError("invalid optional notice")
        return render(event)
    monkeypatch.setattr(notice,"render_notice",fail_second)
    writes=list(e.writes)
    result=b.reconcile()
    assert result["protection_confirmed"] is True
    assert result["notice_error"]=="optional_notice_capture_failed"
    assert original.execute("SELECT count(*) FROM llm_scenario_broker_notices").fetchone()[0]==0
    assert original.execute("SELECT count(*) FROM llm_scenario_broker_evidence WHERE kind='scenario_accounting'").fetchone()[0]>0
    assert not original.in_transaction and e.writes==writes


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
    # Real Bybit flat rows clear optional position prices, not numeric zeroes.
    original_positions=e.get_positions
    def flat_positions(**kwargs):
        response=original_positions(**kwargs)
        response["result"]["list"][0].update(stopLoss="",avgPrice="")
        return response
    e.get_positions=flat_positions
    e.now+=301
    action[0]="WAIT"
    final=rt.tick()
    assert rt.state()["active"] is None,(final,b.reconcile())
    assert rt.state()["breaker"]["consecutive_losses"]==1
    notice_bodies=[json.loads(r[0]) for r in b.conn.execute("SELECT body FROM llm_scenario_broker_notices")]
    assert {"FILLED","PARTIAL","PROTECTION","CLOSED"} <= {n["kind"] for n in notice_bodies}
    assert len([n for n in notice_bodies if n["kind"]=="CLOSED"])==1
    assert not any(n["event_id"]=="scenario-fill-"+e.fills[-1]["execId"] for n in notice_bodies)


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
