from live.scenario_review_memory import apply_review, observe_review
from tests.test_scenario_runtime import setup as setup, proposal, terminal, wait_proposal, settlement


def _active(setup):
    runtime, broker, now, path = setup
    runtime.propose = lambda s, c: dict(proposal(s, c), review={
        "conditions": [condition()], "acknowledgements": []})
    assert runtime.tick()["status"] == "intent_pending"
    broker.evidence = {"intents": [terminal()]}
    broker.ctx["positions"] = [{"quantity": 1, "price": 100}]
    now[0] += 300
    broker.ctx["account_captured_at"] = now[0]
    return runtime, broker, now, path


def test_runtime_wait_persists_hit_before_return_and_restart(setup):
    import sqlite3
    from live.scenario_runtime import ScenarioRuntime
    runtime, broker, now, path = _active(setup)
    broker.ctx["mark_price"] = 106
    def wait(s, c):
        assert c["review_memory"][0]["reached_at"] == now[0]
        return dict(wait_proposal(s, c, action_id="w1"), review={
            "conditions": [condition(110)], "acknowledgements": []})
    runtime.propose = wait
    assert runtime.tick()["status"] == "wait"
    assert len(runtime.state()["active"]["review_memory"]) == 2
    audited = runtime.conn.execute("SELECT proposal FROM llm_scenario_decisions ORDER BY slot DESC").fetchone()[0]
    assert '"review"' in audited
    intent = runtime.conn.execute("SELECT payload FROM llm_scenario_intents").fetchone()[0]
    assert '"review"' not in intent
    runtime.conn.close()
    restored = ScenarioRuntime(sqlite3.connect(path), broker, wait, lambda: {}, clock=lambda: now[0])
    assert restored.state()["active"]["review_memory"][0]["reached_price"] == 106


def test_runtime_stale_and_rejected_metadata_never_adopted(setup):
    runtime, broker, now, _ = _active(setup)
    def stale(s, c):
        broker.ctx["account_version"] = "changed"
        return dict(wait_proposal(s, c, action_id="w1"), review={
            "conditions": [condition(110)], "acknowledgements": []})
    runtime.propose = stale
    assert runtime.tick()["status"] == "stale_proposal"
    assert len(runtime.state()["active"]["review_memory"]) == 1
    now[0] += 300
    runtime.propose = lambda s, c: dict(wait_proposal(s, c, action_id="w2", expires_at=c["now"]-1),
        review={"conditions": [condition(120)], "acknowledgements": []})
    assert runtime.tick()["status"] == "blocked"
    assert len(runtime.state()["active"]["review_memory"]) == 1


def test_reconciled_flat_clears_memory_no_cross_scenario(setup):
    runtime, broker, now, _ = _active(setup)
    broker.evidence = {"intents": [terminal()], "settlement": settlement()}
    broker.ctx["positions"] = []
    runtime.propose = lambda s, c: wait_proposal(s, c, action_id="flat")
    assert runtime.tick()["status"] == "wait"
    assert runtime.state()["active"] is None
    assert runtime._context(runtime.state())["review_memory"] == []


def test_unknown_ack_is_atomic_and_hold_before_hit_needs_new_ack():
    active = {"scenario_id": "s"}
    apply_review(active, {"conditions": [condition()], "acknowledgements": []}, "a", now=100)
    ident = active["review_memory"][0]["id"]
    apply_review(active, {"conditions": [condition(110)], "acknowledgements": [
        {"id": "unknown", "disposition": "replace", "reason": "new structure"}]}, "b")
    assert len(active["review_memory"]) == 1
    assert active["review_status"] == "unknown_ack_id"
    apply_review(active, {"conditions": [], "acknowledgements": [
        {"id": ident, "disposition": "hold", "reason": "thesis intact"}]}, "c")
    assert active["review_memory"][0]["acknowledgement"] is not None
    observe_review(active, {"mark_price": 106, "account_captured_at": 101}, 101)
    assert active["review_memory"][0]["acknowledgement"] is None


def test_bad_review_does_not_block_valid_exit(setup):
    runtime, broker, now, _ = _active(setup)
    runtime.propose = lambda s, c: dict(wait_proposal(s, c, action_id="exit1"), action="EXIT", review={"bad": "metadata"})
    assert runtime.tick()["status"] == "intent_pending"
    assert broker.executed[-1] == "exit1"


