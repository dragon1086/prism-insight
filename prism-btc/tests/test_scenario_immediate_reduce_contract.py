"""Immediate reduction wire, pure sizing and fresh authorization boundaries."""
from copy import deepcopy
from functools import partial
import json
import sqlite3
from types import SimpleNamespace

import pytest

from core.llm_scenario import validate_scenario, ScenarioValidationError
from live.scenario_contract import response_schema, validate_wire_proposal
from tests.test_scenario_llm import context, wire


def holding():
    ctx=context(True)
    ctx.update(immediate_reduce_version=1,accounting_status='confirmed',protection_ok=True,
               legacy_fenced=False,unknown_intents=[],positions=[dict(price=60000,quantity=.013)],
               quantity_step=.001,minimum_quantity=.001,market_maximum_quantity=1)
    return ctx


def reduction(ctx):
    return dict(wire(ctx),action='REDUCE',reduce_fraction=.3)


@pytest.mark.parametrize('side',['LONG','SHORT'])
def test_reduce_wire_compiles_host_plan_without_mutation(side):
    ctx=holding()
    ctx['side']=side
    raw=reduction(ctx)
    before=deepcopy((ctx,raw))
    normalized=validate_wire_proposal(raw,ctx)
    result=validate_scenario(normalized,ctx)
    assert result['reduce_plan']==dict(quantity_before=.013,requested_quantity=.003,target_remainder=.010)
    assert (ctx,raw)==before
    assert not result.get('risk')


def test_legacy_wire_schema_stays_unchanged():
    ctx=context(True)
    assert 'REDUCE' not in response_schema(ctx)['properties']['action']['enum']
    assert 'reduce_fraction' not in response_schema(ctx)['properties']
    with pytest.raises(ValueError):
        validate_wire_proposal(reduction(ctx),ctx)


@pytest.mark.parametrize('fraction',[0,1,-.2,1.2,True,float('nan'),.001,.999])
def test_invalid_fraction_or_subminimum_leg_rejected(fraction):
    ctx=holding()
    raw=reduction(ctx)
    raw['reduce_fraction']=fraction
    if fraction==.999:
        ctx['minimum_quantity']=.002
    with pytest.raises(ValueError):
        validate_scenario(validate_wire_proposal(raw,ctx),ctx)


@pytest.mark.parametrize('field,value',[
    ('scenario_id',None),('positions',[]),('pending_entries',[dict(id='p',price=60000,quantity=.001)]),
    ('accounting_status','pending'),('protection_ok',False),('unknown_intents',['unknown']),
    ('legacy_fenced',True),('market_maximum_quantity',.002),('immediate_reduce_version',None),
    ('entry_contract_observation',dict(status='FAILED')),
])
def test_unsafe_reduction_context_fails_closed(field,value):
    ctx=holding()
    ctx[field]=value
    with pytest.raises(ValueError):
        validate_scenario(validate_wire_proposal(reduction(ctx),ctx),ctx)


@pytest.mark.parametrize('field,value',[
    ('entries',[dict(id='e',price=60000,quantity=.001)]),('take_profits',[dict(id='tp',price=61000,fraction=.5)]),
    ('partial_stops',[dict(id='sl',price=59500,fraction=.5)]),('cancel_entry_ids',['p']),
    ('side','LONG'),('hard_stop',59000),('reduce_plan',dict(requested_quantity=10))])
def test_reduce_cannot_smuggle_plan_changes(field,value):
    ctx=holding()
    raw=reduction(ctx)
    raw[field]=value
    with pytest.raises(ValueError):
        validate_scenario(validate_wire_proposal(raw,ctx),ctx)


def test_host_plan_cannot_be_supplied_directly_to_core():
    ctx=holding()
    normalized=validate_wire_proposal(reduction(ctx),ctx)
    normalized['reduce_plan']={}
    with pytest.raises(ScenarioValidationError,match='unknown scenario fields'):
        validate_scenario(normalized,ctx)


def test_nonreduce_fraction_is_explicit_null_and_stripped():
    ctx=holding()
    raw=dict(wire(ctx),reduce_fraction=None)
    assert 'reduce_fraction' not in validate_wire_proposal(raw,ctx)
    raw['reduce_fraction']=.2
    with pytest.raises(ValueError):
        validate_wire_proposal(raw,ctx)


