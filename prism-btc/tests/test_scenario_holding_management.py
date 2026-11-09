from copy import deepcopy
import pytest
from live.scenario_position_flow import holding_management_view,project_holding_management_input,HOLDING_VIEW_MAX_BYTES
from live.scenario_model_input import project_model_input,restore_model_input
from live.scenario_llm import SYSTEM_PROMPT,MA_STRUCTURE_PROMPT,FLAT_ENTRY_PROMPT,holding_management_instructions


def inputs(side='LONG'):
    long=side=='LONG';c=dict(scenario_id='held',side=side,positions=[dict(price=100.,quantity=1.)],pending_entries=[],now=10000.,account_captured_at=9999.,accounting_status='confirmed',protection_ok=True,new_risk_blocked=False,mark_price=115. if long else 85.,previous_hard_stop=95. if long else 105.,price_tick=.1,recorded_scenario_net=-.1,stop_policy=dict(version=1,anchor_available=True,widening_eligible=True,initial_hard_stop=90. if long else 110.),observed_execution_fee_evidence=dict(status='AVAILABLE',basis='HISTORICAL_VERIFIED_EXECUTIONS',source_sha256='a'*64,rates=dict(TAKER=dict(count=1,minimum=.00055,maximum=.00055,earliest_at=9990.,latest_at=9990.))))
    s=dict(valid=True,as_of_ms=10000000,timeframes={});structure={'primary':{}}
    for frame,duration,atr,ma,close in [('30m',1800000,10,108 if long else 92,110 if long else 90),('1h',3600000,20,105 if long else 95,109 if long else 91)]:
        end=10000000//duration*duration
        s['timeframes'][frame]={'confirmed':dict(is_confirmed=True,open_time_ms=end-duration,atr14_risk_only=atr)}
        structure['primary'][frame]={'points':[dict(phase='CONFIRMED',is_confirmed=True,as_of_ms=end,ma10=ma,close=close)]}
    c['ma_structure']=structure
    return s,c,structure


@pytest.mark.parametrize('side',['LONG','SHORT'])
def test_directional_references_and_existing_restoration_bounds(side):
    s,c,t=inputs(side);before=deepcopy((s,c,t));v=holding_management_view(s,c,t);fast,slow=v['rows']
    assert v['status']=='AVAILABLE' and v['cost_only_exit_estimate_positive'] is True
    assert fast['candidate_stop']==(110. if side=='LONG' else 90.) and fast['requires_stop_revision'] is False
    assert slow['candidate_stop']==c['stop_policy']['initial_hard_stop'] and slow['requires_stop_revision'] is True
    assert v['fee_only_net_if_flatten']==pytest.approx((c['mark_price']-100)*(1 if side=='LONG' else -1)-.1-c['mark_price']*.00055)
    assert (s,c,t)==before


@pytest.mark.parametrize('side',['LONG','SHORT'])
def test_no_stop_loosened_without_existing_permission(side):
    s,c,t=inputs(side);c['stop_policy']['widening_eligible']=False
    v=holding_management_view(s,c,t)
    assert v['rows'][1]['candidate_stop']==c['previous_hard_stop']
    assert v['rows'][1]['requires_stop_revision'] is False


def test_missing_recorded_net_fee_or_atr_never_becomes_known_zero():
    s,c,t=inputs();c['recorded_scenario_net']=None;s['timeframes']['1h']['confirmed']['is_confirmed']=False
    v=holding_management_view(s,c,t)
    assert v['fee_only_net_if_flatten'] is None and v['cost_only_exit_estimate_positive'] is None
    assert v['rows'][1]['status']=='MISSING'
    c['recorded_scenario_net']=0.;c.pop('observed_execution_fee_evidence')
    assert holding_management_view(s,c,t)['fee_only_net_if_flatten'] is None


def test_reference_already_beyond_mark_never_generates_an_invalid_stop():
    s,c,t=inputs();t['primary']['1h']['points'][0]['ma10']=150
    row=holding_management_view(s,c,t)['rows'][1]
    assert row['reference_stop']==130 and row['candidate_stop'] is None


@pytest.mark.parametrize('change',[lambda s,c,t:c.update(scenario_id=None),lambda s,c,t:c.update(positions=[]),lambda s,c,t:c.update(pending_entries=[{'quantity':1}]),lambda s,c,t:c.update(accounting_status='pending'),lambda s,c,t:c.update(protection_ok=False),lambda s,c,t:c.update(account_captured_at=10001),lambda s,c,t:s.update(as_of_ms=10000001),lambda s,c,t:c['positions'][0].update(quantity=float('nan'))])
def test_scope_and_invalid_or_future_facts_are_not_used(change):
    s,c,t=inputs();change(s,c,t);assert holding_management_view(s,c,t) is None


def test_projection_is_bounded_lossless_and_original_immutable(monkeypatch):
    import live.scenario_position_flow as flow
    s,c,t=inputs();c['position_flow']={'keep':'history'};base,_=project_model_input(dict(market_snapshot=s,contract_context=c,response_contract={'keep':'schema'}));original=deepcopy(base)
    result=restore_model_input(project_holding_management_input(base,c));view=result['contract_context'].pop('holding_management')
    assert result==restore_model_input(base) and base==original and flow._size(view)<=HOLDING_VIEW_MAX_BYTES
    monkeypatch.setattr(flow,'_MODEL_INPUT_LIMIT',flow._size(base)+5)
    assert project_holding_management_input(base,c)==base


