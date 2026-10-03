import sqlite3

import pytest

from live.scenario_runtime import REQUIRED_CAPABILITIES, ScenarioRuntime


class Broker:
    environment = "demo"
    lane = "MAIN"
    capabilities = REQUIRED_CAPABILITIES

    def __init__(self):
        self.reconciles = 0
        self.executed = []
        self.evidence = {}
        self.fail = False
        self.ctx = dict(account_version="1", legacy_fenced=False, protection_ok=True,
            new_risk_blocked=False, day="2026-10-03", day_start_equity=10000,
            daily_net_pnl=0, initial_equity=10000, positions=[], pending_entries=[],
            previous_hard_stop=None, realized_loss=0, fees_paid=0, funding_paid=0,
            estimated_cost_rate=.002, slippage_bps=10, mark_price=100)

    def context(self):
        return dict(self.ctx)

    def reconcile(self):
        self.reconciles += 1
        return self.evidence

    def execute(self, payload, ident):
        self.executed.append(ident)
        if self.fail:
            raise TimeoutError("accepted_then_timeout")


def proposal(_snapshot, ctx):
    return dict(schema_version=1, scenario_id=ctx["scenario_id"] or "scenario-1",
        revision=ctx["revision"]+1, input_id=ctx["input_id"], action_id="action-1",
        action="OPEN", side="LONG", confidence=.5, expires_at=ctx["now"]+90,
        hard_stop=90, entries=[dict(id="e1", price=100, quantity=1)],
        take_profits=[dict(id="tp1", price=120, fraction=.5)], partial_stops=[],
        chase=dict(max_bps=10, max_reprices=1), rationale="test", leverage=10)


def wait_proposal(s, c, **changes):
    result = dict(proposal(s, c), action="WAIT", entries=[], take_profits=[], **changes)
    for key in ("hard_stop", "side", "chase"):
        result.pop(key)
    return result


@pytest.fixture
def setup(tmp_path):
    path = tmp_path / "runtime.db"
    broker = Broker()
    now = [1800000000.]
    runtime = ScenarioRuntime(sqlite3.connect(path), broker, proposal,
        lambda: {"valid": True, "as_of_ms": now[0]*1000}, clock=lambda: now[0])
    return runtime, broker, now, path


def test_framing_is_saved_before_proposal(setup):
    import json
    from live.scenario_framing import decision_framing
    runtime, broker, now, path = setup
    seen = []
    def inspect(snapshot, ctx):
        row = runtime.conn.execute("SELECT context FROM llm_scenario_decisions").fetchone()
        saved = json.loads(row[0])
        assert saved["decision_framing"] == decision_framing(snapshot, ctx)
        assert saved["decision_framing"] == ctx["decision_framing"]
        seen.append(saved["decision_framing"]["state"])
        return wait_proposal(snapshot, ctx)
    runtime.propose = inspect
    assert runtime.tick()["status"] == "wait"
    assert seen == ["DEFENSIVE"]  # Minimal fixture has no primary facts.


def terminal():
    return dict(intent_id="action-1", terminal=True, protection_ok=True,
        orders_reconciled=True, executions_complete=True, execution_ids=["fill-1"], filled_quantity=1)


def settlement(**overrides):
    value = dict(scenario_id="scenario-1", flat_confirmed=True, orders_terminal=True,
        executions_complete=True, fees_complete=True, funding_complete=True,
        execution_ids=["fill-1", "exit-1"], gross_pnl=-10, fees=1, funding_net=-1, net_pnl=-12)
    return dict(value, **overrides)


def test_unknown_submission_survives_restart_without_resubmit(setup):
    r, b, now, path = setup
    b.fail = True
    assert r.tick()["reason"] == "submission_unknown"
    r.conn.close()
    now[0] += 300
    restarted = ScenarioRuntime(sqlite3.connect(path), b, proposal, lambda: {}, clock=lambda: now[0])
    assert restarted.tick()["status"] == "intent_pending"
    assert b.executed == ["action-1"]


def test_intent_is_committed_before_submit(setup):
    r, b, _, path = setup
    def execute(payload, ident):
        with sqlite3.connect(path) as other:
            assert other.execute("SELECT status FROM llm_scenario_intents WHERE id=?", (ident,)).fetchone() == ("PENDING",)
    b.execute = execute
    assert r.tick()["status"] == "intent_pending"


def test_slot_claim_blocks_second_runtime_during_llm(setup):
    r, b, now, path = setup
    other = ScenarioRuntime(sqlite3.connect(path), b, proposal, lambda: {}, clock=lambda: now[0])
    def model(s, ctx):
        assert other.tick()["status"] == "duplicate_slot"
        return wait_proposal(s, ctx)
    r.propose = model
    assert r.tick()["status"] == "wait"
    assert other.tick()["status"] == "duplicate_slot"


