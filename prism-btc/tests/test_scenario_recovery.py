"""Recovery authority regressions use no exchange or model network access."""
import json
import sqlite3

import pytest

from live import scenario_recovery as recovery
from live.scenario_runtime import ScenarioRuntime
from tests.test_scenario_runtime import Broker, proposal, wait_proposal, terminal, settlement


@pytest.fixture
def probe(tmp_path):
    now = [1800000000.]
    price = [100.]
    broker = Broker()
    broker.ctx["accounting_status"] = "confirmed"
    conn = sqlite3.connect(tmp_path / "recovery.db")
    snapshot = lambda: {"valid": True, "as_of_ms": now[0] * 1000,
        "timeframes": {"15m": {"closed": {"close": price[0], "ma10": 99.}}}}
    r = ScenarioRuntime(conn, broker, lambda s, c: {}, snapshot,
                        clock=lambda: now[0], recovery_enabled=True)
    state = r.state()
    state["breaker"] = {"blocked": True, "reasons": ["three_losses"],
                        "consecutive_losses": 3, "completed_ids": ["old1", "old2", "old3"]}
    r._save(state)
    def propose(s, c):
        recovery.record_model_request(system_prompt="test", user_prompt=json.dumps(c),
                                      response_schema={})
        first = c.get("recovery", {}).get("first_observation", False)
        p = wait_proposal(s, c) if first else proposal(s, c)
        if not first:
            p["chase"] = {"max_bps": 0, "max_reprices": 0}
        p["recovery"] = {"decision": "OBSERVE" if first else "PROBE",
             "reason": "new structure", "changed_evidence": [] if first else ["timeframes.15m.closed.close"],
             "counterevidence": "reversal remains possible", "invalidation": "price below initial stop"}
        recovery.record_model_wire(json.dumps(p))
        return p
    r.propose = propose
    return r, broker, now, price


def advance(probe):
    r, b, now, price = probe
    assert r.tick()["status"] == "wait"
    now[0] += 300
    price[0] += 1
    return r, b, now, price


def test_first_observation_cannot_grant_and_next_changed_input_can(probe):
    r, b, _, _ = advance(probe)
    assert r.tick()["status"] == "intent_pending"
    state = r.state()
    assert state["breaker"]["blocked"] is True
    assert state["recovery"]["phase"] == "CONSUMED"
    assert state["recovery"]["budget"] == 50
    assert state["active"]["initial_equity"] == 10000
    assert b.executed == ["action-1"]
    events = r.conn.execute("SELECT kind FROM llm_scenario_recovery_events").fetchall()
    assert ("INPUT",) in events and ("MODEL_RAW",) in events and ("PERMIT_CONSUMED",) in events


def test_consumed_and_pending_are_durable_before_broker_submit(probe):
    r, b, _, _ = advance(probe)
    def execute(payload, ident):
        other = sqlite3.connect(r.conn.execute("PRAGMA database_list").fetchone()[2])
        state = json.loads(other.execute("SELECT body FROM llm_scenario_state").fetchone()[0])
        assert state["recovery"]["phase"] == "CONSUMED"
        assert other.execute("SELECT status FROM llm_scenario_intents WHERE id=?", (ident,)).fetchone()[0] == "PENDING"
        assert other.execute("SELECT 1 FROM llm_scenario_recovery_events WHERE kind='PERMIT_CONSUMED'").fetchone()
        other.close()
    b.execute = execute
    assert r.tick()["status"] == "intent_pending"


@pytest.mark.parametrize("field,value", [("new_risk_blocked", True), ("legacy_fenced", True),
                                           ("protection_ok", False), ("accounting_status", "pending")])
def test_hard_guards_cannot_be_overridden(probe, field, value):
    r, b, _, _ = probe
    b.ctx[field] = value
    assert r.tick()["status"] in {"blocked", "fenced"}
    assert not b.executed


def test_daily_latch_cannot_be_overridden(probe):
    r, b, _, _ = probe
    state = r.state()
    state["breaker"]["reasons"].append("daily_loss")
    r._save(state)
    assert r.tick()["status"] == "blocked"
    assert not b.executed


def test_time_change_alone_cannot_grant(probe):
    r, b, now, _ = probe
    assert r.tick()["status"] == "wait"
    now[0] += 300
    assert r.tick()["status"] == "blocked"
    assert not b.executed


def test_forged_evidence_path_cannot_grant(probe):
    r, b, _, _ = advance(probe)
    old = r.propose
    def forged(s, c):
        p = old(s, c)
        p["recovery"]["changed_evidence"] = ["as_of_ms"]
        return p
    r.propose = forged
    assert r.tick()["status"] == "blocked"
    assert not b.executed


def test_missing_raw_journal_cannot_grant(probe, monkeypatch):
    r, b, _, _ = advance(probe)
    monkeypatch.setattr(recovery, "record_model_wire", lambda *a, **kw: None)
    assert r.tick()["status"] == "blocked"
    assert not b.executed


