from copy import deepcopy
import json
from types import SimpleNamespace

import pytest

from analysis.scenario_review_tape import record_review
from live.scenario_calculated_review import research_review
from live.scenario_contract import identity_fields
from live.scenario_preview import response_contract
from live.scenario_review_guard import guard_review


def context():
    return dict(now=1000, input_id="guard-1", input_captured_at=1000,
        account_captured_at=1000, max_input_age_seconds=120, scenario_id=None, revision=0,
        account_version="account-1", accounting_status="confirmed", protection_ok=True,
        legacy_fenced=False, target_status=[], seen_action_ids=[], initial_equity=10000,
        positions=[], pending_entries=[], previous_hard_stop=None, realized_loss=0,
        fees_paid=0, funding_paid=0, estimated_cost_rate=.002, slippage_bps=20,
        new_risk_blocked=False, mark_price=83410, price_tick=.1, quantity_step=.001,
        minimum_quantity=.001, minimum_notional=5)


def wire(ctx, action):
    active = action in {"OPEN", "ADJUST"}
    result = dict(**identity_fields(ctx), action=action, confidence=.7, expires_at=1200,
        rationale="구조와 계산 검토", leverage=10,
        entries=[dict(id="e", price=83410, quantity=.01)] if action == "OPEN" else [],
        take_profits=[dict(id="tp", price=84000, fraction=.5)] if active else [],
        partial_stops=[], cancel_entry_ids=[], side="LONG" if active else None,
        hard_stop=82900 if active else None,
        chase=dict(max_bps=0, max_reprices=0) if active else None)
    if ctx.get("recovery_contract_version") in (1, 2):
        result["recovery"] = None
    return result


def taped(ctx=None, action="OPEN"):
    ctx = ctx or context()
    snapshot, calls = dict(valid=True, as_of_ms=1000000), []
    contract = response_contract(ctx)

    def generate(**request):
        calls.append(request)
        if len(calls) == 1:
            result = dict(wait_proposal=wire(ctx, "WAIT"),
                candidates=[] if action == "WAIT" else [wire(ctx, action)],
                no_candidate_reason="근거 없음" if action == "WAIT" else "")
        else:
            bundle = json.loads(request["user_prompt"])["calculated_candidates"]
            chosen = bundle["candidates"][0]
            assert chosen["eligible"], chosen
            result = dict(selection_token=f"{chosen['id']}:{chosen['receipt_sha256']}", rationale="계산 검토 완료")
        return SimpleNamespace(text=json.dumps(result))

    envelope = record_review(research_review, snapshot, ctx, contract, generate=generate,
        record=lambda event: None, clock=lambda: 1000, bindings={"code": "test"})
    assert envelope["outcome"]["status"] == "COMPLETE", envelope
    return snapshot, ctx, contract, envelope


def check(args, fresh=None):
    return guard_review(*args, fresh or dict(args[1], now=1001, account_captured_at=1001),
                        bindings={"code": "test"})


@pytest.mark.parametrize('message,detail', [('scenario_oauth_failed:http_429',':http_429'),
    ('scenario_oauth_failed:incomplete',':incomplete'), ('scenario_oauth_failed',''),
    ('scenario_oauth_failed:http_429 secret','')])
def test_failed_model_calls_remain_distinct_through_sealed_replay(message, detail):
    ctx=context();snapshot=dict(valid=True,as_of_ms=1000000);contract=response_contract(ctx)
    events=[]
    def generate(**request):
        raise ValueError(message)
    tape=record_review(research_review,snapshot,ctx,contract,generate=generate,
        record=events.append,clock=lambda:1000,bindings={'code':'test'})
    assert tape['outcome']['status']=='FAILED'
    with pytest.raises(ValueError) as exc:
        guard_review(snapshot,ctx,contract,tape,dict(ctx,now=1001,account_captured_at=1001),bindings={'code':'test'})
    assert str(exc.value)=='review_model_call_failed'+detail
    assert events[-1].get('failure_detail')==(detail[1:] if detail else None)


