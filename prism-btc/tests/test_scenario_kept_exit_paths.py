"""No-change payoffs must use live leaves, not resized historical TP fractions."""
from copy import deepcopy
import pytest
from live.scenario_calculated_review import _kept_exit_paths,_project_kept_exit_paths,_model_payload
from live.scenario_model_input import project_model_input,restore_model_input


def context(side='LONG'):
    sign=1 if side=='LONG' else -1
    return dict(input_id='kept-fixture',revision=1,initial_equity=10000.,scenario_id='held',side=side,accounting_status='confirmed',protection_ok=True,
        positions=[dict(price=100.,quantity=.6)],pending_entries=[],now=1000.,account_captured_at=999.,
        mark_price=100.+sign*5,previous_hard_stop=100.-sign*10,recorded_scenario_net=2.5,
        current_plan=dict(take_profits=[dict(id='old',price=999.,fraction=1.)]),
        minimum_quantity=.001,quantity_step=.001,
        target_status=[dict(status='TERMINAL',kind='tp',remaining_quantity='',filled_quantity='.4'),
            dict(status='LIVE',kind='tp',intent_id='a',target_id='t1',price=str(100+sign*10),remaining_quantity='.2'),
            dict(status='LIVE',kind='tp',intent_id='a',target_id='t2',price=str(100+sign*20),remaining_quantity='.3')],
        observed_execution_fee_evidence=dict(status='AVAILABLE',basis='HISTORICAL_VERIFIED_EXECUTIONS',
            source_sha256='a'*64,rates={'TAKER':dict(count=1,minimum=.001,maximum=.001,earliest_at=998,latest_at=998)}))


def path(view,label):return next(p for p in view['joint_exit_paths']['paths']if label in p[0])


@pytest.mark.parametrize('side',['LONG','SHORT'])
def test_actual_leaves_paid_costs_and_residual_stop_are_used_once(side):
    c=context(side);before=deepcopy(c);v=_kept_exit_paths(c);p=path(v,'TP_STOP')
    sign=1 if side=='LONG'else-1
    fee=.001*(.2*(100+sign*10)+.3*(100+sign*20)+.1*(100-sign*10))
    assert v['quantity']==.6 and p[1]==pytest.approx(.1) and p[2]==pytest.approx(7.)
    assert p[3][0]==0 and p[3][2]==pytest.approx(2.5+7-fee)
    assert c==before
    c['current_plan']['take_profits']=[dict(price=1000000,fraction=.9)]
    assert _kept_exit_paths(c)==v


def test_first_tp_then_stop_is_distinct_from_all_targets_reached():
    c=context();v=_kept_exit_paths(c);first=path(v,'TP1_STOP')
    assert first[1]==pytest.approx(.4) and first[2]==pytest.approx(-2.)
    assert first[3][2]==pytest.approx(.442)
    assert path(v,'TP_STOP')[3][2]>first[3][2]


def test_partial_stops_and_overlapping_reduce_only_orders_are_capped():
    c=context();c['target_status'].append(dict(status='LIVE',kind='partial_sl',intent_id='a',target_id='s1',price='95',remaining_quantity='.25'))
    v=_kept_exit_paths(c);p=path(v,'TP1_SL')
    assert p[1]==pytest.approx(.15) and p[2]==pytest.approx(-.75)
    c['target_status'][1]['remaining_quantity']='.8';v=_kept_exit_paths(c)
    assert path(v,'TP_STOP')[1]==0 and path(v,'TP_STOP')[2]==pytest.approx(6.)


def test_live_partial_fill_below_new_order_minimum_is_not_discarded():
    c=context();c.update(positions=[dict(price=100.,quantity=.001)],recorded_scenario_net=0.)
    c['target_status']=c['target_status'][1:2];c['target_status'][0]['remaining_quantity']='.0005'
    p=path(_kept_exit_paths(c),'TP_STOP')
    assert p[1]==.0005 and p[2]==0 and p[3][2]==pytest.approx(-.0001)


