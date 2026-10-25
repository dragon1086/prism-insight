"""Model interpretation hints never replace authoritative validation."""
from copy import deepcopy
import hashlib
import json

import pytest

from live.scenario_calculated_review import _model_payload, DRAFT_PROMPT, SELECT_PROMPT, receipt_hash
from live.scenario_contract import response_schema
from live.scenario_llm import MA_STRUCTURE_PROMPT
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
    assert "eligible=true is selectable" in SELECT_PROMPT
    payload = json.loads(calls[-1]["user_prompt"])
    assert payload["contract_context"]["current_decision_state"]["first_observation"] is False


def test_real_allocation_receipt_authority_false_keeps_selectable_hash_binding():
    from tests.test_scenario_allocation_review import taped
    args, calls = taped("allocation-calculated")
    result = args[3]["outcome"]["result"]
    receipt = json.loads(calls[1]["user_prompt"])["calculated_candidates"]["candidates"][0]
    assert receipt["eligible"] is True
    assert receipt["allocation_receipt"]["executable"] is False
    assert result["selection"]["selection"] == "c0"
    assert result["selected"] == receipt
    hashed = deepcopy(receipt)
    saved_hash = hashed.pop("receipt_sha256")
    assert receipt_hash(hashed) == saved_hash


def test_directional_path_is_symmetric_evidence_not_ohlc_order_or_hard_gate():
    for phrase in ("SHORT: rebound above MA10", "LONG: pullback below MA10",
                   "Do not infer within-candle event order from OHLC",
                   "Do not require an additional retest", "not automatic entry"):
        assert phrase in MA_STRUCTURE_PROMPT


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
    assert payload["calculated_candidates"] == bundle
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


def test_interpretation_prompt_versions_pinned():
    # 2026-10-25: flat entry selection preference; holding/risk/eligibility unchanged.
    assert hashlib.sha256(DRAFT_PROMPT.encode()).hexdigest() == "41995ed45ab97949e3d146c7958044294973703f372c0f4b70a4ae863d22df4c"
    assert hashlib.sha256(SELECT_PROMPT.encode()).hexdigest() == "fdb8576b0d36d1a4626dae7cce646146c4f27afe4ed14c1eab3fb71ef8af9799"