def test_holding_prompt_consolidates_examples_without_touching_flat_or_unconfirmed():
    s,c,t=inputs();base=SYSTEM_PROMPT+MA_STRUCTURE_PROMPT
    text=holding_management_instructions(base,c)
    assert '100-300 USDT' not in text and '800-1000 USDT' not in text and '500 USDT' not in text
    assert 'SUSTAINED DIRECTION' in text and 'WEAKENING/RANGE' in text
    assert 'Retain broker hard SL' in text and 'existing eligible stop_policy' in text
    assert 'bounded MA35 traversal' in text and 'Pending-entry replanning' in text
    assert holding_management_instructions(base,{**c,'scenario_id':None})==base
    assert holding_management_instructions(base,{**c,'accounting_status':'pending'})==base
    with pytest.raises(ValueError,match='holding_prompt_source_changed'):
        holding_management_instructions('unknown prompt',c)


def test_restoration_price_reference_never_overrides_the_host_loss_budget():
    from core.llm_scenario import validate_scenario,ScenarioValidationError
    from tests.test_scenario_stop_policy import candidate
    p,c=candidate();s,_,t=inputs();c.update(now=10000,input_captured_at=10000,account_captured_at=9999,protection_ok=True,price_tick=.1);p['expires_at']=10200
    t['primary']['1h']['points'][0].update(ma10=p['hard_stop']+20,close=c['mark_price'])
    row=holding_management_view(s,c,t)['rows'][1]
    assert row['requires_stop_revision'] and row['candidate_stop']==p['hard_stop']
    assert validate_scenario(p,c)['risk']['within_budget']
    c['positions'][0]['quantity']=.1
    assert holding_management_view(s,c,t)['rows'][1]['candidate_stop']==p['hard_stop']
    with pytest.raises(ScenarioValidationError,match='risk budget exceeded'):
        validate_scenario(p,c)


def test_all_missing_ma_or_quarantine_does_not_add_useless_guidance():
    s,c,t=inputs();s['timeframes']={}
    assert holding_management_view(s,c,t) is None
    s,c,t=inputs();c['management_quarantine']=True
    assert holding_management_view(s,c,t) is None
    assert holding_management_instructions(SYSTEM_PROMPT,c)==SYSTEM_PROMPT


def test_malformed_optional_structure_is_unavailable_not_an_exception():
    s,c,t=inputs();assert holding_management_view(s,c,None) is None


def test_reaction_ratchet_waits_for_observed_favorable_progress_and_known_costs():
    s,c,t=inputs();c['mark_price']=101.
    v=holding_management_view(s,c,t)
    assert v['rows'][0]['ratchet_reference_active'] is False
    assert v['rows'][0]['candidate_stop']==c['previous_hard_stop']
    c['mark_price']=115.;c['recorded_scenario_net']=None
    v=holding_management_view(s,c,t)
    assert v['rows'][0]['ratchet_reference_active'] is None
    assert v['rows'][0]['candidate_stop']==c['previous_hard_stop']


def test_unknown_policy_version_or_new_risk_block_never_offers_restoration():
    s,c,t=inputs();c['stop_policy'].pop('version')
    assert holding_management_view(s,c,t)['rows'][1]['candidate_stop']==c['previous_hard_stop']
    c['stop_policy']['version']=1;c['new_risk_blocked']=True
    assert holding_management_view(s,c,t)['rows'][1]['candidate_stop']==c['previous_hard_stop']


def test_both_calculated_review_phases_use_one_holding_policy():
    from tests.test_scenario_entry_contract_wire import context,taped
    c=context();c.update(accounting_status='confirmed',protection_ok=True,pending_entries=[])
    args,calls=taped(c,status='WEAKENING',choose_wait=True)
    assert args[3]['outcome']['status']=='COMPLETE'
    for q in calls:
        text=q['system_prompt']
        assert text.count('SUSTAINED DIRECTION')==1 and '500 USDT' not in text
        assert '100-300 USDT' not in text and 'Retain broker hard SL' in text
    assert 'When weakening evidence or a nearer TP changes a held trade' not in calls[0]['system_prompt']


def test_management_reference_parameters_are_bound_to_loaded_provenance(monkeypatch):
    from live import scenario_position_flow as flow
    from live.scenario_provenance import review_policy_binding
    assert review_policy_binding()['loaded_parity']['live/scenario_position_flow.py']=='VERIFIED'
    monkeypatch.setattr(flow,'HOLDING_REFERENCES',[('REACTION','30m',1800000,.01)])
    with pytest.raises(ValueError,match='review_mixed_loaded_policy'):
        review_policy_binding()


def test_legacy_proposer_uses_the_same_scoped_holding_policy():
    import json
    from types import SimpleNamespace
    from tests.test_scenario_entry_contract_wire import context,wire
    from live.scenario_llm import propose
    from live.scenario_preview import response_contract
    c=context();c.update(accounting_status='confirmed',protection_ok=True,pending_entries=[])
    captured=[]
    def generate(**q):
        captured.append(q)
        return SimpleNamespace(text=json.dumps(wire(c,'WAIT','INTACT')))
    out=propose(dict(valid=True,as_of_ms=1000000),c,response_contract(c),generate=generate,clock=lambda:1000)
    assert out['action']=='WAIT'
    assert captured[0]['system_prompt'].count('SUSTAINED DIRECTION')==1
    assert '100-300 USDT' not in captured[0]['system_prompt']
    base=SYSTEM_PROMPT+MA_STRUCTURE_PROMPT+FLAT_ENTRY_PROMPT
    assert holding_management_instructions(base,{**c,'scenario_id':None})==base


def test_entry_redesign_keeps_confirmed_holding_prompt_identical():
    import hashlib
    _,c,_=inputs()
    text=holding_management_instructions(SYSTEM_PROMPT+MA_STRUCTURE_PROMPT,c)
    assert hashlib.sha256(text.encode()).hexdigest()=='5569c9efb734a2cd494607671d0b5f6a90e143904118fdd2ad491d89fef06bf1'
