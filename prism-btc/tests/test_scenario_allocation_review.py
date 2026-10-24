"""Offline allocation review and exact fresh-guard provenance regressions."""
from copy import deepcopy
from functools import partial
import json
import sqlite3
from types import SimpleNamespace

import pytest

from analysis.scenario_review_tape import record_review
from live.scenario_calculated_review import (
    allocation_response_schema, calculate_allocation_candidate, research_review,
)
from live.scenario_preview import response_contract
from live.scenario_review_guard import guard_review
from tests.test_scenario_review_guard import context, wire


def allocation_wire(ctx, action="OPEN"):
    raw = wire(ctx, action)
    for entry in raw["entries"]:
        entry.pop("quantity")
        entry["allocation_fraction"] = .5
    return raw


def taped(mode, *, action="OPEN", corrupt=False, ctx_override=None):
    ctx = context()
    ctx.update(ctx_override or {})
    snap = dict(valid=True, as_of_ms=1000000)
    calls = []

    def generate(**request):
        calls.append(request)
        body = json.loads(request["user_prompt"])
        assert body["response_contract"] == request["response_schema"] or mode == "allocation-calculated"
        if len(calls) == 2:
            chosen = next(r for r in body["calculated_candidates"]["candidates"] if r["eligible"])
            result = dict(selection_token=f"{chosen['id']}:{chosen['receipt_sha256']}", rationale="계산 검토")
        else:
            result = allocation_wire(ctx, action)
            if corrupt and result["entries"]:
                result["entries"][0]["quantity"] = .01
            if mode == "allocation-calculated":
                result = dict(wait_proposal=wire(ctx, "WAIT"), candidates=[] if action == "WAIT" else [result],
                              no_candidate_reason="관망 근거" if action == "WAIT" else "")
        return SimpleNamespace(text=json.dumps(result))

    envelope = record_review(partial(research_review, research_mode=mode), snap, ctx,
        response_contract(ctx), generate=generate, record=lambda _: None, clock=lambda: 1000,
        bindings={"test": mode})
    return (snap, ctx, response_contract(ctx), envelope), calls


@pytest.mark.parametrize("mode,count", [("allocation-single", 1), ("allocation-calculated", 2)])
def test_modes_bind_raw_allocation_host_wire_and_fresh_replay(mode, count):
    args, calls = taped(mode)
    assert args[3]["outcome"]["status"] == "COMPLETE", args[3]
    selected = args[3]["outcome"]["result"]["selected"]
    assert "quantity" not in selected["raw_allocation_proposal"]["entries"][0]
    assert "allocation_fraction" not in selected["raw_proposal"]["entries"][0]
    assert selected["raw_proposal"]["entries"][0]["quantity"] > 0
    assert len(calls) == count
    checked = guard_review(*args, dict(args[1], now=1001, account_captured_at=1001),
                           bindings={"test": mode}, research_mode=mode)
    assert checked["receipt"]["orders_submitted"] == 0
    assert len(calls) == count
    with pytest.raises(ValueError):
        guard_review(*args, dict(args[1], now=1001, account_captured_at=1001), bindings={"test": mode})


@pytest.mark.parametrize("mode", ["allocation-single", "allocation-calculated"])
def test_wait_never_sizes_and_mixed_quantity_contract_rejected(mode):
    args, calls = taped(mode, action="WAIT")
    assert args[3]["outcome"]["status"] == "COMPLETE"
    assert len(calls) == 1
    failed, calls = taped(mode, corrupt=True)
    assert failed[3]["outcome"]["status"] != "COMPLETE"
    assert len(calls) == 1


def test_allocation_schema_does_not_modify_legacy_and_prompt_has_no_sizing_arithmetic():
    from live.scenario_contract import response_schema
    ctx = context()
    before = deepcopy(response_schema(ctx))
    schema = allocation_response_schema(ctx)
    assert "allocation_fraction" in schema["properties"]["entries"]["items"]["required"]
    assert response_schema(ctx) == before
    _, calls = taped("allocation-single")
    prompt = calls[0]["system_prompt"]
    assert "Round proposed quantity DOWN" not in prompt
    assert "Per-BTC risk is positive entry-to-SL" not in prompt
    assert "Do not emit entry quantity" in prompt


