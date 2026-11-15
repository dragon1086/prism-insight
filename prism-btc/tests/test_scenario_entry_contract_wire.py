"""Real two-phase protocol/replay checks, no network, orders or profit claims."""
from copy import deepcopy
from functools import partial
import json
from types import SimpleNamespace

import pytest

from analysis.scenario_review_tape import record_review
from live.scenario_calculated_review import research_review
from live.scenario_contract import response_schema, validate_wire_proposal
from live.scenario_entry_contract import bind_contract, contract_context, strip_metadata
from live.scenario_llm import ENTRY_CONTRACT_PROMPT, ScenarioModelError, propose
from live.scenario_model_input import restore_model_input
from live.scenario_preview import response_contract
from live.scenario_review_guard import guard_review
from tests.test_scenario_allocation_review import allocation_wire
from tests.test_scenario_review_guard import context as base_context
from tests.test_scenario_review_guard import model_receipt_hash,model_intent


def definition(side="LONG"):
    return dict(version=1, regime="BREAKOUT", premise="Original breakout", expected_progress="New extreme",
        explicit_failure=dict(source="MARK_PRICE",
        operator="le" if side == "LONG" else "ge", price=83000 if side == "LONG" else 83800,
        minimum_observations=2))


def context(side="LONG", *, legacy=False):
    ctx = base_context()
    ctx.update(scenario_id="active", side=side, positions=[dict(price=83410, quantity=.01)],
               previous_hard_stop=82900 if side == "LONG" else 84000)
    active = dict(scenario_id="active", side=side)
    if not legacy:
        active["entry_contract"] = bind_contract(definition(side), dict(action="OPEN", scenario_id="active",
            action_id="original-open", input_id="original-input", side=side,
            entries=[dict(id="original-entry", price=83410, quantity=.01)],
            hard_stop=82900 if side == "LONG" else 84000), 900)
    ctx.update(contract_context(active, True))
    return ctx


def wire(ctx, action="EXIT", status="FAILED"):
    raw = allocation_wire(ctx, action)
    if action == "ADJUST":
        raw.update(side=ctx["side"], hard_stop=ctx["previous_hard_stop"],
                   take_profits=[dict(id="tp", price=84000 if ctx["side"] == "LONG" else 82500, fraction=.5)])
    raw.update(entry_contract=definition(raw["side"]) if action == "OPEN" else None,
               thesis_assessment=dict(contract_id=ctx["registered_entry_contract"]["contract_id"],
                                      status=status, reason="Observed failure") if ctx.get("registered_entry_contract") else None)
    return raw


def taped(ctx=None, *, mode="economic-reject-calculated-v1", status="FAILED", mutate=None, choose_wait=False):
    ctx = ctx or context()
    snap = dict(valid=True, as_of_ms=1000000)
    contract = response_contract(ctx)
    calls = []
    draft_raw = None

    def generate(**request):
        nonlocal draft_raw
        calls.append(request)
        assert ENTRY_CONTRACT_PROMPT in request["system_prompt"]
        assert len(request["user_prompt"].encode()) <= 100000
        restored = restore_model_input(json.loads(request["user_prompt"]))
        assert restored["contract_context"]["registered_entry_contract"] == ctx["registered_entry_contract"]
        assert restored["contract_context"]["entry_contract_failure_rule"] == ctx["entry_contract_failure_rule"]
        assert ctx["entry_contract_failure_rule"] == dict(
            ctx["registered_entry_contract"]["definition"]["explicit_failure"],
            confirmation_scope="DISTINCT_CONSECUTIVE_FRESH_DECISION_SLOTS")
        if len(calls) == 1:
            raw = dict(wait_proposal=wire(ctx, "WAIT", status), candidates=[wire(ctx, "EXIT", status)], no_candidate_reason="")
        else:
            bundle = restored["calculated_candidates"]
            if status == "FAILED":
                assert bundle["wait"]["template_only"] and bundle["wait"]["eligible"] is False
                assert all(not t.startswith("WAIT:") for t in request["response_schema"]["properties"]["selection_token"]["enum"])
            chosen = bundle["wait"] if choose_wait else bundle["candidates"][0]
            original = draft_raw['wait_proposal'] if choose_wait else draft_raw['candidates'][0]
            assert model_intent(chosen)["thesis_assessment"] == original['thesis_assessment']
            raw = dict(selection_token=f"{chosen['id']}:{model_receipt_hash(chosen)}", rationale="Selected original premise response")
        if mutate:
            mutate(raw, len(calls))
        if len(calls) == 1:
            draft_raw = deepcopy(raw)
        return SimpleNamespace(text=json.dumps(raw))

    envelope = record_review(partial(research_review, research_mode=mode), snap, ctx, contract,
                            generate=generate, record=lambda _: None, clock=lambda: 1000,
                            bindings={"test": "entry-contract"})
    return (snap, ctx, contract, envelope), calls


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
@pytest.mark.parametrize("mode", ["legacy", "action-allocation-calculated-v1", "economic-reject-calculated-v1"])
def test_failed_exit_exact_two_phase_replay_no_wait_authority(side, mode):
    args, calls = taped(context(side), mode=mode)
    assert args[3]["outcome"]["status"] == "COMPLETE", args[3]["outcome"]
    result = args[3]["outcome"]["result"]
    selected = result["selected"]
    assert selected["raw_proposal"]["thesis_assessment"]["status"] == "FAILED"
    assert "thesis_assessment" not in selected["effective_plan"]
    fresh = dict(args[1], now=1001, account_captured_at=1001)
    checked = guard_review(*args, fresh, bindings={"test": "entry-contract"}, research_mode=mode)
    assert checked["payload"]["thesis_assessment"]["status"] == "FAILED"
    assert checked["payload"]["action"] == "EXIT"
    assert checked["receipt"]["execution_authorized"] is False
    assert checked["receipt"]["orders_submitted"] == 0
    assert len(calls) == 2


