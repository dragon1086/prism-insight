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
        for clause in (
            "Price movement alone is not net profit",
            "structural SL (R)", "not a forced allocation",
            "NOT actual breakeven or a mandatory SL boundary",
            "LEG_ONLY projections from WHOLE_SCENARIO projections",
            "Missing fee evidence stays UNKNOWN, not zero and not an automatic WAIT",
            "A winning partial exit does not guarantee a profitable whole trade",
            "do not force a breakeven stop", "Choose the structural invalidation SL first",
            "do not increase leverage to 30x", "ABOVE the support", "BELOW the resistance",
            "not guaranteed reach or fill", "counting recorded costs only once",
            "estimated_net_before_unmodelled_costs is NOT guaranteed future net profit",
        ):
            assert clause in text
        for clause in (
            "Supported trend continuation (LONG or SHORT)",
            "BETWEEN is a location, not proof of a range",
            "One narrowing frame calls for more conservative evidence review, not a new automatic veto",
            "Slow flow never excuses holding an invalidated thesis",
            "Confidence is not measured win probability",
        ):
            assert clause in text
        assert "Start by considering 50% near" not in text
        assert "Compare TP allocations at the same entry and structural SL" in text
        assert "Bounded MA10-to-MA35 traversals retain their original cap" in text
        assert "not a preset near-heavy allocation" in text
        assert "Changing TP fractions does not itself justify moving the first target farther" in text
        assert "explicitly assess any closer supplied reaction zone" in text
        assert "Prefer a structurally justified profit-protecting SL at or beyond" not in text


def test_host_allocation_mode_keeps_new_tp_policy_in_both_phases():
    from tests.test_scenario_allocation_review import taped

    args, calls = taped("allocation-calculated")
    assert args[3]["outcome"]["status"] == "COMPLETE"
    assert len(calls) == 2
    assert "Do not emit entry quantity" in calls[0]["system_prompt"]
    assert "Return exactly selection_token and rationale" in calls[1]["system_prompt"]
    for call in calls:
        text = " ".join(call["system_prompt"].split())
        assert "Price movement alone is not net profit" in text
        assert "NOT actual breakeven or a mandatory SL boundary" in text
