"""Host state/array classification never asks the model to echo permission."""
from copy import deepcopy
import json
from types import SimpleNamespace

import pytest

from analysis.scenario_review_tape import record_review, replay_review
from live.scenario_calculated_review import research_review
from live.scenario_preview import response_contract
from tests.test_scenario_calculated_review import context, wire
from tests.test_scenario_review_guard import model_receipt_hash


def review(*, first=False, count=0, mutate=None, rejected=False):
    ctx=context()
    ctx.update(recovery_contract_version=2,recovery=dict(phase="OBSERVING",first_observation=first,
        eligible=not first,baseline_kind="FIRST_POST_HALT_OBSERVATION"))
    before=deepcopy(ctx)
    calls,events=[],[]
    candidates=[wire(ctx,"LONG") for _ in range(count)]
    if rejected:
        candidates[0]["entries"][0]["quantity"]=10
    def generate(**request):
        calls.append(deepcopy(request))
        if len(calls)==1:
            result=dict(wait_proposal=wire(ctx),candidates=candidates,
                no_candidate_reason="최초 관찰이라 기다린다는 모델 원문")
            if mutate:
                mutate(result)
        else:
            bundle=json.loads(request["user_prompt"])["calculated_candidates"]
            chosen=next((row for row in bundle["candidates"] if row["eligible"]),bundle["wait"])
            result=dict(selection_token=f"{chosen['id']}:{model_receipt_hash(chosen)}",rationale="모델 선택 원문")
        return SimpleNamespace(text=json.dumps(result))
    snapshot=dict(valid=True,as_of_ms=1000000)
    contract=response_contract(ctx)
    envelope=record_review(research_review,snapshot,ctx,contract,generate=generate,
        record=events.append,clock=lambda:1000)
    assert ctx==before
    assert replay_review(envelope,research_review,snapshot,ctx,contract)==envelope["outcome"]
    return envelope,calls,events


@pytest.mark.parametrize("count", [0,1,2])
def test_retained_failure_context_first_false_historical_first_basis_host_classifies(count):
    envelope,calls,_=review(first=False,count=count)
    assert envelope["outcome"]["status"]=="COMPLETE",envelope["outcome"]
    bundle=envelope["outcome"]["result"]["candidates"]
    assert bundle["no_candidate_category"]==("CANDIDATES_PROVIDED" if count else "NO_CANDIDATES_SUPPLIED")
    assert bundle["category_source"]=="HOST_DERIVED_FROM_STATE_AND_ARRAY"
    assert bundle["qualitative_reason_truth"]=="NOT_MECHANICALLY_VERIFIED"
    assert bundle["no_candidate_reason"]=="최초 관찰이라 기다린다는 모델 원문"
    schema=calls[0]["response_schema"]
    assert set(schema["properties"])=={"wait_proposal","candidates","no_candidate_reason"}
    assert schema["properties"]["wait_proposal"]["properties"]["action"]["enum"]==["WAIT"]
    assert schema["properties"]["candidates"]["items"]["properties"]["action"]["enum"]==["OPEN"]
    assert schema["properties"]["candidates"]["maxItems"]==2
    assert len(calls)==(2 if count else 1)


def test_actual_first_observation_wait_only_zero_candidates_and_host_category():
    envelope,calls,_=review(first=True)
    assert envelope["outcome"]["status"]=="COMPLETE"
    assert envelope["outcome"]["result"]["candidates"]["no_candidate_category"]=="FIRST_OBSERVATION"
    assert calls[0]["response_schema"]["properties"]["candidates"]["maxItems"]==0
    rejected,_,_=review(first=True,count=1)
    assert rejected["outcome"]["error"]["message"]=="research_response_contract"


def test_risk_rejected_candidate_remains_submitted_not_no_candidate():
    envelope,_,_=review(count=1,rejected=True)
    assert envelope["outcome"]["status"]=="COMPLETE"
    bundle=envelope["outcome"]["result"]["candidates"]
    assert bundle["no_candidate_category"]=="CANDIDATES_PROVIDED"
    assert bundle["candidates"][0]["eligible"] is False
    assert bundle["candidates"][0]["rejection"]


@pytest.mark.parametrize("fault", ["legacy_category","three","missing_reason","bad_wire"])
def test_unknown_legacy_field_cap_missing_reason_bad_wire_remain_failed_exact(fault):
    def mutate(result):
        if fault=="legacy_category":
            result["no_candidate_category"]="FIRST_OBSERVATION"
        elif fault=="missing_reason":
            result.pop("no_candidate_reason")
        elif fault=="bad_wire":
            result["wait_proposal"]["input_id"]="wrong"
    envelope,_,events=review(count=3 if fault=="three" else 0,mutate=mutate)
    assert envelope["outcome"]["status"]=="FAILED"
    assert envelope["outcome"]["error"]["message"]=="research_response_contract"
    if fault=="legacy_category":
        raw=next(event["raw"] for event in events if event["kind"]=="MODEL_RAW")
        assert json.loads(raw)["no_candidate_category"]=="FIRST_OBSERVATION"


def test_hard_wait_only_permission_is_not_first_observation(monkeypatch):
    from live import scenario_calculated_review as module
    original=module.response_schema
    def wait_only(ctx):
        schema=original(ctx)
        schema["properties"]["action"]["enum"]=["WAIT"]
        return schema
    monkeypatch.setattr(module,"response_schema",wait_only)
    envelope,calls,_=review(first=False)
    assert envelope["outcome"]["status"]=="COMPLETE"
    assert calls[0]["response_schema"]["properties"]["candidates"]["maxItems"]==0
    assert envelope["outcome"]["result"]["candidates"]["no_candidate_category"]=="NO_CANDIDATES_SUPPLIED"


def test_draft_prompt_clarifies_fallback_and_historical_baseline_without_echo_fields():
    from live.scenario_calculated_review import DRAFT_PROMPT
    assert "WAIT alternative" in DRAFT_PROMPT
    from live.scenario_llm import system_instructions
    recovery=system_instructions(dict(recovery_contract_version=2))
    assert "baseline_kind" in recovery and "first_observation" in recovery
    assert "first_observation=true requires WAIT" in recovery
    assert "no_candidate_category" not in DRAFT_PROMPT
