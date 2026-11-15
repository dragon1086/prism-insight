"""Management wording matches host state identity and observed execution outcomes."""
import pytest

from live.scenario_llm import entry_contract_prompt,immediate_reduce_instructions


@pytest.mark.parametrize('version',[1,2])
def test_assessment_contract_compares_state_not_identical_reason(version):
    text=' '.join(entry_contract_prompt({'entry_contract_version':version}).split())
    assert 'must agree on contract_id/status' in text
    assert 'FAILED requires the allowed full EXIT' in text


def test_reduce_preconditions_do_not_ban_verified_live_exit_orders():
    text=' '.join(immediate_reduce_instructions('',{'immediate_reduce_version':1}).split())
    assert 'only when offered for verified protected holdings' in text
    assert 'LIVE exit targets are not pending entries' in text


def test_target_restoration_is_an_observed_outcome_not_a_promise():
    text=' '.join(immediate_reduce_instructions('',{'immediate_reduce_version':1}).split())
    assert 'Restoration failure is not proof targets are live' in text
    assert 'FAILED still requires full EXIT' in text