def test_account_change_while_llm_runs_rejects_proposal(setup):
    r, b, _, _ = setup
    def model(s, ctx):
        b.ctx["account_version"] = "2"
        return proposal(s, ctx)
    r.propose = model
    assert r.tick()["status"] == "stale_proposal"
    assert not b.executed


@pytest.mark.parametrize("change", [{"environment": "live"}, {"lane": "SWING"}, {"capabilities": set()}])
def test_capability_and_demo_fence_keeps_protection(setup, change):
    r, b, _, _ = setup
    for key, value in change.items():
        setattr(b, key, value)
    assert r.tick()["status"] == "execution_disabled"
    assert b.reconciles == (1 if "capabilities" in change else 0)
    assert not b.executed


def test_legacy_and_protection_fences(setup):
    r, b, _, _ = setup
    b.ctx["legacy_fenced"] = True
    assert r.tick()["status"] == "fenced"
    b.ctx.update(legacy_fenced=False, protection_ok=False)
    assert r.tick()["status"] == "fenced"
    assert b.reconciles == 2


def test_llm_failure_preserves_slot_and_protection(setup):
    r, b, _, _ = setup
    r.propose = lambda *_: (_ for _ in ()).throw(TimeoutError())
    assert r.tick()["status"] == "blocked"
    assert r.tick()["status"] == "duplicate_slot"
    assert b.reconciles == 2


def test_protection_contention_after_model_keeps_safe_outcome_audit(setup, monkeypatch):
    from contextlib import contextmanager
    import json
    import live.scenario_runtime as module
    from live.entry_reservations import LockBusy
    r, b, _, _ = setup
    busy = [False]
    original = module.mutation_lock
    @contextmanager
    def lock(conn):
        if busy[0]:
            raise LockBusy("independent protection owns lock")
        with original(conn):
            yield
    monkeypatch.setattr(module, "mutation_lock", lock)
    def model(s, c):
        busy[0] = True
        return wait_proposal(s, c)
    r.propose = model
    result = r.tick()
    assert result == {"status": "lock_busy", "reason": "another_protection_or_execution_tick"}
    outcome = json.loads(r.conn.execute("SELECT outcome FROM llm_scenario_decisions").fetchone()[0])
    assert outcome == result
    assert json.loads(r.conn.execute("SELECT proposal FROM llm_scenario_decisions").fetchone()[0])["action"] == "WAIT"
    assert not b.executed


@pytest.mark.parametrize("error,reason", [
    ("response_contract_mismatch", "llm_output_contract_failed"),
    ("invalid_json", "llm_output_contract_failed"),
    ("oauth_model_failed", "llm_call_failed"),
    ("late_response", "llm_call_failed"),
])
def test_llm_failure_has_safe_audit_category_and_no_orders(setup, error, reason):
    from live.scenario_llm import ScenarioModelError
    r, b, _, _ = setup
    r.propose = lambda *_: (_ for _ in ()).throw(ScenarioModelError(error))
    assert r.tick() == {"status": "blocked", "reason": reason}
    assert reason in r.conn.execute("SELECT outcome FROM llm_scenario_decisions").fetchone()[0]
    assert not b.executed
    assert r.tick()["status"] == "duplicate_slot"


def test_recent_wait_context_is_bounded_causal_and_excludes_rejections(setup):
    r, b, now, _ = setup
    seen = []
    def model(s, ctx):
        seen.append(ctx.get("recent_waits"))
        return wait_proposal(s, ctx, rationale=f"watch {len(seen)}")
    r.propose = model
    for _ in range(5):
        assert r.tick()["status"] == "wait"
        now[0] += 300
    assert seen[0] == []
    assert [x["rationale"] for x in seen[-1]] == ["watch 2", "watch 3", "watch 4"]
    assert all(x["as_of_ms"] < now[0] * 1000 for x in seen[-1])
    r.propose = lambda s, c: wait_proposal(s, c, rationale="invalid", revision=99)
    assert r.tick()["status"] == "blocked"
    now[0] += 300
    r.propose = model
    assert r.tick()["status"] == "wait"
    assert "invalid" not in [x["rationale"] for x in seen[-1]]
    now[0] += 2100
    assert r.tick()["status"] == "wait"
    assert seen[-1] == []
    assert not b.executed


@pytest.mark.parametrize("missing", ["fees_complete", "funding_complete", "executions_complete", "flat_confirmed"])
def test_incomplete_settlement_never_completes(setup, missing):
    r, b, _, _ = setup
    r.tick()
    b.evidence = dict(intents=[terminal()], settlement=settlement(**{missing: False}))
    r.tick()
    assert r.state()["active"] is not None
    assert not r.state()["breaker"].get("completed_ids")


