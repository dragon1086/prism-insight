"""Model interpretation hints never replace authoritative validation."""
from copy import deepcopy
import json

import pytest

from live.scenario_calculated_review import _model_payload, SELECT_PROMPT, receipt_hash
from live.scenario_contract import response_schema
from tests.test_scenario_candidate_protocol import review
from tests.test_scenario_calculated_review import context


@pytest.mark.parametrize("first,eligible", [(True, False), (False, True), (False, False), (None, None)])
def test_current_state_is_literal_not_historical_baseline(first, eligible):
    ctx = context()
    ctx["recovery"] = dict(baseline_kind="FIRST_POST_HALT_OBSERVATION")
    if first is not None:
        ctx["recovery"].update(first_observation=first, eligible=eligible)
    before = deepcopy(ctx)
    payload = _model_payload({}, ctx, response_schema(ctx))
    state = payload["contract_context"]["current_decision_state"]
    assert state["first_observation"] is first
    assert state["recovery_eligible"] is eligible
    assert state["execution_authorization"] == "NOT_GRANTED_BY_MODEL_INPUT"
    assert ctx == before


def test_eligible_receipt_can_be_selected_without_order_authority():
    envelope, calls, _ = review(first=False, count=1)
    assert envelope["outcome"]["result"]["selection"]["selection"] == "c0"
    assert "execution_authorized=false does not reject an eligible schema token" in SELECT_PROMPT
    payload = json.loads(calls[-1]["user_prompt"])
    assert payload["contract_context"]["current_decision_state"]["first_observation"] is False


def test_real_allocation_receipt_authority_false_keeps_selectable_hash_binding():
    from tests.test_scenario_allocation_review import taped
    from tests.test_scenario_review_guard import model_receipt_hash
    args, calls = taped("allocation-calculated")
    result = args[3]["outcome"]["result"]
    view = json.loads(calls[1]["user_prompt"])["calculated_candidates"]["candidates"][0]
    receipt = result["selected"]
    assert view["eligible"] is True and receipt["allocation_receipt"]["executable"] is False
    assert result["selection"]["selection"] == "c0"
    assert model_receipt_hash(view) == receipt["receipt_sha256"]
    hashed = deepcopy(receipt); saved_hash = hashed.pop("receipt_sha256")
    assert receipt_hash(hashed) == saved_hash
    assert 'allocation_receipt' not in view




@pytest.mark.parametrize("side", ["LONG", "SHORT"])
def test_holding_context_and_all_receipt_flags_hashes_stay_unchanged(side):
    ctx = context()
    ctx.update(scenario_id="holding", side=side)
    ctx["recovery"] = dict(first_observation=False, eligible=False,
                           baseline_kind="FIRST_POST_HALT_OBSERVATION")
    receipt = dict(id="c0", eligible=True, allocation_receipt=dict(executable=False))
    receipt["receipt_sha256"] = receipt_hash(receipt)
    bundle = dict(candidates=[receipt])
    before = deepcopy((ctx, bundle))
    payload = _model_payload({}, ctx, response_schema(ctx), calculated_candidates=bundle)
    assert (ctx, bundle) == before
    view = payload["calculated_candidates"]["candidates"][0]
    assert view['host_receipt_sha256'] == receipt['receipt_sha256']
    assert view['eligible'] == receipt['eligible']
    assert 'receipt_sha256' not in view
    state = payload["contract_context"]["current_decision_state"]
    assert state["wire_action_choices"] == response_schema(ctx)["properties"]["action"]["enum"]
    assert state["recovery_eligible"] is False
    assert len(json.dumps(state).encode()) < 300


def test_non_boolean_recovery_metadata_stays_unknown():
    ctx = context()
    ctx["recovery"] = dict(first_observation="false", eligible=1)
    state = _model_payload({}, ctx, response_schema(ctx))["contract_context"]["current_decision_state"]
    assert state["first_observation"] is None
    assert state["recovery_eligible"] is None
