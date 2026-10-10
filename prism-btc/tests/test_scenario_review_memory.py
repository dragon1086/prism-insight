from live.scenario_review_memory import apply_review, observe_review, review_context, RECEIPT_SCOPE
from tests.test_scenario_runtime import setup as setup, proposal, terminal, wait_proposal, settlement


def test_receipt_version_requires_exact_integer():
    for version in (True, 1.0, "1", None):
        active = {"scenario_id": "s", "review_memory": []}
        apply_review(active, {"conditions": [], "acknowledgements": []}, "a", now=1)
        active["review_update_receipt"]["version"] = version
        assert review_context(active)["review_update_receipt"] is None


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
    outcome = runtime.tick()
    assert outcome["status"] == "wait"
    assert outcome["review_update_receipt"]["conditions"][0]["status"] == "ADDED"
    import json
    saved_outcome = runtime.conn.execute("SELECT outcome FROM llm_scenario_decisions ORDER BY slot DESC").fetchone()[0]
    assert json.loads(saved_outcome) == outcome
    assert len(runtime.state()["active"]["review_memory"]) == 2
    audited = runtime.conn.execute("SELECT proposal FROM llm_scenario_decisions ORDER BY slot DESC").fetchone()[0]
    assert '"review"' in audited
    intent = runtime.conn.execute("SELECT payload FROM llm_scenario_intents").fetchone()[0]
    assert '"review"' not in intent
    runtime.conn.close()
    restored = ScenarioRuntime(sqlite3.connect(path), broker, wait, lambda: {}, clock=lambda: now[0])
    assert restored.state()["active"]["review_memory"][0]["reached_price"] == 106
    ctx = restored._context(restored.state())
    assert ctx["review_update_receipt"] == outcome["review_update_receipt"]
    assert ctx["review_free_capacity"] == 1


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
    outcome = runtime.tick()
    assert outcome["status"] == "intent_pending"
    assert outcome["review_update_receipt"]["status"] == "invalid_metadata"
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


def test_1620_capacity_rejection_is_explicit_without_replacing_old_levels():
    # Price/ACK shape retained from the 2026-10-10 16:20 decision, not a fill fixture.
    active = {"scenario_id": "s_898d1750f57314b8d7fcada9"}
    apply_review(active, {"conditions": [condition(p, "le") for p in (82620, 82640, 82660)],
                          "acknowledgements": []}, "earlier")
    ids = [r["id"] for r in active["review_memory"]]
    receipt = apply_review(active, {"conditions": [condition(82595, "le")],
        "acknowledgements": [{"id": ids[0], "disposition": "hold",
                              "reason": "Keep SL; reassess below 82595"}]}, "a_e02d8810354aaa0ca150e590")
    assert receipt["conditions"] == [{**condition(82595, "le"), "status": "NOT_ADDED_CAPACITY"}]
    assert receipt["acknowledgements"] == [{"id": ids[0], "disposition": "hold", "status": "HELD"}]
    assert receipt["free_capacity"] == 0
    assert receipt["retained_ids"] == ids
    assert [r["price"] for r in active["review_memory"]] == [82620, 82640, 82660]


def test_partial_acceptance_and_duplicate_after_capacity_are_reported():
    active = {"scenario_id": "s"}
    apply_review(active, {"conditions": [condition(90, "le"), condition(110)], "acknowledgements": []}, "a")
    ident = active["review_memory"][0]["id"]
    receipt = apply_review(active, {"conditions": [condition(120), condition(130), condition(90, "le")],
                                   "acknowledgements": []}, "b")
    assert [r["status"] for r in receipt["conditions"]] == ["ADDED", "NOT_ADDED_CAPACITY", "ALREADY_RETAINED"]
    assert receipt["conditions"][2]["id"] == ident
    assert receipt["status"] == "capacity_retained"
    assert len(active["review_memory"]) == 3
    assert receipt["scope"] == RECEIPT_SCOPE


