from copy import deepcopy
import json
from types import SimpleNamespace

import pytest

from analysis.scenario_review_tape import record_review, replay_review
from backtest.scenario_tape import TapeMismatch, digest
from live.scenario_calculated_review import research_review
from live.scenario_contract import identity_fields
from live.scenario_preview import response_contract


def context():
    return dict(now=1000, input_id="tape-1", input_captured_at=1000,
        account_captured_at=1000, max_input_age_seconds=120, scenario_id=None, revision=0,
        seen_action_ids=[], initial_equity=10000, positions=[], pending_entries=[],
        previous_hard_stop=None, realized_loss=0, fees_paid=0, funding_paid=0,
        estimated_cost_rate=.002, slippage_bps=20, new_risk_blocked=False,
        mark_price=83410, price_tick=.1, quantity_step=.001, minimum_quantity=.001,
        minimum_notional=5)


def wire(ctx, side=None):
    return dict(**identity_fields(ctx), action="OPEN" if side else "WAIT",
        confidence=.7, expires_at=1200, rationale="가격 흐름과 무효화 근거", leverage=10,
        entries=[dict(id="e", price=83410, quantity=.01)] if side else [],
        take_profits=[dict(id="tp", price=84000, fraction=.5)] if side else [],
        partial_stops=[], cancel_entry_ids=[], side=side,
        hard_stop=82900 if side else None,
        chase=dict(max_bps=0, max_reprices=0) if side else None)


def recorded(*, empty=False, fail_second=False, fail_record=False, usage=None):
    ctx = context()
    snapshot = dict(valid=True, as_of_ms=1000000)
    contract = response_contract(ctx)
    now, calls, events = [1000.0], [], []

    def generate(**request):
        calls.append(deepcopy(request))
        now[0] += 2.5
        if len(calls) == 1:
            result = dict(wait_proposal=wire(ctx), candidates=[] if empty else [wire(ctx, "LONG")],
                no_candidate_reason="구조 없음" if empty else "")
        else:
            if fail_second:
                raise RuntimeError("transport failure")
            bundle = json.loads(request["user_prompt"])["calculated_candidates"]
            chosen = bundle["candidates"][0]
            result = dict(selection_token=f"{chosen['id']}:{chosen['receipt_sha256']}", rationale="계산 확인")
        return SimpleNamespace(text=json.dumps(result), **({'usage':usage} if usage is not None else {}))

    def record(event):
        events.append(deepcopy(event))
        now[0] += .25
        if fail_record:
            raise OSError("record failed")

    envelope = record_review(research_review, snapshot, ctx, contract, generate=generate,
        record=record, clock=lambda: now[0], bindings={"source": "pinned-source"})
    return envelope, snapshot, ctx, contract, calls, events


def replay(data):
    envelope, snapshot, ctx, contract, *_ = data
    return replay_review(envelope, research_review, snapshot, ctx, contract,
                         bindings={"source": "pinned-source"})


def test_two_calls_reproduce_exact_calculation_and_selection_with_record_latency():
    data = recorded()
    envelope, _, _, _, calls, events = data
    assert envelope["outcome"]["status"] == "COMPLETE"
    assert envelope["calls"] == 2
    assert calls[0]["timeout"] == 74.75
    assert calls[1]["timeout"] < calls[0]["timeout"]
    assert events[0]["request"]["timeout"] == 75
    assert events[1]["actual_request"]["timeout"] == 74.75
    result = replay(data)
    assert result == envelope["outcome"]
    selected = result["result"]["selected"]
    assert selected["raw_sha256"] and selected["effective_sha256"]
    assert result["result"]["executable"] is False


def test_empty_candidate_one_call():
    data = recorded(empty=True)
    assert data[0]["calls"] == 1
    assert replay(data)["result"]["selected"]["raw_proposal"]["action"] == "WAIT"


@pytest.mark.parametrize("kwargs", [{"fail_second": True}, {"fail_record": True}])
def test_failed_transcript_replays_as_failed_not_complete(kwargs):
    data = recorded(**kwargs)
    assert data[0]["outcome"]["status"] == "FAILED"
    assert replay(data)["status"] == "FAILED"
    assert any("error" in operation for operation in data[0]["operations"])


@pytest.mark.parametrize("tamper", ["request", "response", "phase", "receipt", "timing", "order", "extra", "missing"])
def test_semantic_tampering_detected_even_if_outer_hash_recomputed(tamper):
    data = list(recorded())
    envelope = data[0]
    ops = envelope["operations"]
    if tamper == "request":
        next(op for op in ops if op["kind"] == "generate")["request"]["timeout"] -= 1
    elif tamper == "response":
        next(op for op in ops if op["kind"] == "generate")["text"] = "{}"
    elif tamper == "phase":
        next(op for op in ops if op["kind"] == "record")["event"]["phase"] = "selection"
    elif tamper == "receipt":
        next(op for op in ops if op["kind"] == "record" and op["event"]["kind"] == "CALCULATED_CANDIDATES")["event"]["sha256"] = "bad"
    elif tamper == "timing":
        next(op for op in ops if op["kind"] == "clock")["value"] += .1
    elif tamper == "order":
        i = next(i for i, op in enumerate(ops) if op["kind"] == "generate")
        ops[i], ops[i+1] = ops[i+1], ops[i]
    elif tamper == "extra":
        ops.append(dict(kind="clock", value=1010))
    else:
        ops.pop()
    del envelope["sha256"]
    envelope["sha256"] = digest(envelope)
    with pytest.raises(TapeMismatch):
        replay(data)


