from copy import deepcopy
from functools import partial
import json
from types import SimpleNamespace
import pytest
from live.scenario_entry_economics import assess, policy
from live.scenario_calculated_review import calculate_candidate, research_review
from live.scenario_preview import response_contract
from live.scenario_review_guard import guard_rejected_review
from analysis.scenario_review_tape import record_review
from tests.test_scenario_calculated_review import context, wire


def supplied():
    return dict(entry_economics=policy(),scenario_id=None,positions=[],pending_entries=[])


def projection(reward=12,loss=10):
    return {'whole_plan_payoff':{'status':'AVAILABLE','net_reward':reward,'full_stop_loss':loss,'ratio':999}}


@pytest.mark.parametrize('reward,status',[(11.99,'REJECTED'),(12,'PASS'),(13,'PASS'),(-1,'REJECTED')])
def test_fee_based_threshold_uses_amounts_not_supplied_ratio(reward,status):
    r=assess({'action':'OPEN'},supplied(),projection(reward))
    assert r['status']==status
    assert r['ratio']==pytest.approx(reward/10)


def test_missing_fees_reuse_existing_reserve_without_claiming_actual_fees():
    p={'quantity':1,'weighted_risk_per_btc':12,'gross_at_full_stop':-10,
       'joint_exit_paths':{'paths':[[['TP_STOP'],0,20,None]]}}
    r=assess({'action':'OPEN'},supplied(),p)
    assert r['status']=='PASS' and r['ratio']==1.5
    assert r['basis']=='HOST_COST_RESERVE_STRESS_NOT_ACTUAL_FEES'
    assert r['actual_future_fees'] is None


@pytest.mark.parametrize('p',[{},projection(12,0),projection(float('nan')),projection(12,float('inf'))])
def test_unavailable_never_becomes_pass(p):
    assert assess({'action':'OPEN'},supplied(),p)['status']=='UNKNOWN'


@pytest.mark.parametrize('action',['WAIT','ADJUST','REDUCE','EXIT'])
def test_existing_management_and_protection_not_gated(action):
    assert assess({'action':action},supplied(),{}) is None


def test_legacy_context_keeps_existing_behavior():
    assert assess({'action':'OPEN'},{'positions':[],'pending_entries':[]},projection(0)) is None


def test_latest_loss_shape_rejected_without_repricing_or_resizing():
    c=context();raw=wire(c,'LONG');before=calculate_candidate(raw,c,candidate_id='c0')
    c['entry_economics']=policy();after=calculate_candidate(raw,c,candidate_id='c0')
    assert before['eligible'] and not after['eligible']
    assert after['rejection_type']=='PREAUTHORITY_ECONOMIC'
    assert after['raw_risk']==before['raw_risk'] and after['effective_plan']==before['effective_plan']


@pytest.mark.parametrize('side,target',[('LONG',86000),('SHORT',80000)])
def test_strong_candidate_stays_eligible(side,target):
    c=context();c['entry_economics']=policy();raw=wire(c,side);raw['take_profits']=[{'id':'far','price':target,'fraction':1}]
    r=calculate_candidate(raw,c,candidate_id='c0')
    assert r['eligible'] and r['entry_economics']['status']=='PASS'


def test_rejection_uses_existing_no_action_disposition_and_frozen_guard():
    from tests.test_scenario_review_guard import context as guarded,wire as guarded_wire
    from tests.test_scenario_allocation_review import allocation_wire
    c=guarded();c['entry_economics']=policy();raw=allocation_wire(c);snap={'valid':True,'as_of_ms':1000000};calls=[]
    def generate(**q):
        calls.append(q);assert len(calls)==1
        from live.scenario_model_input import restore_model_input
        assert restore_model_input(json.loads(q['user_prompt']))['contract_context']['entry_economics']==policy()
        return SimpleNamespace(text=json.dumps({'wait_proposal':guarded_wire(c,'WAIT'),'candidates':[raw],'no_candidate_reason':''}))
    mode='economic-reject-calculated-v1';review=partial(research_review,research_mode=mode)
    tape=record_review(review,snap,c,response_contract(c),generate=generate,record=lambda _:None,clock=lambda:1000,bindings={'test':'economics'})
    assert tape['outcome']['status']=='COMPLETE',tape['outcome']
    assert tape['outcome']['result']['disposition']=='REJECTED_NO_ACTION'
    verified=guard_rejected_review(snap,c,response_contract(c),tape,bindings={'test':'economics'},research_mode=mode)
    assert verified['execution_authorized'] is False and len(calls)==1


def test_unknown_policy_version_never_passes():
    c=supplied();c['entry_economics']['version']=True
    assert assess({'action':'OPEN'},c,projection(100))['status']=='UNKNOWN'


def test_final_guard_rechecks_economics_and_cannot_drop_policy(monkeypatch):
    from tests import test_scenario_review_guard as helpers
    c=helpers.context();c['entry_economics']=policy()
    c['observed_execution_fee_evidence']={'status':'AVAILABLE','basis':'HISTORICAL_VERIFIED_EXECUTIONS','source_sha256':'a'*64,'rates':{'TAKER':{'count':1,'minimum':.00055,'maximum':.00055,'earliest_at':999,'latest_at':999}}}
    original=helpers.wire
    def strong(ctx,action):
        w=original(ctx,action)
        if action=='OPEN':w['take_profits']=[{'id':'far','price':84250,'fraction':1}]
        return w
    monkeypatch.setattr(helpers,'wire',strong);args=helpers.taped(c)
    assert helpers.check(args)['receipt']['execution_authorized'] is False
    fresh=deepcopy(c);fresh.pop('entry_economics')
    with pytest.raises(ValueError,match='review_guard_context_changed:entry_economics'):helpers.check(args,fresh)
    fresh=deepcopy(c);fresh['observed_execution_fee_evidence']['rates']['TAKER'].update(minimum=.002,maximum=.002)
    with pytest.raises(ValueError,match='review_guard_entry_economics_changed'):helpers.check(args,fresh)


def test_production_flat_broker_context_supplies_policy(tmp_path):
    from tests.test_scenario_flat_fee_evidence import seeded
    from live.scenario_broker import ScenarioDemoBroker
    broker,session,_,_=seeded(tmp_path)
    fresh=ScenarioDemoBroker(broker.conn,session=session,expected_main_uid='123',clock=lambda:.2,entry_economics_enabled=True)
    fresh._daily=lambda obs:dict(new_risk_allowed=True,day='x',day_start_equity=10000,daily_net_pnl=0)
    fresh._instrument=lambda:dict(tick='.1',step='.001',minimum='.001',notional='5',market_maximum='100')
    session.get_tickers=lambda **kw:dict(retCode=0,result={'list':[dict(symbol='BTCUSDT',markPrice='100',lastPrice='100',bid1Price='99.9',ask1Price='100.1')]})
    session.get_transaction_log=lambda **kw:pytest.fail('no added financial lookup')
    assert fresh.context()['entry_economics']==policy()


def test_economics_module_is_bound_in_provenance():
    from live.scenario_provenance import review_policy_binding
    result=review_policy_binding(research_mode='economic-reject-calculated-v1')
    assert 'scenario_entry_economics' in json.dumps(result)
