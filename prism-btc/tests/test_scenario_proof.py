"""Finite contract coverage and counterfactual mutations, not trade samples."""
import copy
import hashlib
import itertools

import pytest

from analysis.scenario_proof import content_hash, verify_checkpoint_transition, verify_ma_claims
from live.scenario_review_memory import apply_review


def condition(price=120, operator="ge"):
    return {"source": "MARK_PRICE", "operator": operator, "price": price}


def saved(index, operator="ge"):
    return {**condition(100 + index, operator), "id": f"old_{index}", "created_at": 0,
            "reached_at": None, "reached_price": None, "acknowledgement": None}


def packet(before=None, metadata=None, *, presented=None, action="WAIT", side="LONG", flat=False):
    before = copy.deepcopy(before if before is not None else [])
    presented = copy.deepcopy(before if presented is None else presented)
    suffix = hashlib.sha256(b"input-1").hexdigest()[:24]
    scenario = "s_" + suffix if flat else "scenario-1"
    action_id = "a_" + suffix
    proposed = {"input_id": "input-1", "scenario_id": scenario, "action_id": action_id,
                "action": action, "review": metadata if metadata is not None else
                {"conditions": [condition()], "acknowledgements": []}}
    model_input = {"contract_context": {"input_id": "input-1", "scenario_id": None if flat else scenario,
                                       "review_memory": presented, "side": side}}
    # Production is the subject under test. The verifier never calls it.
    active = {"scenario_id": scenario, "review_memory": copy.deepcopy(before)}
    receipt = apply_review(active, proposed["review"], action_id, now=123, presented=presented)
    return {"version": 1, "input": model_input, "proposal": proposed,
            "binding": {"input_id": "input-1", "scenario_id": scenario, "action_id": action_id,
                        "input_sha256": content_hash(model_input), "proposal_sha256": content_hash(proposed),
                        "source_revision": "fixture-only-not-live-evidence",
                        "source_ids": {key: "fixture:" + key for key in
                                       ("input", "proposal", "before", "after", "receipt", "core_validation")}},
            "core_validated": True, "before": before, "presented": presented,
            "after": active["review_memory"], "receipt": receipt, "applied_at": 123}


def rebind(value):
    value["binding"]["input_sha256"] = content_hash(value["input"])
    if "proposal" in value:
        value["binding"]["proposal_sha256"] = content_hash(value["proposal"])
    return value


def test_complete_finite_checkpoint_domain():
    """All 576 combinations of the explicitly declared finite domain.

    Slots 0..3 × operator ge/le × side LONG/SHORT × WAIT/ADJUST/EXIT/OPEN
    × no/new/existing condition × no/hold-all/replace-all acknowledgement.
    This is bounded deterministic coverage, not a proof over all numeric inputs.
    """
    checked = 0
    for slots, operator, side, action, addition, ack_mode in itertools.product(
            range(4), ("ge", "le"), ("LONG", "SHORT"), ("WAIT", "ADJUST", "EXIT", "OPEN"),
            ("none", "new", "existing"), ("none", "hold", "replace")):
        before = [saved(index, operator) for index in range(slots)]
        conditions = [] if addition == "none" else [condition(100 if addition == "existing" else 120, operator)]
        acks = [] if ack_mode == "none" else [
            {"id": row["id"], "disposition": ack_mode, "reason": "Bound finite test"} for row in before]
        value = packet(before, {"conditions": conditions, "acknowledgements": acks}, action=action, side=side)
        original = copy.deepcopy(value)
        result = verify_checkpoint_transition(value)
        assert result["status"] == "VERIFIED", (slots, operator, side, action, addition, ack_mode, result)
        assert value == original
        checked += 1
    assert checked == 576


def test_flat_open_uses_input_derived_scenario():
    value = packet(action="OPEN", flat=True)
    assert verify_checkpoint_transition(value)["status"] == "VERIFIED"
    value["binding"]["scenario_id"] = "another-scenario"
    assert verify_checkpoint_transition(value)["status"] == "MISMATCH"


@pytest.mark.parametrize("kind", ["unknown", "new_hit", "not_presented"])
def test_invalid_ack_is_atomic(kind):
    before, presented = [saved(0), saved(1)], [saved(0), saved(1)]
    identifier = "absent" if kind == "unknown" else "old_1"
    if kind == "new_hit":
        before[1].update(reached_at=120, reached_price=123)
    if kind == "not_presented":
        presented.pop()
    acks = [{"id": "old_0", "disposition": "replace", "reason": "valid first"},
            {"id": identifier, "disposition": "replace", "reason": "invalid second"}]
    value = packet(before, {"conditions": [condition()], "acknowledgements": acks}, presented=presented)
    assert value["after"] == before
    assert verify_checkpoint_transition(value)["status"] == "VERIFIED"
    value["after"].pop(0)
    assert verify_checkpoint_transition(value)["status"] == "MISMATCH"


