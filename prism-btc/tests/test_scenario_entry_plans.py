from copy import deepcopy
from functools import partial
import json
from types import SimpleNamespace
import pytest

from live.scenario_entry_plans import prepare, constrain, _preview, MAX_PLANS, MAX_BYTES
from live.scenario_calculated_review import _action_allocation_branches, research_review
from live.scenario_contract import _matches
from live.scenario_preview import response_contract
from live.scenario_fee_projection import demo_entry_fee_reference
from analysis.scenario_review_tape import record_review,replay_review
from tests.test_scenario_entry_geometry import inputs
from tests.test_scenario_review_guard import context,wire
from tests.test_scenario_allocation_review import allocation_wire
from tests.test_scenario_review_guard import model_receipt_hash


def sample():
    s,geo,t=inputs();c=context();c.update(geo,input_captured_at=geo['now'],prepared_entry_plans_version=1,
        conditional_entry_version=1,entry_fee_reference=demo_entry_fee_reference())
    return s,c,t


def offer(c,p):
    raw=allocation_wire(c);raw.update(side=p['side'],expires_at=c['now']+60,hard_stop=p['hard_stop'],
        entries=[dict(id=p['id'],price=p['entry_price'],allocation_fraction=.5,trigger_price=p['trigger_price'])],
        take_profits=deepcopy(p['take_profits']),partial_stops=[],
        entry_readiness=dict(status='SUPPORTED_NOW',reason='Current primary-frame structure independently supports the entry.'))
    return raw


def test_coupled_examples_include_native_rounding_costs_and_dust_at_stop():
    s,c,t=sample();before=deepcopy((s,c,t));menu=prepare(s,c,t)
    assert menu['status']=='AVAILABLE' and 0<len(menu['layouts'])<=MAX_PLANS
    assert {p['side']for p in menu['layouts']}=={'LONG','SHORT'}
    assert len(json.dumps(menu).encode())<=MAX_BYTES
    for p in menu['layouts']:
        assert sum(v['fraction']for v in p['take_profits'])==1
        assert p['default_unpriced_runner_fraction']==0
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


@pytest.mark.parametrize('damage',['price','stop','side','target','chase'])
def test_prices_and_splits_cannot_be_mixed_between_layouts(damage):
    s,c,t=sample();menu=prepare(s,c,t);p=offer(c,menu['layouts'][0]);schema=constrain(_action_allocation_branches(c,{'OPEN'}),menu)
    assert _matches(p,schema)
    if damage=='price':p['entries'][0]['price']+=.1
    if damage=='stop':p['hard_stop']+=.1
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


@pytest.mark.parametrize('readiness,expected_calls,action,with_peer',[
    ('SUPPORTED_NOW',2,'OPEN',False),('WAIT_FOR_EVIDENCE',1,'WAIT',False),
    ('WAIT_FOR_EVIDENCE',2,'OPEN',True)])
@pytest.mark.parametrize('side',['LONG','SHORT'])
def test_two_phase_runtime_uses_menu_and_replays_with_original_host_checks(monkeypatch,readiness,expected_calls,action,side,with_peer):
    s,c,t=sample();calls=[]
    s.setdefault('timeframes',{}).setdefault('30m',{})['volume_4d_30m']={'latest_closed_over_overall_median':.2}
    # Supply the same timestamped structure to this synthetic fixture, not real model calls.
    from live import scenario_calculated_review as m
    monkeypatch.setattr(m,'build_ma_structure_context',lambda snapshot,ctx:t)
    def generate(**q):
        from live.scenario_model_input import restore_model_input
        calls.append(q);body=restore_model_input(json.loads(q['user_prompt']))
        if 'wait_proposal'in q['response_schema']['properties']:
            menu=body['prepared_entry_plans'];p=offer(c,next(v for v in menu['layouts']if v['side']==side));wait=wire(c,'WAIT');wait['expires_at']=c['now']+60
            p['entry_readiness']=dict(status=readiness,reason='Low participation needs a new reversal observation.'if readiness=='WAIT_FOR_EVIDENCE'else'Price structure already supports entry despite low volume.')
            result=dict(wait_proposal=wait,candidates=[p],no_candidate_reason='')
            if with_peer:
                result['candidates'].append(offer(c,next(v for v in menu['layouts']if v['id']!=p['entries'][0]['id'])))
            assert body['response_contract']['authority'].startswith('API response_schema')
        else:
            assert body['prepared_entry_plan_sources']
            chosen=next(v for v in body['calculated_candidates']['candidates']if v['eligible'])
            result=dict(selection_token=chosen['id']+':'+model_receipt_hash(chosen),rationale='구조와 검산 검토')
        assert _matches(result,q['response_schema'])
        return SimpleNamespace(text=json.dumps(result))
    review=partial(research_review,research_mode='economic-reject-calculated-v1');contract=response_contract(c)
    tape=record_review(review,s,c,contract,generate=generate,record=lambda e:None,clock=lambda:c['now'])
    assert tape['outcome']['status']=='COMPLETE',tape['outcome']
    result=tape['outcome']['result']
    selected=result['selected']
    assert len(calls)==expected_calls and (selected.get('effective_plan')or selected['raw_proposal'])['action']==action
    assert 'entry_readiness'not in result['selected']['raw_proposal']
    assert result['candidates']['entry_readiness_reviews'][0]['selectable']==(readiness=='SUPPORTED_NOW')
    if with_peer:
        assert result['selected']['id']=='c1'
        assert [v['id']for v in result['candidates']['candidates']]==['c1']
    if action=='WAIT':
        assert result['candidates']['candidates']==[]
        assert result['candidates']['no_candidate_category']=='ENTRY_EVIDENCE_PENDING'
    assert replay_review(tape,review,s,c,contract)==tape['outcome']


