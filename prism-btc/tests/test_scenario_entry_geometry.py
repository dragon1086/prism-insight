from copy import deepcopy
import json
import pytest
from live.scenario_entry_geometry import build,project,MAX_BYTES
from live.scenario_entry_economics import policy
from live.scenario_model_input import project_model_input,restore_model_input


def inputs():
    c={'scenario_id':None,'positions':[],'pending_entries':[],'accounting_status':'confirmed','now':10000,'account_captured_at':9999.9,'mark_price':100,'initial_equity':10000,'estimated_cost_rate':.002,'slippage_bps':20,'entry_economics':policy(),'limit_price_quotes':{'bid':100,'ask':100.1,'captured_at':9999.9},'observed_execution_fee_evidence':{'status':'AVAILABLE','basis':'HISTORICAL_VERIFIED_EXECUTIONS','source_sha256':'a'*64,'rates':{'TAKER':{'count':1,'minimum':.00055,'maximum':.00055,'earliest_at':9990,'latest_at':9990}}}}
    p={'phase':'CONFIRMED','is_confirmed':True,'as_of_ms':9000000,'high':115,'low':90,'ma10':98,'ma35':110,'close':100}
    t={'primary':{'30m':{'points':[p,dict(p,high=105,low=95)]},'1h':{'points':[]}},'levels':{}}
    s={'valid':True,'as_of_ms':10000000,'timeframes':{'30m':{'confirmed':{'is_confirmed':True,'open_time_ms':7200000,'atr14_risk_only':10}}}}
    return s,c,t


def rows(view):return [dict(zip(view['columns'],r)) for r in view['rows']]


def test_both_directions_and_observed_level_alternative_are_bounded():
    s,c,t=inputs();before=deepcopy((s,c,t));v=build(s,c,t)
    assert v['status']=='AVAILABLE' and len(v['rows'])<=12
    assert {r['side'] for r in rows(v)}=={'LONG','SHORT'}
    assert {r['entry_kind'] for r in rows(v)}=={'QUOTE_REFERENCE','LEVEL_LIMIT_IF_FILLED'}
    assert len(json.dumps(v,ensure_ascii=False).encode())<=MAX_BYTES
    assert (s,c,t)==before


def test_fee_arithmetic_is_before_generation_not_an_order_or_win_rate():
    s,c,t=inputs();v=build(s,c,t)
    for r in rows(v):
        e=r['worst_entry_bound'];stop=r['stop_level'];target=r['target_level'];f=.00055;sign=1 if r['side']=='LONG' else -1
        reward=sign*(target-e)-(e+target)*f;loss=sign*(e-stop)+(e+stop)*f
        assert r['reward_per_btc']==pytest.approx(reward)
        assert r['loss_per_btc']==pytest.approx(loss)
        assert r['single_target_ratio']==pytest.approx(reward/loss)
        assert 'action' not in r and 'eligible' not in r and 'probability' not in r
    assert any(r['single_target_ratio']<0 for r in rows(v)) is False  # Good fixture levels, not a gate.


def test_missing_fees_use_named_reserve_and_keep_actual_unknown():
    s,c,t=inputs();c.pop('observed_execution_fee_evidence');v=build(s,c,t)
    assert v['status']=='AVAILABLE' and {r['cost_basis'] for r in rows(v)}=={'RESERVE'}
    assert c.get('observed_execution_fee_evidence') is None


@pytest.mark.parametrize('mutation',[lambda s,c,t:c.update(scenario_id='held'),lambda s,c,t:c.update(positions=[{'price':100,'quantity':1}]),lambda s,c,t:c.update(pending_entries=[{'quantity':1}]),lambda s,c,t:c.update(accounting_status='pending'),lambda s,c,t:c['limit_price_quotes'].update(captured_at=9980),lambda s,c,t:s.update(as_of_ms=10000001),lambda s,c,t:c['limit_price_quotes'].update(ask=float('nan'))])
def test_unsafe_or_stale_inputs_do_not_make_geometry(mutation):
    s,c,t=inputs();mutation(s,c,t);assert build(s,c,t)['status']=='UNAVAILABLE'


def test_future_and_forming_levels_are_not_used_and_atr_unknown_stays_none():
    s,c,t=inputs();t['primary']['30m']['points'] += [dict(t['primary']['30m']['points'][0],phase='FORMING',is_confirmed=False,low=99,high=101),dict(t['primary']['30m']['points'][0],as_of_ms=11000000,low=97,high=103)]
    s['timeframes']['30m']['confirmed']['is_confirmed']=False
    v=build(s,c,t)
    assert all(r['stop_level'] not in [99,97] and r['target_level'] not in [101,103] for r in rows(v))
    assert all(r['stop_distance_atr30'] is None for r in rows(v))


def test_projection_preserves_every_preexisting_fact_history_and_schema():
    s,c,t=inputs();c['ma_structure']=t;c['history']=[{'reason':'keep'}];raw={'market_snapshot':s,'contract_context':c,'response_contract':{'keep':True}}
    base,_=project_model_input(raw);before=deepcopy(base);after=restore_model_input(project(base,s,c));view=after['contract_context'].pop('entry_geometry')
    assert view['status']=='AVAILABLE' and after==restore_model_input(base) and base==before


def test_optional_view_never_trades_history_for_bytes(monkeypatch):
    from live import scenario_entry_geometry as m
    s,c,t=inputs();c['ma_structure']=t;base,_=project_model_input({'market_snapshot':s,'contract_context':c})
    monkeypatch.setattr(m,'INPUT_LIMIT',len(json.dumps(base,ensure_ascii=False,separators=(',',':')).encode())+10)
    assert project(base,s,c)==base