def test_exact_replayed_open_is_fresh_checked_not_authorized(monkeypatch):
    from live import scenario_review_guard as module
    args = taped()
    original = deepcopy(args)
    calls = []
    real = module.validate_execution_prices

    def once(payload, ctx):
        calls.append(deepcopy(payload))
        return real(payload, ctx)

    monkeypatch.setattr(module, "validate_execution_prices", once)
    result = check(args)
    assert len(calls) == 1
    assert result["payload"]["action"] == "OPEN"
    assert result["validated"]["risk"]["proposed_risk"] == pytest.approx(8.4262)
    assert result["receipt"]["execution_authorized"] is False
    assert result["receipt"]["orders_submitted"] == 0
    assert args == original


@pytest.mark.parametrize("key,value", [
    ("account_version", "account-2"), ("positions", [{"price": 83410, "quantity": .001}]),
    ("pending_entries", [{"id": "p", "price": 83410, "quantity": .001}]),
    ("scenario_id", "different"), ("revision", 2), ("previous_hard_stop", 82800),
    ("initial_equity", 9999), ("fees_paid", .01), ("funding_paid", .01),
    ("realized_loss", .01), ("estimated_cost_rate", .001), ("slippage_bps", 10),
    ("scenario_risk_fraction", .01), ("minimum_notional", 10), ("price_tick", 1),
    ("quantity_step", .01), ("minimum_quantity", .01),
    ("target_status", [{"status": "FILLED"}]), ("protection_ok", False),
    ("accounting_status", "pending"), ("legacy_fenced", True), ("new_risk_blocked", True),
])
def test_changed_account_exposure_risk_or_lifecycle_rejected(key, value):
    args = taped()
    fresh = dict(args[1], now=1001, account_captured_at=1001)
    fresh[key] = value
    with pytest.raises(ValueError, match="review_guard_context_changed"):
        check(args, fresh)


@pytest.mark.parametrize("field,value", [("now", 1121), ("now", 999),
    ("account_captured_at", 880), ("account_captured_at", 1002)])
def test_stale_or_future_inputs_rejected(field, value):
    args = taped()
    fresh = dict(args[1], now=1001, account_captured_at=1001)
    fresh[field] = value
    with pytest.raises(ValueError, match="review_guard_stale"):
        check(args, fresh)


def test_mark_drift_allowed_but_crossed_stop_rejected():
    args = taped()
    fresh = dict(args[1], now=1001, account_captured_at=1001, mark_price=83420)
    assert check(args, fresh)["validated"]["entries"][0]["price"] == 83410
    fresh["mark_price"] = 82899
    with pytest.raises(ValueError, match="stop_crossed_market"):
        check(args, fresh)


def test_quote_drift_same_effective_prices_allowed_changed_buffer_rejected():
    ctx = context()
    ctx.update(execution_price_policy={"version": "round-limit-v1"},
               limit_price_quotes=dict(bid=83400, ask=83401, captured_at=1000))
    args = taped(ctx)
    fresh = dict(ctx, now=1001, account_captured_at=1001,
                 limit_price_quotes=dict(bid=83401, ask=83402, captured_at=1001))
    assert check(args, fresh)["validated"]["entries"][0]["price"] == 83417.3
    fresh["limit_price_quotes"] = dict(bid=83411, ask=83412, captured_at=1001)
    with pytest.raises(ValueError, match="review_guard_effective_plan_changed"):
        check(args, fresh)


@pytest.mark.parametrize("action", ["WAIT", "EXIT", "ADJUST"])
def test_wait_and_holding_paths_require_complete_review_and_fresh_context(action):
    ctx = context()
    if action != "WAIT":
        ctx.update(scenario_id="active", revision=1, side="LONG", previous_hard_stop=82800,
                   positions=[dict(price=83300, quantity=.02)])
    args = taped(ctx, action)
    assert check(args)["validated"]["action"] == action
    fresh = dict(ctx, now=1001, account_captured_at=1001)
    if action != "WAIT":
        fresh["positions"] = [dict(price=83300, quantity=.01)]
        with pytest.raises(ValueError, match="context_changed:positions"):
            check(args, fresh)
    envelope = args[3]
    envelope["operations"].pop()
    with pytest.raises(ValueError):
        check(args)


