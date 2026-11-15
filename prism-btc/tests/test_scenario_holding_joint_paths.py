"""Held-lot paths must not re-charge entry fees or resurrect prior reductions."""
from copy import deepcopy

import pytest

from live.scenario_calculated_review import _holding_projection_values, _gross_projection
from tests.test_scenario_joint_exit_projection import fixture, paths


def held(side='LONG'):
    plan, ctx = fixture(side)
    plan.update(action='ADJUST', entries=[])
    ctx.update(scenario_id='held', side=side, accounting_status='confirmed',
        positions=[dict(price=100, quantity=1)], pending_entries=[],
        current_plan=deepcopy(plan), recorded_scenario_net=7,
        observed_execution_fee_evidence=dict(status='AVAILABLE',
            basis='HISTORICAL_VERIFIED_EXECUTIONS', source_sha256='a'*64,
            rates=dict(TAKER=dict(count=1,minimum=.001,maximum=.001,
                earliest_at=999,latest_at=999))))
    return plan, ctx


def displayed(plan, ctx):
    return _gross_projection(plan,ctx,lots=ctx['positions'],holding_paths=True,
        assumption='CONFIRMED_REMAINING_NEW_REVISION_NO_ADDITIONAL_FILLS')


@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
def test_confirmed_path_adds_recorded_net_once_and_never_entry_fees(side):
    plan, ctx = held(side)
    before = deepcopy((plan, ctx))
    result = _holding_projection_values(plan, ctx)
    assert (plan, ctx) == before
    assert 'joint_exit_paths' not in result['confirmed_remaining']
    projection = displayed(plan,ctx)
    joint = projection['joint_exit_paths']
    assert joint['version'] == 3
    assert joint['recorded_net'] == 7
    assert joint['gross_scope'] == 'CONFIRMED_REMAINING_ONLY'
    assert joint['fee_net_scope'] == 'WHOLE_SCENARIO_RECORDED_NET_ONCE'
    assert 'joint_exit_paths' not in result['hypothetical_all_fills']
    for row in paths(projection).values():
        fee = row['fee_only']
        assert fee['assumed_entry_fee'] == 0
        assert fee['estimated_net_before_unmodelled_costs'] == pytest.approx(
            7 + row['gross_pnl'] - fee['assumed_exit_fee'])


@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
@pytest.mark.parametrize('missing', ['recorded_net', 'fee_evidence'])
def test_missing_accounting_or_fee_keeps_net_unknown_but_remaining_gross(side, missing):
    plan, ctx = held(side)
    if missing == 'recorded_net':
        ctx['recorded_scenario_net'] = None
    else:
        ctx['observed_execution_fee_evidence'] = None
    projection = displayed(plan,ctx)
    assert projection['joint_exit_paths']['paths']
    assert all(row[3] is None for row in projection['joint_exit_paths']['paths'])


@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
def test_revised_quota_only_uses_current_lots_pending_and_prior_fills_not_recreated(side):
    plan, ctx = held(side)
    ctx['positions'] = [dict(price=100,quantity=.4)]
    ctx['pending_entries'] = [dict(price=100,quantity=.6)]
    ctx['current_plan']['take_profits'][0]['fraction'] = 1
    ctx['target_status'] = {'old-filled-tp': {'filled_quantity': .6}}
    result = _holding_projection_values(plan, ctx)
    assert result['confirmed_remaining']['quantity'] == .4
    assert result['hypothetical_all_fills']['quantity'] == 1
    assert 'joint_exit_paths' not in result['hypothetical_all_fills']
    assert all(0 <= r[1] <= .4 for r in displayed(plan,ctx)['joint_exit_paths']['paths'])
    assert result['confirmed_remaining']['targets'][0]['quantity'] == .2


def test_partial_tp_then_larger_remaining_loss_is_not_hidden_by_profitable_leg():
    plan, ctx = held('SHORT')
    ctx.update(quantity_step=.001,minimum_quantity=.001,
        positions=[dict(price=81284.2,quantity=.047)],recorded_scenario_net=.99)
    plan.update(hard_stop=81490,take_profits=[dict(id='near',price=81187.3,fraction=.5)],partial_stops=[])
    projection = displayed(plan,ctx)
    assert projection['targets'][0]['gross_pnl'] > 0
    rows=paths(projection)
    assert rows['TP_THEN_HARD_SL']['gross_pnl'] < 0
    assert rows['TP_THEN_HARD_SL']['fee_only']['estimated_net_before_unmodelled_costs'] < 0




