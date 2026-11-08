from functools import partial
from types import SimpleNamespace
import json
import sqlite3
import pytest
from analysis.scenario_review_tape import record_review
from live.scenario_calculated_review import research_review
from live.scenario_entry_economics import policy
from live.scenario_preview import response_contract
from live.scenario_review_guard import guard_rejected_review
from tests.test_scenario_review_guard import context,wire
from tests.test_scenario_allocation_review import allocation_wire
from utils.btc_review_retention import candidates


def source():
    c=context();c.update(entry_economics=policy(),historical_unsettled=[],unknown_intents=False,incident_epoch_blocked=False)
    snap={'valid':True,'as_of_ms':1000000};raw=allocation_wire(c)
    def generate(**q):return SimpleNamespace(text=json.dumps({'wait_proposal':wire(c,'WAIT'),'candidates':[raw],'no_candidate_reason':''}))
    mode='economic-reject-calculated-v1';binding={'test':'retention'}
    tape=record_review(partial(research_review,research_mode=mode),snap,c,response_contract(c),generate=generate,record=lambda _:None,clock=lambda:1000,bindings=binding)
    assert tape['outcome']['status']=='COMPLETE' and tape['outcome']['result']['disposition']=='REJECTED_NO_ACTION'
    receipt=guard_rejected_review(snap,c,response_contract(c),tape,bindings=binding,research_mode=mode)
    conn=sqlite3.connect(':memory:');conn.executescript('CREATE TABLE llm_scenario_review_tapes(slot INTEGER PRIMARY KEY,body TEXT);CREATE TABLE llm_scenario_decisions(slot INTEGER PRIMARY KEY,snapshot TEXT,context TEXT,proposal TEXT,outcome TEXT);CREATE TABLE llm_scenario_review_events(slot INTEGER,sequence INTEGER,body TEXT,PRIMARY KEY(slot,sequence));')
    result={'status':'rejected_no_action','reason':'economic_rejection','rejection_codes':receipt['rejection_codes'],'rejection_receipt':receipt}
    conn.execute('INSERT INTO llm_scenario_review_tapes VALUES(3,?)',(json.dumps(tape),));conn.execute('INSERT INTO llm_scenario_decisions VALUES(3,?,?,NULL,?)',(json.dumps(snap),json.dumps(c),json.dumps(result)))
    events=[o['event'] for o in tape['operations'] if o['kind']=='record']
    conn.executemany('INSERT INTO llm_scenario_review_events VALUES(3,?,?)',[(i,json.dumps(v)) for i,v in enumerate(events)])
    return conn


def test_seven_day_flat_non_authorizing_economics_rejection_is_candidate_only():
    conn=source();before=conn.total_changes
    assert len(candidates(conn,7*86400+3000)['candidates'])==1
    assert candidates(conn,7*86400+600)['candidates']==[]
    assert conn.total_changes==before and conn.execute('SELECT count(*) FROM llm_scenario_review_tapes').fetchone()[0]==1


@pytest.mark.parametrize('case',['position','unknown','policy','authority','tape','event','proposal','receipt_shape'])
def test_uncertain_or_modified_rejection_is_retained(case):
    conn=source()
    if case in {'position','unknown','policy'}:
        c=json.loads(conn.execute('SELECT context FROM llm_scenario_decisions').fetchone()[0])
        if case=='position':c['positions']=[{'quantity':1}]
        if case=='unknown':c['unknown_intents']=True
        if case=='policy':c.pop('entry_economics')
        conn.execute('UPDATE llm_scenario_decisions SET context=?',(json.dumps(c),))
    elif case=='authority':
        o=json.loads(conn.execute('SELECT outcome FROM llm_scenario_decisions').fetchone()[0]);o['rejection_receipt']['execution_authorized']=True
        conn.execute('UPDATE llm_scenario_decisions SET outcome=?',(json.dumps(o),))
    elif case=='receipt_shape':
        o=json.loads(conn.execute('SELECT outcome FROM llm_scenario_decisions').fetchone()[0]);o['rejection_receipt']=None
        conn.execute('UPDATE llm_scenario_decisions SET outcome=?',(json.dumps(o),))
    elif case=='tape':conn.execute('UPDATE llm_scenario_review_tapes SET body=?',('{"sha256":"wrong"}',))
    elif case=='event':conn.execute('DELETE FROM llm_scenario_review_events WHERE sequence=0')
    elif case=='proposal':conn.execute('UPDATE llm_scenario_decisions SET proposal=?',('{"action":"OPEN"}',))
    assert candidates(conn,7*86400+3000)['candidates']==[]


def test_real_runtime_rejection_ages_out_without_creating_orders(tmp_path):
    from live.scenario_runtime import ScenarioRuntime
    from live.scenario_review_adapter import make_calculated_review_proposer
    from tests.test_scenario_review_runtime import Broker,wire as runtime_wire
    now=1800000000.;broker=Broker(now);broker.ctx.update(entry_economics=policy(),historical_unsettled=[],unknown_intents=False,incident_epoch_blocked=False)
    captured={}
    def generate(**q):
        raw=runtime_wire(captured,'OPEN');raw['entries'][0].pop('quantity');raw['entries'][0]['allocation_fraction']=1
        return SimpleNamespace(text=json.dumps({'wait_proposal':runtime_wire(captured,'WAIT'),'candidates':[raw],'no_candidate_reason':''}))
    mode='economic-reject-calculated-v1';adapter=make_calculated_review_proposer(generate=generate,clock=lambda:now,research_mode=mode)
    def propose(snapshot,ctx,*,record):
        captured.update(ctx);return adapter(snapshot,ctx,record=record)
    conn=sqlite3.connect(tmp_path/'reject.db');runtime=ScenarioRuntime(conn,broker,lambda *_:pytest.fail('legacy'),lambda:dict(valid=True,as_of_ms=now*1000),clock=lambda:now,calculated_review_enabled=True,calculated_review_propose=propose,research_review_mode=mode)
    assert runtime.tick()['status']=='rejected_no_action'
    before=conn.total_changes
    rows=candidates(conn,now+7*86400+300)['candidates']
    assert len(rows)==1 and rows[0]['slot']==int(now//300)
    assert conn.total_changes==before and broker.executed==[]