@pytest.mark.parametrize("status", ["INTACT", "WEAKENING", "UNKNOWN"])
def test_other_assessments_preserve_wait_choice(status):
    args, calls = taped(status=status, choose_wait=True)
    assert args[3]["outcome"]["status"] == "COMPLETE", args[3]["outcome"]
    assert args[3]["outcome"]["result"]["selected"]["id"] == "WAIT"
    assert len(calls) == 2


@pytest.mark.parametrize("mutation", [
    lambda r: r["candidates"][0]["thesis_assessment"].update(status="INTACT"),
    lambda r: r.update(candidates=[], no_candidate_reason="wait instead"),
    lambda r: r["candidates"][0].update(action="ADJUST"),
    lambda r: r["wait_proposal"]["thesis_assessment"].update(contract_id="forged"),
    lambda r: r["wait_proposal"].pop("thesis_assessment"),
])
def test_invalid_or_conflicting_failed_draft_never_gets_selection(mutation):
    args, calls = taped(mutate=lambda raw, phase: mutation(raw) if phase == 1 else None)
    assert args[3]["outcome"]["status"] != "COMPLETE"
    assert len(calls) == 1


@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
@pytest.mark.parametrize('status,reasons', [
    ('WEAKENING', ('확정 실패 관측은 0회입니다.', '확정 실패 관측은 0회.')),
    ('INTACT', ('원래 가정 유지', '원래 가정이 유지됩니다.')),
    ('UNKNOWN', ('근거 불충분', '추가 근거가 필요합니다.')),
    ('FAILED', ('원래 가정이 깨졌습니다.', '원래 가정 실패')),
])
def test_same_state_different_reasons_preserved_through_selection_and_guard(side, status, reasons):
    def mutate(raw, phase):
        if phase == 1:
            raw['wait_proposal']['thesis_assessment']['reason'] = reasons[0]
            raw['candidates'][0]['thesis_assessment']['reason'] = reasons[1]
    args, calls = taped(context(side), status=status, mutate=mutate)
    assert args[3]['outcome']['status'] == 'COMPLETE', args[3]['outcome']
    result = args[3]['outcome']['result']
    assert result['candidates']['wait']['raw_proposal']['thesis_assessment']['reason'] == reasons[0]
    assert result['selected']['raw_proposal']['thesis_assessment']['reason'] == reasons[1]
    fresh = dict(args[1], now=1001, account_captured_at=1001)
    checked = guard_review(*args, fresh, bindings={'test': 'entry-contract'},
                           research_mode='economic-reject-calculated-v1')
    assert checked['payload']['thesis_assessment']['reason'] == reasons[1]
    assert checked['payload']['action'] == 'EXIT'
    assert len(calls) == 2


@pytest.mark.parametrize('rehash_envelope', [False, True])
def test_reason_remains_bound_to_original_receipt_and_exact_tape(rehash_envelope):
    from backtest.scenario_tape import digest
    args, _ = taped()
    envelope = deepcopy(args[3])
    envelope['outcome']['result']['selected']['raw_proposal']['thesis_assessment']['reason'] = '변조된 설명'
    if rehash_envelope:
        envelope['sha256'] = digest({k: v for k, v in envelope.items() if k != 'sha256'})
    fresh = dict(args[1], now=1001, account_captured_at=1001)
    with pytest.raises(ValueError):
        guard_review(*args[:3], envelope, fresh, bindings={'test': 'entry-contract'},
                     research_mode='economic-reject-calculated-v1')