@pytest.mark.parametrize("mutation", ["forged_added", "wrong_id", "wrong_price", "extra_deletion",
                                     "free_capacity", "retained_ids", "wrong_action", "wrong_source",
                                     "wrong_status", "wrong_version", "hash", "same_id_price"])
def test_counterfactual_mutations_are_detected(mutation):
    value = packet([saved(0)])
    if mutation == "forged_added":
        value["receipt"]["conditions"][0]["status"] = "ALREADY_RETAINED"
    elif mutation == "wrong_id":
        value["after"][-1]["id"] = value["receipt"]["conditions"][0]["id"] = "forged"
        value["receipt"]["retained_ids"][-1] = "forged"
    elif mutation == "wrong_price":
        value["after"][-1]["price"] += 1
    elif mutation == "extra_deletion":
        value["after"].pop(0)
    elif mutation == "free_capacity":
        value["receipt"]["free_capacity"] = 3
    elif mutation == "retained_ids":
        value["receipt"]["retained_ids"].reverse()
    elif mutation == "wrong_action":
        value["receipt"]["action_id"] = "other-action"
    elif mutation == "wrong_source":
        value["after"][-1]["source"] = "LAST_PRICE"
    elif mutation == "wrong_status":
        value["receipt"]["status"] = "capacity_retained"
    elif mutation == "wrong_version":
        value["receipt"]["version"] = True
    elif mutation == "hash":
        value["input"]["contract_context"]["side"] = "SHORT"
    else:
        value["before"][0]["price"] += 1
    assert verify_checkpoint_transition(value)["status"] == "MISMATCH"


def test_capacity_receipt_cannot_claim_added_even_with_consistent_forged_state():
    value = packet([saved(i) for i in range(3)])
    assert value["receipt"]["conditions"][0]["status"] == "NOT_ADDED_CAPACITY"
    assert verify_checkpoint_transition(value)["status"] == "VERIFIED"
    value["after"].pop(0)
    value["after"].append({**saved(9), **condition()})
    value["receipt"]["conditions"][0].update(status="ADDED", id="old_9")
    value["receipt"]["retained_ids"] = [row["id"] for row in value["after"]]
    assert verify_checkpoint_transition(value)["status"] == "MISMATCH"


@pytest.mark.parametrize("field", ["before", "after", "presented", "receipt", "applied_at", "core_validated"])
def test_missing_boundaries_never_pass(field):
    value = packet()
    del value[field]
    assert verify_checkpoint_transition(value)["status"] == "INSUFFICIENT"


def test_missing_binding_invalid_metadata_and_rejected_core_never_pass():
    value = packet()
    value["binding"]["source_ids"].pop("before")
    assert verify_checkpoint_transition(value)["status"] == "INSUFFICIENT"
    value = packet(metadata={"wrong": "metadata"}, action="EXIT")
    assert verify_checkpoint_transition(value)["status"] == "INSUFFICIENT"
    # Offline result does not authorize or block the underlying protective EXIT.
    assert value["proposal"]["action"] == "EXIT"
    value["after"].append(saved(1))
    assert verify_checkpoint_transition(value)["status"] == "MISMATCH"
    value = packet()
    value["core_validated"] = False
    assert verify_checkpoint_transition(value)["status"] == "MISMATCH"
    value.update(after=value["before"], receipt=None)
    assert verify_checkpoint_transition(value)["status"] == "INSUFFICIENT"


def ma_packet():
    level = {"timeframe": "4h", "ma": "ma35", "is_confirmed": True, "price": 84000,
             "as_of_ms": 123000}
    model_input = {"contract_context": {"input_id": "input-1", "scenario_id": "scenario-1", "ma_structure": {
        "levels": {"4h.ma35.confirmed": level}, "levels_price_basis": "LAST_TRADE_OHLC_SMA"}}}
    digest = content_hash(model_input)
    text = "The 4h MA35 confirmed value is 84000. This is not a trade recommendation."
    action_id = "a_" + hashlib.sha256(b"input-1").hexdigest()[:24]
    proposal = {"input_id": "input-1", "scenario_id": "scenario-1", "action_id": action_id,
                "action": "WAIT", "rationale": text}
    return {"version": 1, "input": model_input,
            "proposal": proposal,
            "binding": {"input_id": "input-1", "input_sha256": digest, "source_revision": "fixture",
                        "scenario_id": "scenario-1", "action_id": action_id,
                        "proposal_sha256": content_hash(proposal),
                        "source_ids": {"input": "fixture:input", "proposal": "fixture:proposal",
                                       "text": "fixture:rationale"}},
            "source_text": text, "source_text_sha256": content_hash(text),
            "claims": [{"level_id": "4h.ma35.confirmed", "timeframe": "4h", "ma": "ma35",
                        "phase": "confirmed", "price": 84000, "as_of_ms": 123000,
                        "basis": "LAST_TRADE_OHLC_SMA", "input_sha256": digest,
                        "origin": "manual_annotation", "excerpt": "4h MA35 confirmed value is 84000"}]}