def test_possible_price_adjustment_is_reserved_once_without_post_resize():
    ctx = context()
    ctx.update(quantity_step=.000001, minimum_quantity=.000001,
        execution_price_policy=dict(version="round-limit-v1"),
        limit_price_quotes=dict(bid=83400, ask=83401, captured_at=1000))
    raw = allocation_wire(ctx)
    receipt = calculate_allocation_candidate(raw, ctx, candidate_id="c0")
    assert receipt["eligible"] is True
    assert receipt['raw_proposal']['entries'][0]['quantity']==receipt['effective_plan']['entries'][0]['quantity']
    from live.scenario_risk_allocation import validate_allocation_prices
    validate_allocation_prices(receipt['effective_plan'],receipt['allocation_receipt'],ctx)
    changed=deepcopy(receipt['effective_plan'])
    changed['entries'][0]['price']+=100
    with pytest.raises(ValueError,match='allocation_budget_exceeded'):
        validate_allocation_prices(changed,receipt['allocation_receipt'],ctx)
    assert raw == allocation_wire(ctx)


def test_priced_allocation_fresh_guard_preserves_quantity_and_rejects_changed_price():
    args,_=taped('allocation-calculated',ctx_override=dict(
        execution_price_policy=dict(version='round-limit-v1'),
        limit_price_quotes=dict(bid=83400,ask=83401,captured_at=1000)))
    assert args[3]['outcome']['status']=='COMPLETE'
    fresh=dict(args[1],now=1001,account_captured_at=1001,
               limit_price_quotes=dict(bid=83401,ask=83402,captured_at=1001))
    checked=guard_review(*args,fresh,bindings={'test':'allocation-calculated'},research_mode='allocation-calculated')
    selected=args[3]['outcome']['result']['selected']
    assert checked['validated']['entries']==selected['effective_plan']['entries']
    fresh['limit_price_quotes']=dict(bid=83411,ask=83412,captured_at=1001)
    with pytest.raises(ValueError,match='review_guard_effective_plan_changed'):
        guard_review(*args,fresh,bindings={'test':'allocation-calculated'},research_mode='allocation-calculated')


def test_first_observation_is_wait_only_even_without_phase_label():
    schema = allocation_response_schema(dict(context(), recovery={"first_observation": True}))
    assert schema["properties"]["action"]["enum"] == ["WAIT"]
    assert schema["properties"]["entries"]["maxItems"] == 0


@pytest.mark.parametrize("mode", ["allocation-single", "allocation-calculated",
                                  "action-allocation-single-v1", "action-allocation-calculated-v1"])
def test_runtime_trusted_mode_reaches_both_fresh_guards(tmp_path, monkeypatch, mode):
    from live import scenario_review_guard
    from live.scenario_review_adapter import make_calculated_review_proposer
    from live.scenario_runtime import ScenarioRuntime
    from tests.test_scenario_review_runtime import Broker, wire as runtime_wire
    now = 1800000000.
    broker, captured, phases = Broker(now), {}, []
    original_guard = scenario_review_guard.guard_review

    def observed_guard(*args, **kwargs):
        phases.append(kwargs["research_mode"])
        return original_guard(*args, **kwargs)

    monkeypatch.setattr(scenario_review_guard, "guard_review", observed_guard)

    def generate(**request):
        if "selection_token" in request["response_schema"]["properties"]:
            chosen = json.loads(request["user_prompt"])["calculated_candidates"]["candidates"][0]
            result = dict(selection_token=f"{chosen['id']}:{chosen['receipt_sha256']}", rationale="검산 확인")
        else:
            result = runtime_wire(captured, "OPEN")
            result["entries"][0].pop("quantity")
            result["entries"][0]["allocation_fraction"] = .5
            if mode in {"allocation-calculated", "action-allocation-calculated-v1"}:
                result = dict(wait_proposal=runtime_wire(captured, "WAIT"), candidates=[result], no_candidate_reason="")
            elif mode == "action-allocation-single-v1":
                result = dict(proposal=result)
        return SimpleNamespace(text=json.dumps(result))

    adapter = make_calculated_review_proposer(generate=generate, clock=lambda: now, research_mode=mode)

    def propose(snapshot, ctx, *, record):
        captured.update(ctx)
        return adapter(snapshot, ctx, record=record)

    runtime = ScenarioRuntime(sqlite3.connect(tmp_path / "runtime.db"), broker,
        lambda *_: pytest.fail("legacy proposer"), lambda: dict(valid=True, as_of_ms=now*1000),
        clock=lambda: now, calculated_review_enabled=True, calculated_review_propose=propose,
        research_review_mode=mode)
    result = runtime.tick()
    assert result["status"] == "intent_pending", result
    assert phases == [mode, mode]
    assert len(broker.executed) == 1


def test_envelope_tampering_cannot_rewrite_allocation():
    args, _ = taped("allocation-single")
    envelope = deepcopy(args[3])
    envelope["outcome"]["result"]["selected"]["raw_allocation_proposal"]["entries"][0]["allocation_fraction"] = 1
    with pytest.raises(ValueError):
        guard_review(*args[:3], envelope, dict(args[1], now=1001, account_captured_at=1001),
            bindings={"test": "allocation-single"}, research_mode="allocation-single")