def test_thin_room_stays_negative_instead_of_becoming_a_valid_plan():
    s,c,t=inputs();p=t['primary']['30m']['points'][0];t['primary']['30m']['points']=[dict(p,high=100.15,low=99.95,ma10=99.98,ma35=100.05)]
    v=build(s,c,t)
    assert v['status']=='AVAILABLE' and any(r['single_target_ratio']<0 for r in rows(v))
    assert any(r['fee_to_price_stop_risk']>1 for r in rows(v))


def test_round_reference_uses_existing_static_worst_entry_bound():
    from core.scenario_limit_prices import POLICY_VERSION,possible_entry_price_anchor
    s,c,t=inputs();c.update(execution_price_policy={'version':POLICY_VERSION},price_tick=.1)
    v=build(s,c,t)
    for r in rows(v):
        raw=r['raw_entry_reference'];anchor=possible_entry_price_anchor({'price':raw},c,r['side'])
        expected=(max(raw,float(anchor)) if r['side']=='LONG' else min(raw,float(anchor))) if anchor is not None else raw
        assert r['worst_entry_bound']==expected


def test_holding_or_conflicting_inputs_are_unchanged():
    s,c,t=inputs();c['ma_structure']=t;base,_=project_model_input({'market_snapshot':s,'contract_context':c})
    altered=deepcopy(c);altered['limit_price_quotes']['bid']=99
    assert project(base,s,altered)==base
    held=deepcopy(base);logical=restore_model_input(held);logical['contract_context']['scenario_id']='active';held,_=project_model_input(logical)
    assert project(held,s,c)==held


def test_other_projected_history_is_never_evicted(monkeypatch):
    import live.scenario_model_input as model_input
    s,c,t=inputs();c.update(ma_structure=t,history=[{'reason':'keep'}]);base,_=project_model_input({'market_snapshot':s,'contract_context':c});real=model_input.project_model_input
    def evict(value,**kwargs):
        value=deepcopy(value);value['contract_context'].pop('history',None);return real(value,**kwargs)
    monkeypatch.setattr(model_input,'project_model_input',evict)
    assert project(base,s,c)==base


def test_real_payload_pipeline_only_adds_view_before_draft(monkeypatch):
    from live.scenario_calculated_review import _model_payload
    s,c,t=inputs();c.update(ma_structure=t,input_id="geometry-test",revision=0)
    monkeypatch.setattr("live.scenario_calculated_review.build_ma_structure_context",lambda *args:t)
    draft=restore_model_input(_model_payload(s,c,{}))
    select=restore_model_input(_model_payload(s,c,{},calculated_candidates={}))
    assert draft['contract_context']['entry_geometry']['status']=='AVAILABLE'
    assert 'entry_geometry' not in select['contract_context']
    c['scenario_id']='held'
    assert 'entry_geometry' not in restore_model_input(_model_payload(s,c,{}))['contract_context']


def test_geometry_limits_are_part_of_loaded_code_provenance(monkeypatch):
    from live import scenario_entry_geometry as geometry
    from live.scenario_provenance import review_policy_binding
    before=review_policy_binding()
    assert before['loaded_parity']['live/scenario_entry_geometry.py']=='VERIFIED'
    monkeypatch.setattr(geometry,'MAX_ROWS',100)
    with pytest.raises(ValueError,match='review_mixed_loaded_policy'):
        review_policy_binding()


def test_cost_share_and_both_confirmed_atrs_are_computed_before_draft():
    s,c,t=inputs();s['timeframes']['1h']={'confirmed':{'is_confirmed':True,'open_time_ms':3600000,'atr14_risk_only':20}}
    c.update(entry_contract_version=2,input_captured_at=9999.)
    v=build(s,c,t)
    assert v['version']==2 and v['entry_deadline_seconds_remaining']==200.
    for r in rows(v):
        gap=abs(r['worst_entry_bound']-r['stop_level'])
        reference=c['limit_price_quotes']['ask' if r['side']=='LONG' else 'bid']
        assert r['stop_distance_atr1h']==pytest.approx(gap/20,abs=1e-6)
        assert r['entry_distance_atr30']==pytest.approx(abs(r['raw_entry_reference']-reference)/10,abs=1e-6)
        assert r['fee_to_price_stop_risk']==pytest.approx((r['worst_entry_bound']+r['stop_level'])*.00055/gap,abs=1e-6)
        assert r['reserve_to_price_stop_risk'] is None


def test_unknown_fees_and_unconfirmed_atr_do_not_turn_into_zero():
    s,c,t=inputs();c.pop('observed_execution_fee_evidence');s['timeframes']['1h']={'confirmed':{'is_confirmed':True,'open_time_ms':0,'atr14_risk_only':20}}
    v=build(s,c,t)
    assert v['entry_deadline_seconds_remaining'] is None
    for r in rows(v):
        assert r['fee_to_price_stop_risk'] is None and r['stop_distance_atr1h'] is None
        risk=abs(r['worst_entry_bound']-r['stop_level'])
        assert r['reserve_to_price_stop_risk']==pytest.approx((r['loss_per_btc']-risk)/risk,abs=1e-6)


def test_expired_original_entry_deadline_is_not_extended():
    s,c,t=inputs();c.update(entry_contract_version=2,input_captured_at=9890.)
    assert build(s,c,t)['entry_deadline_seconds_remaining']==0