def reduce_tape():
    from tests.test_scenario_review_guard import context as guard_context
    from live.scenario_calculated_review import research_review
    from live.scenario_preview import response_contract
    from analysis.scenario_review_tape import record_review
    ctx=guard_context()
    ctx.update(holding())
    snapshot=dict(valid=True,as_of_ms=1000000)
    calls=[]
    def generate(**request):
        calls.append(request)
        if len(calls)==1:
            value=dict(wait_proposal=dict(wire(ctx),reduce_fraction=None),candidates=[reduction(ctx)],no_candidate_reason='')
        else:
            token=next(t for t in request['response_schema']['properties']['selection_token']['enum'] if t.startswith('c0:'))
            value=dict(selection_token=token,rationale='Reduce weak holding advantage')
        return SimpleNamespace(text=json.dumps(value))
    review=partial(research_review,research_mode='economic-reject-calculated-v1')
    contract=response_contract(ctx)
    tape=record_review(review,snapshot,ctx,contract,generate=generate,record=lambda e:None,
                       clock=lambda:1000,bindings={'code':'test'})
    assert tape['outcome']['status']=='COMPLETE',tape['outcome']
    assert len(calls)==2
    return snapshot,ctx,contract,tape,calls


def test_calculated_reduce_roundtrip_and_fresh_guard():
    from live.scenario_review_guard import guard_review
    snapshot,ctx,contract,tape,calls=reduce_tape()
    selected=tape['outcome']['result']['selected']
    assert selected['effective_plan']['reduce_plan']['requested_quantity']==.003
    assert selected['projection']['assumption']=='REDUCE_LEG_AT_MARK_NOT_FILL_OR_WHOLE_SCENARIO_NET'
    assert selected['projection']['actual_net_pnl'] is None
    assert selected['projection']['remaining_targets']=='PROPORTIONALLY_SCALE_VERIFIED_UNFILLED_QUOTAS_TO_ACTUAL_REMAINDER_NO_REFILL'
    result=guard_review(snapshot,ctx,contract,tape,dict(ctx,now=1001,account_captured_at=1001),
                        bindings={'code':'test'},research_mode='economic-reject-calculated-v1')
    assert result['validated']['action']=='REDUCE'
    assert all('Immediate partial market reduction is unsupported' not in c['system_prompt'] for c in calls)
    assert all('FAILED still requires full EXIT' in c['system_prompt'] for c in calls)
    assert all('relative to the current position' in c['system_prompt'] for c in calls)


@pytest.mark.parametrize('field,value',[
    ('positions',[dict(price=60000,quantity=.012)]),('immediate_reduce_version',None),
    ('market_maximum_quantity',.002),('unknown_intents',True)])
def test_reduce_fresh_guard_rejects_changed_quantity_or_authority(field,value):
    from live.scenario_review_guard import guard_review
    snapshot,ctx,contract,tape,_=reduce_tape()
    fresh=dict(ctx,now=1001,account_captured_at=1001)
    fresh[field]=value
    with pytest.raises(ValueError,match='review_guard_context_changed'):
        guard_review(snapshot,ctx,contract,tape,fresh,bindings={'code':'test'},
                     research_mode='economic-reject-calculated-v1')


