"""Research action contract must reject inactive fields before economics."""
from copy import deepcopy
from functools import partial
import json
from types import SimpleNamespace

import pytest

from live.scenario_contract import _matches
from live.scenario_calculated_review import (
    allocation_response_schema, action_allocation_response_schema, research_review, receipt_hash,
)
from analysis.scenario_review_tape import record_review
from live.scenario_preview import response_contract
from live.scenario_review_guard import guard_review
from tests.test_scenario_allocation_review import allocation_wire
from tests.test_scenario_review_guard import context
from tests.test_scenario_review_guard import model_receipt_hash


def test_wait_chase_regression():
    from live.scenario_calculated_review import action_allocation_response_schema
    ctx = context()
    bad = allocation_wire(ctx, "WAIT")
    bad["chase"] = dict(max_bps=0, max_reprices=0)
    assert _matches(bad, allocation_response_schema(ctx))  # Original failure shape.
    assert not _matches(dict(proposal=bad), action_allocation_response_schema(ctx))
    good = deepcopy(bad)
    good["chase"] = None
    assert _matches(dict(proposal=good), action_allocation_response_schema(ctx))


@pytest.mark.parametrize("action", ["WAIT", "OPEN", "ADJUST", "EXIT"])
def test_action_branches_and_inactive_metadata(action):
    ctx = context()
    if action in {"ADJUST", "EXIT"}:
        ctx.update(scenario_id="active", side="LONG")
    ctx.update(stop_policy={"version": 1}, review_contract_version=1,
               recovery_contract_version=2,
               pending_entries=[dict(id="old", price=83000, quantity=.01)])
    p = allocation_wire(ctx, action)
    p.update(stop_revision=dict(reason="kept", structure_reference="MA35", input_id=ctx["input_id"]),
             review=None, cancel_entry_ids=["old"])
    schema = action_allocation_response_schema(ctx)
    assert _matches(dict(proposal=p), schema)
    changed = deepcopy(p)
    if action in {"WAIT", "EXIT"}:
        for field, value in [("side", "LONG"), ("hard_stop", 82900),
                             ("chase", dict(max_bps=0, max_reprices=0)),
                             ("entries", [dict(id="e", price=83000, allocation_fraction=.1)]),
                             ("take_profits", [dict(id="t", price=84000, fraction=.5)]),
                             ("partial_stops", [dict(id="s", price=83000, fraction=.5)])]:
            assert not _matches(dict(proposal=dict(p, **{field: value})), schema)
    elif action == "OPEN":
        changed["entries"] = []
        assert not _matches(dict(proposal=changed), schema)
    else:
        changed["side"] = "SHORT"
        assert not _matches(dict(proposal=changed), schema)
    p["cancel_entry_ids"] = ["unknown"]
    assert not _matches(dict(proposal=p), schema)


def test_first_observation_intersection_and_legacy_unchanged():
    ctx = dict(context(), recovery=dict(first_observation=True))
    before = deepcopy(allocation_response_schema(ctx))
    schema = action_allocation_response_schema(ctx)
    assert [s["properties"]["action"]["enum"] for s in schema["properties"]["proposal"]["anyOf"]] == [["WAIT"]]
    assert allocation_response_schema(ctx) == before
    assert not _matches(dict(proposal=allocation_wire(ctx)), schema)


def test_array_min_items_is_checked_locally():
    schema = dict(type="array", items=dict(type="integer"), minItems=1, maxItems=2)
    assert not _matches([], schema)
    assert _matches([1], schema)
    assert _matches([1, 2], schema)
    assert not _matches([1, 2, 3], schema)
    assert not _matches([True], schema)


