from utils.btc_review_references import read_tape
from live.scenario_review_storage import review_text
"""Fake-model full runtime proof of raw explanation and compiled plan boundaries."""
from copy import deepcopy
import json
import sqlite3
from types import SimpleNamespace

import pytest

from live.scenario_contract import response_schema
from live.scenario_review_adapter import make_calculated_review_proposer
from live.scenario_runtime import ScenarioRuntime
from live.scenario_model_input import restore_model_input
from tests.test_scenario_review_runtime import Broker,wire
from tests.test_scenario_ma_context import NOW,point,snapshot


def explanation(target='CURRENT:TP:old',side='LONG',missing=False):
    return dict(interpretation='원문 의견은 사실 검증과 구분한다. 반응 구간은 첫 TP 전이라는 미검증 해석.',
        evidence_refs=['MISSING' if missing else 'MARK'],comparisons=[dict(
            level_ref='MISSING' if missing else f"P:30m:0:{'high' if side=='LONG' else 'low'}",
            target_ref=target)])


def run_pipeline(tmp_path,action='WAIT',side='LONG',missing=False,failed=False,advance=False,host_failed=False,capture_audit=False):
    now=NOW/1000
    ticks=[0]
    def clock():
        ticks[0]+=1
        return now+ticks[0]*.001 if advance else now
    conn=sqlite3.connect(tmp_path/'pipeline.sqlite')
    class CapableBroker(Broker):
        capabilities=Broker.capabilities | {'immediate_reduce_v1'}
        def prepare_reduce(self,payload,ident):
            assert conn.in_transaction
            assert conn.execute('SELECT id FROM llm_scenario_intents WHERE id=?',(ident,)).fetchone()
    broker=CapableBroker(now)
    stop=84000 if side=='LONG' else 86000
    broker.ctx.update(mark_price=85100,positions=[dict(price=85000,quantity=.013)],
        previous_hard_stop=stop,market_maximum_quantity=1,
        execution_price_policy=dict(version='round-limit-v1'),
        limit_price_quotes=dict(bid=85099.9,ask=85100.1,captured_at=now))
    snap=snapshot(before=point('30m',2,close=85998 if side=='LONG' else 84002))
    snap['valid']=True
    requests=[]
    def generate(**request):
        requests.append(deepcopy(request))
        data=restore_model_input(json.loads(request['user_prompt']))
        ctx=data['contract_context']
        assert ctx['explanation_contract_version']==1
        assert isinstance(ctx['explanation_source']['points'],list)
        if 'selection_token' in request['response_schema']['properties']:
            token=next(t for t in request['response_schema']['properties']['selection_token']['enum'] if t.startswith('c0:'))
            return SimpleNamespace(text=json.dumps(dict(selection_token=token,
                rationale=explanation('c0:TP:new' if action=='ADJUST' else 'CURRENT:TP:old',side,missing))))
        wait=wire(ctx,'WAIT')
        wait['rationale']=explanation(side=side,missing=missing)
        candidate=None
        if action!='WAIT':
            candidate=wire(ctx,'ADJUST' if action=='ADJUST' else 'WAIT')
            candidate['action']=action
            candidate['rationale']=explanation('c0:TP:new' if action=='ADJUST' else 'CURRENT:TP:old',side,missing)
            if action=='ADJUST':
                candidate.update(side=side,hard_stop=stop,
                    chase=dict(max_bps=0,max_reprices=0),
                    take_profits=[dict(id='new',price=86000 if side=='LONG' else 84000,fraction=.5)])
            elif action=='REDUCE':
                candidate['reduce_fraction']=.3
        if failed:
            for proposal in (wait,candidate):
                proposal.update(entry_contract=None,thesis_assessment=dict(
                    contract_id=ctx['registered_entry_contract']['contract_id'],status='FAILED',reason='Independent failed premise'))
        return SimpleNamespace(text=json.dumps(dict(wait_proposal=wait,candidates=[] if candidate is None else [candidate],
            no_candidate_reason=explanation(side=side,missing=missing))))
    adapter=make_calculated_review_proposer(generate=generate,clock=clock)
    r=ScenarioRuntime(conn,broker,lambda *_:pytest.fail('legacy called'),lambda:deepcopy(snap),clock=clock,
        calculated_review_enabled=True,calculated_review_propose=adapter)
    old_plan=dict(action='OPEN',side=side,hard_stop=stop,
                  take_profits=[dict(id='old',price=87000 if side=='LONG' else 83000,fraction=.5)])
    state=r.state()
    active=dict(scenario_id='existing',initial_equity=10000,side=side,hard_stop=stop,revision=0,
                created_at=now-600,expires_at=now+900,current_plan=old_plan)
    if failed:
        from live.scenario_entry_contract import bind_contract
        initial=dict(old_plan,scenario_id='existing',action_id='original',input_id='original-input',
                     revision=0,
                     entries=[dict(id='e',price=85000,quantity=.013)])
        definition=dict(version=1,regime='TREND',premise='Original purpose',expected_progress='Directional progress',
            explicit_failure=dict(source='MARK_PRICE',operator='le' if side=='LONG' else 'ge',
                                  price=84500 if side=='LONG' else 85500,minimum_observations=2))
        contract=bind_contract(definition,initial,now-600)
        active['entry_contract']=contract
        conn.execute('INSERT INTO llm_scenario_entry_contracts VALUES(?,?)',('existing',json.dumps(contract,sort_keys=True)))
        conn.execute("INSERT INTO llm_scenario_intents VALUES(?,?,?,'TERMINAL',NULL)",('original','existing',json.dumps(initial)))
        if host_failed:
            from live.scenario_entry_contract import observe_contract
            mark=84490 if side=='LONG' else 85510
            broker.ctx.update(mark_price=mark,entry_thesis=dict(status='VERIFIED',side=side,
                first_fill_at=now-500,observed_at=now,current_mark=mark))
            previous=dict(scenario_id='existing',account_captured_at=now-300,mark_price=mark,
                account_version='previous',entry_thesis=dict(status='VERIFIED',side=side,
                    first_fill_at=now-500,observed_at=now-300,current_mark=mark))
            observe_contract(active,previous,int(now//300)-1,now-300)
    state['active']=active
    r._save(state)
    if capture_audit:
        from live.scenario_provenance import run_capture
        with run_capture(conn):
            outcome=r.tick()
    else:
        outcome=r.tick()
    recorded=conn.execute('SELECT body FROM llm_scenario_review_tapes').fetchone()
    tape=json.loads(read_tape(recorded[0], conn, conn.execute('SELECT slot FROM llm_scenario_review_tapes').fetchone()[0], decode_legacy=review_text)) if recorded else None
    return r,broker,outcome,tape,requests,old_plan


@pytest.mark.parametrize('side',['LONG','SHORT'])
@pytest.mark.parametrize('action',['WAIT','ADJUST','REDUCE','EXIT'])
def test_real_runtime_raw_and_compiled_explanations_are_separately_bound(tmp_path,side,action):
    r,broker,outcome,tape,requests,old_plan=run_pipeline(tmp_path,action,side,advance=action=='ADJUST')
    assert outcome['status']==('wait' if action=='WAIT' else 'intent_pending'),outcome
    assert len(requests)==(1 if action=='WAIT' else 2)
    result=tape['outcome']['result']
    selected=result['selected']
    expected=explanation('c0:TP:new' if action=='ADJUST' else 'CURRENT:TP:old',side)
    assert selected['raw_proposal']['rationale']==expected
    receipt=selected['explanation']
    from live.scenario_explanation import digest
    from live.scenario_calculated_review import receipt_hash
    assert receipt['raw']==expected
    assert receipt['raw_sha256']==digest(expected)
    assert receipt['sha256']==digest({k:v for k,v in receipt.items() if k!='sha256'})
    assert selected['receipt_sha256']==receipt_hash({k:v for k,v in selected.items() if k!='receipt_sha256'})
    raw_events=[json.loads(op['event']['raw']) for op in tape['operations']
                if op.get('event',{}).get('kind')=='MODEL_RAW']
    assert raw_events[0]['wait_proposal']['rationale']==explanation(side=side)
    if action!='WAIT':
        assert raw_events[0]['candidates'][0]['rationale']==expected
        assert raw_events[1]['rationale']==expected
    assert receipt['comparisons'][0]['relation']==('AFTER' if action=='ADJUST' else 'BEFORE')
    if action!='WAIT':
        assert selected['effective_plan']['rationale']==receipt['text']
        assert broker.executed[0]['rationale']==receipt['text']
        selection=result['selection']['explanation']
        assert selection['raw']==expected
        assert selection['comparisons'][0]['target']['price']==receipt['comparisons'][0]['target']['price']
    if action=='ADJUST':
        effective=selected['effective_plan']['take_profits'][0]['price']
        assert effective != (86000 if side=='LONG' else 84000)
        assert receipt['comparisons'][0]['target']['price']==effective
    else:
        assert r.state()['active']['current_plan']==old_plan
    assert expected['interpretation'] in receipt['text']
    assert '모델 해석(미검증)' in receipt['text']
    assert '공식 수치 대조' in receipt['text']
    r.conn.close()


def test_unknown_explanation_ref_does_not_delay_failed_full_exit(tmp_path):
    r,broker,outcome,tape,requests,_=run_pipeline(tmp_path,'EXIT',missing=True,failed=True)
    assert outcome['status']=='intent_pending',outcome
    assert len(requests)==2 and broker.executed[0]['action']=='EXIT'
    result=tape['outcome']['result']
    assert result['selected']['explanation']['comparisons'][0]['relation']=='UNKNOWN'
    assert result['candidates']['wait']['executable'] is False
    assert result['selected']['raw_proposal']['thesis_assessment']['status']=='FAILED'
    r.conn.close()


def test_host_confirmed_failure_exits_before_any_explanation_model_call(tmp_path):
    r,broker,outcome,tape,requests,_=run_pipeline(tmp_path,'EXIT',missing=True,failed=True,host_failed=True)
    assert outcome['status']=='intent_pending',outcome
    assert requests==[] and tape is None
    assert len(broker.executed)==1 and broker.executed[0]['action']=='EXIT'
    r.conn.close()


@pytest.mark.parametrize('scope',['legacy','flat','pending'])
def test_inactive_explanation_contract_does_not_change_legacy_wire(scope):
    from tests.test_scenario_llm import context
    ctx=context(scope!='flat')
    if scope=='pending':
        ctx['positions']=[]
        ctx['pending_entries']=[dict(id='p',price=60000,quantity=.001)]
    before=response_schema(ctx)
    if scope!='legacy':
        ctx['explanation_contract_version']=1
    assert response_schema(ctx)==before
    assert before['properties']['rationale']['type']=='string'


@pytest.mark.parametrize('scope',['single','flat','pending'])
def test_runtime_does_not_enable_explanation_for_out_of_scope_inputs(tmp_path,scope):
    now=1800000000.
    conn=sqlite3.connect(tmp_path/'out-of-scope.sqlite')
    broker=Broker(now)
    # Untrusted broker-supplied interpretation capabilities must be removed.
    broker.ctx.update(explanation_contract_version=1,explanation_source={'forged':True})
    if scope=='single':
        broker.ctx.update(positions=[dict(price=100,quantity=.01)],previous_hard_stop=90)
    elif scope=='pending':
        broker.ctx.update(pending_entries=[dict(id='pending',price=100,quantity=.01)],previous_hard_stop=90)
    mode='action-allocation-single-v1' if scope=='single' else 'legacy'
    calls=[]
    def generate(**request):
        calls.append(request)
        ctx=restore_model_input(json.loads(request['user_prompt']))['contract_context']
        assert 'explanation_contract_version' not in ctx
        assert 'explanation_source' not in ctx
        wait=wire(ctx,'WAIT')
        value={'proposal':wait} if scope=='single' else dict(wait_proposal=wait,candidates=[],no_candidate_reason='No new entry')
        return SimpleNamespace(text=json.dumps(value))
    adapter=make_calculated_review_proposer(generate=generate,clock=lambda:now,research_mode=mode)
    r=ScenarioRuntime(conn,broker,lambda *_:pytest.fail('legacy called'),
        lambda:dict(valid=True,as_of_ms=now*1000),clock=lambda:now,
        calculated_review_enabled=True,calculated_review_propose=adapter,research_review_mode=mode)
    if scope!='flat':
        state=r.state()
        state['active']=dict(scenario_id='active',initial_equity=10000,side='LONG',hard_stop=90,
                             revision=0,created_at=now-600,expires_at=now+600)
        r._save(state)
    outcome=r.tick()
    assert outcome['status']=='wait',outcome
    assert len(calls)==1 and broker.executed==[]
    saved=json.loads(conn.execute('SELECT context FROM llm_scenario_decisions').fetchone()[0])
    assert 'explanation_contract_version' not in saved and 'explanation_source' not in saved
    conn.close()
