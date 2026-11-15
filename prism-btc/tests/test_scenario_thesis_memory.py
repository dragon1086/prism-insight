"""Prior model opinions survive projection without becoming execution authority."""
from copy import deepcopy
import json
import sqlite3

import pytest

from live.scenario_calculated_review import _model_payload
from live.scenario_entry_contract import contract_context
from live.scenario_llm import ENTRY_CONTRACT_PROMPT
from live.scenario_model_input import restore_model_input
from live.scenario_runtime import ScenarioRuntime
from tests.test_scenario_entry_contract_runtime import active, definition
from tests.test_scenario_entry_contract_wire import context as wire_context, taped
from tests.test_scenario_runtime import Broker, proposal, terminal, wait_proposal


def remembered():
    state = active()
    state["last_thesis_assessment"] = dict(contract_id=state["entry_contract"]["contract_id"],
        status="WEAKENING", reason="15m BETWEEN; breakout follow-through weakened")
    state["last_thesis_assessment_at"] = 1200
    return state


def test_prior_opinion_is_bounded_detached_and_survives_both_review_phases():
    state = remembered()
    original = deepcopy(state)
    ctx = dict(wire_context(), scenario_id="s", **contract_context(state, True))
    previous = ctx["previous_thesis_assessment"]
    assert previous == dict(state["last_thesis_assessment"], assessed_at=1200,
                            source="HISTORICAL_MODEL_OPINION")
    for candidates in (None, {}):
        payload = _model_payload(dict(valid=True), ctx, {}, calculated_candidates=candidates)
        assert restore_model_input(payload)["contract_context"]["previous_thesis_assessment"] == dict(
            previous, reason='OMITTED_RETIRED_TIMEFRAME_OPINION')
        assert previous['reason'] == original['last_thesis_assessment']['reason']
    previous["reason"] = "changed copy"
    assert state == original


@pytest.mark.parametrize("mutation", ["wrong_contract", "bad_status", "long_reason", "missing", "legacy"])
def test_untrusted_or_unbound_prior_is_not_presented(mutation):
    state = remembered()
    if mutation == "wrong_contract":
        state["last_thesis_assessment"]["contract_id"] = "another-scenario"
    elif mutation == "bad_status":
        state["last_thesis_assessment"]["status"] = "REENTER"
    elif mutation == "long_reason":
        state["last_thesis_assessment"]["reason"] = "x" * 241
    elif mutation == "missing":
        del state["last_thesis_assessment"]
    else:
        del state["entry_contract"]
    assert contract_context(state, True)["previous_thesis_assessment"] is None
    assert contract_context(state, False) == {}


@pytest.mark.parametrize("at", [None, True, float("nan"), 800])
def test_old_or_invalid_timestamp_remains_unknown(at):
    state = remembered()
    state["last_thesis_assessment_at"] = at
    prior = contract_context(state, True)["previous_thesis_assessment"]
    assert prior["assessed_at"] is None
    assert prior["status"] == "WEAKENING"




@pytest.mark.parametrize("status", ["INTACT", "WEAKENING", "UNKNOWN"])
def test_previous_weakening_does_not_force_exit_or_lock_status(status):
    ctx = wire_context()
    state = dict(scenario_id=ctx["scenario_id"], entry_contract=ctx["registered_entry_contract"],
        last_thesis_assessment=dict(contract_id=ctx["registered_entry_contract"]["contract_id"],
                                   status="WEAKENING", reason="Prior concern"),
        last_thesis_assessment_at=950)
    ctx.update(contract_context(state, True))
    args, calls = taped(ctx, status=status, choose_wait=True)
    assert args[3]["outcome"]["status"] == "COMPLETE"
    for call in calls:
        restored = restore_model_input(json.loads(call["user_prompt"]))
        assert restored["contract_context"]["previous_thesis_assessment"] == ctx["previous_thesis_assessment"]


@pytest.mark.parametrize("action", ["WAIT", "ADJUST"])
def test_runtime_persists_assessment_time_across_restart(tmp_path, action):
    path = tmp_path / "state.db"
    now, broker, contexts = [1800000000.], Broker(), []
    def model(snapshot, ctx):
        contexts.append(deepcopy(ctx))
        if not ctx["scenario_id"]:
            return dict(proposal(snapshot, ctx), entry_contract=definition(), thesis_assessment=None)
        response = (dict(proposal(snapshot, ctx), action="ADJUST", action_id="review", entries=[])
                    if action == "ADJUST" and len(contexts) == 2 else wait_proposal(snapshot, ctx, action_id="wait"))
        return dict(response, entry_contract=None, thesis_assessment=dict(
            contract_id=ctx["registered_entry_contract"]["contract_id"], status="WEAKENING", reason="Prior concern"))
    def runtime():
        return ScenarioRuntime(sqlite3.connect(path), broker, model,
            lambda: dict(valid=True, as_of_ms=now[0]*1000), clock=lambda: now[0], entry_contract_enabled=True)
    r = runtime()
    assert r.tick()["status"] == "intent_pending"
    broker.evidence = dict(intents=[terminal()])
    broker.ctx["positions"] = [dict(price=100, quantity=1)]
    now[0] += 300
    assert r.tick()["status"] == ("wait" if action == "WAIT" else "intent_pending")
    saved = r.state()["active"]
    assert saved["last_thesis_assessment_at"] == now[0]
    assert contexts[-1]["previous_thesis_assessment"] is None
    r.conn.close()
    if action == "ADJUST":
        broker.evidence = dict(intents=[dict(terminal(), intent_id="review")])
    now[0] += 300
    r = runtime()
    assert r.tick()["status"] == "wait"
    assert contexts[-1]["previous_thesis_assessment"] == dict(saved["last_thesis_assessment"],
        assessed_at=now[0]-300, source="HISTORICAL_MODEL_OPINION")
    r.conn.close()