def test_assessment_prompt_contract_matches_comparison_and_preserves_input_budget():
    args, calls = taped(status='WEAKENING')
    rule = next(rule for rule in args[2]['rules'] if rule.startswith('entry_contract is non-null'))
    assert 'share contract_id/status; reasons may differ' in rule
    assert len(rule.encode()) <= 394  # Previous exact-assessment rule byte budget.
    assert 'WAIT template and candidates must agree on contract_id/status' in calls[0]['system_prompt']


@pytest.mark.parametrize('choose_wait', [False, True])
def test_weakening_adjust_and_wait_remain_selectable_with_different_reasons(choose_wait):
    ctx = context()
    def mutate(raw, phase):
        if phase == 1:
            raw['candidates'][0] = wire(ctx, 'ADJUST', 'WEAKENING')
            raw['candidates'][0]['thesis_assessment']['reason'] = '실패 관측 0회.'
            raw['wait_proposal']['thesis_assessment']['reason'] = '실패 관측 0회입니다.'
    args, calls = taped(ctx, status='WEAKENING', mutate=mutate, choose_wait=choose_wait)
    assert args[3]['outcome']['status'] == 'COMPLETE', args[3]['outcome']
    result = args[3]['outcome']['result']
    assert result['selected']['raw_proposal']['action'] == ('WAIT' if choose_wait else 'ADJUST')
    fresh = dict(ctx, now=1001, account_captured_at=1001)
    checked = guard_review(*args, fresh, bindings={'test': 'entry-contract'},
                           research_mode='economic-reject-calculated-v1')
    assert checked['payload']['action'] == ('WAIT' if choose_wait else 'ADJUST')
    assert len(calls) == 2


@pytest.mark.parametrize('assessment', [None, [], 'WEAKENING'])
def test_missing_or_malformed_candidate_assessment_remains_blocked(assessment):
    def mutate(raw, phase):
        if phase == 1:
            raw['candidates'][0]['thesis_assessment'] = assessment
    args, calls = taped(mutate=mutate)
    assert args[3]['outcome']['status'] != 'COMPLETE'
    assert len(calls) == 1


@pytest.mark.parametrize('field,value', [
    ('reason', ''), ('reason', '   '), ('reason', None), ('reason', 0),
    ('reason', 'x' * 241), ('contract_id', 'forged'), ('status', 'INTACT'),
    ('unexpected', 'value'),
])
def test_reason_difference_does_not_bypass_invalid_assessment(field, value):
    def mutate(raw, phase):
        if phase == 1:
            raw['candidates'][0]['thesis_assessment'].update({field: value})
    args, calls = taped(mutate=mutate)
    assert args[3]['outcome']['status'] != 'COMPLETE'
    assert len(calls) == 1


@pytest.mark.parametrize('action', ['WAIT', 'ADJUST'])
def test_failed_nonexit_stays_blocked_even_with_different_reason(action):
    def mutate(raw, phase):
        if phase == 1:
            raw['candidates'][0]['action'] = action
            raw['candidates'][0]['thesis_assessment']['reason'] = '다른 설명'
    args, calls = taped(mutate=mutate)
    assert args[3]['outcome']['status'] != 'COMPLETE'
    assert len(calls) == 1


def test_failed_wait_token_is_not_a_selection_loophole():
    args, calls = taped(choose_wait=True)
    assert args[3]["outcome"]["status"] != "COMPLETE"
    assert len(calls) == 2


@pytest.mark.parametrize("action", ["WAIT", "ADJUST"])
def test_normal_wire_rejects_failed_nonexit(action):
    ctx = context()
    with pytest.raises(ValueError, match="failed_requires_exit"):
        validate_wire_proposal(wire(ctx, action), ctx)


@pytest.mark.parametrize("action", ["WAIT", "EXIT"])
def test_single_pass_never_uses_template_exception(action):
    ctx = context()
    kwargs = dict(generate=lambda **_: SimpleNamespace(text=json.dumps(wire(ctx, action))), clock=lambda: 1000)
    if action == "WAIT":
        with pytest.raises(ScenarioModelError, match="failed_requires_exit"):
            propose(dict(valid=True, as_of_ms=1000000), ctx, response_contract(ctx), **kwargs)
    else:
        assert propose(dict(valid=True, as_of_ms=1000000), ctx, response_contract(ctx), **kwargs)["action"] == "EXIT"


