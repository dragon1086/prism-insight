from copy import deepcopy
import json
from types import SimpleNamespace

import pytest

from live.scenario_calculated_review import calculate_candidate, research_review
from live.scenario_contract import identity_fields
from live.scenario_preview import response_contract


def context():
    return dict(now=1000, input_id="research-1", input_captured_at=1000,
        account_captured_at=1000, max_input_age_seconds=120, scenario_id=None, revision=0,
        seen_action_ids=[], initial_equity=10000, positions=[], pending_entries=[],
        previous_hard_stop=None, realized_loss=0, fees_paid=0, funding_paid=0,
        estimated_cost_rate=.002, slippage_bps=20, new_risk_blocked=False,
        mark_price=83410, price_tick=.1, quantity_step=.001, minimum_quantity=.001,
        minimum_notional=5)


def wire(ctx, side=None):
    result = dict(**identity_fields(ctx), action="OPEN" if side else "WAIT",
        confidence=.7, expires_at=1200, rationale="가격 흐름과 무효화 근거",
        leverage=10, entries=[dict(id="e", price=83410, quantity=.01)] if side else [],
        take_profits=[dict(id="tp", price=84000 if side == "LONG" else 82500, fraction=.5)] if side else [],
        partial_stops=[], cancel_entry_ids=[], side=side,
        hard_stop=82900 if side == "LONG" else 84000 if side else None,
        chase=dict(max_bps=0, max_reprices=0) if side else None)
    if ctx.get("recovery_contract_version") in (1, 2):
        result["recovery"] = None
    return result


