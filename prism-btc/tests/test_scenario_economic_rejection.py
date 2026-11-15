"""New economic-only disposition; malformed and legacy cases stay fail closed."""
from copy import deepcopy
from functools import partial
import json
import sqlite3
from types import SimpleNamespace

import pytest

from analysis.scenario_review_tape import record_review
from live.scenario_calculated_review import research_review
from live.scenario_preview import response_contract
from live.scenario_review_guard import guard_rejected_review
from tests.test_scenario_allocation_review import allocation_wire
from tests.test_scenario_review_guard import context, wire
from tests.test_scenario_review_guard import model_receipt_hash


def run(mode, *, mutate=None, second=None, context_change=None):
    ctx = context()
    if context_change:
        context_change(ctx)
    snap = dict(valid=True, as_of_ms=1000000)
    raw = allocation_wire(ctx)
    raw["entries"][0]["allocation_fraction"] = .000001
    if mutate:
        mutate(raw)
    candidates = [raw] + ([second(ctx)] if second else [])
    calls = []

    def generate(**request):
        calls.append(request)
        if len(calls) == 2:
            bundle = json.loads(request["user_prompt"])["calculated_candidates"]
            chosen = next(r for r in bundle["candidates"] if r["eligible"])
            result = dict(selection_token=f"{chosen['id']}:{model_receipt_hash(chosen)}", rationale="선택")
        elif "single" in mode:
            result = dict(proposal=raw)
        else:
            result = dict(wait_proposal=wire(ctx, "WAIT"), candidates=candidates, no_candidate_reason="")
        return SimpleNamespace(text=json.dumps(result))

    envelope = record_review(partial(research_review, research_mode=mode), snap, ctx,
        response_contract(ctx), generate=generate, record=lambda _: None, clock=lambda: 1000,
        bindings={"test": mode})
    return (snap, ctx, response_contract(ctx), envelope), calls


@pytest.mark.parametrize("mode", ["economic-reject-single-v1", "economic-reject-calculated-v1"])
def test_typed_rejection_complete_replay_no_selection(mode):
    args, calls = run(mode)
    outcome = args[3]["outcome"]
    assert outcome["status"] == "COMPLETE", outcome
    result = outcome["result"]
    assert result["disposition"] == "REJECTED_NO_ACTION"
    assert "selected" not in result
    assert len(calls) == 1
    receipt = result["candidates"]["candidates"][0]
    assert receipt["rejection"] == "allocation_below_minimum"
    assert receipt["raw_model_wrapper"]
    checked = guard_rejected_review(*args, bindings={"test": mode}, research_mode=mode)
    assert checked["execution_authorized"] is False
    assert len(calls) == 1


@pytest.mark.parametrize("mutate", [
    lambda r: r.update(input_id="forged"),
    lambda r: r.update(expires_at=999),
    lambda r: r.update(cancel_entry_ids=["unknown"]),
    lambda r: r["take_profits"][0].update(fraction=2),
    lambda r: r["entries"].append(dict(r["entries"][0], id="bad", price=-1)),
    lambda r: r.update(chase=dict(max_bps=999, max_reprices=1)),
])
def test_small_allocation_does_not_hide_malformed_fields(mutate):
    args, calls = run("economic-reject-calculated-v1", mutate=mutate, second=allocation_wire)
    assert args[3]["outcome"]["status"] != "COMPLETE"
    assert len(calls) == 1


def test_economic_candidate_can_coexist_with_eligible_candidate():
    args, calls = run("economic-reject-calculated-v1", second=allocation_wire)
    assert args[3]["outcome"]["status"] == "COMPLETE", args[3]["outcome"]
    assert args[3]["outcome"]["result"]["selected"]["id"] == "c1"
    assert len(calls) == 2


def test_arbitrary_exception_with_known_message_is_not_economic(monkeypatch):
    from live import scenario_risk_allocation

    def unknown(*args, **kwargs):
        raise ValueError("allocation_below_minimum")
    monkeypatch.setattr(scenario_risk_allocation, "allocate_entry_risk", unknown)
    args, calls = run("economic-reject-single-v1")
    assert args[3]["outcome"]["status"] != "COMPLETE"
    assert len(calls) == 1