@pytest.mark.parametrize("key", ["registered_entry_contract", "entry_contract_status", "entry_contract_version", "entry_contract_failure_rule"])
def test_fresh_guard_rejects_contract_changes(key):
    args, _ = taped()
    fresh = dict(args[1], now=1001, account_captured_at=1001)
    fresh[key] = {"changed": True}
    with pytest.raises(ValueError, match="context_changed"):
        guard_review(*args, fresh, bindings={"test": "entry-contract"}, research_mode="economic-reject-calculated-v1")


def test_legacy_unknown_is_nullable_not_retroactively_registered():
    ctx = context(legacy=True)
    assert response_schema(ctx)["properties"]["thesis_assessment"] == {"type": "null"}
    assert validate_wire_proposal(wire(ctx), ctx)["thesis_assessment"] is None


def test_protective_exit_ignores_bad_advisory_but_not_entry_contract():
    ctx = dict(context(), review_contract_version=1)
    raw = wire(ctx)
    raw["review"] = "invalid optional advisory"
    normalized = validate_wire_proposal(raw, ctx)
    assert normalized["action"] == "EXIT"
    assert normalized["thesis_assessment"]["status"] == "FAILED"
    assert "thesis_assessment" not in strip_metadata(normalized)
    raw.pop("entry_contract")
    with pytest.raises(ValueError):
        validate_wire_proposal(raw, ctx)


def test_disabled_legacy_schema_does_not_accept_new_fields():
    ctx = base_context()
    assert "entry_contract" not in response_schema(ctx)["properties"]
    raw = allocation_wire(ctx, "WAIT")
    raw.update(entry_contract=None, thesis_assessment=None)
    with pytest.raises(ValueError, match="unknown_fields"):
        validate_wire_proposal(raw, ctx)


def test_benign_fresh_observation_change_does_not_block_exit():
    args, _ = taped()
    fresh = dict(args[1], now=1001, account_captured_at=1001,
                 entry_contract_observation=dict(status="INTACT", count=0))
    checked = guard_review(*args, fresh, bindings={"test": "entry-contract"}, research_mode="economic-reject-calculated-v1")
    assert checked["payload"]["action"] == "EXIT"


def test_forged_failed_wait_receipt_cannot_bypass_exact_replay():
    args, _ = taped()
    forged = deepcopy(args[3])
    result = forged["outcome"]["result"]
    result["selected"] = result["candidates"]["wait"]
    result["selection"] = dict(selection="WAIT", receipt_sha256=result["selected"]["receipt_sha256"], rationale="forged")
    with pytest.raises(ValueError):
        guard_review(*args[:3], forged, dict(args[1], now=1001, account_captured_at=1001),
                     bindings={"test": "entry-contract"}, research_mode="economic-reject-calculated-v1")


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
def test_open_definition_survives_allocation_projection_and_replay(side):
    ctx = base_context()
    ctx.update(contract_context(None, True))
    snap = dict(valid=True, as_of_ms=1000000)
    contract = response_contract(ctx)
    calls = []

    def generate(**request):
        calls.append(request)
        restored = restore_model_input(json.loads(request["user_prompt"]))
        if len(calls) == 1:
            candidate = allocation_wire(ctx)
            candidate.update(side=side, hard_stop=82900 if side == "LONG" else 84000,
                take_profits=[dict(id="tp", price=84000 if side == "LONG" else 82500, fraction=.5)],
                entry_contract=definition(side), thesis_assessment=None)
            wait = allocation_wire(ctx, "WAIT")
            wait.update(entry_contract=None, thesis_assessment=None)
            raw = dict(wait_proposal=wait, candidates=[candidate], no_candidate_reason="")
        else:
            candidate = restored["calculated_candidates"]["candidates"][0]
            assert model_intent(candidate)["entry_contract"] == definition(side)
            assert model_intent(candidate)["entry_contract"] == definition(side)
            raw = dict(selection_token=f"{candidate['id']}:{model_receipt_hash(candidate)}", rationale="Original contract")
        return SimpleNamespace(text=json.dumps(raw))

    mode = "economic-reject-calculated-v1"
    envelope = record_review(partial(research_review, research_mode=mode), snap, ctx, contract,
        generate=generate, record=lambda _: None, clock=lambda: 1000, bindings={"test": "entry-contract"})
    assert envelope["outcome"]["status"] == "COMPLETE", envelope["outcome"]
    checked = guard_review(snap, ctx, contract, envelope, dict(ctx, now=1001, account_captured_at=1001),
                            bindings={"test": "entry-contract"}, research_mode=mode)
    assert checked["payload"]["entry_contract"] == definition(side)
    assert "entry_contract" not in checked["validated"]
    assert len(calls) == 2


