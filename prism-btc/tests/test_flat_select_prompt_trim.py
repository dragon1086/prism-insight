from copy import deepcopy

import pytest

from live import scenario_llm
from live.scenario_calculated_review import SELECT_PROMPT, _flat_selection_prompt
from tests.test_scenario_calculated_review import context, run


def flat():
    ctx = context()
    ctx.update(target_status=[], unknown_intents=False, legacy_fenced=False,
               protection_ok=True, accounting_status='confirmed')
    return ctx


def system():
    return scenario_llm.SYSTEM_PROMPT + scenario_llm.MA_STRUCTURE_PROMPT + scenario_llm.FLAT_ENTRY_PROMPT + SELECT_PROMPT


def test_only_inapplicable_flat_select_spans_are_removed():
    original = system()
    shortened = _flat_selection_prompt(original, flat())
    assert len(original.encode()) - len(shortened.encode()) == 9581
    for common in ('Partial SL remains conditional reduceOnly protection',
                   'Distinguish order mechanics from evidence:',
                   'On every OPEN, holding review and ADJUST,',
                   'Use verified positions, pending entries and current accounting inputs,',
                   'Unknown actual future fees alone are not a new automatic WAIT gate;'):
        assert common in shortened
    assert 'On every holding review distinguish current holding advantage' not in shortened


@pytest.mark.parametrize(('key', 'value'), [
    ('scenario_id', 'active'), ('positions', [{'quantity': .01}]),
    ('pending_entries', [{'id': 'pending'}]), ('target_status', [{'id': 'live'}]),
    ('unknown_intents', True), ('unknown_intents', None), ('legacy_fenced', True),
    ('protection_ok', False), ('new_risk_blocked', True), ('accounting_status', 'pending'),
])
def test_active_pending_or_unverified_context_keeps_full_prompt(key, value):
    ctx = flat()
    ctx[key] = value
    original = system()
    assert _flat_selection_prompt(original, ctx) == original


def test_missing_scenario_or_changed_delimiter_keeps_full_prompt():
    ctx = flat()
    del ctx['scenario_id']
    original = system()
    assert _flat_selection_prompt(original, ctx) == original
    malformed = original.replace('On every holding review distinguish current holding advantage', 'changed marker')
    assert _flat_selection_prompt(malformed, flat()) == malformed
    duplicate = original + '\nOn every holding review distinguish current holding advantage from merely NOT FAILED:\n'
    assert _flat_selection_prompt(duplicate, flat()) == duplicate


@pytest.mark.parametrize('key', ['positions', 'pending_entries', 'target_status', 'unknown_intents',
                                 'legacy_fenced', 'protection_ok', 'new_risk_blocked', 'accounting_status'])
def test_missing_flat_evidence_keeps_full_prompt(key):
    ctx = flat()
    del ctx[key]
    original = system()
    assert _flat_selection_prompt(original, ctx) == original


def test_real_review_changes_only_flat_selection_system_prompt():
    ctx = flat()
    before = deepcopy(ctx)
    result, events, calls = run(ctx=ctx)
    assert len(calls) == 2 and result['orders_submitted'] == 0
    assert 'On every holding review distinguish current holding advantage' in calls[0]['system_prompt']
    assert 'On every holding review distinguish current holding advantage' not in calls[1]['system_prompt']
    assert 'Partial SL remains conditional reduceOnly protection' in calls[1]['system_prompt']
    assert 'Unknown actual future fees alone are not a new automatic WAIT gate;' in calls[1]['system_prompt']
    assert ctx == before


def test_flat_review_replays_and_revalidates_without_order():
    from tests.test_scenario_review_guard import context as guard_context, taped, check
    ctx = guard_context()
    ctx['unknown_intents'] = False
    args = taped(ctx=ctx)
    result = check(args)
    assert result['validated']['action'] == 'OPEN'
    assert result['receipt']['orders_submitted'] == 0
