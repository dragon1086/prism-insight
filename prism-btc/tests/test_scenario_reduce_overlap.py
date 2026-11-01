"""A reduction remains possible after a separate partial-stop fill."""
import json
import pytest
from live.scenario_broker import BrokerNotReady, ScenarioDemoBroker
from tests import test_scenario_execution as execution_tests
from tests.test_scenario_execution import persist
from tests.test_scenario_immediate_reduce_execution import auto_fill, market_posts


@pytest.fixture(name='live')
def execution_fixture(tmp_path):
    return execution_tests.live.__wrapped__(tmp_path)


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