def test_nonfinite_review_does_not_block_exit_audit(setup):
    runtime, broker, now, _ = _active(setup)
    runtime.propose = lambda s, c: dict(wait_proposal(s, c, action_id="exit1"), action="EXIT", review={
        "conditions": [condition(float("inf"))], "acknowledgements": []})
    assert runtime.tick()["status"] == "intent_pending"
    assert runtime.state()["active"]["review_status"] == "invalid_metadata"


def test_oversized_invalid_and_corrupt_memory_are_nonblocking():
    from live.scenario_review_memory import valid_review
    assert not valid_review({"conditions": [condition(10**1000)], "acknowledgements": []})
    active = {"scenario_id": "s", "review_memory": [{"broken": True}]}
    observe_review(active, {"mark_price": 106, "account_captured_at": 100}, 100)
    apply_review(active, {"conditions": [condition()], "acknowledgements": []}, "a")
    assert active["review_status"] == "invalid_saved_memory"


def test_acceptance_time_and_unseen_fresh_hit_cannot_be_retired():
    import copy
    active = {"scenario_id": "s"}
    apply_review(active, {"conditions": [condition()], "acknowledgements": []}, "a", now=100)
    observe_review(active, {"mark_price": 106, "account_captured_at": 99}, 100)
    assert active["review_memory"][0]["reached_at"] is None
    presented = copy.deepcopy(active["review_memory"])
    observe_review(active, {"mark_price": 106, "account_captured_at": 101}, 101)
    apply_review(active, {"conditions": [], "acknowledgements": [{"id": presented[0]["id"],
        "disposition": "replace", "reason": "Old level"}]}, "b", now=101, presented=presented)
    assert active["review_status"] == "new_hit_not_presented"
    assert active["review_memory"][0]["reached_at"] == 101


def test_wire_optin_is_backward_compatible_and_ignores_bad_metadata():
    from live.scenario_contract import identity_fields, response_schema, validate_wire_proposal
    ctx = {"input_id": "i", "revision": 0, "scenario_id": "s"}
    value = {**identity_fields(ctx), "action": "EXIT", "side": None, "confidence": .5,
             "expires_at": 1000, "hard_stop": None, "entries": [], "take_profits": [],
             "partial_stops": [], "cancel_entry_ids": [], "chase": None,
             "rationale": "exit", "leverage": 10}
    assert "review" not in response_schema(ctx)["properties"]
    assert "review" not in validate_wire_proposal(value, ctx)
    ctx["review_contract_version"] = 1
    assert "review" in response_schema(ctx)["properties"]
    assert validate_wire_proposal(value, ctx)["review"] is None
    assert validate_wire_proposal(dict(value, review="bad"), ctx)["review"] == "bad"


def condition(price=105, operator="ge"):
    return {"source": "MARK_PRICE", "operator": operator, "price": price}


def test_sticky_hit_and_no_silent_replacement():
    active = {"scenario_id": "s"}
    apply_review(active, {"conditions": [condition()], "acknowledgements": []}, "a")
    observe_review(active, {"mark_price": 106, "account_captured_at": 100}, 100)
    old = active["review_memory"][0].copy()
    observe_review(active, {"mark_price": 99, "account_captured_at": 110}, 110)
    apply_review(active, {"conditions": [condition(110)], "acknowledgements": []}, "b")
    assert active["review_memory"][0] == old
    assert len(active["review_memory"]) == 2


def test_invalid_missing_empty_never_erase_and_stale_never_hits():
    active = {"scenario_id": "s"}
    apply_review(active, {"conditions": [condition()], "acknowledgements": []}, "a")
    for timestamp in (None, -100, 101):
        observe_review(active, {"mark_price": 106, "account_captured_at": timestamp}, 100)
    assert active["review_memory"][0]["reached_at"] is None
    for metadata in (None, {}, {"conditions": [], "acknowledgements": []}):
        apply_review(active, metadata, "b")
    assert len(active["review_memory"]) == 1


def test_explicit_replace_capacity_and_short_threshold():
    active = {"scenario_id": "s"}
    apply_review(active, {"conditions": [condition(90, "le"), condition(110), condition(120)], "acknowledgements": []}, "a")
    observe_review(active, {"mark_price": 89, "account_captured_at": 100}, 100)
    ident = active["review_memory"][0]["id"]
    apply_review(active, {"conditions": [condition(80, "le")], "acknowledgements": []}, "b")
    assert len(active["review_memory"]) == 3
    apply_review(active, {"conditions": [condition(80, "le")], "acknowledgements": [
        {"id": ident, "disposition": "replace", "reason": "Fresh structure invalidates the old level"}]}, "c")
    assert len(active["review_memory"]) == 3
    assert all(row["id"] != ident for row in active["review_memory"])
