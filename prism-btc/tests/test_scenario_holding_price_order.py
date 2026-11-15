"""Numeric ordering only: old confirmed extrema must not move across a TP."""
from copy import deepcopy

import pytest

from live.scenario_model_input import project_model_input,restore_model_input
from live.scenario_position_flow import project_holding_price_order_input,_size


def payload(side='SHORT'):
    return dict(contract_context=dict(immediate_reduce_version=1,scenario_id='holding',side=side,
        positions=[dict(price=80700,quantity=.01)],mark_price=80600,
        current_plan=dict(take_profits=[dict(id='tp',price=80487.3,fraction=.5)]),
        ma_structure=dict(primary={frame:dict(points=[
            dict(phase='CONFIRMED',is_confirmed=True,low=80450.7,high=80720),
            dict(phase='CONFIRMED',is_confirmed=True,low=80551.8,high=80700),
            dict(phase='CONFIRMED',is_confirmed=True,low=80570,high=80680),
            dict(phase='FORMING',is_confirmed=False,low=80590,high=80660)]) for frame in ('30m','1h')})),
        market_snapshot={},response_contract={})


@pytest.mark.parametrize('side',['LONG','SHORT'])
def test_older_confirmed_low_is_on_correct_side_of_first_tp(side):
    raw=payload(side)
    before=deepcopy(raw)
    baseline,audit=project_model_input(raw)
    projected,_=project_holding_price_order_input(baseline,audit)
    restored=restore_model_input(projected)
    view=restored['contract_context'].pop('holding_price_order')
    prices=[row[0] for row in view['prices']]
    assert prices==sorted(prices,reverse=side=='SHORT')
    if side=='SHORT':
        assert prices.index(80551.8)<prices.index(80487.3)<prices.index(80450.7)
    else:
        assert prices.index(80450.7)<prices.index(80487.3)<prices.index(80551.8)
    old=next(refs for price,refs in view['prices'] if price==80450.7)
    assert '1h.0.low' in old and '30m.0.low' in old
    assert restored==restore_model_input(baseline) and raw==before
    assert _size(view)<=2000


@pytest.mark.parametrize('fault',['no_version','flat','no_position'])
def test_scope_exclusions_are_byte_identical(fault):
    raw=payload()
    if fault=='no_version':
        raw['contract_context'].pop('immediate_reduce_version')
    if fault=='flat':
        raw['contract_context']['scenario_id']=None
    if fault=='no_position':
        raw['contract_context']['positions']=[]
    baseline,audit=project_model_input(raw)
    assert project_holding_price_order_input(baseline,audit)==(baseline,audit)


def test_missing_sources_and_equal_prices_are_not_invented():
    raw=payload()
    raw['contract_context']['ma_structure']['primary'].pop('1h')
    raw['contract_context']['current_plan']['take_profits'][0]['price']=80600
    projected,_=project_holding_price_order_input(project_model_input(raw)[0])
    view=restore_model_input(projected)['contract_context']['holding_price_order']
    assert view['status']=='PARTIAL'
    assert next(refs for p,refs in view['prices'] if p==80600)==['M','T0']
    assert all(not ref.startswith('1h.') for _,refs in view['prices'] for ref in refs)


def test_optional_view_does_not_evict_existing_history_economics_or_receipts():
    raw=payload()
    raw['existing_economics']='preserve'
    raw['padding']='x'*(100000-_size(raw)-13)
    baseline,audit=project_model_input(raw)
    assert _size(baseline)<=100000 and _size(baseline)>99900
    projected,_=project_holding_price_order_input(baseline,audit)
    restored=restore_model_input(projected)
    view=restored['contract_context'].pop('holding_price_order',None)
    assert view is None or view['status']=='OMITTED_INPUT_BUDGET'
    assert restored==restore_model_input(baseline)
    assert _size(projected)<=100000


def test_overbound_targets_are_not_silently_truncated():
    raw=payload()
    raw['contract_context']['current_plan']['take_profits']*=21
    projected,_=project_holding_price_order_input(project_model_input(raw)[0])
    assert restore_model_input(projected)['contract_context']['holding_price_order']['status']=='UNAVAILABLE'


def test_final_single_draft_select_paths_add_only_optional_view():
    import json
    from types import SimpleNamespace
    from live.scenario_llm import propose
    from live.scenario_calculated_review import _model_payload,_base_model_payload
    from live.scenario_preview import response_contract
    from tests.test_scenario_immediate_reduce_contract import holding
    from tests.test_scenario_llm import wire
    ctx=holding()
    ctx['current_plan']={'take_profits':[dict(id='tp',price=61000,fraction=.5)]}
    original=deepcopy(ctx)
    snapshot=dict(valid=True,as_of_ms=1000000)
    contract=response_contract(ctx)
    calls=[]
    def generate(**request):
        calls.append(request)
        return SimpleNamespace(text=json.dumps(dict(wire(ctx),reduce_fraction=None)))
    propose(snapshot,ctx,contract,generate=generate,clock=lambda:1000)
    assert 'holding_price_order' in restore_model_input(json.loads(calls[0]['user_prompt']))['contract_context']
    assert 'Use the correct LONG/SHORT price ordering' in calls[0]['system_prompt']
    for bundle in (None,{'candidates':[]}):
        baseline=_base_model_payload(snapshot,ctx,contract,calculated_candidates=bundle)
        actual=restore_model_input(_model_payload(snapshot,ctx,contract,calculated_candidates=bundle))
        assert actual['contract_context'].pop('holding_price_order')['status']=='PARTIAL'
        assert actual==restore_model_input(baseline)
    assert ctx==original
