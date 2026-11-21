"""Entry forecasts never release the host risk reserve or claim observed fees."""
from copy import deepcopy
import pytest
from live.scenario_fee_projection import demo_entry_fee_reference, fee_only_projection, DEMO_REFERENCE_BASIS
from live.scenario_entry_economics import policy
from live.scenario_calculated_review import calculate_candidate
from tests.test_scenario_calculated_review import context, wire
from tests.test_scenario_fee_projection import evidence


def forecast(**changes):
    args=dict(side='SHORT',entry_price=70527,exit_price=69757.3,quantity=1,
              evidence={'status':'UNKNOWN'},now=1001,new_entry=True,
              entry_fee_reference=demo_entry_fee_reference())
    args.update(changes)
    return fee_only_projection(**args)


def test_reference_includes_both_fees_and_nonzero_slippage_without_observed_claim():
    r=forecast()
    assert r['basis']==DEMO_REFERENCE_BASIS
    assert r['estimated_net_before_unmodelled_costs']==pytest.approx(70527-69757.3-(70527+69757.3)*(.00055+.0005))
    assert r['future_funding']=='UNKNOWN_EXCLUDED'
    assert r['future_slippage']=='ASSUMED_5BPS_EACH_FILL'
    assert r['fee_rate_basis']=='PUBLISHED_NONVIP_TAKER_ASSUMPTION'


@pytest.mark.parametrize('field,value',[('version',True),('version',2),('environment','live'),
    ('taker_fee_rate',0),('taker_fee_rate',float('nan')),('current_account_rate_verified',True),
    ('slippage_rate_per_fill',0),('slippage_rate_per_fill',-.1),('source','unverified')])
def test_bad_reference_never_becomes_a_forecast(field,value):
    ref=demo_entry_fee_reference();ref[field]=value
    assert forecast(entry_fee_reference=ref)['status']=='UNKNOWN'


def test_historical_rate_wins_without_refreshing_evidence():
    e=evidence(.001);before=deepcopy(e);r=forecast(evidence=e)
    assert r['assumed_fee_rate']==.001
    assert r['fee_rate_basis']=='HISTORICAL_TAKER_MAX_ASSUMPTION_NOT_CURRENT_TIER'
    assert e==before


def test_no_opt_in_and_holdings_keep_legacy_behavior():
    assert forecast(entry_fee_reference=None)['status']=='UNKNOWN'
    assert forecast(new_entry=False)['status']=='UNKNOWN'
    assert forecast(recorded_net=1)['status']=='UNKNOWN'
    e=evidence()
    assert forecast(new_entry=False,evidence=e)==forecast(new_entry=False,evidence=e,entry_fee_reference=None)
    assert forecast(evidence=e,now=90000,entry_fee_reference=None)['status']=='UNKNOWN'
    assert forecast(evidence=e,now=90000)['fee_rate_basis']=='PUBLISHED_NONVIP_TAKER_ASSUMPTION'


def test_new_forecast_preserves_plan_quantity_and_full_risk_reserve():
    c=context();c['entry_economics']=policy();p=wire(c,'LONG')
    p['take_profits']=[dict(id='tp',price=85000,fraction=1)]
    old=calculate_candidate(p,c,candidate_id='c0')
    changed=deepcopy(c);changed['entry_fee_reference']=demo_entry_fee_reference()
    new=calculate_candidate(p,changed,candidate_id='c0')
    assert new['eligible'] and new['entry_economics']['minimum_ratio']==1.2
    assert old['raw_risk']==new['raw_risk']
    assert old['effective_plan']==new['effective_plan']
    assert old['projection']['weighted_risk_per_btc']==new['projection']['weighted_risk_per_btc']
    assert new['entry_economics']['basis']=='DEMO_ENTRY_FEE_AND_SLIPPAGE_ASSUMPTIONS_FUNDING_EXCLUDED'
    assert new['projection']['quantity']==old['projection']['quantity']


def test_geometry_and_entry_projection_use_the_same_reference_basis():
    from tests.test_scenario_entry_geometry import inputs,rows
    from live.scenario_entry_geometry import build
    s,c,t=inputs();c['observed_execution_fee_evidence']={'status':'UNKNOWN'}
    c['entry_fee_reference']=demo_entry_fee_reference();view=build(s,c,t)
    assert view['status']=='AVAILABLE'
    assert {r['cost_basis']for r in rows(view)}=={'REFERENCE'}
    for row in rows(view):
        e,exit_=row['worst_entry_bound'],row['target_level'];sign=1 if row['side']=='LONG'else-1
        assert row['reward_per_btc']==pytest.approx(sign*(exit_-e)-(e+exit_)*.00105)


def test_replay_profile_is_explicit_and_leaves_original_contract_unchanged():
    from analysis.scenario_replay import contract_for
    from tests.test_scenario_replay import inputs
    b,_=inputs()
    old=contract_for(b,'OHLC',1,10000,policy_profile='deployed-main-v3')
    new=contract_for(b,'OHLC',1,10000,policy_profile='deployed-cost-reference-v4')
    assert old['broker_flags']==dict(entry_economics_enabled=True)
    assert new['broker_flags']==dict(entry_economics_enabled=True,entry_fee_reference_enabled=True)


@pytest.mark.parametrize('profile,enabled',[('deployed-main-v3',False),('deployed-cost-reference-v4',True)])
def test_real_broker_context_adds_reference_only_for_explicit_profile(tmp_path,profile,enabled):
    import sqlite3,json
    from types import SimpleNamespace
    from analysis.scenario_replay import run_replay
    from tests.test_scenario_replay import inputs
    b,m=inputs(300000)
    run_replay(b,m,tmp_path/'run',mode='fixture',tape_path=tmp_path/'tape',
        policy_profile=profile,model_generator=lambda **kw:SimpleNamespace(text='{}'))
    with sqlite3.connect(tmp_path/'run'/'replay.sqlite') as conn:
        c=json.loads(conn.execute('SELECT context FROM llm_scenario_decisions').fetchone()[0])
        assert ('entry_fee_reference' in c) is enabled
        if enabled:
            assert c['entry_fee_reference']['current_account_rate_verified'] is False
            assert c['observed_execution_fee_evidence']['status']=='UNKNOWN'
        assert c['estimated_cost_rate']==.002 and c['slippage_bps']==20
        assert conn.execute('SELECT count(*) FROM llm_scenario_intents').fetchone()[0]==0


def test_reference_survives_model_input_projection():
    from live.scenario_model_input import project_model_input,restore_model_input
    c=context();c['entry_fee_reference']=demo_entry_fee_reference()
    projected,_=project_model_input(dict(contract_context=c))
    assert restore_model_input(projected)['contract_context']['entry_fee_reference']==c['entry_fee_reference']


def test_real_two_pass_prompt_describes_the_same_costs_as_host():
    from tests.test_scenario_calculated_review import run
    c=context();c['entry_economics']=policy();c['entry_fee_reference']=demo_entry_fee_reference()
    p=wire(c,'LONG');p['take_profits']=[dict(id='far',price=86000,fraction=1)]
    result,events,calls=run(c,candidates=[p])
    assert len(calls)==2
    text=calls[1]['system_prompt']
    assert '5bps/fill slippage assumptions' in text
    assert 'assumed entry slippage is already deducted' in text
    assert 'funding/slippage are excluded, NOT zero.' not in text