@pytest.mark.parametrize('value', [None, True, '7', float('nan'), float('inf')])
def test_invalid_recorded_net_only_makes_optional_path_net_unknown(value):
    plan, ctx = held()
    ctx['recorded_scenario_net'] = value
    joint = displayed(plan,ctx)['joint_exit_paths']
    assert joint['recorded_net'] is None
    assert all(row[3] is None for row in joint['paths'])
    assert any(row[2] != 0 for row in joint['paths'])


def test_optional_view_preserves_receipts_source_and_baseline_on_size_fallback(monkeypatch):
    from live import scenario_calculated_review as review
    from live import scenario_position_flow as flow
    from live.scenario_model_input import restore_model_input
    from live.scenario_preview import response_contract
    from tests.test_scenario_calculated_review import holding, holding_wire
    ctx=holding()
    candidate=review.calculate_candidate(holding_wire(ctx),ctx,candidate_id='c0')
    assert candidate['eligible']
    bundle=dict(candidates=[candidate])
    market=dict(valid=True,as_of_ms=int(ctx['now']*1000))
    before=deepcopy((bundle,ctx))
    augmented=review._model_payload(market,ctx,response_contract(ctx),calculated_candidates=bundle)
    logical=restore_model_input(augmented)
    view=logical['contract_context'].pop('holding_exit_paths')
    assert view[0][:2]==['c0',candidate['receipt_sha256']]
    assert logical['calculated_candidates']['candidates'][0]['projection']==candidate['projection']
    assert logical['calculated_candidates']['candidates'][0]['host_receipt_sha256']==candidate['receipt_sha256']
    assert (bundle,ctx)==before
    baseline,_=flow.project_position_flow_input(logical)
    monkeypatch.setattr(flow,'_MODEL_INPUT_LIMIT',flow._size(baseline))
    fallback=review._model_payload(market,ctx,response_contract(ctx),calculated_candidates=bundle)
    assert fallback==baseline
    assert (bundle,ctx)==before


@pytest.mark.parametrize('tamper',['receipt','context'])
def test_optional_view_missing_binding_does_not_reject_candidate(tamper):
    from live import scenario_calculated_review as review
    from live.scenario_model_input import restore_model_input
    from live.scenario_preview import response_contract
    from tests.test_scenario_calculated_review import holding, holding_wire
    ctx=holding()
    candidate=review.calculate_candidate(holding_wire(ctx),ctx,candidate_id='c0')
    if tamper=='receipt':
        candidate['receipt_sha256']='b'*64
    else:
        candidate['context_sha256']='b'*64
        candidate['receipt_sha256']=review.receipt_hash({k:v for k,v in candidate.items() if k!='receipt_sha256'})
    bundle=dict(candidates=[candidate])
    # Ordinary receipt validation remains elsewhere; this view never repairs it.
    try:
        payload=review._model_payload(dict(valid=True,as_of_ms=1000000),ctx,
            response_contract(ctx),calculated_candidates=bundle)
    except ValueError:
        assert tamper=='receipt'
        return
    restored=restore_model_input(payload)
    assert 'holding_exit_paths' not in restored['contract_context']
    assert restored['calculated_candidates']['candidates'][0]['projection']==candidate['projection']
    assert restored['calculated_candidates']['candidates'][0]['host_receipt_sha256']==candidate['receipt_sha256']


def test_view_uses_bound_validation_clock_not_earlier_frozen_fee_freshness():
    from live import scenario_calculated_review as review
    from live.scenario_model_input import restore_model_input
    from live.scenario_preview import response_contract
    from tests.test_scenario_calculated_review import holding, holding_wire
    ctx=holding()
    ctx.update(now=100000,input_captured_at=100000,account_captured_at=100000,recorded_scenario_net=7)
    ctx['observed_execution_fee_evidence']=held()[1]['observed_execution_fee_evidence']
    ctx['observed_execution_fee_evidence']['rates']['TAKER'].update(earliest_at=13601,latest_at=13601)
    raw=holding_wire(ctx)
    raw['expires_at']=100200
    candidate=review.calculate_candidate(raw,dict(ctx,now=100002),candidate_id='c0',original_input_now=100000)
    assert candidate['eligible']
    bundle=dict(candidates=[candidate])
    market=dict(valid=True,as_of_ms=100000000)
    payload=review._model_payload(market,ctx,response_contract(ctx),calculated_candidates=bundle)
    view=restore_model_input(payload)['contract_context']['holding_exit_paths']
    assert all(row[3] is None for row in view[0][2]['paths'])
    ctx['positions'][0]['quantity'] /= 2
    payload=review._model_payload(market,ctx,response_contract(ctx),calculated_candidates=bundle)
    assert 'holding_exit_paths' not in restore_model_input(payload)['contract_context']
