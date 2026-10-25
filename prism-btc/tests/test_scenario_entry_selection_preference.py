"""Prompt preference is not a host eligibility threshold or holding exit rule."""
from copy import deepcopy

import pytest

from live.scenario_calculated_review import DRAFT_PROMPT, SELECT_PROMPT, calculate_candidate, receipt_hash
from tests.test_scenario_calculated_review import context, wire, run
from tests.test_scenario_fee_projection import evidence


def test_entry_preference_is_explicitly_scoped_and_structure_first():
    assert 'Only for fresh flat OPEN' in DRAFT_PROMPT
    assert 'trend participation is the default purpose' in DRAFT_PROMPT
    assert 'never stretch a target or tighten SL merely to reach a ratio' in DRAFT_PROMPT
    assert 'not holding ADJUST, REDUCE or confirmed-failure EXIT' in SELECT_PROMPT
    assert 'compare the supplied ratio explicitly with 2:1' in SELECT_PROMPT
    assert 'prefer another supplied, eligible, structurally grounded candidate, or WAIT' in SELECT_PROMPT
    assert 'not a new eligibility gate' in SELECT_PROMPT
    assert 'For holding management: No fixed payoff gate.' in SELECT_PROMPT


def test_no_style_relabel_or_invented_statistics_or_unknown_cost_pass():
    for prompt in (DRAFT_PROMPT, SELECT_PROMPT):
        assert 'do not infer purpose from regime or goal labels alone' in prompt
        assert 'never relabel a plan to evade comparison' in prompt
    assert 'confidence is not a type-specific win rate or expected value' in SELECT_PROMPT
    assert 'UNKNOWN is neither zero nor a 2:1 pass' in SELECT_PROMPT
    assert 'not an automatic WAIT gate' in SELECT_PROMPT
    assert all(label in SELECT_PROMPT for label in ('계획:', '경로:', '연장:'))


def test_two_to_one_preference_distinguishes_priced_targets_from_runner_stress():
    assert 'distinguish a fully priced target plan from TP_STOP residual-hard-SL stress' in SELECT_PROMPT
    assert 'intentional unpriced runner of at least minimum quantity' in SELECT_PROMPT
    assert 'do not prefer WAIT or fixing TP on all lots from that number alone' in SELECT_PROMPT
    assert 'compare structural trend participation and invalidation separately' in SELECT_PROMPT
    assert 'Never invent unpriced upside as a 2:1 pass' in SELECT_PROMPT
    assert 'dust or mechanically adding a runner cannot evade this comparison' in SELECT_PROMPT


@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
@pytest.mark.parametrize('ratio', [1.99, 2, 2.01])
def test_ratio_boundaries_never_change_host_eligibility_or_risk(side, ratio):
    ctx = context()
    ctx['observed_execution_fee_evidence'] = evidence(.001)
    ctx['price_tick'] = .00000001
    raw = wire(ctx, side)
    entry, stop, rate = raw['entries'][0]['price'], raw['hard_stop'], .001
    sign = 1 if side == 'LONG' else -1
    full_loss = sign * (entry - stop) + rate * (entry + stop)
    target = (ratio * full_loss + sign * entry + rate * entry) / (sign - rate)
    raw['take_profits'] = [dict(id='tp', price=round(target, 8), fraction=1)]
    before = deepcopy(raw)
    receipt = calculate_candidate(raw, ctx, candidate_id='c0')
    assert receipt['eligible'] is True, receipt
    assert receipt['projection']['whole_plan_payoff']['ratio'] == pytest.approx(ratio)
    assert raw == before == receipt['raw_proposal']
    baseline = calculate_candidate(wire(ctx, side), ctx, candidate_id='c0')
    assert receipt['raw_risk'] == baseline['raw_risk']
    assert receipt['receipt_sha256'] == receipt_hash({k: v for k, v in receipt.items() if k != 'receipt_sha256'})


def test_assembled_calls_keep_selection_schema_and_three_item_format():
    result, _, calls = run()
    assert result['selection']['selection'] == 'c0'
    assert DRAFT_PROMPT in calls[0]['system_prompt']
    assert SELECT_PROMPT in calls[1]['system_prompt']
    assert set(calls[1]['response_schema']['properties']) == {'selection_token', 'rationale'}
