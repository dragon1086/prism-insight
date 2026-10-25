"""Evidence wording constraints are not additional execution gates."""
from live.scenario_calculated_review import DRAFT_PROMPT, SELECT_PROMPT


def test_forming_extreme_is_not_a_completed_swing_or_mandatory_close_gate():
    for prompt in (DRAFT_PROMPT, SELECT_PROMPT):
        assert 'forming high below a completed high' in prompt
        assert 'forming low above a completed low' in prompt
        assert 'not by itself a confirmed swing reversal' in prompt
        assert 'Do not require candle closure' in prompt


def test_repeated_weakening_is_not_new_by_wording_and_failure_still_exits():
    for prompt in (DRAFT_PROMPT, SELECT_PROMPT):
        assert 'Rephrasing the same weakening evidence is not new deterioration' in prompt
        assert 'Missing reduction history is unknown' in prompt
        assert 'never delay confirmed-failure EXIT' in prompt


def test_partial_reduction_has_no_mandatory_fraction_or_new_host_gate():
    assert 'No reduction is mandatory without structural evidence' in DRAFT_PROMPT
    assert 'For holding management: No fixed payoff gate.' in SELECT_PROMPT
    assert 'not an automatic WAIT gate' in SELECT_PROMPT
