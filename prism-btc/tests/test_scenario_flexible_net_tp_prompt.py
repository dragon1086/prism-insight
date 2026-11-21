"""Both model phases receive the same user-approved exit/cost semantics."""
import pytest

from tests.test_scenario_calculated_review import context, wire, run


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
@pytest.mark.parametrize("holding", [False, True])
def test_draft_and_selection_share_flexible_tp_and_cost_semantics(side, holding):
    ctx = context()
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
        for clause in ('Fixed leverage is 10','conditional payoff is not expected return',
                       'Unknown costs/funding are not zero','Profits never replenish the original budget',
                       'Price above/below MA10/MA35 describes location, not direction',
                       'not an automatic veto','never restore sold lots'):
            assert clause in text
        assert '50% near'not in text and 'not a preset near-heavy'not in text
        if not holding:
            assert 'Choose structural invalidation with noise room' in text
            assert 'Do not stretch TP or squeeze SL merely to pass host economics' in text


def test_host_allocation_mode_keeps_new_tp_policy_in_both_phases():
    from tests.test_scenario_allocation_review import taped

    args, calls = taped("allocation-calculated")
    assert args[3]["outcome"]["status"] == "COMPLETE"
    assert len(calls) == 2
    assert 'new entries use allocation_fraction, not BTC quantity' in calls[0]['system_prompt']
    assert set(calls[1]['response_schema']['properties'])=={'selection_token','rationale'}
    for call in calls:
        text=' '.join(call['system_prompt'].split())
        assert 'conditional payoff is not expected return' in text
        assert 'Unknown costs/funding are not zero' in text
