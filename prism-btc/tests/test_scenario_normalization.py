"""Version-two automatic recovery uses isolated runtime, model and exchange fakes."""
import json
import sqlite3

import pytest

from live import scenario_recovery as recovery
from live.scenario_runtime import ScenarioRuntime
from tests.test_scenario_recovery import probe  # noqa: F401
from tests.test_scenario_runtime import proposal, wait_proposal, terminal, settlement


@pytest.fixture
def automatic(probe):  # noqa: F811 - pytest's imported fixture injection
    r, b, now, price = probe
    r.automatic_normalization_enabled = True
    def model(s, c):
        recovery.record_model_request(system_prompt="test", user_prompt=json.dumps(c), response_schema={})
        first = c.get("recovery", {}).get("first_observation", False)
        p = wait_proposal(s, c) if first else proposal(s, c)
        p.update(action_id="a" + str(int(now[0])), scenario_id=c["scenario_id"] or "s" + str(int(now[0])))
        if not first:
            p["chase"] = {"max_bps": 0, "max_reprices": 0}
        p["recovery"] = {"decision": "OBSERVE" if first else "PROBE", "reason": "test",
            "changed_evidence": [] if first else ["timeframes.15m.closed.close"],
            "counterevidence": "test", "invalidation": "test"}
        recovery.record_model_wire(json.dumps(p))
        return p
    r.propose = model
    return r, b, now, price


def open_probe(fixture, first=True):
    r, _, now, price = fixture
    if first:
        assert r.tick()["status"] == "wait"
    now[0] += 300
    price[0] += 1
    assert r.tick()["status"] == "intent_pending"
    return r.state()["recovery"]


def finish(fixture, net=10, nofill=False):
    r, b, now, _ = fixture
    p = r.state()["recovery"]
    t = dict(terminal(), intent_id=p["action_id"])
    s = settlement(scenario_id=p["scenario_id"], gross_pnl=net+2, net_pnl=net)
    if nofill:
        t.update(execution_ids=[], filled_quantity=0)
        s.update(gross_pnl=0, fees=0, funding_net=0, net_pnl=0, execution_ids=[], no_fills_confirmed=True)
    b.evidence = {"intents": [t], "settlement": s}
    now[0] += 300
    assert r.tick()["status"] == "wait"  # New cycle's baseline only.
    return s