@pytest.mark.parametrize("mode", ["action-allocation-single-v1", "action-allocation-calculated-v1"])
def test_invalid_wait_raw_is_retained_without_repair(mode):
    ctx, events = context(), []
    bad = allocation_wire(ctx, "WAIT")
    bad["chase"] = dict(max_bps=0, max_reprices=0)
    raw = dict(proposal=bad) if "single" in mode else dict(wait_proposal=bad, candidates=[], no_candidate_reason="관망")
    text = json.dumps(raw)
    with pytest.raises(ValueError, match="research_response_contract"):
        research_review(dict(valid=True, as_of_ms=1000000), ctx, response_contract(ctx),
                        generate=lambda **_: SimpleNamespace(text=text), record=events.append,
                        clock=lambda: 1000, research_mode=mode)
    assert [event["kind"] for event in events] == ["MODEL_REQUEST", "MODEL_RAW"]
    assert events[-1]["raw"] == text


def test_rejected_single_allocation_keeps_wrapper_hash():
    ctx, events = context(), []
    p = allocation_wire(ctx)
    p["entries"][0]["allocation_fraction"] = 2
    wrapper = dict(proposal=p)
    with pytest.raises(ValueError, match="research_allocation_rejected"):
        research_review(dict(valid=True, as_of_ms=1000000), ctx, response_contract(ctx),
                        generate=lambda **_: SimpleNamespace(text=json.dumps(wrapper)), record=events.append,
                        clock=lambda: 1000, research_mode="action-allocation-single-v1")
    rejection = events[-1]["body"]
    assert events[-1]["kind"] == "CALCULATED_CANDIDATE_REJECTED"
    assert rejection["raw_model_wrapper"] == wrapper
    assert rejection["raw_model_wrapper_sha256"] == receipt_hash(wrapper)
    assert rejection["raw_allocation_sha256"] == receipt_hash(p)


@pytest.mark.parametrize("mode", ["action-allocation-single-v1", "action-allocation-calculated-v1"])
@pytest.mark.parametrize("action", ["WAIT", "OPEN", "ADJUST", "EXIT"])
def test_wrapper_extraction_receipts_and_zero_call_fresh_replay(mode, action):
    ctx = context()
    if action in {"ADJUST", "EXIT"}:
        ctx.update(scenario_id="active", side="LONG", positions=[dict(price=83410, quantity=.01)],
                   previous_hard_stop=82800)
    snap = dict(valid=True, as_of_ms=1000000)
    calls = []

    def generate(**request):
        calls.append(request)
        if len(calls) == 2:
            body = json.loads(request["user_prompt"])
            selected = body["calculated_candidates"]["candidates"][0]
            assert selected["eligible"], selected
            raw = dict(selection_token=f"{selected['id']}:{model_receipt_hash(selected)}", rationale="선택")
        elif "single" in mode:
            raw = dict(proposal=allocation_wire(ctx, action))
        else:
            raw = dict(wait_proposal=allocation_wire(ctx, "WAIT"),
                       candidates=[] if action == "WAIT" else [allocation_wire(ctx, action)],
                       no_candidate_reason="관망" if action == "WAIT" else "")
        assert _matches(raw, request["response_schema"])
        return SimpleNamespace(text=json.dumps(raw))

    contract = response_contract(ctx)
    envelope = record_review(partial(research_review, research_mode=mode), snap, ctx, contract,
        generate=generate, record=lambda _: None, clock=lambda: 1000, bindings={"mode": mode})
    assert envelope["outcome"]["status"] == "COMPLETE", envelope["outcome"]
    selected = envelope["outcome"]["result"]["selected"]
    assert selected["raw_model_wrapper_sha256"] == receipt_hash(selected["raw_model_wrapper"])
    assert selected["raw_allocation_sha256"] == receipt_hash(selected["raw_allocation_proposal"])
    assert selected["raw_allocation_proposal"]["action"] == action
    n = len(calls)
    checked = guard_review(snap, ctx, contract, envelope, dict(ctx, now=1001, account_captured_at=1001),
                           bindings={"mode": mode}, research_mode=mode)
    assert checked["payload"]["action"] == action
    assert len(calls) == n
    tampered = deepcopy(envelope)
    tampered["outcome"]["result"]["selected"]["raw_model_wrapper"]["extra"] = 1
    with pytest.raises(ValueError):
        guard_review(snap, ctx, contract, tampered, dict(ctx, now=1001, account_captured_at=1001),
                     bindings={"mode": mode}, research_mode=mode)