def test_confirmed_settlement_counts_once_and_latches_three_losses(setup):
    r, b, now, _ = setup
    r.tick()
    state = r.state()
    state["breaker"] = dict(consecutive_losses=2, completed_ids=["old-1", "old-2"])
    r._save(state)
    b.evidence = dict(intents=[terminal()], settlement=settlement())
    r.tick()
    assert r.state()["active"] is None
    assert r.state()["breaker"]["consecutive_losses"] == 3
    assert r.state()["breaker"]["blocked"]
    now[0] += 86400
    b.ctx["day"] = "2026-10-04"
    r.tick()
    assert r.state()["breaker"]["blocked"]
    assert r.state()["breaker"]["consecutive_losses"] == 3
    assert len(b.executed) == 1


def test_daily_loss_blocks_entry_not_reconciliation(setup):
    r, b, _, _ = setup
    b.ctx["daily_net_pnl"] = -400
    assert r.tick()["status"] == "blocked"
    assert r.state()["breaker"]["blocked"]
    assert b.reconciles == 1
    assert not b.executed


def test_acceptance_cannot_release_intent(setup):
    r, b, _, _ = setup
    r.tick()
    b.evidence = {"intents": [dict(intent_id="action-1", terminal=True)]}
    assert r.tick()["status"] == "intent_pending"


def test_stale_market_snapshot_rejected(setup):
    r, b, now, _ = setup
    r.snapshot = lambda: {"as_of_ms": (now[0]-121)*1000}
    assert r.tick()["status"] == "blocked"
    assert not b.executed


def test_wait_is_audited(setup):
    r, _, _, _ = setup
    r.propose = wait_proposal
    assert r.tick()["status"] == "wait"
    row = r.conn.execute("SELECT snapshot, context, proposal, outcome FROM llm_scenario_decisions").fetchone()
    assert all(row)


def test_abandoned_zero_fill_does_not_reset_loss_streak(setup):
    r, b, _, _ = setup
    r.tick()
    state = r.state()
    state["breaker"] = dict(consecutive_losses=2, completed_ids=["old1", "old2"])
    r._save(state)
    b.evidence = dict(intents=[dict(terminal(), execution_ids=[], filled_quantity=0)],
        settlement=settlement(execution_ids=[], no_fills_confirmed=True, gross_pnl=0, fees=0, funding_net=0, net_pnl=0))
    r.tick()
    assert r.state()["active"] is None
    assert r.state()["breaker"]["consecutive_losses"] == 2


def test_wait_cancel_is_durable_action(setup):
    r, b, now, _ = setup
    r.tick()
    b.evidence = dict(intents=[terminal()])
    b.ctx["pending_entries"] = [dict(id="pending1", price=100, quantity=1)]
    now[0] += 300
    r.propose = lambda s, c: wait_proposal(s, c, action_id="cancel-1", cancel_entry_ids=["pending1"])
    assert r.tick()["status"] == "intent_pending"
    assert b.executed == ["action-1", "cancel-1"]


def live_evidence():
    return dict(terminal(), terminal=False, exchange_order_ids=["exchange-1"],
                open_entries=[dict(id="e1", price=100, quantity=.5)])


def test_reconciled_live_entry_allows_cancel_without_waiting_for_terminal(setup):
    r, b, now, _ = setup
    r.tick()
    b.evidence = dict(intents=[live_evidence()])
    b.ctx["pending_entries"] = live_evidence()["open_entries"]
    now[0] += 300
    r.propose = lambda s, c: wait_proposal(s, c, action_id="cancel-1", cancel_entry_ids=["e1"])
    assert r.tick()["status"] == "intent_pending"
    assert b.executed == ["action-1", "cancel-1"]
    assert r.state()["active"]["hard_stop"] == 90
    assert r.conn.execute("SELECT status FROM llm_scenario_intents WHERE id='action-1'").fetchone()[0] == "LIVE_RECONCILED"


def test_live_risk_missing_blocks_and_live_cannot_settle(setup):
    r, b, now, _ = setup
    r.tick()
    b.evidence = dict(intents=[live_evidence()], settlement=settlement())
    now[0] += 300
    assert r.tick()["status"] == "blocked"
    assert r.state()["active"] is not None
    assert len(b.executed) == 1


def test_missing_live_evidence_returns_to_unknown_fence(setup):
    r, b, now, _ = setup
    r.tick()
    b.evidence = dict(intents=[live_evidence()])
    b.ctx["pending_entries"] = live_evidence()["open_entries"]
    r.tick()
    b.evidence = {}
    now[0] += 300
    assert r.tick()["status"] == "intent_pending"
    assert len(b.executed) == 1