def run(ctx=None, candidates=None, *, mutate=None, clock=lambda: 1000, fail_second=False):
    ctx = ctx or context()
    candidates = [wire(ctx, "LONG")] if candidates is None else candidates
    events, calls = [], []
    def generate(**kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            result = dict(wait_proposal=wire(ctx), candidates=candidates,
                          no_candidate_reason="가격 구조 근거 부족" if not candidates else "")
        else:
            if fail_second:
                raise RuntimeError("secret")
            data = json.loads(kwargs["user_prompt"])["calculated_candidates"]
            chosen = next((r for r in data["candidates"] if r["eligible"]), data["wait"])
            result = dict(selection_token=f"{chosen['id']}:{chosen['receipt_sha256']}",
                          rationale="계산과 추세 검토 후 선택")
        if mutate:
            mutate(result, len(calls), kwargs)
        return SimpleNamespace(text=json.dumps(result))
    result = research_review(dict(valid=True, as_of_ms=1000000), ctx, response_contract(ctx),
        generate=generate, record=events.append, clock=clock)
    return result, events, calls


def test_known_842_62_risk_error_is_calculated_by_host():
    receipt = calculate_candidate(wire(context(), "LONG"), context(), candidate_id="c0")
    assert receipt["eligible"]
    assert receipt["raw_per_entry_risk"][0]["risk_per_btc"] == pytest.approx(842.62)
    assert receipt["raw_risk"]["proposed_risk"] == pytest.approx(8.4262)
    assert receipt["projection"]["actual_net_pnl"] is None
    assert receipt["projection"]["targets"][0]["gross_pnl"] == pytest.approx(2.95)


@pytest.mark.parametrize("side,stop_pnl,tp_pnl", [("LONG", -5.1, 2.95), ("SHORT", -5.9, 4.55)])
def test_long_short_gross_and_partial_quantity(side, stop_pnl, tp_pnl):
    receipt = calculate_candidate(wire(context(), side), context(), candidate_id="c0")
    assert receipt["eligible"]
    p = receipt["projection"]
    assert p["gross_at_full_stop"] == pytest.approx(stop_pnl)
    assert p["targets"][0]["quantity"] == .005
    assert p["targets"][0]["gross_pnl"] == pytest.approx(tp_pnl)
    assert "NOT_PER_SIDE_FEE" in receipt["cost_semantics"]["estimated_cost_rate_meaning"]


def test_round_buffer_both_raw_effective_prices_and_hashes_preserved():
    ctx = context()
    ctx.update(execution_price_policy=dict(version="round-limit-v1"),
               limit_price_quotes=dict(bid=83400, ask=83401, captured_at=1000))
    original = wire(ctx, "LONG")
    r = calculate_candidate(original, ctx, candidate_id="c0")
    assert r["raw_proposal"]["entries"][0]["price"] == 83410
    assert r["effective_plan"]["entries"][0]["price"] == 83417.3
    assert r["effective_plan"]["take_profits"][0]["price"] == 83992.7
    assert r["raw_sha256"] != r["effective_sha256"]
    assert original == wire(ctx, "LONG")


def test_two_phase_exact_selection_no_order_and_audit():
    result, events, calls = run()
    assert result["executable"] is False and result["orders_submitted"] == 0
    assert result["selected"]["raw_proposal"] == wire(context(), "LONG")
    assert result["calls"] == len(calls) == 2
    assert [(e["kind"], e.get("phase")) for e in events][:4] == [
        ("MODEL_REQUEST", "candidate"), ("MODEL_RAW", "candidate"),
        ("CALCULATED_CANDIDATES", None), ("MODEL_REQUEST", "selection")]
    assert all(c["retry_transport"] is False and c["timeout"] <= 75 for c in calls)
    assert all(c["model"] == "gpt-6.1-sol" and c["reasoning_effort"] == "low" and c["mcp_profile"] is None for c in calls)


def test_no_candidates_wait_is_one_call():
    result, _, calls = run(candidates=[])
    assert result["selection"]["selection"] == "WAIT"
    assert result["calls"] == len(calls) == 1


def test_rejected_risk_stays_visible_but_not_selectable():
    p = wire(context(), "LONG")
    p["entries"][0]["quantity"] = 1
    result, _, _ = run(candidates=[p])
    assert result["selection"]["selection"] == "WAIT"
    r = result["candidates"]["candidates"][0]
    assert r["raw_risk"]["proposed_risk"] == pytest.approx(842.62)
    assert not r["eligible"]


@pytest.mark.parametrize("field,value", [("selection_token", "invented"), ("receipt_sha256", "bad"),
                                          ("price", 84000), ("rationale", "")])
def test_selection_tamper_rejected(field, value):
    def mutate(result, phase, _):
        if phase == 2:
            result[field] = value
    with pytest.raises(ValueError):
        run(mutate=mutate)


def test_second_failure_does_not_fallback_or_return_first_proposal():
    with pytest.raises(ValueError, match="research_model_call_failed") as e:
        run(fail_second=True)
    assert "secret" not in str(e.value)


@pytest.mark.parametrize("field,value", [("account_captured_at", None), ("account_captured_at", 879),
    ("account_captured_at", 1001), ("input_captured_at", 879), ("positions", [{"quantity": 1}]),
    ("pending_entries", [{"quantity": 1}]), ("positions", None)])
def test_missing_stale_or_nonflat_rejects_without_call(field, value):
    ctx = context()
    ctx[field] = value
    with pytest.raises(ValueError):
        research_review(dict(valid=True, as_of_ms=1000000), ctx, {},
            generate=lambda **kw: pytest.fail("must not call"), record=lambda e: None, clock=lambda: 1000)


def test_first_observation_wait_only_and_no_open_allowed():
    ctx = context()
    ctx.update(recovery_contract_version=2, recovery=dict(phase="OBSERVING", first_observation=True))
    p = wire(ctx, "LONG")
    p["recovery"] = None
    with pytest.raises(ValueError, match="research_response_contract"):
        run(ctx=ctx, candidates=[p])


def test_candidate_limit_and_no_candidate_reason():
    with pytest.raises(ValueError, match="research_response_contract"):
        run(candidates=[wire(context(), "LONG")] * 3)
    def mutate(result, phase, _):
        if phase == 1:
            result["no_candidate_reason"] = ""
    with pytest.raises(ValueError, match="research_missing_candidate_reason"):
        run(candidates=[], mutate=mutate)


@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
@pytest.mark.parametrize('alternative', ['entry_location', 'protected_remainder'])
def test_distinct_entry_alternatives_select_one_unchanged_plan(side, alternative):
    ctx = context()
    primary = wire(ctx, side)
    second = deepcopy(primary)
    if alternative == 'entry_location':
        second['entries'][0]['price'] += -40 if side == 'LONG' else 40
    else:
        second['take_profits'][0]['fraction'] = .25
    candidates = [primary, second]
    before = deepcopy(candidates)
    def choose_second(result, phase, request):
        if phase == 2:
            rows = json.loads(request['user_prompt'])['calculated_candidates']['candidates']
            assert len(rows) == 2 and all(row['eligible'] for row in rows)
            assert rows[0]['receipt_sha256'] != rows[1]['receipt_sha256']
            result['selection_token'] = 'c1:' + rows[1]['receipt_sha256']
    result, _, calls = run(ctx=ctx, candidates=candidates, mutate=choose_second)
    assert len(calls) == 2 and result['selection']['selection'] == 'c1'
    assert result['selected']['raw_proposal'] == second
    assert candidates == before
    assert result['orders_submitted'] == 0


def test_deadline_not_renewed_and_stale_account_between_calls():
    for elapsed in (76, 71):
        now = [1000.]
        ctx = context()
        if elapsed == 71:
            ctx["account_captured_at"] = 950
        def mutate(_result, phase, _kwargs):
            if phase == 1:
                now[0] += elapsed
        with pytest.raises(ValueError, match="research_deadline|research_stale"):
            run(ctx=ctx, mutate=mutate, clock=lambda: now[0])


def test_second_call_remaining_timeout_and_expiry():
    now = [1000.]
    def mutate(_result, phase, _kwargs):
        now[0] += 20
    result, _, calls = run(mutate=mutate, clock=lambda: now[0])
    assert calls[1]["timeout"] == 55
    assert result["elapsed_seconds"] == 40
    p = wire(context(), "LONG")
    p["expires_at"] = 1030
    now[0] = 1000
    with pytest.raises(ValueError, match="research_selected_expired"):
        run(candidates=[p], mutate=mutate, clock=lambda: now[0])


@pytest.mark.parametrize("mutator", [lambda p: p["entries"][0].update(price=83410.01),
    lambda p: p["entries"][0].update(quantity=.0001), lambda p: p.update(hard_stop=85000),
    lambda p: p.update(hard_stop=float("nan"))])
def test_invalid_candidate_fails_closed(mutator):
    p = wire(context(), "LONG")
    mutator(p)
    try:
        r = calculate_candidate(p, context(), candidate_id="c0")
    except ValueError:
        return
    assert not r["eligible"]


def test_missing_step_is_not_assumed_and_below_minimum_tp_is_zero_not_rounded_up():
    ctx = context()
    del ctx["quantity_step"]
    assert not calculate_candidate(wire(context(), "LONG"), ctx, candidate_id="c0")["eligible"]
    p = wire(context(), "LONG")
    p["take_profits"][0]["fraction"] = .01
    target = calculate_candidate(p, context(), candidate_id="c0")["projection"]["targets"][0]
    assert target["quantity"] == 0 and target["below_minimum"]


def test_input_size_mutation_and_production_capture_rejected():
    ctx = context()
    ctx["large"] = "x" * 100001
    with pytest.raises(ValueError, match="research_input_size"):
        run(ctx=ctx)
    ctx = context()
    def mutate(_result, _phase, _kwargs):
        ctx["initial_equity"] = 99999
    with pytest.raises(ValueError, match="research_input_mutated"):
        run(ctx=ctx, mutate=mutate)
    from live.scenario_recovery import _wire
    token = _wire.set((None, None, None))
    try:
        with pytest.raises(ValueError, match="production_wire_capture_forbidden"):
            run()
    finally:
        _wire.reset(token)


def test_recorder_cannot_mutate_selected_receipts():
    events = []
    ctx = context()
    def record(event):
        events.append(deepcopy(event))
        if event["kind"] == "CALCULATED_CANDIDATES":
            event["body"]["candidates"][0]["raw_proposal"]["hard_stop"] = 1
    def generate(**kw):
        data = json.loads(kw["user_prompt"])
        if "calculated_candidates" not in data:
            answer = dict(wait_proposal=wire(ctx), candidates=[wire(ctx, "LONG")], no_candidate_reason="")
        else:
            c = data["calculated_candidates"]["candidates"][0]
            answer = dict(selection_token=f"c0:{c['receipt_sha256']}", rationale="판단 근거")
        return SimpleNamespace(text=json.dumps(answer))
    result = research_review(dict(valid=True, as_of_ms=1000000), ctx, response_contract(ctx),
        generate=generate, record=record, clock=lambda: 1000)
    assert result["selected"]["raw_proposal"]["hard_stop"] == 82900


def test_empty_category_cannot_claim_economic_rejection_or_false_first_observation():
    for category in ("ECONOMIC_REJECTION", "FIRST_OBSERVATION", "CANDIDATES_PROVIDED"):
        def mutate(result, _phase, _kwargs):
            result["no_candidate_category"] = category
        with pytest.raises(ValueError):
            run(candidates=[], mutate=mutate)
    ctx = context()
    ctx.update(recovery_contract_version=2, recovery=dict(phase="OBSERVING", first_observation=True))
    result, _, _ = run(ctx=ctx, candidates=[])
    assert result["candidates"]["no_candidate_category"] == "FIRST_OBSERVATION"


def test_per_btc_reward_and_ratio_are_host_calculated():
    r = calculate_candidate(wire(context(), "LONG"), context(), candidate_id="c0")
    unit = r["effective_per_entry_risk"][0]
    assert unit["stop_distance_per_btc"] == 510
    assert unit["cost_reserve_per_btc"] == 166.82
    assert unit["stop_slippage_reserve_per_btc"] == 165.8
    assert unit["risk_per_btc"] == pytest.approx(842.62)
    target = r["projection"]["targets"][0]
    assert target["gross_reward_per_btc"] == 590
    assert target["gross_reward_to_reserved_risk_ratio"] == pytest.approx(590 / 842.62)


def test_calculator_nonflat_rejects_and_rejected_risk_preserves_cost_semantics():
    ctx = context()
    ctx["positions"] = [dict(price=83410, quantity=.01)]
    with pytest.raises(ValueError, match="research_flat_only"):
        calculate_candidate(wire(ctx, "LONG"), ctx, candidate_id="c0")
    p = wire(context(), "LONG")
    p["entries"][0]["quantity"] = 1
    r = calculate_candidate(p, context(), candidate_id="c0")
    assert r["rejection"] == "risk budget exceeded"
    assert r["cost_semantics"]["actual_future_fees"] is None


def test_slow_recording_reduces_sent_timeout_and_blocks_late_final_result():
    now = [1000.]
    events, calls = [], []
    ctx = context()
    def record(event):
        events.append(event)
        if event["kind"] == "MODEL_REQUEST":
            now[0] += 10
        if event["kind"] == "RESEARCH_RESULT":
            now[0] += 80
    def generate(**kw):
        calls.append(kw)
        return SimpleNamespace(text=json.dumps(dict(wait_proposal=wire(ctx), candidates=[],
             no_candidate_reason="구조 근거 부족")))
    with pytest.raises(ValueError, match="research_deadline"):
        research_review(dict(valid=True, as_of_ms=1000000), ctx, response_contract(ctx),
            generate=generate, record=record, clock=lambda: now[0])
    assert calls[0]["timeout"] == 65
    raw = next(e for e in events if e["kind"] == "MODEL_RAW")
    assert raw["actual_request"] == calls[0]
    assert raw["prepared_request_sha256"] != raw["actual_request_sha256"]
    assert events[-1]["pending_post_record_time_check"]


def test_receipt_hash_binds_snapshot_even_when_input_id_and_plan_are_same():
    ctx = context()
    hashes = []
    def generate(**kw):
        return SimpleNamespace(text=json.dumps(dict(wait_proposal=wire(ctx), candidates=[],
             no_candidate_reason="구조 근거 부족")))
    for snapshot in (dict(valid=True, as_of_ms=1000000), dict(valid=True, as_of_ms=1000000, extra=1)):
        result = research_review(snapshot, ctx, response_contract(ctx), generate=generate,
                                 record=lambda e: None, clock=lambda: 1000)
        hashes.append(result["selected"]["receipt_sha256"])
    assert hashes[0] != hashes[1]


@pytest.mark.parametrize("raw", ["x" * 24001, '{"a":1,"a":2}', '{"a":NaN}', '[]'])
def test_bad_or_oversize_raw_response_never_triggers_second_call(raw):
    calls = []
    ctx = context()
    def generate(**kw):
        calls.append(kw)
        return SimpleNamespace(text=raw)
    with pytest.raises(ValueError):
        research_review(dict(valid=True, as_of_ms=1000000), ctx, response_contract(ctx),
            generate=generate, record=lambda e: None, clock=lambda: 1000)
    assert len(calls) == 1


def test_failed_required_request_record_prevents_any_model_call():
    def record(_event):
        raise OSError("disk unavailable")
    ctx = context()
    with pytest.raises(OSError):
        research_review(dict(valid=True, as_of_ms=1000000), ctx, response_contract(ctx),
            generate=lambda **kw: pytest.fail("unrecorded call"), record=record, clock=lambda: 1000)


def test_both_directions_can_be_proposed_without_code_choosing_prices():
    proposals = [wire(context(), "LONG"), wire(context(), "SHORT")]
    result, _, _ = run(candidates=proposals)
    assert [r["raw_proposal"] for r in result["candidates"]["candidates"]] == proposals
    assert all(r["eligible"] for r in result["candidates"]["candidates"])


def test_minimum_notional_is_preserved():
    ctx = context()
    ctx["minimum_notional"] = 1000
    result = calculate_candidate(wire(ctx, "LONG"), ctx, candidate_id="c0")
    assert not result["eligible"] and result["rejection"] == "entry_below_minimum"


def test_original_stricter_input_age_not_extended():
    ctx = context()
    ctx["max_input_age_seconds"] = 30
    now = [1000]
    def mutate(_result, _phase, _kwargs):
        now[0] += 20
    with pytest.raises(ValueError, match="research_stale"):
        run(ctx=ctx, mutate=mutate, clock=lambda: now[0])


def test_selection_token_schema_binds_id_and_hash_without_free_hash_field():
    result, _, calls = run()
    schema = calls[1]["response_schema"]
    assert set(schema["properties"]) == {"selection_token", "rationale"}
    tokens = schema["properties"]["selection_token"]["enum"]
    assert f"c0:{result['selected']['receipt_sha256']}" in tokens
    assert all(len(token.partition(":")[2]) == 64 for token in tokens)
    def mutate(answer, phase, _kwargs):
        if phase == 2:
            answer["selection_token"] = "WAIT:" + answer["selection_token"].partition(":")[2]
    with pytest.raises(ValueError, match="research_response_contract"):
        run(mutate=mutate)


def test_retained_row13_hash_typo_is_not_an_enum_value():
    from live.scenario_contract import _matches
    correct = "cdbfdf4d4a1fbec4a5895f1697be512f65643ea0977ce482f969202f503f3a0c"
    observed_typo = "cdbfdf4d1a4fbec4a5895f1697be512f65643ea0977ce482f969202f503f3a0c"
    schema = dict(type="string", enum=["WAIT:" + correct])
    assert _matches("WAIT:" + correct, schema)
    assert not _matches("WAIT:" + observed_typo, schema)


def test_market_account_candidates_preserved_and_each_phase_uses_its_own_schema():
    from live.scenario_model_input import restore_model_input
    result, _, calls = run()
    payloads = [json.loads(c["user_prompt"]) for c in calls]
    restored = [restore_model_input(p) for p in payloads]
    assert restored[0]["market_snapshot"] == restored[1]["market_snapshot"]
    contexts = [deepcopy(p['contract_context']) for p in restored]
    displays = [ctx.pop('entry_display') for ctx in contexts]
    assert contexts[0] == contexts[1]
    assert displays[0]['confirmed_close_tail'] == displays[1]['confirmed_close_tail']
    assert 'candidate_price_order' not in displays[0]
    assert displays[1]['candidate_price_order'][0][:2] == [
        result['candidates']['candidates'][0]['id'], result['candidates']['candidates'][0]['receipt_sha256']]
    assert restored[1]["calculated_candidates"] == result["candidates"]
    assert all("research_response_contract" not in p for p in payloads)
    assert restored[0]["response_contract"] == response_contract(context())
    assert set(calls[0]["response_schema"]["properties"]) == {
        "wait_proposal", "candidates", "no_candidate_reason"}
    assert restored[1]["response_contract"] == calls[1]["response_schema"]
    assert "wait_proposal" not in calls[1]["response_schema"]["properties"]


def test_retained_row17_size_boundary_without_dropping_evidence_or_raising_cap():
    from live.scenario_calculated_review import _encoded, _model_payload
    from live.scenario_model_input import restore_model_input
    # Row17's retained base was 91,641 bytes, obsolete contract 7,437 bytes.
    # Pad neutral fixture evidence to reproduce that boundary, not its market.
    ctx = context()
    ctx["retained_evidence"] = ""
    snap = dict(valid=True, as_of_ms=1000000)
    contract = response_contract(ctx)
    contract["fixture_rule"] = ""
    contract["fixture_rule"] = "x" * (7437 - len(_encoded(contract).encode()))
    ctx["retained_evidence"] = "x" * (91641 - len(_encoded(_model_payload(snap, ctx, contract)).encode()))
    calls = []
    p = wire(ctx, "LONG")
    p["rationale"] = "x" * 2000
    def generate(**kw):
        calls.append(kw)
        if len(calls) == 1:
            answer = dict(wait_proposal=wire(ctx), candidates=[p],
                 no_candidate_reason="")
        else:
            token = kw["response_schema"]["properties"]["selection_token"]["enum"][0]
            answer = dict(selection_token=token, rationale="프로토콜 검증")
        return SimpleNamespace(text=json.dumps(answer))
    result = research_review(snap, ctx, contract, generate=generate, record=lambda e: None, clock=lambda: 1000)
    assert len(calls[0]["user_prompt"].encode()) == 91641
    assert len(calls[1]["user_prompt"].encode()) < 100000
    first_payload = json.loads(calls[0]["user_prompt"])
    old_second = dict(first_payload, calculated_candidates=result["candidates"],
                      research_response_contract=calls[1]["response_schema"])
    assert len(_encoded(old_second).encode()) > 100000
    second = restore_model_input(json.loads(calls[1]["user_prompt"]))
    assert second["contract_context"]["retained_evidence"] == ctx["retained_evidence"]
    assert second["calculated_candidates"] == result["candidates"]


def holding(side="LONG"):
    ctx = context()
    ctx.update(scenario_id="active", revision=3, side=side, accounting_status="confirmed",
        positions=[dict(price=83000, quantity=.06)], previous_hard_stop=82500 if side == "LONG" else 83500,
        mark_price=83400 if side == "LONG" else 82600,
        current_plan=dict(side=side, entries=[dict(price=83000, quantity=.1)],
                          rationale="Do not recycle old history"),
        target_status=[dict(id="old_tp", filled_quantity=.04, status="TERMINAL")],
        realized_loss=3, fees_paid=2, funding_paid=.5)
    return ctx


def holding_wire(ctx, action="ADJUST"):
    p = wire(ctx)
    p["action"] = action
    if action == "ADJUST":
        p.update(side=ctx["side"], hard_stop=ctx["previous_hard_stop"],
            chase=dict(max_bps=0, max_reprices=0),
            take_profits=[dict(id="new_tp", price=84000 if ctx["side"] == "LONG" else 82000, fraction=.5)])
    if (ctx.get("stop_policy") or {}).get("version") == 1:
        p["stop_revision"] = None
    return p


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
def test_holding_tp_uses_remaining_not_original_pre_partial_exit_size(side):
    ctx = holding(side)
    r = calculate_candidate(holding_wire(ctx), ctx, candidate_id="c0")
    assert r["eligible"]
    actual = r["projection"]["confirmed_remaining"]
    assert actual["quantity"] == .06
    assert actual["targets"][0]["quantity"] == .03
    assert actual["targets"][0]["gross_pnl"] == 30
    assert r["raw_risk"]["consumed"] == 5.5
    assert r["projection"]["hypothetical_all_fills"]["quantity"] == .06
    assert "old_tp" not in [x["id"] for x in actual["targets"]]


def test_partially_filled_entry_and_cancellation_keep_only_pending_remainder_reserved():
    ctx = holding()
    # A .1 BTC entry has filled .04; only its .06 remainder is pending.
    ctx.update(positions=[dict(price=83000, quantity=.04)],
        pending_entries=[dict(id="original_entry", price=83000, quantity=.06)])
    p = holding_wire(ctx)
    p["cancel_entry_ids"] = ["original_entry"]
    r = calculate_candidate(p, ctx, candidate_id="c0")
    assert r["eligible"]
    assert r["projection"]["confirmed_remaining"]["quantity"] == .04
    assert r["projection"]["confirmed_remaining"]["targets"][0]["quantity"] == .02
    assert r["projection"]["hypothetical_all_fills"]["quantity"] == .1
    assert r["raw_risk"]["remaining_risk"] == pytest.approx(.1 * (500 + 166 + 165))
    without_cancel = calculate_candidate(dict(p, cancel_entry_ids=[]), ctx, candidate_id="c1")
    assert r["raw_risk"] == without_cancel["raw_risk"]


def test_pending_add_new_incremental_entry_is_hypothetical_not_current_position():
    ctx = holding()
    ctx["pending_entries"] = [dict(id="pending", price=83100, quantity=.01)]
    p = holding_wire(ctx)
    p["entries"] = [dict(id="new", price=83200, quantity=.02)]
    r = calculate_candidate(p, ctx, candidate_id="c0")
    assert r["eligible"]
    assert r["projection"]["confirmed_remaining"]["quantity"] == .06
    assert r["projection"]["hypothetical_all_fills"]["quantity"] == .09
    assert r["projection"]["hypothetical_all_fills"]["targets"][0]["quantity"] == .045
    assert r["raw_risk"]["consumed"] == 5.5
    assert r["raw_risk"]["proposed_risk"] == pytest.approx(.02 * (700 + 166.4 + 165))


def test_pending_only_active_is_not_flat_and_does_not_invent_confirmed_position():
    ctx = holding()
    ctx.update(positions=[], pending_entries=[dict(id="pending", price=83000, quantity=.04)])
    r = calculate_candidate(holding_wire(ctx), ctx, candidate_id="c0")
    assert r["eligible"]
    assert r["projection"]["confirmed_remaining"]["status"] == "NO_POSITION_IN_THIS_ASSUMPTION"
    assert r["projection"]["hypothetical_all_fills"]["quantity"] == .04


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
def test_holding_tightening_and_bounded_restoration_use_host_policy(side):
    ctx = holding(side)
    p = holding_wire(ctx)
    p["hard_stop"] = 82800 if side == "LONG" else 83200
    assert calculate_candidate(p, ctx, candidate_id="c0")["eligible"]
    p["hard_stop"] = 82400 if side == "LONG" else 83600
    rejected = calculate_candidate(p, ctx, candidate_id="c0")
    assert not rejected["eligible"] and "stop widening forbidden" in rejected["rejection"]
    ctx["stop_policy"] = dict(version=1, anchor_available=True, widening_eligible=True,
                               initial_hard_stop=82000 if side == "LONG" else 84000)
    p["stop_revision"] = dict(reason="가격 구조 유지", structure_reference="30m MA35", input_id=ctx["input_id"])
    assert calculate_candidate(p, ctx, candidate_id="c0")["eligible"]
    p["hard_stop"] = 81900 if side == "LONG" else 84100
    assert not calculate_candidate(p, ctx, candidate_id="c0")["eligible"]


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
def test_exit_mark_projection_is_estimate_and_cancellation_does_not_release_risk(side):
    ctx = holding(side)
    ctx["pending_entries"] = [dict(id="pending", price=83000, quantity=.01)]
    p = holding_wire(ctx, "EXIT")
    p["cancel_entry_ids"] = ["pending"]
    r = calculate_candidate(p, ctx, candidate_id="c0")
    assert r["eligible"] and r["effective_plan"]["action"] == "EXIT"
    assert r["projection"]["estimated_gross_at_mark"] == 24
    assert r["projection"]["confirmed_quantity"] == .06
    assert not r["projection"]["settlement_confirmed"]
    assert not r["projection"]["pending_cancellation_confirmed"]
    assert r["current_reserved_risk"]["remaining_risk"] > 0
    assert r["projection"]["actual_net_pnl"] is None


def test_unknown_accounting_does_not_become_zero_or_block_core_exit():
    ctx = holding()
    ctx.update(accounting_status="pending", fees_paid=None, realized_loss=None, funding_paid=None)
    r = calculate_candidate(holding_wire(ctx, "EXIT"), ctx, candidate_id="c0")
    assert r["eligible"]
    assert r["projection"]["status"] == "UNAVAILABLE"
    assert r["current_reserved_risk"]["status"] == "UNAVAILABLE"
    assert r["projection"]["actual_net_pnl"] is None
    assert not calculate_candidate(holding_wire(ctx), ctx, candidate_id="c0")["eligible"]


def test_projection_missing_mark_or_side_stays_unknown_without_rejecting_exit():
    for missing in ("mark_price", "side"):
        ctx = holding()
        del ctx[missing]
        r = calculate_candidate(holding_wire(ctx, "EXIT"), ctx, candidate_id="c0")
        assert r["eligible"] and r["projection"]["status"] == "UNAVAILABLE"


def test_realized_profit_does_not_replenish_budget_and_opposite_adjustment_rejected():
    ctx = holding()
    ctx["realized_net_profit"] = 1000
    p = holding_wire(ctx)
    p["entries"] = [dict(id="new", price=83200, quantity=1)]
    assert not calculate_candidate(p, ctx, candidate_id="c0")["eligible"]
    p = holding_wire(ctx)
    p.update(side="SHORT", hard_stop=83500, take_profits=[dict(id="tp", price=82000, fraction=.5)])
    r = calculate_candidate(p, ctx, candidate_id="c0")
    assert not r["eligible"] and "opposite side" in r["rejection"]


def test_holding_research_two_phase_preserves_candidates_and_excludes_flat_prompt():
    from live.scenario_llm import FLAT_ENTRY_PROMPT
    ctx = holding()
    calls = []
    def generate(**kw):
        calls.append(kw)
        if len(calls) == 1:
            answer = dict(wait_proposal=holding_wire(ctx, "WAIT"),
                candidates=[holding_wire(ctx), holding_wire(ctx, "EXIT")],
                 no_candidate_reason="")
        else:
            token = next(x for x in kw["response_schema"]["properties"]["selection_token"]["enum"] if x.startswith("c1:"))
            answer = dict(selection_token=token, rationale="추세 이탈 근거로 전량 청산 검토")
        return SimpleNamespace(text=json.dumps(answer))
    result = research_review(dict(valid=True, as_of_ms=1000000), ctx, response_contract(ctx),
        generate=generate, record=lambda e: None, clock=lambda: 1000)
    assert result["selected"]["raw_proposal"]["action"] == "EXIT"
    assert not result["executable"]
    assert FLAT_ENTRY_PROMPT not in calls[0]["system_prompt"]
    assert calls[0]["response_schema"]["properties"]["candidates"]["items"]["properties"]["action"]["enum"] == ["ADJUST", "EXIT"]
    presented = json.loads(calls[0]["user_prompt"])["contract_context"]
    assert "rationale" not in presented["current_plan"]
    assert presented["positions"] == ctx["positions"]


def test_multiple_remaining_lots_target_projection_is_explicit_pro_rata_not_realized():
    ctx = holding()
    ctx["positions"] = [dict(price=83000, quantity=.02), dict(price=83200, quantity=.04)]
    r = calculate_candidate(holding_wire(ctx), ctx, candidate_id="c0")
    assert r["eligible"]
    p = r["projection"]["confirmed_remaining"]
    assert p["weighted_entry"] == pytest.approx(83133.33333333333)
    assert p["targets"][0]["quantity"] == .03
    assert p["targets"][0]["gross_pnl"] == pytest.approx(26)
    assert p["cost_basis"] == "QUANTITY_WEIGHTED_REMAINING_LOTS_PRO_RATA_ESTIMATE"
    assert p["future_target_fills_confirmed"] is False


def test_active_wait_preserves_lifecycle_without_flat_facts_or_old_rationale():
    from live.scenario_llm import FLAT_ENTRY_PROMPT
    ctx = holding()
    calls = []
    def generate(**kw):
        calls.append(kw)
        return SimpleNamespace(text=json.dumps(dict(wait_proposal=holding_wire(ctx, "WAIT"),
            candidates=[],  no_candidate_reason="기존 보호 유지 근거")))
    result = research_review(dict(valid=True, as_of_ms=1000000), ctx, response_contract(ctx),
        generate=generate, record=lambda e: None, clock=lambda: 1000)
    assert result["calls"] == 1 and result["selection"]["selection"] == "WAIT"
    assert FLAT_ENTRY_PROMPT not in calls[0]["system_prompt"]
    presented = json.loads(calls[0]["user_prompt"])["contract_context"]
    assert "current_primary_frame_facts" not in presented
    assert "rationale" not in presented["current_plan"]


def test_wait_schema_expresses_existing_inactive_protection_rules_only():
    ctx = holding()
    ctx["stop_policy"] = dict(version=1)
    calls = []
    def generate(**kw):
        calls.append(kw)
        return SimpleNamespace(text=json.dumps(dict(wait_proposal=holding_wire(ctx, "WAIT"),
            candidates=[],  no_candidate_reason="보호 유지")))
    research_review(dict(valid=True, as_of_ms=1000000), ctx, response_contract(ctx),
        generate=generate, record=lambda e: None, clock=lambda: 1000)
    properties = calls[0]["response_schema"]["properties"]
    wait = properties["wait_proposal"]["properties"]
    for field in ("side", "hard_stop", "chase", "stop_revision"):
        assert wait[field] == {"type": "null"}
    for field in ("entries", "take_profits", "partial_stops"):
        assert wait[field]["maxItems"] == 0
    candidate = properties["candidates"]["items"]["properties"]
    assert candidate["side"]["enum"] == ["LONG", "SHORT", None]
    assert "maxItems" not in candidate["take_profits"]


@pytest.mark.parametrize("field,value", [("side", "LONG"), ("hard_stop", 82970),
    ("chase", dict(max_bps=0, max_reprices=0)),
    ("entries", [dict(id="e", price=83000, quantity=.001)]),
    ("take_profits", [dict(id="tp", price=84000, fraction=1)]),
    ("partial_stops", [dict(id="sl", price=82900, fraction=.5)]),
    ("stop_revision", dict(reason="구조", structure_reference="30m", input_id="research-1"))])
def test_invalid_wait_output_is_rejected_by_schema_before_second_call(field, value):
    ctx = holding()
    ctx["stop_policy"] = dict(version=1)
    calls = []
    def generate(**kw):
        calls.append(kw)
        wait = holding_wire(ctx, "WAIT")
        wait[field] = value
        return SimpleNamespace(text=json.dumps(dict(wait_proposal=wait, candidates=[],
             no_candidate_reason="보호 유지")))
    with pytest.raises(ValueError, match="research_response_contract"):
        research_review(dict(valid=True, as_of_ms=1000000), ctx, response_contract(ctx),
            generate=generate, record=lambda e: None, clock=lambda: 1000)
    assert len(calls) == 1


def test_valid_holding_cancel_only_wait_keeps_current_protection_and_metadata():
    ctx = holding()
    ctx.update(pending_entries=[dict(id="pending", price=83100, quantity=.01)],
               review_contract_version=1, recovery_contract_version=2)
    original = deepcopy(ctx)
    wait = holding_wire(ctx, "WAIT")
    wait.update(cancel_entry_ids=["pending"], review=dict(conditions=[], acknowledgements=[]), recovery=None)
    result = research_review(dict(valid=True, as_of_ms=1000000), ctx, response_contract(ctx),
        generate=lambda **kw: SimpleNamespace(text=json.dumps(dict(wait_proposal=wait, candidates=[],
             no_candidate_reason="추가 진입 취소, 기존 보호 유지"))),
        record=lambda e: None, clock=lambda: 1000)
    assert ctx == original
    assert result["selected"]["raw_proposal"] == wait
    assert result["selected"]["raw_proposal"]["cancel_entry_ids"] == ["pending"]
    assert result["selected"]["raw_proposal"]["review"] == wait["review"]
    assert result["executable"] is False and result["orders_submitted"] == 0
    assert ctx["previous_hard_stop"] == original["previous_hard_stop"]
    assert ctx["positions"] == original["positions"]