def test_repeated_cycles_promote_each_level_only_on_fresh_accepted_open(automatic):
    r, b, _, _ = automatic
    seen = set()
    for i in range(7):
        p = open_probe(automatic, first=i == 0)
        assert p["stage"] == min(i // 3, 2)
        assert p["risk_fraction"] == recovery.STAGE_FRACTIONS[p["stage"]]
        assert p["budget"] == 10000 * p["risk_fraction"]
        assert p["permit_id"] not in seen
        seen.add(p["permit_id"])
        finish(automatic)
        assert r.state()["breaker"]["blocked"] is True
        assert r.state()["breaker"]["consecutive_losses"] == 0
    assert len(b.executed) == 7
    assert r.state()["recovery"]["stage"] == 2
    events = [x[0] for x in r.conn.execute("SELECT kind FROM llm_scenario_recovery_events")]
    assert events.count("MIGRATION") == 1
    assert events.count("STAGE_TRANSITION") == 2


def test_loss_demotes_and_resets_but_does_not_require_operator(automatic):
    r, b, _, _ = automatic
    for i in range(4):
        open_probe(automatic, first=i == 0)
        finish(automatic, net=-10 if i == 3 else 10)
    rec = r.state()["recovery"]
    assert rec["stage"] == 0 and rec["stage_evidence"]["filled_count"] == 0
    assert rec["phase"] == "OBSERVING"
    assert rec["stage_evidence"]["stage_start_equity"] is None
    b.ctx["initial_equity"] = 9876
    permit = open_probe(automatic, first=False)
    assert permit["risk_fraction"] == .005
    assert permit["stage_evidence"]["stage_start_equity"] == 9876


def test_nofill_has_no_performance_credit_and_new_baseline_required(automatic):
    r, _, now, _ = automatic
    open_probe(automatic)
    finish(automatic, nofill=True)
    assert r.state()["recovery"]["stage_evidence"]["filled_count"] == 0
    assert r.tick()["status"] == "duplicate_slot"
    now[0] += 300
    assert r.tick()["failure_code"] == "recovery_no_changed_evidence"


def test_settlement_is_idempotent_and_unknown_does_not_rearm(automatic):
    r, b, now, _ = automatic
    p = open_probe(automatic)
    now[0] += 300
    assert r.tick()["status"] == "intent_pending"
    assert r.state()["recovery"]["permit_id"] == p["permit_id"]
    s = finish(automatic)
    state = r.state()
    before = json.dumps(state, sort_keys=True)
    recovery.settled(r.conn, state, s, now[0])
    assert json.dumps(state, sort_keys=True) == before
    assert len(b.executed) == 1


@pytest.mark.parametrize("bad", ["missing", "foreign", "unconfirmed"])
def test_legacy_done_migration_requires_exact_confirmed_settlement(automatic, bad):
    r, _, _, _ = automatic
    state = r.state()
    state["recovery"] = {"phase": "DONE_REVIEW_REQUIRED", "scenario_id": "old", "permit_id": "permit"}
    if bad != "missing":
        s = settlement(scenario_id="foreign" if bad == "foreign" else "old", fees_complete=bad != "unconfirmed")
        r.conn.execute("INSERT INTO llm_scenario_settlements VALUES(?,?)", ("old", json.dumps(s)))
    r._save(state)
    assert r.tick()["status"] == "blocked"
    assert r.state()["recovery"].get("policy_version") is None


def test_legacy_done_migrates_once_with_references_and_restart(automatic):
    r, b, now, _ = automatic
    state = r.state()
    state["recovery"] = {"phase": "DONE_REVIEW_REQUIRED", "scenario_id": "old", "permit_id": "permit"}
    r.conn.execute("INSERT INTO llm_scenario_settlements VALUES(?,?)", ("old", json.dumps(settlement(scenario_id="old"))))
    r._save(state)
    assert r.tick()["status"] == "wait"
    rec = r.state()["recovery"]
    assert rec["previous_permit_id"] == "permit" and rec["epoch_start_equity"] == 10000
    path = r.conn.execute("PRAGMA database_list").fetchone()[2]
    other = ScenarioRuntime(sqlite3.connect(path), b, r.propose, r.snapshot, clock=lambda: now[0],
                            automatic_normalization_enabled=True)
    assert other.tick()["status"] == "duplicate_slot"
    assert other.state()["recovery"]["epoch_id"] == rec["epoch_id"]
    assert other.conn.execute("SELECT COUNT(*) FROM llm_scenario_recovery_events WHERE kind='MIGRATION'").fetchone()[0] == 1


@pytest.mark.parametrize("field,value", [("new_risk_blocked", True), ("legacy_fenced", True),
    ("protection_ok", False), ("accounting_status", "pending")])
def test_hard_guards_block_automatic_migration(automatic, field, value):
    r, b, _, _ = automatic
    b.ctx[field] = value
    assert r.tick()["status"] in {"blocked", "fenced"}
    assert not b.executed and r.state().get("recovery", {}).get("policy_version") is None


def test_stage_offer_does_not_change_active_permit_cap(automatic):
    r, _, _, _ = automatic
    open_probe(automatic)
    state = r.state()
    state["recovery"]["stage_evidence"].update(filled_count=3, net_pnl=100, peak_net_pnl=100)
    assert recovery.offered_fraction(state) == .01
    assert recovery.risk_fraction(state, state["active"]["scenario_id"]) == .005
    state["recovery"]["risk_fraction"] = .03
    assert recovery.managed_permit(state, state["active"]) is None
    assert recovery.risk_fraction(state, state["active"]["scenario_id"]) == .005


@pytest.mark.parametrize("phase", ["CONSUMED", "DONE_REVIEW_REQUIRED"])
def test_legacy_valuation_never_trusts_injected_fraction(phase):
    state = {"recovery": {"scenario_id": "legacy", "phase": phase,
                          "risk_fraction": .02}}
    assert recovery.risk_fraction(state, "legacy") == .005


def test_daily_stop_overrides_all_stages(automatic):
    r, b, now, _ = automatic
    open_probe(automatic)
    finish(automatic)
    b.ctx["daily_net_pnl"] = -400
    now[0] += 300
    assert r.tick()["status"] == "blocked"
    assert "daily_loss" in r.state()["breaker"]["reasons"]


def test_migration_event_failure_keeps_legacy_state(automatic, monkeypatch):
    r, b, _, _ = automatic
    before = r.state()
    def fail(*args, **kwargs):
        raise sqlite3.OperationalError("audit unavailable")
    monkeypatch.setattr(recovery, "journal", fail)
    assert r.tick()["status"] == "blocked"
    assert r.state().get("recovery") == before.get("recovery")
    assert not b.executed


def test_active_legacy_finishes_at_old_cap_before_migration(probe):  # noqa: F811
    r, b, now, price = probe
    assert r.tick()["status"] == "wait"
    now[0] += 300
    price[0] += 1
    assert r.tick()["status"] == "intent_pending"
    r.automatic_normalization_enabled = True
    state = r.state()
    r._context(state)
    assert state["recovery"].get("policy_version") is None
    assert recovery.risk_fraction(state, "scenario-1") == .005
    b.evidence = {"intents": [terminal()], "settlement": settlement()}
    old_model = r.propose
    r.propose = lambda s, c: dict(old_model(s, c), action_id="fresh-baseline")
    now[0] += 300
    assert r.tick()["status"] == "wait"
    assert r.state()["recovery"]["policy_version"] == 2
    assert r.state()["recovery"]["stage"] == 0


@pytest.mark.parametrize("change", [
    {"stage": -1}, {"stage": True}, {"stage": 3},
    {"filled_count": -1}, {"filled_count": True}, {"filled_count": 1.5},
    {"net_pnl": -1}, {"peak_net_pnl": -1}, {"max_realized_drawdown": -1},
    {"net_pnl": 10}, {"stage_start_equity": 0}, {"stage_start_equity": True},
    {"filled_count": 3, "stage_start_equity": None}])
def test_invalid_observing_state_cannot_offer_more_risk(automatic, change):
    r, b, now, _ = automatic
    assert r.tick()["status"] == "wait"
    state = r.state()
    if "stage" in change:
        state["recovery"].update(change)
    else:
        state["recovery"]["stage_evidence"].update(change)
    r._save(state)
    now[0] += 300
    assert r.tick()["failure_code"] == "normalization_state_invalid"
    assert not b.executed


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -float("inf")])
def test_nonfinite_performance_cannot_offer_stage(value):
    rec = {"policy_version": 2, "stage": 0, "stage_evidence": {
        "filled_count": 3, "net_pnl": value, "peak_net_pnl": value,
        "max_realized_drawdown": 0., "stage_start_equity": 10000}}
    with pytest.raises(ValueError, match="normalization_state_invalid"):
        recovery.offered_stage(rec)


