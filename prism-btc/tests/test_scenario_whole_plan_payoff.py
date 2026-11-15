"""Whole-plan arithmetic is advisory and never discounts risk for partial SL."""
import json
from copy import deepcopy

import pytest

from live.scenario_calculated_review import _gross_projection, calculate_candidate, receipt_hash
from tests.test_scenario_calculated_review import context, wire
from tests.test_scenario_joint_exit_projection import fixture


def funded(side='LONG'):
    plan, ctx = fixture(side)
    ctx['observed_execution_fee_evidence'] = dict(status='AVAILABLE',
        basis='HISTORICAL_VERIFIED_EXECUTIONS', source_sha256='a' * 64,
        rates={'TAKER': dict(count=1, minimum=.001, maximum=.001,
                             earliest_at=999, latest_at=999)})
    return plan, ctx


@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
def test_whole_path_uses_rounded_quantities_and_no_partial_stop_credit(side):
    plan, ctx = funded(side)
    plan['take_profits'][0]['fraction'] = .65
    before = deepcopy(plan)
    result = _gross_projection(plan, ctx)
    payoff = result['whole_plan_payoff']
    path = next(p for p in result['joint_exit_paths']['paths'] if 'TP_STOP' in p[0])
    assert path[1] == .4
    assert payoff['status'] == 'AVAILABLE'
    assert payoff['net_reward'] == path[3][2]
    assert payoff['full_stop_loss'] == -result['fee_only_projection_at_full_stop']['estimated_net_before_unmodelled_costs']
    assert payoff['ratio'] == pytest.approx(payoff['net_reward'] / payoff['full_stop_loss'])
    plan['partial_stops'] = []
    assert _gross_projection(plan, ctx)['whole_plan_payoff'] == payoff
    assert before['entries'] == plan['entries']
    assert len(json.dumps({'whole_plan_payoff': payoff}, separators=(',', ':')).encode()) < 200


@pytest.mark.parametrize('case', ['missing_fee', 'zero_fee', 'nonpositive_loss', 'no_targets', 'subminimum_targets'])
def test_unknown_cost_and_nonpositive_loss_are_not_invented(case):
    plan, ctx = funded()
    if case == 'missing_fee':
        ctx.pop('observed_execution_fee_evidence')
    elif case == 'zero_fee':
        ctx['observed_execution_fee_evidence']['rates']['TAKER'].update(minimum=0, maximum=0)
    elif case == 'nonpositive_loss':
        plan['hard_stop'] = 110
    elif case == 'no_targets':
        plan['take_profits'] = []
    else:
        plan['take_profits'][0]['fraction'] = .05
    projection = _gross_projection(plan, ctx)
    payoff = projection['whole_plan_payoff']
    assert payoff == dict(status='UNKNOWN', net_reward=None, full_stop_loss=None, ratio=None)
    if case in {'no_targets', 'subminimum_targets'}:
        stop = next(p for p in projection['joint_exit_paths']['paths'] if 'STOP' in p[0])
        assert stop[1] == 1
        assert stop[2] == projection['gross_at_full_stop'] == -20


@pytest.mark.parametrize('boundary', ['holding', 'pending', 'lots', 'not_open', 'zero_quantity'])
def test_only_existing_flat_fresh_open_scope(boundary):
    plan, ctx = funded()
    kwargs = {}
    if boundary == 'holding':
        ctx['scenario_id'] = 'held'
        ctx['positions'] = plan['entries']
    elif boundary == 'pending':
        ctx['pending_entries'] = plan['entries']
    elif boundary == 'lots':
        kwargs['lots'] = plan['entries']
    elif boundary == 'not_open':
        plan['action'] = 'ADJUST'
    else:
        plan['entries'] = []
    assert 'whole_plan_payoff' not in _gross_projection(plan, ctx, **kwargs)


def test_candidate_raw_economics_and_canonical_receipt_binding():
    ctx = context()
    raw = wire(ctx, 'LONG')
    before = deepcopy(raw)
    receipt = calculate_candidate(raw, ctx, candidate_id='c0')
    assert receipt['eligible']
    assert raw == before == receipt['raw_proposal']
    assert receipt['receipt_sha256'] == receipt_hash({k: v for k, v in receipt.items() if k != 'receipt_sha256'})
    receipt['projection']['whole_plan_payoff']['ratio'] = 999
    assert receipt['receipt_sha256'] != receipt_hash({k: v for k, v in receipt.items() if k != 'receipt_sha256'})


@pytest.mark.parametrize('rows', [[], [[['TP_STOP'], .4, 4, None]],
    [[['TP_STOP'], .4, 4, [0, 0, float('nan')]]],
    [[['TP_STOP'], .4, 4, [0, 0, float('inf')]]]])
def test_missing_or_nonfinite_path_never_affects_other_projection(monkeypatch, rows):
    from live import scenario_calculated_review as module
    plan, ctx = funded()
    monkeypatch.setattr(module, '_joint_exit_paths', lambda *a: dict(version=2, paths=rows))
    projection = _gross_projection(plan, ctx)
    assert projection['whole_plan_payoff'] == dict(status='UNKNOWN', net_reward=None, full_stop_loss=None, ratio=None)
    assert projection['quantity'] == 1
    assert projection['gross_at_full_stop'] == -20


def test_prompt_labels_scope_units_and_excluded_unknown_costs():
    from live.scenario_calculated_review import _model_payload
    from live.scenario_model_input import restore_model_input
    view=restore_model_input(_model_payload({},context(),{},calculated_candidates={}))
    units=view['calculation_field_units']
    assert 'without partial SL risk credit' in units['whole_plan_payoff']['full_stop_loss']
    assert 'USDT' in units['whole_plan_payoff']['net_reward']
    assert 'not expectancy/probability' in units['whole_plan_payoff']['ratio']
    assert 'unmodelled, not zero' in units['joint_exit_paths']['assumptions']
