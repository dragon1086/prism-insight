"""Flat-entry framing only; no model-behavior or performance assertions."""
from copy import deepcopy

import pytest

from live.scenario_llm import FLAT_ENTRY_PROMPT
from tests.test_scenario_calculated_review import context, run, wire


MARKER = 'Flat-entry comparison only'


@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
def test_flat_draft_and_selection_receive_price_basis_without_contract_changes(side):
    ctx = context()
    raw = wire(ctx, side)
    before = deepcopy((ctx, raw))
    result, _, calls = run(ctx, [raw])
    assert (ctx, raw) == before
    assert result['selected']['raw_proposal'] == raw
    assert result['selected']['raw_proposal']['partial_stops'] == []
    assert set(calls[1]['response_schema']['properties']) == {'selection_token', 'rationale'}
    for call in calls:
        prompt = ' '.join(call['system_prompt'].split())
        assert prompt.count(MARKER) == 1
        assert 'numeric crossing alone suffices' in prompt
        assert 'Keep each frame, phase and timestamp separate' in prompt
        assert 'not a price breakout' in prompt
        assert 'no habitual extra close, crossover or retest' in prompt
        assert 'Do not pre-apply the buffer' in prompt
        assert 'cost-net opportunity' in prompt
        assert len(call['user_prompt'].encode()) <= 100000


def test_flat_phase_sources_and_no_candidate_do_not_invent_plans():
    _, _, calls = run(candidates=[])
    assert len(calls) == 1
    prompt = ' '.join(calls[0]['system_prompt'].split())
    assert 'current_primary_frame_facts contains forming Last endpoints' in prompt
    assert 'not an invented confirmed.close' in prompt
    assert 'Missing qualitative evidence permits no candidates' in prompt


def test_flat_select_wait_still_explains_existing_eligible_plan_without_new_fields():
    def choose_wait(reply, phase, request):
        if phase == 2:
            reply['selection_token'] = next(t for t in request['response_schema']['properties']['selection_token']['enum']
                                           if t.startswith('WAIT:'))
    result, _, calls = run(mutate=choose_wait)
    assert result['selection']['selection'] == 'WAIT'
    prompt = ' '.join(calls[1]['system_prompt'].split())
    assert 'specific material contradiction or inadequate opportunity' in prompt
    assert 'Unknown obstacles are unknown' in prompt
    assert set(calls[1]['response_schema']['properties']) == {'selection_token', 'rationale'}


@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
@pytest.mark.parametrize('phase', ['pending', 'holding'])
def test_active_and_pending_never_receive_entry_location_framing(side, phase):
    ctx = context()
    original = wire(ctx, side)
    ctx.update(scenario_id='active', revision=1, side=side,
               positions=[dict(price=83410, quantity=.01)] if phase == 'holding' else [],
               pending_entries=[] if phase == 'holding' else [dict(id='pending', price=83410, quantity=.01)],
               previous_hard_stop=original['hard_stop'])
    candidate = wire(ctx, side)
    candidate.update(action='ADJUST', entries=[])
    before = deepcopy(candidate)
    result, _, calls = run(ctx, [candidate])
    assert result['selected']['raw_proposal'] == before
    for call in calls:
        assert MARKER not in call['system_prompt']
        assert FLAT_ENTRY_PROMPT not in call['system_prompt']