@pytest.mark.parametrize("field", ["entry_contract", "thesis_assessment"])
def test_optin_metadata_cannot_be_omitted(field):
    ctx = context()
    raw = wire(ctx)
    raw.pop(field)
    with pytest.raises(ValueError):
        validate_wire_proposal(raw, ctx)


@pytest.mark.parametrize("field", ["premise", "expected_progress"])
def test_open_text_limits_are_schema_enforced(field):
    ctx = base_context()
    ctx.update(contract_context(None, True))
    raw = allocation_wire(ctx)
    raw["entries"][0].pop("allocation_fraction")
    raw["entries"][0]["quantity"] = .01
    raw.update(entry_contract=definition(), thesis_assessment=None)
    raw["entry_contract"][field] = "x" * 241
    with pytest.raises(ValueError, match="contract_mismatch"):
        validate_wire_proposal(raw, ctx)


@pytest.mark.parametrize("mode", ["allocation-single", "economic-reject-single-v1"])
@pytest.mark.parametrize("action", ["WAIT", "EXIT"])
def test_single_calculated_mode_failed_is_exit_only(mode, action):
    ctx = context()
    raw = wire(ctx, action)
    if mode == "economic-reject-single-v1":
        raw = {"proposal": raw}
    kwargs = dict(generate=lambda **_: SimpleNamespace(text=json.dumps(raw)), record=lambda _: None,
                  clock=lambda: 1000, research_mode=mode)
    if action == "WAIT":
        with pytest.raises(ValueError, match="failed_requires_exit"):
            research_review(dict(valid=True, as_of_ms=1000000), ctx, response_contract(ctx), **kwargs)
    else:
        result = research_review(dict(valid=True, as_of_ms=1000000), ctx, response_contract(ctx), **kwargs)
        assert result["selected"]["raw_proposal"]["thesis_assessment"]["status"] == "FAILED"
        assert result["orders_submitted"] == 0


@pytest.mark.parametrize("key", ["input_id", "scenario_id", "action_id"])
def test_exact_action_source_ids_remain_required(key):
    ctx = context()
    raw = wire(ctx)
    raw[key] = "other-source"
    with pytest.raises(ValueError, match="identity_mismatch"):
        validate_wire_proposal(raw, ctx)


def test_new_confirmed_host_failure_invalidates_reviewed_wait():
    from live.scenario_entry_contract import observe_contract

    ctx = context()
    record = bind_contract(definition(), dict(action="OPEN", scenario_id="active",
                           action_id="original-open", input_id="original-input", side="LONG",
                           entries=[dict(id="original-entry", price=83410, quantity=.01)], hard_stop=82900), 1)
    ctx["registered_entry_contract"] = record
    args, _ = taped(ctx, status="INTACT", choose_wait=True)
    active = dict(scenario_id="active", side="LONG", entry_contract=record)
    for at in (600, 900):
        observed = dict(ctx, account_captured_at=at, mark_price=82999,
                        account_version=f"account-{at}", entry_thesis=dict(status="VERIFIED", side="LONG",
                        first_fill_at=500, observed_at=at, current_mark=82999))
        observe_contract(active, observed, at // 300, at)
    assert active["entry_contract_observation"]["status"] == "FAILED"
    fresh = dict(ctx, now=1001, account_captured_at=1001,
                 entry_contract_observation=active["entry_contract_observation"])
    with pytest.raises(ValueError, match="failed_requires_exit"):
        guard_review(*args, fresh, bindings={"test": "entry-contract"}, research_mode="economic-reject-calculated-v1")


def test_old_duplicate_failure_basis_is_rejected_not_second_authority():
    ctx = base_context()
    ctx.update(contract_context(None, True))
    raw = allocation_wire(ctx)
    raw["entries"][0].pop("allocation_fraction")
    raw["entries"][0]["quantity"] = .01
    raw.update(entry_contract=definition(), thesis_assessment=None)
    raw["entry_contract"]["failure_basis"] = "Different numeric failure at 83600"
    with pytest.raises(ValueError, match="contract_mismatch"):
        validate_wire_proposal(raw, ctx)
