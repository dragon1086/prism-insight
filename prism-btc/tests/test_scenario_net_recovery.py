import sqlite3
import json
from copy import deepcopy

import pytest

from live import scenario_recovery as recovery
from tests.test_scenario_recovery import probe  # noqa: F401
from tests.test_scenario_runtime import proposal, wait_proposal
from tests.test_scenario_normalization import open_probe, finish


@pytest.fixture
def automatic_net(probe):  # noqa: F811
    r,b,now,price=probe
    r.automatic_normalization_enabled=True
    def model(s,c):
        recovery.record_model_request(system_prompt='test',user_prompt=json.dumps(c),response_schema={})
        first=c.get('recovery',{}).get('first_observation',False)
        p=wait_proposal(s,c) if first else proposal(s,c)
        p.update(action_id='a'+str(int(now[0])),scenario_id=c['scenario_id'] or 's'+str(int(now[0])))
        if not first:
            p['chase']={'max_bps':0,'max_reprices':0}
        p['recovery']=dict(decision='OBSERVE' if first else 'PROBE',reason='test',
            changed_evidence=[] if first else ['timeframes.30m.closed.close'],counterevidence='test',invalidation='test')
        recovery.record_model_wire(json.dumps(p))
        return p
    r.propose=model
    return r,b,now,price


def evidence_for(pnls):
    rows=[dict(scenario_id=f's{i}',settlement_hash=str(i)*64,net_pnl=pnl,settled_at=i+1) for i,pnl in enumerate(pnls)]
    e=recovery._stage_evidence(10000,rule_version=3,window_origin='a'*64)
    e.update(window=rows,**recovery._window_metrics(rows))
    return e


@pytest.mark.parametrize('pnls,offered', [([15,-2,15],1),([2,2,-15],0),([-51,30,30],0),([10,-10,10],1),
    ([-3,-2,20,-1,20],1)])
def test_cost_net_and_drawdown_not_win_streak(pnls,offered):
    assert recovery.offered_stage(dict(policy_version=2,stage=0,stage_evidence=evidence_for(pnls)))==offered


def test_gross_profit_erased_by_fees_is_not_a_positive_settlement():
    from tests.test_scenario_runtime import settlement
    proof=settlement(gross_pnl=1,fees=2,funding_net=0,net_pnl=-1)
    assert recovery._confirmed(proof,proof['scenario_id'])
    e=evidence_for([proof['net_pnl']])
    assert e['positive_count']==0 and e['net_losses']==1


def test_rolling_expiry_and_initial_zero_drawdown():
    e=evidence_for([-10,2,2,2,2,2])
    assert e['max_realized_drawdown']==10
    assert e['net_pnl']==0
    # Rolling calculation starts a new zero baseline but keeps the stage anchor.
    rows=(e['window']+[dict(scenario_id='s6',settlement_hash='a'*64,net_pnl=-2,settled_at=7)])[-6:]
    m=recovery._window_metrics(rows)
    assert m['net_pnl']==8 and m['max_realized_drawdown']==2
    old=evidence_for([100,0,0,0,0,-10])
    tail=(old['window']+[dict(scenario_id='s6',settlement_hash='a'*64,net_pnl=0,settled_at=7)])[-6:]
    assert recovery._window_metrics(tail)['net_pnl']==-10
    assert recovery._window_metrics(tail)['positive_count']==0


@pytest.mark.parametrize('change', [lambda e:e.update(net_pnl=999.),lambda e:e.update(positive_count=True),
    lambda e:e['window'][0].update(settlement_hash='bad'),lambda e:e['window'].append(deepcopy(e['window'][0])),
    lambda e:e['window'][0].update(settled_at=-1),lambda e:e['window'][0].update(net_pnl=float('nan'))])
def test_malformed_evidence_fails_closed(change):
    e=evidence_for([10,10])
    change(e)
    with pytest.raises(ValueError,match='normalization_state_invalid'):
        recovery.offered_stage(dict(policy_version=2,stage=0,stage_evidence=e))


def test_runtime_small_loss_preserves_window_promotes_only_next_open(automatic_net):
    r,_,_,_=automatic_net
    for i,pnl in enumerate([15,-2,15]):
        assert open_probe(automatic_net,first=i==0)['stage']==0
        finish(automatic_net,net=pnl)
    rec=r.state()['recovery']
    assert rec['stage']==0 and rec['stage_evidence']['net_pnl']==28
    assert rec['stage_evidence']['positive_count']==2
    assert open_probe(automatic_net,first=False)['stage']==1
    assert r.state()['recovery']['stage_evidence']['window']==[]


