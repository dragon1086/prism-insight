"""Prompt assembly/contract checks, not model behavior or profitability proof."""
from copy import deepcopy
import json
from types import SimpleNamespace

import pytest

from live.scenario_llm import FLAT_ENTRY_PROMPT
from live.scenario_model_input import restore_model_input
from tests.test_scenario_calculated_review import context, run, wire


MARKER = "Before opportunity framing, compare actual 30m/1h highs/lows"


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
@pytest.mark.parametrize("pattern", ["contraction_stalled", "fresh_breakout_narrowing", "strong_expansion", "tiny_widening_no_progress"])
def test_mirrored_structural_inputs_keep_both_phases_and_host_contract(side, pattern):
    ctx = context()
    # Supplied paired facts are retained, not a new classifier or forced decision.
    ctx["test_structural_facts"] = dict(side=side, pattern=pattern,
        price_progress=1 if side == "LONG" else -1,
        ma_gap_narrowing=pattern in {"contraction_stalled", "fresh_breakout_narrowing"})
    before = deepcopy(ctx)
    result, _, calls = run(ctx, [wire(ctx, side)])
    assert result["selected"]["raw_proposal"]["side"] == side
    assert ctx == before
    assert len(calls) == 2
    for call in calls:
        policy = " ".join(call["system_prompt"].split())
        assert FLAT_ENTRY_PROMPT in call["system_prompt"]
        assert policy.count(MARKER) == 1
        for clause in (
            "old MA order and ABOVE/BELOW persist",
            "If no structural entry edge exists, prefer WAIT; small risk cannot repair it",
            "grounded reachable target path; use the preceding edge-before-size sequence",
            "In calculated review, use host-calculated candidate economics; do not discard",
            "a structurally justified candidate solely on an estimated-cost impression before that calculation",
            "The host still enforces economic and safety limits",
            "SL at structural invalidation with noise room",
            "Contraction alone is not a veto",
            "while the old MA gap still narrows, before a lagging crossover",
            "Do not require another MA10 cross, a candle close or unanimous 30m/1h alignment",
            "forming progress is provisional",
            "a tiny WIDENING sign alone is not strong expansion",
            "name the timeframe and confirmed/forming phase",
            "ma_structure.primary[frame].confirmed_gap.state or forming_gap.state",
            "matching timestamped transitions.gap_state/gap_change_price",
            "Do not transfer another frame's expansion to 30m/1h",
            "while the still-bullish MA gap narrows",
            "a still-bearish narrowing gap",
            "directional price progress, not same-direction MA-gap expansion",
            "in recovery reasoning as well as the final rationale",
            "For LONG inspect higher lows",
            "for SHORT mirror with lower highs",
            "not a mandatory short target for every flat entry",
            "preserve later targets and a strong runner",
        ):
            assert clause in policy
        assert policy.index(MARKER) < policy.index("Compare a capped marketable LIMIT now")
        payload = restore_model_input(json.loads(call["user_prompt"]))
        assert payload["contract_context"]["test_structural_facts"] == ctx["test_structural_facts"]
        assert len(call["user_prompt"].encode()) <= 100000


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
@pytest.mark.parametrize("phase", ["pending", "holding"])
def test_active_and_pending_prompts_do_not_receive_flat_entry_change(side, phase):
    ctx = context()
    candidate = wire(ctx, side)
    ctx.update(scenario_id="active", revision=1, side=side,
               positions=[dict(price=83410, quantity=.01)] if phase == "holding" else [],
               pending_entries=[] if phase == "holding" else [dict(id="pending", price=83410, quantity=.01)],
               previous_hard_stop=candidate["hard_stop"])
    candidate = wire(ctx, side)
    candidate.update(action="ADJUST", entries=[])
    before = deepcopy(ctx)
    _, _, calls = run(ctx, [candidate])
    assert len(calls) == 2
    assert ctx == before
    for call in calls:
        assert MARKER not in " ".join(call["system_prompt"].split())
        assert FLAT_ENTRY_PROMPT not in call["system_prompt"]


def test_host_allocation_both_phases_include_priority_without_new_fields():
    from tests.test_scenario_allocation_review import taped

    args, calls = taped("allocation-calculated")
    assert args[3]["outcome"]["status"] == "COMPLETE"
    assert len(calls) == 2
    for call in calls:
        assert MARKER in " ".join(call["system_prompt"].split())
    assert "Do not emit entry quantity" in calls[0]["system_prompt"]
    assert "Return exactly selection_token and rationale" in calls[1]["system_prompt"]
    assert set(calls[1]["response_schema"]["properties"]) == {"selection_token", "rationale"}


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
def test_mirrored_real_derived_gap_facts_remain_frame_and_phase_specific(side):
    from live.scenario_calculated_review import research_review
    from live.scenario_preview import response_contract
    from tests.test_scenario_ma_context import NOW, point

    ctx = context()
    ctx.update(now=NOW / 1000, input_captured_at=NOW / 1000, account_captured_at=NOW / 1000)
    # Price moves toward a cross while the old contrary-order gap contracts.
    direction = 1 if side == "LONG" else -1
    frames = {}
    for frame in ("15m", "30m", "1h"):
        before_gap, after_gap = (10, 20) if frame == "15m" else (25.86, 6.09)
        order = direction if frame == "15m" else -direction
        before = point(frame, 2, close=83410, fast=83410 + order * before_gap, slow=83410)
        after = point(frame, 1, close=83410 + direction * 20,
                      fast=83410 + order * after_gap, slow=83410, confirmed=False)
        frames[frame] = dict(ma_path=dict(version=1, confirmed_points=[before], forming_point=after,
            confirmed_gap=dict(state="NARROWING", absolute_price=before_gap),
            forming_gap=dict(state="WIDENING" if frame == "15m" else "NARROWING", absolute_price=after_gap)))
    snap = dict(valid=True, as_of_ms=NOW, timeframes=frames)
    before_input = deepcopy((ctx, snap))
    calls = []

    def generate(**request):
        calls.append(request)
        payload = restore_model_input(json.loads(request["user_prompt"]))
        primary = payload["contract_context"]["ma_structure"]["primary"]
        assert '15m' not in primary  # Retired-frame expansion cannot become current confirmation.
        for frame in ("30m", "1h"):
            assert primary[frame]["confirmed_gap"]["state"] == "NARROWING"
            assert primary[frame]["forming_gap"]["state"] == "NARROWING"
            transition = primary[frame]["transitions"][-1]
            assert transition["gap_state"] == "NARROWING"
            assert transition["gap_change_price"] == pytest.approx(-19.77)
            assert transition["close_change_price"] == direction * 20
            assert transition["provisional"] is True
        assert "matching timestamped transitions.gap_state/gap_change_price" in " ".join(request["system_prompt"].split())
        if len(calls) == 1:
            candidate, wait = wire(ctx, side), wire(ctx)
            candidate["expires_at"] = wait["expires_at"] = ctx["now"] + 100
            reply = dict(wait_proposal=wait, candidates=[candidate], no_candidate_reason="")
        else:
            candidate = next(c for c in payload["calculated_candidates"]["candidates"] if c["eligible"])
            reply = dict(selection_token=f"{candidate['id']}:{candidate['receipt_sha256']}", rationale="fixture")
        return SimpleNamespace(text=json.dumps(reply))

    result = research_review(snap, ctx, response_contract(ctx), generate=generate,
                             record=lambda _: None, clock=lambda: ctx["now"])
    assert len(calls) == 2
    assert result["selected"]["raw_proposal"]["side"] == side
    assert (ctx, snap) == before_input