@pytest.mark.parametrize('missing',['recorded','fee'])
def test_missing_finance_stays_unknown_with_gross_still_visible(missing):
    c=context()
    if missing=='recorded':c['recorded_scenario_net']=None
    else:c.pop('observed_execution_fee_evidence')
    p=path(_kept_exit_paths(c),'TP_STOP')
    assert p[2]==pytest.approx(7.) and p[3]is None


@pytest.mark.parametrize('change',[
    lambda c:c.update(scenario_id=None),lambda c:c.update(protection_ok=False),
    lambda c:c.update(accounting_status='pending'),lambda c:c.update(pending_entries=[{}]),
    lambda c:c.update(account_captured_at=1001),lambda c:c.update(account_captured_at=800),
    lambda c:c['target_status'][1].update(status='UNKNOWN'),
    lambda c:c['target_status'][1].update(remaining_quantity=''),
    lambda c:c['target_status'][1].update(remaining_quantity='NaN'),
    lambda c:c['target_status'][1].update(price='104'),
    lambda c:c['target_status'].append(deepcopy(c['target_status'][1])),
])
def test_unsafe_or_ambiguous_sources_do_not_invent_a_path(change):
    c=context();change(c);assert _kept_exit_paths(c)is None


def test_confirmed_no_live_targets_has_only_full_stop_exposure():
    c=context();c['target_status']=[];v=_kept_exit_paths(c)
    assert len(v['joint_exit_paths']['paths'])==1 and path(v,'STOP')[2]==pytest.approx(-6.)


def test_projection_preserves_source_history_schema_and_immutable_input():
    c=context();raw=dict(contract_context=c,market_snapshot={},response_contract={'keep':True},history=['keep'])
    base,_=project_model_input(raw);before=deepcopy(base)
    restored=restore_model_input(_project_kept_exit_paths(base,c));view=restored['contract_context'].pop('existing_exit_paths')
    assert view['source']=='BROKER_REPORTED_LIVE_LEAVES_NOT_PLAN_FRACTIONS'
    assert restored==restore_model_input(base) and base==before
    changed=deepcopy(c);changed['positions'][0]['quantity']=.2
    assert _project_kept_exit_paths(base,changed)==base


def test_optional_paths_never_evict_original_facts(monkeypatch):
    import live.scenario_model_input as model
    c=context();base,_=project_model_input(dict(contract_context=c,history=['keep']))
    real=model.project_model_input
    def drop_history(value,**kwargs):
        copied=deepcopy(value);copied.pop('history',None);return real(copied,**kwargs)
    monkeypatch.setattr(model,'project_model_input',drop_history)
    assert _project_kept_exit_paths(base,c)==base


def test_real_payload_has_kept_paths_in_both_phases_without_changing_flat(monkeypatch):
    import live.scenario_calculated_review as review
    c=context();market={'valid':True,'as_of_ms':1000000}
    monkeypatch.setattr(review,'build_ma_structure_context',lambda *args:{})
    for bundle in [None,{'candidates':[]}]:
        value=restore_model_input(_model_payload(market,c,{},calculated_candidates=bundle))
        assert value['contract_context']['existing_exit_paths']['quantity']==.6
    c['scenario_id']=None;c['positions']=[]
    assert 'existing_exit_paths' not in restore_model_input(_model_payload(market,c,{}))['contract_context']


def test_actual_selection_prompt_and_both_requests_receive_same_keep_comparison():
    import json
    from tests.test_scenario_calculated_review import context as base_context,holding_wire,run
    c=base_context();c.update(context(),realized_loss=0.,fees_paid=0.,funding_paid=0.)
    p=holding_wire(c);p['take_profits']=[dict(id='new',price=110.,fraction=.5)]
    _,_,calls=run(c,[p])
    assert len(calls)==2
    assert 'For unchanged WAIT, existing_exit_paths' in calls[-1]['system_prompt']
    assert 'it has no invented TP path' not in calls[-1]['system_prompt']
    for call in calls:
        value=restore_model_input(json.loads(call['user_prompt']))
        assert value['contract_context']['existing_exit_paths']['quantity']==.6