def test_runtime_recovery_input_reference_restores_original(automatic_net):
    from live.scenario_review_storage import review_text
    from utils.btc_recovery_references import MAGIC, read_recovery

    r, _, _, _ = automatic_net
    open_probe(automatic_net)
    slot, stored = r.conn.execute("SELECT slot,body FROM llm_scenario_recovery_events WHERE kind='INPUT' ORDER BY rowid DESC LIMIT 1").fetchone()
    assert isinstance(stored, bytes) and stored.startswith(MAGIC)
    body = json.loads(read_recovery(stored, r.conn, slot, decode_legacy=review_text, expected_kind='INPUT'))
    ctx = json.loads(r.conn.execute('SELECT context FROM llm_scenario_decisions WHERE slot=?', (slot,)).fetchone()[0])
    assert body['recovery'] == ctx['recovery']
    assert body['context_hash'] == recovery.digest(ctx)


def test_runtime_small_loss_at_stage_one_preserves_positive_net(automatic_net):
    r,_,_,_=automatic_net
    for i,pnl in enumerate([15,15,15,-2]):
        open_probe(automatic_net,first=i==0)
        finish(automatic_net,net=pnl)
    e=r.state()['recovery']['stage_evidence']
    assert r.state()['recovery']['stage']==1 and e['net_pnl']==13
    open_probe(automatic_net,first=False)
    finish(automatic_net,net=-20)
    assert r.state()['recovery']['stage']==0
    assert r.state()['recovery']['stage_evidence']['window']==[]
    assert r.state()['recovery']['stage_evidence']['stage_start_equity'] is None


def test_runtime_old_active_settles_unchanged_then_prospective_migration(automatic_net):
    r,_,_,_=automatic_net
    open_probe(automatic_net)
    state=r.state()
    old=recovery._stage_evidence(10000)
    old.update(filled_count=1,positive_count=1,net_pnl=10.,peak_net_pnl=10.)
    state['recovery']['stage_evidence']=old
    r._save(state)
    finish(automatic_net,net=10)
    rec=r.state()['recovery']
    assert rec['stage']==0 and rec['stage_evidence']['promotion_rule_version']==3
    assert rec['stage_evidence']['window']==[]
    journal=[json.loads(row[0]) for row in r.conn.execute("SELECT body FROM llm_scenario_recovery_events WHERE kind='PROMOTION_RULE_MIGRATION'")]
    assert journal[-1]['before']['positive_count']==2
    assert open_probe(automatic_net,first=False)['stage']==0


@pytest.mark.parametrize('field,value', [('settlement_hash','f'*64),('scenario_id','foreign'),
    ('settled_at',1800000601.),('net_pnl',20)])
def test_runtime_forged_source_hash_cannot_authorize(automatic_net,field,value):
    r,_,now,_=automatic_net
    open_probe(automatic_net)
    finish(automatic_net,net=15)
    state=r.state()
    e=state['recovery']['stage_evidence']
    e['window'][0][field]=value
    e.update(recovery._window_metrics(e['window']))
    r._save(state)
    now[0]+=300
    assert r.tick()['failure_code']=='normalization_state_invalid'


def test_realized_drawdown_demotes_once_even_if_window_net_positive(automatic_net):
    r,_,now,_=automatic_net
    for i,pnl in enumerate([15,15,150,-120]):
        open_probe(automatic_net,first=i==0)
        finish(automatic_net,net=pnl)
    assert r.state()['recovery']['stage']==0
    events=[json.loads(row[0]) for row in r.conn.execute("SELECT body FROM llm_scenario_recovery_events WHERE kind='STAGE_TRANSITION'")]
    assert events[-1]['previous_evidence']['net_pnl']==30
    assert events[-1]['previous_evidence']['max_realized_drawdown']==120
    count=len(events)
    now[0]+=300
    r.tick()
    assert r.conn.execute("SELECT COUNT(*) FROM llm_scenario_recovery_events WHERE kind='STAGE_TRANSITION'").fetchone()[0]==count