def test_exact_replacement_receipt_enables_new_condition():
    import copy
    active = {"scenario_id": "s"}
    apply_review(active, {"conditions": [condition(p, "le") for p in (82620, 82640, 82660)],
                          "acknowledgements": []}, "a")
    presented = copy.deepcopy(active["review_memory"])
    ident = presented[0]["id"]
    receipt = apply_review(active, {"conditions": [condition(82595, "le")],
        "acknowledgements": [{"id": ident, "disposition": "replace", "reason": "Updated thesis"}]},
        "b", presented=presented)
    assert receipt["acknowledgements"] == [{"id": ident, "disposition": "replace", "status": "REPLACED"}]
    assert receipt["conditions"][0]["status"] == "ADDED"
    assert ident not in receipt["retained_ids"]
    assert receipt["conditions"][0]["id"] in receipt["retained_ids"]
    assert [r["price"] for r in active["review_memory"]] == [82640, 82660, 82595]


def test_ack_failure_receipts_are_atomic_including_late_unknown_and_fresh_hit():
    import copy
    for failure in ("unknown_ack_id", "new_hit_not_presented"):
        active = {"scenario_id": "s"}
        apply_review(active, {"conditions": [condition(), condition(110)], "acknowledgements": []}, "a", now=100)
        presented = copy.deepcopy(active["review_memory"])
        if failure == "new_hit_not_presented":
            observe_review(active, {"mark_price": 111, "account_captured_at": 101}, 101)
            # First ACK unchanged; second ACK sees a hit that was not in model input.
            presented[0] = copy.deepcopy(active["review_memory"][0])
        before = copy.deepcopy(active["review_memory"])
        receipt = apply_review(active, {"conditions": [condition(120)], "acknowledgements": [
            {"id": before[0]["id"], "disposition": "replace", "reason": "first"},
            {"id": "unknown" if failure == "unknown_ack_id" else before[1]["id"],
             "disposition": "replace", "reason": "second"}]}, "b", now=101, presented=presented)
        assert receipt["status"] == failure
        assert all(r["status"] == "NOT_APPLIED" for r in receipt["conditions"] + receipt["acknowledgements"])
        assert active["review_memory"] == before


def test_receipt_legacy_invalid_state_privacy_and_bounded_history():
    import json
    import copy
    active = {"scenario_id": "s"}
    assert review_context(active) == {"review_free_capacity": 3, "review_update_receipt": None}
    apply_review(active, {"conditions": [condition()], "acknowledgements": []}, "a")
    old = copy.deepcopy(active["review_memory"])
    for index in range(100):
        receipt = apply_review(active, {"conditions": [condition()], "acknowledgements": [
            {"id": old[0]["id"], "disposition": "hold", "reason": "PRIVATE_REASON" * 15}]}, str(index))
        assert "PRIVATE_REASON" not in json.dumps(receipt)
        assert len(json.dumps(receipt)) < 2000
    assert active["review_update_receipt"]["action_id"] == "99"
    returned = review_context(active)
    returned["review_update_receipt"]["conditions"].clear()
    assert len(active["review_update_receipt"]["conditions"]) == 1
    for metadata, status in ((None, "missing_metadata"), ({"secret": "PRIVATE"}, "invalid_metadata")):
        receipt = apply_review(active, metadata, "b")
        assert receipt["status"] == status
        assert receipt["conditions"] == receipt["acknowledgements"] == []
        assert "PRIVATE" not in json.dumps(receipt)
    for corrupt in (None, [{"broken": "PRIVATE"}], [{**old[0], "id": "x" * 10000}]):
        active["review_memory"] = corrupt
        receipt = apply_review(active, {"conditions": [], "acknowledgements": []}, "c")
        assert receipt["status"] == "invalid_saved_memory"
        assert receipt["free_capacity"] is None
        assert receipt["retained_ids"] == []
    for corrupt_receipt in ({"secret": "PRIVATE"}, {**receipt, "status": []}, {**receipt, "conditions": [None]}):
        active["review_update_receipt"] = corrupt_receipt
        assert review_context(active)["review_update_receipt"] is None