@pytest.mark.parametrize("key,value", [("timeframe", "12h"), ("ma", "ma10"), ("phase", "forming"),
                                       ("price", 84001), ("as_of_ms", 120000), ("basis", "MARK_PRICE"),
                                       ("excerpt", "not in original"), ("input_sha256", "other-input")])
def test_ma_counterfactuals(key, value):
    data = ma_packet()
    assert verify_ma_claims(data)["status"] == "VERIFIED"
    data["claims"][0][key] = value
    assert verify_ma_claims(data)["status"] == "MISMATCH"


def test_ma_zero_coverage_and_missing_references_are_not_passes():
    data = ma_packet()
    data["claims"] = []
    assert verify_ma_claims(data)["status"] == "UNASSESSED_PROSE"
    data = ma_packet()
    del data["claims"][0]["level_id"]
    assert verify_ma_claims(data)["status"] == "MISSING_REFERENCE"
    data["claims"][0]["level_id"] = "12h.ma35.confirmed"
    assert verify_ma_claims(data)["status"] == "MISSING_REFERENCE"


def test_ma_missing_phase_origin_and_ambiguous_equal_prices_not_guessed():
    data = ma_packet()
    data["input"]["contract_context"]["ma_structure"]["levels"]["4h.ma35.confirmed"].pop("is_confirmed")
    rebind(data)
    data["claims"][0]["input_sha256"] = data["binding"]["input_sha256"]
    assert verify_ma_claims(data)["status"] == "INSUFFICIENT"
    data = ma_packet()
    data["claims"][0].pop("origin")
    assert verify_ma_claims(data)["status"] == "INSUFFICIENT"
    data = ma_packet()
    data["claims"][0].pop("level_id")
    levels = data["input"]["contract_context"]["ma_structure"]["levels"]
    levels["4h.ma10.confirmed"] = {**levels["4h.ma35.confirmed"], "ma": "ma10"}
    rebind(data)
    assert verify_ma_claims(data)["status"] == "MISSING_REFERENCE"


def test_ma_claim_scope_does_not_certify_free_prose_or_authenticity():
    data = ma_packet()
    result = verify_ma_claims(data)
    assert result["status"] == "VERIFIED"
    assert result["verified_claims"] == 1
    assert result["prose_status"] == "UNASSESSED_PROSE"
    assert result["annotation_semantics"] == "NOT_CERTIFIED"
    assert "NOT_SOURCE_AUTHENTICITY" in result["scope"]
    assert result["packet_sha256"] == content_hash(data)
    data["source_text"] += " edited"
    assert verify_ma_claims(data)["status"] == "MISMATCH"


def test_ma_detached_text_and_noncanonical_source_fail_closed():
    data = ma_packet()
    data["source_text"] += " detached"
    data["source_text_sha256"] = content_hash(data["source_text"])
    assert verify_ma_claims(data)["status"] == "MISMATCH"
    data = ma_packet()
    data["input"]["contract_context"]["ma_structure"]["levels"]["4h.ma35.confirmed"]["ma"] = "ma10"
    rebind(data)
    data["claims"][0]["input_sha256"] = data["binding"]["input_sha256"]
    assert verify_ma_claims(data)["status"] == "INSUFFICIENT"


def test_multiple_conditions_can_be_added_retained_and_capacity_rejected():
    value = packet([saved(0), saved(1)], {"conditions": [condition(100), condition(120), condition(130)],
                                        "acknowledgements": []})
    assert [row["status"] for row in value["receipt"]["conditions"]] == [
        "ALREADY_RETAINED", "ADDED", "NOT_ADDED_CAPACITY"]
    assert verify_checkpoint_transition(value)["status"] == "VERIFIED"


@pytest.mark.parametrize("bad", [None, [], {}, {"version": True}, {"version": 1, "input": float("nan")}])
def test_corrupt_packets_fail_closed(bad):
    assert verify_checkpoint_transition(bad)["status"] == "INSUFFICIENT"
    assert verify_ma_claims(bad)["status"] == "INSUFFICIENT"
