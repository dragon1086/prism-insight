from copy import deepcopy
from functools import partial
import json
from types import SimpleNamespace
import pytest

from live.scenario_entry_plans import prepare, constrain, _preview, MAX_BYTES
from live.scenario_calculated_review import _action_allocation_branches, research_review
from live.scenario_contract import _matches
from live.scenario_preview import response_contract
from live.scenario_fee_projection import demo_entry_fee_reference
from analysis.scenario_review_tape import record_review,replay_review
from tests.test_scenario_entry_geometry import inputs
from tests.test_scenario_review_guard import context,wire
from tests.test_scenario_allocation_review import allocation_wire


def sample():
    s,geo,t=inputs();c=context();c.update(geo,input_captured_at=geo['now'],prepared_entry_plans_version=1,
        conditional_entry_version=1,entry_fee_reference=demo_entry_fee_reference())
    return s,c,t


def offer(c,p):
    raw=allocation_wire(c);raw.update(side=p['side'],expires_at=c['now']+60,hard_stop=p['hard_stop'],
        entries=[dict(id=p['id'],price=p['entry_price'],allocation_fraction=.5,trigger_price=p['trigger_price'])],
        take_profits=deepcopy(p['take_profits']),partial_stops=[])
    return raw


def test_coupled_examples_include_native_rounding_costs_and_dust_at_stop():
    s,c,t=sample();before=deepcopy((s,c,t));menu=prepare(s,c,t)
    assert menu['status']=='AVAILABLE' and 0<len(menu['layouts'])<=6
    assert {p['side']for p in menu['layouts']}=={'LONG','SHORT'}
    assert len(json.dumps(menu).encode())<=MAX_BYTES
    for p in menu['layouts']:
        assert sum(v['fraction']for v in p['take_profits'])==1
        assert p['unpriced_runner_fraction']==0
        assert p['fill_probability']=='UNKNOWN'and p['support_strength']=='NOT_CERTIFIED'
        raw=dict(action='OPEN',side=p['side'],hard_stop=p['hard_stop'],
            entries=[dict(id=p['id'],price=p['entry_price'])],take_profits=p['take_profits'],partial_stops=[])
        if p['trigger_price']is not None:raw['entries'][0]['trigger_price']=p['trigger_price']
        assert _preview(raw,c)==p['sizing_examples']
        for v in p['sizing_examples']:
            assert v['ratio']>=1.2 and v['reserved_risk']<=c['initial_equity']*.02
            assert all(q>=c['minimum_quantity']for q in v['target_quantities'])
            assert v['residual_quantity']==pytest.approx(v['quantity']-sum(v['target_quantities']))
    assert (s,c,t)==before


@pytest.mark.parametrize('damage',['price','stop','fraction','side','target','chase'])
def test_prices_and_splits_cannot_be_mixed_between_layouts(damage):
    s,c,t=sample();menu=prepare(s,c,t);p=offer(c,menu['layouts'][0]);schema=constrain(_action_allocation_branches(c,{'OPEN'}),menu)
    assert _matches(p,schema)
    if damage=='price':p['entries'][0]['price']+=.1
    if damage=='stop':p['hard_stop']+=.1
    if damage=='fraction':p['take_profits'][0]['fraction']=.4
    if damage=='side':p['side']='SHORT'
    if damage=='target':p['take_profits'][0]['price']+=.1
    if damage=='chase':p['chase']['max_bps']=1
    assert not _matches(p,schema)


@pytest.mark.parametrize('damage',[lambda s,c,t:c.update(positions=[dict(price=100,quantity=1)]),
    lambda s,c,t:c.update(pending_entries=[dict(price=100,quantity=1)]),lambda s,c,t:c.update(new_risk_blocked=True),
    lambda s,c,t:c.update(fees_paid=None),lambda s,c,t:c['limit_price_quotes'].update(captured_at=1),
    lambda s,c,t:s.update(valid=False),lambda s,c,t:c.update(protection_ok=False)])