def test_zero_nofill_and_duplicate_do_not_create_wins(automatic_net):
    r,_,now,_=automatic_net
    open_probe(automatic_net)
    finish(automatic_net,nofill=True)
    assert r.state()['recovery']['stage_evidence']['window']==[]
    open_probe(automatic_net,first=False)
    s=finish(automatic_net,net=0)
    before=r.state()
    after=deepcopy(before)
    recovery.settled(r.conn,after,s,now[0])
    assert after==before
    assert after['recovery']['stage_evidence']['filled_count']==1
    assert after['recovery']['stage_evidence']['positive_count']==0


@pytest.mark.parametrize('field,value',[('epoch_id','foreign'),('stage',1),('window_origin','f'*64)])
def test_valid_settlement_cannot_be_spliced_into_different_window(automatic_net,field,value):
    r,_,now,_=automatic_net
    open_probe(automatic_net)
    finish(automatic_net,net=15)
    state=r.state()
    target=state['recovery']['stage_evidence'] if field=='window_origin' else state['recovery']
    target[field]=value
    r._save(state)
    now[0]+=300
    assert r.tick()['failure_code']=='normalization_state_invalid'


def test_repeated_stage_zero_cannot_reuse_prior_stage_zero_wins(automatic_net):
    r,_,now,_=automatic_net
    for i,pnl in enumerate([15,15]):
        open_probe(automatic_net,first=i==0)
        finish(automatic_net,net=pnl)
    old=deepcopy(r.state()['recovery']['stage_evidence'])
    open_probe(automatic_net,first=False)
    finish(automatic_net,net=-10)
    state=r.state()
    assert state['recovery']['stage']==0
    assert state['recovery']['stage_evidence']['window_origin']!=old['window_origin']
    state['recovery']['stage_evidence'].update(window=old['window'],**recovery._window_metrics(old['window']))
    state['recovery']['stage_evidence']['stage_start_equity']=10000
    r._save(state)
    now[0]+=300
    assert r.tick()['failure_code']=='normalization_state_invalid'


def test_source_lookup_is_indexed():
    conn=sqlite3.connect(':memory:')
    recovery.ensure_schema(conn)
    plan=conn.execute("EXPLAIN QUERY PLAN SELECT body FROM llm_scenario_recovery_events WHERE kind='SETTLEMENT' AND scenario_id=?",('s',)).fetchall()
    assert any('llm_scenario_recovery_source_lookup' in row[3] for row in plan)


@pytest.mark.parametrize('mode',['delete_loss','clear_window','fake_empty_origin'])
def test_cherry_picked_valid_rows_cannot_replace_complete_window(automatic_net,mode):
    r,_,now,_=automatic_net
    for i,pnl in enumerate([15,-2,15]):
        open_probe(automatic_net,first=i==0)
        finish(automatic_net,net=pnl)
    state=r.state()
    e=state['recovery']['stage_evidence']
    e['window']=[row for row in e['window'] if row['net_pnl']>0] if mode=='delete_loss' else []
    e.update(recovery._window_metrics(e['window']))
    if mode=='fake_empty_origin':
        e['window_origin']='f'*64
    r._save(state)
    now[0]+=300
    assert r.tick()['failure_code']=='normalization_state_invalid'


def test_missing_bootstrap_receipt_blocks_new_window(automatic_net):
    r,_,now,_=automatic_net
    assert r.tick()['status']=='wait'
    state=r.state()
    state['recovery']['bootstrap_evidence_event_id']='missing'
    r._save(state)
    now[0]+=300
    assert r.tick()['failure_code']=='normalization_state_invalid'


def test_v3_small_loss_keeps_stage_zero_evidence():
    conn=sqlite3.connect(':memory:')
    conn.execute('CREATE TABLE llm_scenario_settlements(scenario_id TEXT,evidence TEXT)')
    recovery.ensure_schema(conn)
    evidence=recovery._stage_evidence(10000,rule_version=3,window_origin='a'*64)
    rec=dict(policy_version=2,epoch_id='epoch',stage=0,stage_evidence=evidence,
        started_at=1,epoch_start_equity=10000,phase='CONSUMED',scenario_id='s',permit_id='p')
    state={'recovery':rec}
    recovery.journal(conn,'PERMIT_CONSUMED',rec,99,scenario_id='s')
    settlement=dict(scenario_id='s',flat_confirmed=True,orders_terminal=True,executions_complete=True,
        fees_complete=True,funding_complete=True,gross_pnl=-8,fees=2,funding_net=0,net_pnl=-10,execution_ids=['e'])
    recovery.settled(conn,state,settlement,100)
    assert state['recovery']['stage_evidence']['filled_count']==1
    assert state['recovery']['stage_evidence']['net_pnl']==-10