def test_missing_lifecycle_evidence_is_not_success():
    args = taped()
    fresh = dict(args[1], now=1001, account_captured_at=1001)
    del fresh["account_version"]
    with pytest.raises(ValueError, match="context_changed:account_version"):
        check(args, fresh)


def test_wrong_source_binding_rejected():
    args = taped()
    with pytest.raises(ValueError):
        guard_review(*args, dict(args[1], now=1001), bindings={"code": "other"})


def test_tampered_result_rejected_even_when_envelope_resealed():
    from backtest.scenario_tape import digest
    args = taped()
    envelope = args[3]
    envelope["outcome"]["result"]["selected"]["raw_proposal"]["entries"][0]["price"] = 83300
    del envelope["sha256"]
    envelope["sha256"] = digest(envelope)
    with pytest.raises(ValueError):
        check(args)


def test_shorter_original_age_limit_preserved():
    ctx = dict(context(), max_input_age_seconds=10)
    args = taped(ctx)
    with pytest.raises(ValueError, match="review_guard_stale_input"):
        check(args, dict(ctx, now=1011, account_captured_at=1011))


@pytest.mark.parametrize("action", ["WAIT", "EXIT"])
def test_stable_pending_accounting_does_not_block_wait_or_exit(action):
    ctx = dict(context(), scenario_id="active", revision=1, side="LONG",
               previous_hard_stop=82800, positions=[dict(price=83300, quantity=.02)],
               accounting_status="pending", recovery_hard_blocked=False)
    args = taped(ctx, action)
    fresh = dict(ctx, now=1001, account_captured_at=1001, new_risk_blocked=True,
                 recovery_hard_blocked=True, stop_policy={"widening_eligible": False})
    assert check(args, fresh)["validated"]["action"] == action
    if action == "EXIT":
        projection = args[3]["outcome"]["result"]["selected"]["projection"]
        assert projection["status"] == "UNAVAILABLE"
        assert projection["actual_net_pnl"] is None
    for key, value in [("account_version", "changed"),
                       ("positions", [dict(price=83300, quantity=.01)])]:
        with pytest.raises(ValueError, match="review_guard_context_changed"):
            check(args, dict(fresh, **{key: value}))


@pytest.mark.parametrize("flag", ["new_risk_blocked", "recovery_hard_blocked"])
def test_de_risk_flags_must_still_be_boolean(flag):
    args = taped(action="WAIT")
    fresh = dict(args[1], now=1001, account_captured_at=1001)
    fresh[flag] = 1
    with pytest.raises(ValueError, match="review_guard_unsafe_lifecycle"):
        check(args, fresh)


@pytest.mark.parametrize("action", ["WAIT", "OPEN"])
def test_original_recovery_wire_contract_need_not_be_in_fresh_observation(action):
    ctx = dict(context(), recovery_contract_version=2, scenario_risk_fraction=.005)
    args = taped(ctx, action)
    fresh = dict(ctx, now=1001, account_captured_at=1001)
    del fresh["recovery_contract_version"]
    result = check(args, fresh)
    assert result["payload"]["action"] == action
    assert "recovery" in result["payload"]
    assert "recovery" not in result["validated"]
    for key, value in [("input_id", "different"), ("scenario_risk_fraction", .02)]:
        with pytest.raises(ValueError, match="review_guard_context_changed"):
            check(args, dict(fresh, **{key: value}))


def test_final_explanation_returned_without_mutating_original_candidate():
    from live.scenario_calculated_review import receipt_hash
    args = taped()
    selected = args[3]["outcome"]["result"]["selected"]
    original_hash = receipt_hash(selected["raw_proposal"])
    assert selected["raw_proposal"]["rationale"] == "구조와 계산 검토"
    result = check(args)
    assert result["payload"]["rationale"] == selected["raw_proposal"]["rationale"]
    assert result["validated"]["rationale"] == selected["raw_proposal"]["rationale"]
    assert args[3]['outcome']['result']['selection']['rationale'] == "계산 검토 완료"
    assert receipt_hash(selected["raw_proposal"]) == original_hash == selected["raw_sha256"]
