"""Lifecycle boundaries behind subscriber-facing entry/exit distinctions."""
import pytest

from tests.test_scenario_runtime import setup as setup, proposal, terminal, settlement


@pytest.mark.parametrize("scenario_id", ["scenario-1", "unrelated-new-cycle"])
def test_active_cycle_cannot_open_another_cycle(setup, scenario_id):
    runtime, broker, now, _ = setup
    assert runtime.tick()["status"] == "intent_pending"
    broker.evidence = dict(intents=[terminal()])
    broker.ctx["positions"] = [dict(price=100, quantity=1)]
    now[0] += 300
    runtime.propose = lambda s,c: dict(proposal(s,c), scenario_id=scenario_id, action_id="second")
    assert runtime.tick()["status"] == "blocked"
    assert broker.executed == ["action-1"]
    assert runtime.state()["active"]["scenario_id"] == "scenario-1"


@pytest.mark.parametrize("missing", ["flat_confirmed", "orders_terminal", "executions_complete", "fees_complete", "funding_complete"])
def test_incomplete_settlement_cannot_start_new_cycle(setup, missing):
    runtime, broker, now, _ = setup
    assert runtime.tick()["status"] == "intent_pending"
    broker.evidence = dict(intents=[terminal()], settlement=settlement(**{missing:False}))
    now[0] += 300
    runtime.propose = lambda s,c: dict(proposal(s,c), scenario_id="new-cycle", action_id="second")
    assert runtime.tick()["status"] == "blocked"
    assert broker.executed == ["action-1"]
    assert runtime.state()["active"] is not None
    assert runtime.conn.execute("SELECT count(*) FROM llm_scenario_settlements").fetchone()[0] == 0


def test_completed_settlement_allows_new_cycle_but_never_reuses_prior_identity(setup):
    runtime, broker, now, _ = setup
    assert runtime.tick()["status"] == "intent_pending"
    broker.evidence = dict(intents=[terminal()], settlement=settlement())
    now[0] += 300
    runtime.propose = lambda s,c: dict(proposal(s,c), action_id="second")
    assert runtime.tick()["status"] == "reused_scenario"
    assert broker.executed == ["action-1"]
    assert runtime.state()["active"] is None
    now[0] += 300
    runtime.propose = lambda s,c: dict(proposal(s,c), scenario_id="new-cycle", action_id="new-cycle-entry")
    assert runtime.tick()["status"] == "intent_pending"
    assert broker.executed == ["action-1", "new-cycle-entry"]
    assert runtime.state()["active"]["scenario_id"] == "new-cycle"


def test_addition_is_same_cycle_with_same_initial_risk_budget(setup):
    runtime, broker, now, _ = setup
    assert runtime.tick()["status"] == "intent_pending"
    broker.evidence = dict(intents=[terminal()])
    broker.ctx["positions"] = [dict(price=100, quantity=1)]
    now[0] += 300
    runtime.propose = lambda s,c: dict(proposal(s,c), action="ADJUST", action_id="additional", hard_stop=95)
    assert runtime.tick()["status"] == "intent_pending"
    active = runtime.state()["active"]
    assert active["scenario_id"] == "scenario-1"
    assert active["initial_equity"] == 10000
    assert active["current_plan"]["risk"]["budget"] == 200
    assert broker.executed == ["action-1", "additional"]