@pytest.mark.parametrize("change", [
    lambda c: c.pop("mark_price"),
    lambda c: c.update(mark_price=82000),
    lambda c: c.update(accounting_status="unconfirmed"),
    lambda c: c.update(accounting_status="pending"),
    lambda c: c.update(protection_ok=False),
    lambda c: c.update(legacy_fenced=True),
])
@pytest.mark.parametrize("second", [None, allocation_wire])
def test_bad_trusted_input_never_becomes_economic_rejection(change, second):
    args, _ = run("economic-reject-calculated-v1", context_change=change, second=second)
    assert args[3]["outcome"]["status"] != "COMPLETE"


def test_nonfinite_input_fails_before_tape_or_model():
    with pytest.raises(ValueError, match="Out of range float"):
        run("economic-reject-single-v1", context_change=lambda c: c.update(mark_price=float("nan")))


def test_legacy_single_still_stops_and_tampering_never_replays():
    args, _ = run("action-allocation-single-v1")
    assert args[3]["outcome"]["status"] != "COMPLETE"
    args, _ = run("economic-reject-single-v1")
    modified = deepcopy(args[3])
    modified["outcome"]["result"]["candidates"]["candidates"][0]["rejection"] = "forged"
    with pytest.raises(Exception):
        guard_rejected_review(*args[:3], modified, bindings={"test": "economic-reject-single-v1"},
                              research_mode="economic-reject-single-v1")


@pytest.mark.parametrize("mode", ["economic-reject-single-v1", "economic-reject-calculated-v1"])
@pytest.mark.parametrize("audit_failure", [False, True])
def test_runtime_rejects_before_payload_or_authority_and_next_tick_runs(tmp_path, mode, audit_failure):
    from live.scenario_review_adapter import make_calculated_review_proposer
    from live.scenario_runtime import ScenarioRuntime
    from tests.test_scenario_review_runtime import Broker, wire as runtime_wire
    now = [1800000000.]
    broker = Broker(now[0])
    captured, calls = {}, []

    def generate(**request):
        calls.append(request)
        raw = runtime_wire(captured, "OPEN")
        raw["entries"][0].pop("quantity")
        raw["entries"][0]["allocation_fraction"] = .000001
        result = dict(proposal=raw) if "single" in mode else dict(
            wait_proposal=runtime_wire(captured, "WAIT"), candidates=[raw], no_candidate_reason="")
        return SimpleNamespace(text=json.dumps(result))

    adapter = make_calculated_review_proposer(generate=generate, clock=lambda: now[0], research_mode=mode)

    def propose(snapshot, ctx, *, record):
        captured.update(ctx)

        def checked_record(event):
            if audit_failure and event["kind"] == "RESEARCH_REJECTION":
                raise OSError("simulated audit failure")
            record(event)
        return adapter(snapshot, ctx, record=checked_record)

    runtime = ScenarioRuntime(sqlite3.connect(tmp_path / "reject.db"), broker,
        lambda *_: pytest.fail("legacy called"), lambda: dict(valid=True, as_of_ms=now[0]*1000),
        clock=lambda: now[0], calculated_review_enabled=True, calculated_review_propose=propose,
        research_review_mode=mode)
    for _ in range(2):
        outcome = runtime.tick()
        assert outcome["status"] == ("blocked" if audit_failure else "rejected_no_action"), outcome
        now[0] += 300
        broker.ctx["account_captured_at"] = now[0]
    assert len(calls) == 2
    assert broker.executed == []
    assert runtime.conn.execute("SELECT COUNT(*) FROM llm_scenario_intents").fetchone()[0] == 0
    assert runtime.conn.execute("SELECT COUNT(*) FROM llm_scenario_decisions WHERE proposal IS NOT NULL").fetchone()[0] == 0