def test_offered_promotion_wait_does_not_change_committed_stage(automatic):
    r, _, now, price = automatic
    for i in range(3):
        open_probe(automatic, first=i == 0)
        finish(automatic)
    assert r.state()["recovery"]["stage"] == 0
    def wait(s, c):
        assert c["recovery"]["offered_stage"] == 1
        assert c["scenario_risk_fraction"] == .01
        recovery.record_model_request(system_prompt="test", user_prompt=json.dumps(c), response_schema={})
        p = wait_proposal(s, c)
        p["action_id"] = "wait-promotion"
        p["recovery"] = {"decision": "OBSERVE", "reason": "not yet", "changed_evidence": [],
                         "counterevidence": "test", "invalidation": "test"}
        recovery.record_model_wire(json.dumps(p))
        return p
    r.propose = wait
    now[0] += 300
    price[0] += 1
    assert r.tick()["status"] == "wait"
    assert r.state()["recovery"]["stage"] == 0


def test_normal_loss_demotes_one_level_with_fresh_baseline(automatic):
    r, _, _, _ = automatic
    for i in range(7):
        permit = open_probe(automatic, first=i == 0)
        finish(automatic, net=-10 if i == 6 else 10)
    assert permit["stage"] == 2
    state = r.state()
    assert state["recovery"]["stage"] == 1
    assert state["recovery"]["stage_evidence"]["filled_count"] == 0
    assert state["recovery"]["baseline_slot"] == int(automatic[2][0] // 300)
    assert open_probe(automatic, first=False)["risk_fraction"] == .01


@pytest.mark.parametrize("failed_kind", ["REARM", "STAGE_TRANSITION"])
def test_settlement_journal_failure_rolls_back_and_replays_exactly_once(automatic, monkeypatch, failed_kind):
    r, b, now, _ = automatic
    permit = open_probe(automatic)
    before = r.state()
    net = -10 if failed_kind == "STAGE_TRANSITION" else 10
    s = settlement(scenario_id=permit["scenario_id"], gross_pnl=net+2, net_pnl=net)
    b.evidence = {"intents": [dict(terminal(), intent_id=permit["action_id"])], "settlement": s}
    original = recovery.journal
    def fail(conn, kind, *args, **kwargs):
        if kind == failed_kind:
            raise sqlite3.OperationalError("audit unavailable")
        return original(conn, kind, *args, **kwargs)
    monkeypatch.setattr(recovery, "journal", fail)
    now[0] += 300
    assert r.tick()["status"] == "blocked"
    assert r.state() == before
    assert not r.conn.execute("SELECT 1 FROM llm_scenario_settlements").fetchone()
    assert r.conn.execute("SELECT status FROM llm_scenario_intents WHERE id=?", (permit["action_id"],)).fetchone()[0] == "PENDING"
    monkeypatch.setattr(recovery, "journal", original)
    assert r.tick()["status"] == "wait"
    state = r.state()
    assert state["breaker"]["completed_ids"].count(permit["scenario_id"]) == 1
    assert state["recovery"]["stage_evidence"]["filled_count"] == (0 if net < 0 else 1)
    assert r.conn.execute("SELECT COUNT(*) FROM llm_scenario_recovery_events WHERE kind='SETTLEMENT'").fetchone()[0] == 1
    assert r.tick()["status"] == "duplicate_slot"


def test_delayed_proposal_state_change_cannot_consume_permit(automatic):
    r, b, now, price = automatic
    assert r.tick()["status"] == "wait"
    original = r.propose
    def change_state(s, c):
        p = original(s, c)
        state = r.state()
        state["version"] += 1
        r._save(state)
        return p
    r.propose = change_state
    now[0] += 300
    price[0] += 1
    assert r.tick()["status"] == "stale_proposal"
    assert r.state()["recovery"]["phase"] == "OBSERVING"
    assert not b.executed
    assert not r.conn.execute("SELECT 1 FROM llm_scenario_recovery_events WHERE kind='PERMIT_CONSUMED'").fetchone()


def test_normal_original_open_chase_allowed_but_second_open_denied(automatic):
    r, b, now, _ = automatic
    permit = open_probe(automatic)
    state = r.state()
    # Isolated permission boundary fixture; real promotion is tested separately.
    state["recovery"].update(stage=2, risk_fraction=.02, budget=200)
    payload = json.loads(r.conn.execute("SELECT payload FROM llm_scenario_intents WHERE id=?",
                                       (permit["action_id"],)).fetchone()[0])
    context = dict(b.ctx, recovery_hard_blocked=False)
    assert recovery.authorize_entry(state, payload, context, now[0])
    payload["entries"][0]["price"] += .1
    assert recovery.authorize_entry(state, payload, context, now[0])
    payload["action_id"] = "forged-second-open"
    assert not recovery.authorize_entry(state, payload, context, now[0])
    payload["action"] = "ADJUST"
    assert recovery.authorize_entry(state, payload, context, now[0])