def test_live_evidence_capture_metadata_does_not_stale_model(setup):
    r, b, now, _ = setup
    r.tick()
    b.ctx["pending_entries"] = live_evidence()["open_entries"]
    def reconcile():
        b.reconciles += 1
        return dict(intents=[dict(live_evidence(), request_id=str(b.reconciles), captured_at=b.reconciles)])
    b.reconcile = reconcile
    now[0] += 300
    r.propose = lambda s, c: wait_proposal(s, c, action_id="wait-2")
    assert r.tick()["status"] == "wait"


@pytest.mark.parametrize("action", ["WAIT", "EXIT"])
def test_wait_exit_cannot_poison_stop_then_widen_adjust(setup, action):
    r, b, now, _ = setup
    r.tick()
    b.evidence = dict(intents=[terminal()])
    now[0] += 300
    r.propose = lambda s, c: dict(proposal(s, c), action=action, action_id="poison",
        entries=[], take_profits=[], hard_stop=50)
    assert r.tick()["status"] == "blocked"
    assert r.state()["active"]["hard_stop"] == 90
    now[0] += 300
    r.propose = lambda s, c: dict(proposal(s, c), action="ADJUST", action_id="widen",
        entries=[], take_profits=[], hard_stop=80)
    assert r.tick()["status"] == "blocked"
    assert r.state()["active"]["hard_stop"] == 90
    assert len(b.executed) == 1


def test_invalid_optional_notice_does_not_rollback_confirmed_settlement(setup):
    r, b, _, _ = setup
    r.tick()
    b.evidence = dict(intents=[terminal()], settlement=settlement(),
                      notices=[dict(event_id="bad", kind="FILLED", timestamp=1)])
    r.tick()
    assert r.state()["active"] is None
    assert r.conn.execute("SELECT count(*) FROM llm_scenario_notice_errors").fetchone()[0] == 1


def test_reconciled_notice_is_queued_without_network(setup):
    r, b, now, _ = setup
    b.evidence = dict(notices=[dict(event_id="pending1", kind="PENDING", timestamp=now[0])])
    r.propose = wait_proposal
    assert r.tick()["status"] == "wait"
    assert r.conn.execute("SELECT status FROM llm_scenario_outbox").fetchone()[0] == "QUEUED"
    assert r.conn.execute("SELECT count(*) FROM llm_scenario_outbox").fetchone()[0] == 1


def test_adjust_stop_is_not_confirmed_from_submission(setup):
    r,b,now,_=setup
    r.tick()
    b.evidence={"intents":[terminal()]}
    b.ctx.update(side="LONG",mark_price=100)
    now[0]+=300
    r.propose=lambda s,c:dict(proposal(s,c),action="ADJUST",action_id="tighten",entries=[],hard_stop=95)
    assert r.tick()["status"]=="intent_pending"
    assert r.state()["active"]["hard_stop"]==90
    assert r.state()["active"]["desired_hard_stop"]==95
    b.evidence.update(scenario_id="scenario-1",protection_confirmed=True,confirmed_hard_stop=95)
    r.tick()
    assert r.state()["active"]["hard_stop"]==95


def test_live_reduce_orders_do_not_fence_llm_management(setup):
    r,b,now,_=setup
    r.tick()
    b.evidence={"intents":[dict(live_evidence(),open_entries=[])]}
    now[0]+=300
    r.propose=lambda s,c:wait_proposal(s,c,action_id="wait-2")
    assert r.tick()["status"]=="wait"


def test_oldest_primary_observation_bounds_final_validation(setup):
    r,b,now,_=setup
    r.snapshot=lambda:{"valid":True,"as_of_ms":now[0]*1000,
        "timeframes":{"30m":{"forming":{"observed_at_ms":(now[0]-100)*1000}}}}
    def model(s,c):
        value=proposal(s,c)
        now[0]+=30
        return value
    r.propose=model
    assert r.tick()["status"]=="blocked"
    assert b.executed==[]


def test_flat_halt_skips_llm_but_still_reconciles(setup):
    r,b,now,_=setup
    b.ctx["daily_net_pnl"]=-500
    r.propose=lambda *args:pytest.fail("halted flat account must not call LLM")
    assert r.tick()=={"status":"blocked","reason":"new_risk_halted"}
    assert b.reconciles>0
    assert r.state()["breaker"]["blocked"]


def test_next_judgment_receives_previous_plan_and_verified_action_history(setup):
    r,b,now,_=setup
    r.tick()
    b.evidence={'intents':[terminal()]}
    now[0]+=300
    def model(s,c):
        assert c['current_plan']['action']=='OPEN'
        assert c['current_plan']['take_profits'][0]['price']==120
        assert c['recent_actions'][-1]['verified_executed_quantity']==1
        return wait_proposal(s,c,action_id='wait-2')
    r.propose=model
    assert r.tick()['status']=='wait'
