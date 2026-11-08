"""Flat-entry framing only; no model-behavior or performance assertions."""
from copy import deepcopy

import pytest

from live.scenario_llm import FLAT_ENTRY_PROMPT
from tests.test_scenario_calculated_review import context, run, wire


MARKER = 'Separate directional evidence from executable entry location'


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
        assert 'trigger is an activation condition, not a better entry price' in prompt
        assert 'actual recent confirmed-close sequence with the separate forming' in prompt
        assert 'not evidence that confirmed closes are rising/falling' in prompt
        assert 'not a mandatory close/retest gate' in prompt
        assert 'In DRAFT use the original proposed limit/worst permitted entry price' in prompt
        assert 'do not anticipate host price adjustments' in prompt
        assert 'In calculated SELECT use the supplied effective_plan entry price' in prompt
        assert 'entry cap, first TP and first evidenced reaction zone, if any' in prompt
        assert 'a strong immediate break or valid numeric crossing remains allowed' in prompt
        assert 'Supplied host entry_economics governs fresh OPEN eligibility' in prompt
        assert len(call['user_prompt'].encode()) <= 100000


def test_flat_phase_sources_and_no_candidate_do_not_invent_plans():
    _, _, calls = run(candidates=[])
    assert len(calls) == 1
    prompt = ' '.join(calls[0]['system_prompt'].split())
    assert 'current_primary_frame_facts is FORMING_LAST_CLOSE' in prompt
    assert 'confirmed OHLC comes from timestamped recent_confirmed_bars or CONFIRMED points' in prompt
    assert 'not a confirmed-bar rise/fall' in prompt
    assert 'do not invent a confirmed.close path' in prompt
    assert 'No candidate or first observation: do not invent a plan to compare' in prompt


def test_flat_select_wait_still_explains_existing_eligible_plan_without_new_fields():
    def choose_wait(reply, phase, request):
        if phase == 2:
            reply['selection_token'] = next(t for t in request['response_schema']['properties']['selection_token']['enum']
                                           if t.startswith('WAIT:'))
    result, _, calls = run(mutate=choose_wait)
    assert result['selection']['selection'] == 'WAIT'
    prompt = ' '.join(calls[1]['system_prompt'].split())
    assert 'even when selecting WAIT' in prompt
    assert 'existing rationale as decision evidence' in prompt
    assert 'State when no intervening zone is evidenced or its evidence is unknown' in prompt
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