def test_missing_actual_request_cannot_grant(probe, monkeypatch):
    r, b, _, _ = advance(probe)
    monkeypatch.setattr(recovery, "record_model_request", lambda *a, **kw: None)
    assert r.tick()["failure_code"] == "recovery_missing_request"
    assert not b.executed


@pytest.mark.parametrize("change,code", [
    ({"expires_at": 1800001000}, "recovery_entry_deadline_invalid"),
    ({"chase": {"max_bps": 1, "max_reprices": 1}}, "recovery_chase_forbidden")])
def test_probe_contract_denials_are_durable(probe, change, code):
    r, b, now, _ = advance(probe)
    old = r.propose
    r.propose = lambda s, c: dict(old(s, c), **change)
    assert r.tick()["failure_code"] == code
    slot = int(now[0] // 300)
    assert r.conn.execute("SELECT 1 FROM llm_scenario_recovery_events WHERE slot=? AND kind='DECISION'", (slot,)).fetchone()
    outcome = json.loads(r.conn.execute("SELECT outcome FROM llm_scenario_decisions WHERE slot=?", (slot,)).fetchone()[0])
    assert outcome["failure_code"] == code
    assert not b.executed


def test_probe_cannot_spend_normal_two_percent_budget(probe):
    r, b, _, _ = advance(probe)
    old = r.propose
    def excessive(s, c):
        value = old(s, c)
        value["entries"][0]["quantity"] = 10
        return value
    r.propose = excessive
    assert r.tick()["status"] == "blocked"
    assert not b.executed
    assert r.state()["recovery"]["phase"] == "OBSERVING"


def test_missing_input_journal_cannot_call_model(probe, monkeypatch):
    r, b, _, _ = probe
    monkeypatch.setattr(recovery, "journal", lambda *a, **kw: (_ for _ in ()).throw(sqlite3.OperationalError()))
    r.propose = lambda *a: pytest.fail("model must not run")
    assert r.tick()["status"] == "blocked"
    assert not b.executed


@pytest.mark.parametrize("net", [-12, 12, 0])
def test_completed_probe_requires_review_even_win_or_breakeven(probe, net):
    r, b, now, _ = advance(probe)
    assert r.tick()["status"] == "intent_pending"
    b.evidence = {"intents": [terminal()], "settlement": settlement(
        gross_pnl=net+2, net_pnl=net)}
    now[0] += 300
    assert r.tick()["status"] == "blocked"
    state = r.state()
    assert state["recovery"]["phase"] == "DONE_REVIEW_REQUIRED"
    assert state["breaker"]["blocked"] is True
    assert state["breaker"]["consecutive_losses"] == (4 if net < 0 else 3)
    assert "scenario-1" in state["breaker"]["completed_ids"]
    assert len(b.executed) == 1


def test_zero_fill_attempt_is_also_consumed(probe):
    r, b, now, _ = advance(probe)
    assert r.tick()["status"] == "intent_pending"
    term = dict(terminal(), execution_ids=[], filled_quantity=0)
    b.evidence = {"intents": [term], "settlement": settlement(gross_pnl=0, fees=0,
        funding_net=0, net_pnl=0, execution_ids=[], no_fills_confirmed=True)}
    now[0] += 300
    assert r.tick()["status"] == "blocked"
    assert r.state()["recovery"]["phase"] == "DONE_REVIEW_REQUIRED"
    assert r.state()["breaker"]["consecutive_losses"] == 3


def test_unknown_submit_never_rearms_after_restart(probe):
    r, b, now, _ = advance(probe)
    b.fail = True
    assert r.tick()["reason"] == "submission_unknown"
    path = r.conn.execute("PRAGMA database_list").fetchone()[2]
    r.conn.close()
    now[0] += 300
    new = ScenarioRuntime(sqlite3.connect(path), b, r.propose, r.snapshot,
                          clock=lambda: now[0], recovery_enabled=True)
    assert new.tick()["status"] == "intent_pending"
    assert new.state()["recovery"]["phase"] == "CONSUMED"
    assert len(b.executed) == 1


def test_journal_failure_does_not_break_existing_protection(probe, monkeypatch):
    r, b, now, _ = advance(probe)
    assert r.tick()["status"] == "intent_pending"
    calls = b.reconciles
    monkeypatch.setattr(recovery, "journal", lambda *a, **kw: (_ for _ in ()).throw(sqlite3.OperationalError()))
    now[0] += 300
    r.tick()
    assert b.reconciles > calls


def test_risk_fraction_survives_expiry_and_settlement(probe):
    r, _, now, _ = advance(probe)
    r.tick()
    state = r.state()
    now[0] += 3600
    assert not recovery.authorize_pending(state, state["active"], now[0])
    assert recovery.risk_fraction(state, "scenario-1") == .005
    assert recovery.risk_fraction(state, "other") == .02


def test_active_probe_context_exposes_consumed_contract(probe):
    r, _, _, _ = advance(probe)
    r.tick()
    ctx = r._context(r.state())
    assert ctx["recovery_contract_version"] == 1
    assert ctx["recovery"]["phase"] == "CONSUMED"
    assert ctx["recovery"]["eligible"] is False
    assert ctx["scenario_risk_fraction"] == .005


def test_authority_rejects_duplicate_or_removed_conditional_fields(probe):
    r, b, now, _ = advance(probe)
    r.tick()
    state = r.state()
    p = state["active"]["current_plan"]
    assert recovery.authorize_entry(state, p, b.ctx, now[0])
    assert not recovery.authorize_entry(state, dict(p, entries=p["entries"] * 2), b.ctx, now[0])
    state["recovery"]["initial_entries"][0]["trigger_price"] = 100
    assert not recovery.authorize_entry(state, p, b.ctx, now[0])


def test_historical_summary_is_accounting_not_cause(probe):
    r, _, _, _ = probe
    ev = settlement(scenario_id="old3")
    r.conn.execute("INSERT INTO llm_scenario_settlements VALUES(?,?)", ("old3", json.dumps(ev)))
    rows = recovery.historical_outcomes(r.conn, r.state())
    assert [row["status"] for row in rows] == ["UNKNOWN", "UNKNOWN", "CONFIRMED"]
    assert rows[2]["kind"] == "ACCOUNTING_ONLY_NOT_CAUSE"
    assert rows[2]["net_pnl"] == -12
    assert rows[2]["evidence_hash"] == recovery.digest(ev)


def test_huge_number_is_not_finite():
    assert recovery._number(10 ** 1000) is False


def test_mixed_loaded_source_denies_probe_but_keeps_observing(probe, monkeypatch):
    r, b, _, _ = advance(probe)
    monkeypatch.setattr(recovery, "versions", lambda: {"loaded_manifest": {"loaded_code_status": "MIXED"}})
    assert r.tick()["failure_code"] == "recovery_mixed_loaded_code"
    assert not b.executed


def test_baseline_and_full_input_are_one_transaction(probe):
    r, b, now, _ = probe
    r.conn.execute("""CREATE TRIGGER reject_snapshot BEFORE UPDATE OF snapshot
        ON llm_scenario_decisions BEGIN SELECT RAISE(ABORT, 'disk_failure'); END""")
    r.conn.commit()
    assert r.tick()["status"] == "blocked"
    assert "recovery" not in r.state()
    assert not r.conn.execute("SELECT 1 FROM llm_scenario_recovery_events WHERE kind='INPUT'").fetchone()
    row = r.conn.execute("SELECT snapshot,context FROM llm_scenario_decisions WHERE slot=?", (int(now[0] // 300),)).fetchone()
    assert row == (None, None)
    assert not b.executed


def test_recheck_records_account_change_before_stale_denial(probe):
    r, b, now, _ = advance(probe)
    previous = r.propose
    def change_account(snapshot, context):
        payload = previous(snapshot, context)
        b.ctx["account_version"] = "changed-after-call"
        return payload
    r.propose = change_account
    assert r.tick()["status"] == "stale_proposal"
    body = json.loads(r.conn.execute("SELECT body FROM llm_scenario_recovery_events WHERE slot=? AND kind='RECHECK'", (int(now[0] // 300),)).fetchone()[0])
    assert body["input_account_version"] == "1"
    assert body["context"]["account_version"] == "changed-after-call"
    assert body["input_id"]
    assert not b.executed


def test_missing_recheck_record_blocks_probe(probe, monkeypatch):
    r, b, _, _ = advance(probe)
    previous = recovery.journal
    def fail_recheck(conn, kind, *args, **kwargs):
        if kind == "RECHECK":
            raise sqlite3.OperationalError("disk_failure")
        return previous(conn, kind, *args, **kwargs)
    monkeypatch.setattr(recovery, "journal", fail_recheck)
    assert r.tick()["failure_code"] == "recovery_recheck_log_unavailable"
    assert not b.executed
    assert r.state()["recovery"]["phase"] == "OBSERVING"


def test_missing_recheck_does_not_block_active_exit(probe, monkeypatch):
    r, b, now, _ = advance(probe)
    assert r.tick()["status"] == "intent_pending"
    b.evidence = {"intents": [terminal()]}
    b.ctx["positions"] = [{"price": 100, "quantity": 1}]
    r.propose = lambda s, c: dict(wait_proposal(s, c), action="EXIT", action_id="probe-exit")
    previous = recovery.journal
    def fail_recheck(conn, kind, *args, **kwargs):
        if kind == "RECHECK":
            raise sqlite3.OperationalError("disk_failure")
        return previous(conn, kind, *args, **kwargs)
    monkeypatch.setattr(recovery, "journal", fail_recheck)
    now[0] += 300
    assert r.tick()["status"] == "intent_pending"
    assert b.executed == ["action-1", "probe-exit"]