@pytest.mark.parametrize('damage',['missing','unknown'])
def test_entry_readiness_cannot_default_missing_or_unknown_to_ready(damage):
    s,c,t=sample();menu=prepare(s,c,t);p=offer(c,menu['layouts'][0])
    if damage=='missing':p.pop('entry_readiness')
    else:p['entry_readiness']['status']='UNKNOWN'
    assert not _matches(p,constrain(_action_allocation_branches(c,{'OPEN'}),menu))


def test_duplicate_targets_remain_rejected_by_original_host():
    from live.scenario_calculated_review import calculate_economic_candidate
    s,c,t=sample();menu=prepare(s,c,t);raw=offer(c,menu['layouts'][0])
    raw.pop('entry_readiness')
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


def test_forming_and_confirmed_invalidation_options_keep_their_source_phase():
    s,c,t=sample();c.pop('conditional_entry_version');forming=deepcopy(t['primary']['30m']['points'][0]);forming.update(phase='FORMING',is_confirmed=False,as_of_ms=s['as_of_ms']-1,high=103.,low=97.)
    t['primary']['30m']['points'].append(forming);menu=prepare(s,c,t)
    short=[p for p in menu['layouts']if p['side']=='SHORT'and p['entry_kind']=='CURRENT_LIMIT']
    assert {p['stop_phase']for p in short}=={'FORMING','CONFIRMED'}
    assert next(p['hard_stop']for p in short if p['stop_phase']=='FORMING')<next(p['hard_stop']for p in short if p['stop_phase']=='CONFIRMED')
    assert all(p['support_strength']=='NOT_CERTIFIED'for p in short)


@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
def test_stop_variants_do_not_crowd_out_feasible_entry_styles(side):
    s,c,t=sample()
    forming=deepcopy(t['primary']['30m']['points'][0])
    forming.update(phase='FORMING',is_confirmed=False,as_of_ms=s['as_of_ms']-1,high=103.,low=97.)
    t['primary']['30m']['points'].append(forming)
    menu=prepare(s,c,t)
    plans=[p for p in menu['layouts']if p['side']==side]
    assert len(plans)<=MAX_PLANS//2
    assert {p['entry_kind']for p in plans}=={'CURRENT_LIMIT','BREAKOUT_TRIGGER','PULLBACK_LIMIT'}


@pytest.mark.parametrize('side,forming_close,confirmed_close,expected', [
    ('SHORT', 100.2, 102., 102.), ('LONG', 99.9, 98., 98.)])
def test_pullback_uses_independent_structure_instead_of_forming_close(
        side, forming_close, confirmed_close, expected):
    s,c,t=sample()
    for point in t['primary']['30m']['points']:
        point['close']=confirmed_close
    forming=deepcopy(t['primary']['30m']['points'][0])
    forming.update(phase='FORMING',is_confirmed=False,as_of_ms=s['as_of_ms']-1,
                   high=103.,low=97.,close=forming_close)
    t['primary']['30m']['points'].append(forming)
    menu=prepare(s,c,t)
    plan=next(p for p in menu['layouts']if p['side']==side and p['entry_kind']=='PULLBACK_LIMIT')
    assert plan['entry_price']==expected


@pytest.mark.parametrize('side,price', [('SHORT', 100.2), ('LONG', 99.9)])
def test_pullback_keeps_same_price_when_confirmed_structure_also_supports_it(side,price):
    s,c,t=sample()
    t['primary']['30m']['points'][0]['close']=price
    forming=deepcopy(t['primary']['30m']['points'][0])
    forming.update(phase='FORMING',is_confirmed=False,as_of_ms=s['as_of_ms']-1,
                   high=103.,low=97.)
    t['primary']['30m']['points'].append(forming)
    plan=next(p for p in prepare(s,c,t)['layouts']if p['side']==side and p['entry_kind']=='PULLBACK_LIMIT')
    assert plan['entry_price']==price