def test_unsealed_and_context_or_source_mismatch():
    data = list(recorded())
    original = deepcopy(data[0])
    del data[0]["sha256"]
    with pytest.raises(TapeMismatch):
        replay(data)
    data[0] = original
    data[2]["mark_price"] += 1
    with pytest.raises(TapeMismatch, match="inputs"):
        replay(data)
    with pytest.raises(TapeMismatch, match="inputs"):
        replay_review(original, research_review, data[1], context(), data[3], bindings={"source": "changed"})


def test_input_mutation_is_not_sealed():
    def mutate(snapshot, context, contract, **kwargs):
        context["mutated"] = True
        return {}
    with pytest.raises(TapeMismatch, match="input_mutated"):
        record_review(mutate, {}, {}, {}, generate=None, record=None, clock=None)


def test_third_call_never_reaches_generator():
    calls = []
    def generator(**request):
        calls.append(request)
        return SimpleNamespace(text="{}")
    def excessive(snapshot, context, contract, *, generate, **kwargs):
        for _ in range(3):
            generate(request="fixed")
    tape = record_review(excessive, {}, {}, {}, generate=generator, record=None, clock=None)
    assert len(calls) == 2 and tape["outcome"]["status"] == "FAILED"
    assert tape["outcome"]["error"]["message"] == "review_tape_call_limit"
    with pytest.raises(TapeMismatch):
        replay_review(tape, excessive, {}, {}, {})


def test_missing_final_outcome_is_not_completion():
    data = list(recorded())
    data[0]["outcome"] = {"status": "INCOMPLETE"}
    del data[0]["sha256"]
    data[0]["sha256"] = digest(data[0])
    with pytest.raises(TapeMismatch):
        replay(data)


def test_size_limit_precedes_generator(monkeypatch):
    import analysis.scenario_review_tape as module
    monkeypatch.setattr(module, "MAX_BYTES", 10)
    with pytest.raises(TapeMismatch, match="size"):
        record_review(lambda *_: pytest.fail("no execution"), {}, {}, {},
                      generate=None, record=None, clock=None)


def test_economic_replay_effects_once_then_guard_replay_is_pure():
    envelope,snapshot,ctx,contract,*_=recorded()
    clocks=iter(op["value"] for op in envelope["operations"] if op["kind"]=="clock")
    events,boundaries=[],[]
    result=replay_review(envelope,research_review,snapshot,ctx,contract,
        bindings={"source":"pinned-source"},economic_clock=lambda:next(clocks),
        economic_record=events.append,operation_complete=boundaries.append)
    assert result==envelope["outcome"]
    assert events==[op["event"] for op in envelope["operations"] if op["kind"]=="record"]
    assert boundaries.count("generate_return")==2
    before=deepcopy((events,boundaries))
    assert replay_review(envelope,research_review,snapshot,ctx,contract,
        bindings={"source":"pinned-source"})==result
    assert (events,boundaries)==before


def test_sanitized_oauth_timeout_category_replays_without_leaking_transport():
    from live.scenario_oauth import ScenarioOAuthTimeout
    def review(snapshot, ctx, contract, *, generate, record, clock):
        try:
            generate()
        except Exception as exc:
            record({'kind': 'MODEL_ERROR', 'category': type(exc).__name__})
            raise ValueError('research_model_call_failed') from None
    def generate():
        raise ScenarioOAuthTimeout('scenario_oauth_failed') from None
    tape = record_review(review, {}, {}, {}, generate=generate, record=lambda e: None, clock=lambda: 1)
    assert tape['outcome']['status'] == 'FAILED'
    assert any(op.get('error') == {'type': 'ScenarioOAuthTimeout', 'message': 'scenario_oauth_failed'}
               for op in tape['operations'])
    assert replay_review(tape, review, {}, {}, {}) == tape['outcome']


def test_usage_counters_roundtrip_without_response_bodies_or_double_counting():
    data=recorded(empty=True,usage=dict(input_tokens=100,output_tokens=20,
        input_tokens_details=dict(cached_tokens=60),output_tokens_details=dict(reasoning_tokens=10),secret='not retained'))
    e=data[0];op=next(o for o in e['operations']if o['kind']=='generate')
    assert op['usage']==dict(input_tokens=100,output_tokens=20,cached_input_tokens=60,reasoning_tokens=10)
    event=next(o['event']for o in e['operations']if o['kind']=='record'and o['event']['kind']=='MODEL_RAW')
    assert event['usage']==op['usage']and 'secret'not in json.dumps(op['usage'])
    assert replay(data)==e['outcome']
