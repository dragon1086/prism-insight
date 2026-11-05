from copy import deepcopy

import pytest

from live import scenario_llm
from live.scenario_calculated_review import _flat_selection_prompt
from tests.test_flat_select_prompt_trim import flat, system


def full_system():
    return system() + scenario_llm.PENDING_WAIT_PROMPT


def test_flat_v2_omits_legacy_recovery_and_unoffered_renewal_only():
    ctx = flat()
    ctx.update(recovery_contract_version=2, pending_wait_policy_version=1)
    before = deepcopy(ctx)
    result = _flat_selection_prompt(full_system(), ctx)
    assert 'When recovery_contract_version=1 and recovery.phase=OBSERVING' not in result
    assert 'When recovery_contract_version=2, closing one confirmed scenario' in result
    assert 'A hard stop such as daily loss, unknown execution, bad data,' in result
    assert 'The initial lease ends at the next' in result
    assert 'When pending_renewal_offer is supplied' not in result
    assert 'normal fill protection and original failure/EXIT rules always retain priority.' in result
    assert ctx == before


@pytest.mark.parametrize('version', [1, None, True, '2'])
def test_legacy_or_unknown_recovery_keeps_version_one_instructions(version):
    ctx = flat()
    ctx['recovery_contract_version'] = version
    assert 'When recovery_contract_version=1 and recovery.phase=OBSERVING' in _flat_selection_prompt(full_system(), ctx)


@pytest.mark.parametrize(('key', 'value'), [
    ('pending_renewal_version', 1), ('pending_renewal_offer', {'offer_id': 'a'}),
    ('pending_wait_policy_version', True), ('pending_wait_policy_version', None),
])
def test_possible_or_unknown_renewal_keeps_full_renewal_instructions(key, value):
    ctx = flat()
    ctx.update(recovery_contract_version=2, pending_wait_policy_version=1)
    ctx[key] = value
    assert 'When pending_renewal_offer is supplied' in _flat_selection_prompt(full_system(), ctx)


def test_legacy_marker_mismatch_does_not_remove_recovery():
    ctx = flat()
    ctx['recovery_contract_version'] = 2
    original = full_system().replace('version-1 one-shot restrictions above. Closing', 'changed marker')
    assert 'When recovery_contract_version=1 and recovery.phase=OBSERVING' in _flat_selection_prompt(original, ctx)


def test_active_or_pending_context_keeps_entire_prompt():
    ctx = flat()
    ctx.update(recovery_contract_version=2, pending_wait_policy_version=1, pending_entries=[{'id': 'p'}])
    assert _flat_selection_prompt(full_system(), ctx) == full_system()