@pytest.mark.parametrize('prepare_fails',[False,True])
@pytest.mark.parametrize('submission',['complete','ack_pending','ack_lost','query_unknown'])
def test_runtime_atomically_prepares_reduce_and_preserves_original_plan(tmp_path,prepare_fails,submission):
    from tests.test_scenario_review_runtime import Broker,wire as runtime_wire
    from live.scenario_runtime import ScenarioRuntime
    from live.scenario_review_adapter import make_calculated_review_proposer
    from live.scenario_model_input import restore_model_input
    now=1800000000.
    conn=sqlite3.connect(tmp_path/'reduce.db')
    class ReduceBroker(Broker):
        capabilities=Broker.capabilities | {'immediate_reduce_v1'}
        def execute(self,payload,ident):
            super().execute(payload,ident)
            if submission!='complete':
                from live.scenario_broker import BrokerNotReady
                conn.execute('CREATE TABLE llm_scenario_children(intent_id TEXT,scenario_id TEXT,kind TEXT,status TEXT,order_id TEXT)')
                conn.execute('INSERT INTO llm_scenario_children VALUES(?,?,?,?,?)',
                    (ident,payload['scenario_id'],'reduce','LIVE' if submission=='ack_pending' else 'UNKNOWN',
                     None if submission=='ack_lost' else 'ack-order'))
                conn.commit()
                raise BrokerNotReady('exact_order_query_unknown' if submission=='query_unknown' else 'reduce_result_pending')
        def prepare_reduce(self,payload,ident):
            assert conn.in_transaction
            assert conn.execute('SELECT id FROM llm_scenario_intents WHERE id=?',(ident,)).fetchone()
            conn.execute('INSERT INTO prepared VALUES(?)',(ident,))
            if prepare_fails:
                raise ValueError('synthetic_prepare_failure')
    broker=ReduceBroker(now)
    broker.ctx.update(positions=[dict(price=100,quantity=.013)],previous_hard_stop=90,market_maximum_quantity=1)
    calls=[]
    def generate(**request):
        calls.append(request)
        ctx=restore_model_input(json.loads(request['user_prompt']))['contract_context']
        if len(calls)==1:
            wait=dict(runtime_wire(ctx,'WAIT'),reduce_fraction=None)
            candidate=dict(runtime_wire(ctx,'WAIT'),action='REDUCE',reduce_fraction=.3)
            value=dict(wait_proposal=wait,candidates=[candidate],no_candidate_reason='')
        else:
            token=next(t for t in request['response_schema']['properties']['selection_token']['enum'] if t.startswith('c0:'))
            value=dict(selection_token=token,rationale='Reduce only')
        return SimpleNamespace(text=json.dumps(value))
    r=ScenarioRuntime(conn,broker,lambda *_:pytest.fail('legacy'),
        lambda:dict(valid=True,as_of_ms=now*1000),clock=lambda:now,calculated_review_enabled=True,
        calculated_review_propose=make_calculated_review_proposer(generate=generate,clock=lambda:now))
    conn.execute('CREATE TABLE prepared(id TEXT)')
    conn.commit()
    current_plan=dict(action='OPEN',side='LONG',hard_stop=90,take_profits=[dict(id='tp',price=120,fraction=.5)])
    state=r.state()
    state['active']=dict(scenario_id='existing',initial_equity=10000,side='LONG',
        hard_stop=90,revision=0,created_at=now-600,expires_at=now+900,current_plan=deepcopy(current_plan))
    r._save(state)
    outcome=r.tick()
    if prepare_fails:
        assert not conn.execute('SELECT id FROM prepared').fetchall()
        assert not conn.execute('SELECT id FROM llm_scenario_intents').fetchall()
        assert broker.executed==[]
        assert r.state()['active']['revision']==0
    else:
        assert len(broker.executed)==1
        assert broker.executed[0]['reduce_plan']['requested_quantity']==.003
        assert conn.execute('SELECT count(*) FROM prepared').fetchone()[0]==1
        assert r.state()['active']['revision']==1
        assert outcome['reason']==({'complete':'awaiting_exact_evidence','ack_pending':'awaiting_reduce_evidence',
                                    'ack_lost':'submission_unknown','query_unknown':'submission_unknown'}[submission])
    assert r.state()['active']['current_plan']==current_plan
    assert r.state()['active']['initial_equity']==10000


def test_failed_model_assessment_requires_full_exit_not_reduce():
    from live.scenario_entry_contract import bind_contract
    ctx=holding()
    contract=bind_contract(dict(version=1,regime='TREND',premise='Higher lows',expected_progress='Advance',
        explicit_failure=dict(source='MARK_PRICE',operator='le',price=59500,minimum_observations=2)),
        dict(action='OPEN',scenario_id=ctx['scenario_id'],action_id='initial',input_id='old',
             side='LONG',entries=[dict(price=60000,quantity=.013)],hard_stop=59000),900)
    ctx.update(entry_contract_version=1,entry_contract_status='REGISTERED',registered_entry_contract=contract)
    raw=dict(reduction(ctx),entry_contract=None,thesis_assessment=dict(
        contract_id=contract['contract_id'],status='FAILED',reason='Entry premise failed'))
    with pytest.raises(ValueError,match='entry_contract_failed_requires_exit'):
        validate_wire_proposal(raw,ctx)
    raw.update(action='EXIT',reduce_fraction=None)
    assert validate_wire_proposal(raw,ctx)['action']=='EXIT'


def test_reduce_helper_is_source_bound_for_review_and_execution():
    from live.scenario_provenance import review_policy_binding,_manifest
    binding=review_policy_binding(research_mode='economic-reject-calculated-v1')
    name='live/scenario_immediate_reduce.py'
    assert name in binding['source_sha256']
    assert binding['loaded_parity'][name]=='VERIFIED'
    manifest=_manifest()
    assert manifest['loaded_code_checks'][name]=='VERIFIED'


def test_failed_restore_disables_only_new_reduce_not_management_schema():
    ctx=holding()
    normalized=validate_wire_proposal(reduction(ctx),ctx)
    ctx['immediate_reduce_available']=False
    schema=response_schema(ctx)
    assert 'reduce_fraction' in schema['properties']
    assert 'REDUCE' not in schema['properties']['action']['enum']
    assert {'WAIT','ADJUST','EXIT'} <= set(schema['properties']['action']['enum'])
    with pytest.raises(ValueError):
        validate_scenario(normalized,ctx)