def test_missing_unsafe_or_stale_inputs_never_get_certified(damage):
    s,c,t=sample();damage(s,c,t);assert prepare(s,c,t)['layouts']==[]


def test_unavailable_direction_keeps_original_contract_and_no_facts_are_changed():
    s,c,t=sample();menu=prepare(s,c,t);menu['layouts']=[v for v in menu['layouts']if v['side']=='SHORT']
    schema=constrain(_action_allocation_branches(c,{'OPEN'}),menu)
    assert any(b['properties']['side']['enum']==['LONG']for b in schema['anyOf'])


def test_two_phase_runtime_uses_menu_and_replays_with_original_host_checks(monkeypatch):
    s,c,t=sample();calls=[]
    # Supply the same timestamped structure to this synthetic fixture, not real model calls.
    from live import scenario_calculated_review as m
    monkeypatch.setattr(m,'build_ma_structure_context',lambda snapshot,ctx:t)
    def generate(**q):
        from live.scenario_model_input import restore_model_input
        calls.append(q);body=restore_model_input(json.loads(q['user_prompt']))
        if 'wait_proposal'in q['response_schema']['properties']:
            menu=body['prepared_entry_plans'];p=offer(c,menu['layouts'][0]);wait=wire(c,'WAIT');wait['expires_at']=c['now']+60
            result=dict(wait_proposal=wait,candidates=[p],no_candidate_reason='')
            assert body['response_contract']['authority'].startswith('API response_schema')
        else:
            assert body['prepared_entry_plan_sources']
            chosen=next(v for v in body['calculated_candidates']['candidates']if v['eligible'])
            result=dict(selection_token=chosen['id']+':'+chosen['receipt_sha256'],rationale='구조와 검산 검토')
        assert _matches(result,q['response_schema'])
        return SimpleNamespace(text=json.dumps(result))
    review=partial(research_review,research_mode='economic-reject-calculated-v1');contract=response_contract(c)
    tape=record_review(review,s,c,contract,generate=generate,record=lambda e:None,clock=lambda:c['now'])
    assert tape['outcome']['status']=='COMPLETE',tape['outcome']
    assert len(calls)==2 and tape['outcome']['result']['selected']['effective_plan']['action']=='OPEN'
    assert replay_review(tape,review,s,c,contract)==tape['outcome']


def test_duplicate_targets_remain_rejected_by_original_host():
    from live.scenario_calculated_review import calculate_economic_candidate
    s,c,t=sample();menu=prepare(s,c,t);raw=offer(c,menu['layouts'][0])
    raw['take_profits']=[deepcopy(raw['take_profits'][0]),deepcopy(raw['take_profits'][0])]
    with pytest.raises(ValueError):calculate_economic_candidate(raw,c,candidate_id='c0')


def test_new_replay_profile_has_all_live_flags_without_changing_old_profiles(tmp_path):
    import sqlite3
    from analysis.scenario_replay import contract_for,run_replay
    from tests.test_scenario_replay import inputs as market
    b,m=market(300000)
    old=contract_for(b,'OHLC',1,10000,policy_profile='deployed-cost-reference-v4')
    assert old['broker_flags']==dict(entry_economics_enabled=True,entry_fee_reference_enabled=True)
    new=contract_for(b,'OHLC',1,10000,policy_profile='deployed-entry-plans-v5')
    assert new['broker_flags']['prepared_entry_plans_enabled'] is True
    assert new['broker_flags']['convergence_entry_enabled'] is True
    run_replay(b,m,tmp_path/'run',mode='fixture',tape_path=tmp_path/'tape',policy_profile='deployed-entry-plans-v5',
        model_generator=lambda **q:SimpleNamespace(text='{}'))
    with sqlite3.connect(tmp_path/'run'/'replay.sqlite')as conn:
        c=json.loads(conn.execute('SELECT context FROM llm_scenario_decisions').fetchone()[0])
        assert c['prepared_entry_plans_version']==1
        assert c['entry_fee_reference']['slippage_rate_per_fill']==.0005
        assert conn.execute('SELECT count(*)FROM llm_scenario_intents').fetchone()[0]==0