def test_runtime_capacity_receipt_survives_restart_and_noop_replaces_only_latest(setup):
    import sqlite3
    import json
    from live.scenario_runtime import ScenarioRuntime
    runtime, broker, now, path = _active(setup)
    def wait(s, c):
        return dict(wait_proposal(s, c, action_id="fill_capacity"), review={
            "conditions": [condition(110), condition(120)], "acknowledgements": []})
    runtime.propose = wait
    assert runtime.tick()["review_update_receipt"]["free_capacity"] == 0
    now[0] += 300
    broker.ctx["account_captured_at"] = now[0]
    runtime.propose = lambda s, c: dict(wait_proposal(s, c, action_id="rejected_condition"), review={
        "conditions": [condition(130)], "acknowledgements": []})
    outcome = runtime.tick()
    assert outcome["review_update_receipt"]["conditions"][0]["status"] == "NOT_ADDED_CAPACITY"
    runtime.conn.close()
    restored = ScenarioRuntime(sqlite3.connect(path), broker, lambda s, c: wait_proposal(s, c, action_id="noop"),
                               lambda: {"valid": True, "as_of_ms": now[0]*1000}, clock=lambda: now[0])
    assert restored._context(restored.state())["review_update_receipt"] == outcome["review_update_receipt"]
    now[0] += 300
    broker.ctx["account_captured_at"] = now[0]
    assert restored.tick()["review_update_receipt"]["status"] == "missing_metadata"
    assert len(restored.state()["active"]["review_memory"]) == 3
    outcomes = [json.loads(r[0]) for r in restored.conn.execute("SELECT outcome FROM llm_scenario_decisions")]
    assert outcome in outcomes  # Earlier rejection remains in immutable decision history.


def test_stale_core_proposal_does_not_create_new_receipt(setup):
    import copy
    runtime, broker, now, _ = _active(setup)
    previous = copy.deepcopy(runtime.state()["active"]["review_update_receipt"])
    def stale(s, c):
        broker.ctx["account_version"] = "changed"
        return dict(wait_proposal(s, c, action_id="bad"), review={"conditions": [condition(120)], "acknowledgements": []})
    runtime.propose = stale
    outcome = runtime.tick()
    assert outcome["status"] == "stale_proposal"
    assert "review_update_receipt" not in outcome
    assert runtime.state()["active"]["review_update_receipt"] == previous


def test_invalid_metadata_does_not_block_protective_adjust_or_enter_order_payload(setup):
    import json
    runtime, broker, now, _ = _active(setup)
    runtime.propose = lambda s, c: dict(proposal(s, c), action="ADJUST", action_id="protective",
        entries=[], hard_stop=95, review={"bad": "metadata"})
    outcome = runtime.tick()
    assert outcome["status"] == "intent_pending"
    assert outcome["review_update_receipt"]["status"] == "invalid_metadata"
    assert broker.executed[-1] == "protective"
    assert runtime.state()["active"]["desired_hard_stop"] == 95
    assert runtime.state()["active"]["hard_stop"] == 90
    intent = json.loads(runtime.conn.execute("SELECT payload FROM llm_scenario_intents WHERE id='protective'").fetchone()[0])
    assert "review" not in intent and "review_update_receipt" not in intent


def test_unconfirmed_execution_receipt_is_advisory_not_order_confirmation(setup):
    runtime, broker, now, _ = _active(setup)
    broker.fail = True
    runtime.propose = lambda s, c: dict(wait_proposal(s, c, action_id="exit_unknown"), action="EXIT", review={
        "conditions": [condition(120)], "acknowledgements": []})
    outcome = runtime.tick()
    assert outcome["reason"] == "submission_unknown"
    assert outcome["review_update_receipt"]["scope"] == RECEIPT_SCOPE
    assert outcome["review_update_receipt"]["conditions"][0]["status"] == "ADDED"
    assert runtime.conn.execute("SELECT status FROM llm_scenario_intents WHERE id='exit_unknown'").fetchone()[0] == "PENDING"
