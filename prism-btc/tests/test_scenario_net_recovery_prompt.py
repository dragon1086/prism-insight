"""Recovery evidence informs model context but never grants model risk authority."""
import pytest

from tests.test_scenario_calculated_review import context, wire, run


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
@pytest.mark.parametrize("holding", [False, True])
def test_rolling_net_rule_is_shared_across_candidate_and_selection(side, holding):
    ctx = context()
    ctx["recovery_contract_version"] = 2
    candidate = wire(ctx, side)
    if holding:
        ctx.update(scenario_id="active", revision=1, side=side,
                   positions=[dict(price=83410, quantity=.01)],
                   previous_hard_stop=candidate["hard_stop"])
        candidate = wire(ctx, side)
        candidate.update(action="ADJUST", entries=[])
    _, _, calls = run(ctx, [candidate])
    assert len(calls) == 2
    for call in calls:
        text = " ".join(call["system_prompt"].split())
        for clause in ('Recovery permissions/stage/cap come from the host',
                       'No promotion arithmetic or higher cap requests',
                       'Original-batch-only: zero chase, no later adds',
                       'Unsettled exposure grants no attempt'):
            assert clause in text
        assert 'last six prospective'not in text
