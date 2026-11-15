from copy import deepcopy
import pytest
from tests.test_flat_select_prompt_trim import flat,system


@pytest.mark.parametrize('version',[1,2])
def test_recovery_uses_current_host_permission_not_conflicting_historical_algorithms(version):
    ctx=flat();ctx['recovery_contract_version']=version;before=deepcopy(ctx);text=system(ctx)
    assert 'Recovery permissions/stage/cap come from the host' in text
    assert 'first_observation=true requires WAIT' in text
    assert 'No promotion arithmetic or higher cap requests' in text
    assert 'version-1 one-shot restrictions'not in text
    assert 'last six prospective'not in text
    assert ctx==before


@pytest.mark.parametrize('version',[None,True,'2'])
def test_unknown_recovery_version_is_not_reinterpreted_as_version_one(version):
    ctx=flat();ctx['recovery_contract_version']=version
    assert 'Recovery permissions/stage/cap'not in system(ctx)
    assert 'missing evidence stays UNKNOWN'in system(ctx)


def test_pending_policy_only_appears_for_an_actual_pending_entry():
    ctx=flat();ctx['pending_wait_policy_version']=1
    assert 'Review even zero-fill reservations'not in system(ctx)
    ctx.update(scenario_id='pending',pending_entries=[{'id':'entry'}])
    text=system(ctx)
    assert 'Review even zero-fill reservations'in text
    assert 'Time passing alone grants no renewal'in text
    assert 'Flat-entry comparison only'not in text
    assert 'SUSTAINED DIRECTION'not in text