@pytest.mark.parametrize('side,close', [('SHORT', 100.2), ('LONG', 99.9)])
def test_forming_close_alone_cannot_create_a_pullback(monkeypatch,side,close):
    from live import scenario_entry_plans as m
    s,c,t=sample()
    points=[['P:30m:0:high',103.,'CONFIRMED',s['as_of_ms']-1],
            ['P:30m:0:low',97.,'CONFIRMED',s['as_of_ms']-1],
            ['P:30m:1:close',close,'FORMING',s['as_of_ms']-1]]
    monkeypatch.setattr(m,'source_from_structure',lambda *args:dict(points=points))
    plans=m.prepare(s,c,t)['layouts']
    assert any(p['side']==side and p['entry_kind']=='CURRENT_LIMIT'for p in plans)
    assert not any(p['side']==side and p['entry_kind']=='PULLBACK_LIMIT'for p in plans)


def test_layout_keeps_prices_but_allows_exit_weights_and_subset():
    s,c,t=sample();menu=prepare(s,c,t)
    plan=next(p for p in menu['layouts'] if len(p['take_profits'])==2)
    raw=offer(c,plan);schema=constrain(_action_allocation_branches(c,{'OPEN'}),menu)
    raw['take_profits'][0]['fraction']=.1
    raw['take_profits'][1]['fraction']=.7
    assert _matches(raw,schema)
    raw['take_profits']=raw['take_profits'][1:]
    raw['take_profits'][0]['fraction']=1.
    assert _matches(raw,schema)


def test_no_sampled_allocation_does_not_remove_structural_prices(monkeypatch):
    from live import scenario_entry_plans as m
    s,c,t=sample();monkeypatch.setattr(m,'_preview',lambda *args:[])
    menu=m.prepare(s,c,t)
    assert menu['status']=='AVAILABLE' and menu['layouts']
    assert all(p['sizing_examples']==[] for p in menu['layouts'])


@pytest.mark.parametrize('side',['LONG','SHORT'])
def test_example_exit_weights_pass_actual_host_not_only_schema(side):
    from live.scenario_calculated_review import calculate_economic_candidate
    s,c,t=sample();menu=prepare(s,c,t)
    tested=0
    for p in menu['layouts']:
        if p['side']!=side:continue
        for example in p['sizing_examples']:
            raw=offer(c,p);raw.pop('entry_readiness')
            raw['confidence']=example['confidence']
            raw['entries'][0]['allocation_fraction']=example['allocation_fraction']
            raw['take_profits']=[dict(p['take_profits'][i],fraction=f) for i,f in zip(example['target_indices'],example['target_fractions'])]
            receipt=calculate_economic_candidate(raw,c,candidate_id='c0')
            assert receipt['eligible'],receipt.get('rejection')
            assert receipt['entry_economics']['ratio']>=1.2
            tested+=1
    assert tested


@pytest.mark.parametrize('side,nearest_stop', [('LONG',94.9),('SHORT',105.1)])
def test_cost_supported_stop_variant_is_not_hidden_by_unassessed_same_style(monkeypatch,side,nearest_stop):
    from live import scenario_entry_plans as m
    s,c,t=sample();before=deepcopy((s,c,t));original=m._preview
    def preview(raw,context):
        if raw['side']==side and raw['hard_stop']==nearest_stop:return []
        return original(raw,context)
    monkeypatch.setattr(m,'_preview',preview)
    plans=[p for p in m.prepare(s,c,t)['layouts']if p['side']==side][:3]
    assert [p['entry_kind']for p in plans]==['CURRENT_LIMIT','BREAKOUT_TRIGGER','PULLBACK_LIMIT']
    assert all(p['sizing_examples']for p in plans)
    assert all(p['hard_stop']!=nearest_stop for p in plans)
    assert (s,c,t)==before



def test_extra_stops_preserve_every_original_plan_and_cost_example(monkeypatch):
    from live import scenario_entry_plans as m
    s,c,t=sample();before=deepcopy((s,c,t))
    monkeypatch.setattr(m,'MAX_PLANS',6);original=m.prepare(s,c,t)
    monkeypatch.setattr(m,'MAX_PLANS',12);expanded=m.prepare(s,c,t)
    assert expanded['layouts'][:len(original['layouts'])]==original['layouts']
    assert len(expanded['layouts'])>len(original['layouts'])
    assert expanded['meaning']==original['meaning']
    assert len(json.dumps(expanded,ensure_ascii=False,allow_nan=False).encode())<=m.MAX_BYTES
    for side in ('LONG','SHORT'):
        assert len({p['hard_stop']for p in expanded['layouts']
                    if p['side']==side and p['entry_kind']=='CURRENT_LIMIT'})==2
    assert (s,c,t)==before


def test_extra_stops_cannot_overflow_and_remove_the_original_menu(monkeypatch):
    from live import scenario_entry_plans as m
    s,c,t=sample();monkeypatch.setattr(m,'MAX_PLANS',6);original=m.prepare(s,c,t)
    size=len(json.dumps(original,ensure_ascii=False,allow_nan=False).encode())
    monkeypatch.setattr(m,'MAX_PLANS',12);monkeypatch.setattr(m,'MAX_BYTES',size)
    assert m.prepare(s,c,t)==original
    monkeypatch.setattr(m,'MAX_BYTES',size-1)
    assert m.prepare(s,c,t)['status']=='UNAVAILABLE'
