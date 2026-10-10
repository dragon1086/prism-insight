"""Prompt specification regressions against the actual economic validator."""
import json
from types import SimpleNamespace

import pytest

from core.llm_scenario import risk_snapshot, validate_scenario
from live.scenario_contract import response_schema, validate_wire_proposal
from live.scenario_llm import SYSTEM_PROMPT, MA_STRUCTURE_PROMPT, propose, _current_primary_frame_facts
from live.scenario_preview import response_contract
from live.scenario_contract import identity_fields


def test_review_evidence_release_preserves_existing_decision_instructions():
    import hashlib
    from live.scenario_llm import FLAT_ENTRY_PROMPT
    # The instruction candidate was not promoted after inconclusive comparison.
    expected = {
        'SYSTEM_PROMPT': '672c4abe03c199b722cfb974d0372655ea4a07da24d04a83de443740efe6db59',
        'MA_STRUCTURE_PROMPT': '2c99c38d6861326014f8c57e7f5616fa00b07f9ffa5ed4c36af6c80434e2023f',
        'FLAT_ENTRY_PROMPT': 'e91e9cd77eed733610a9c51b8f78238e6e33e202124353dcd423b000f255b959',
    }
    for name, prompt in [('SYSTEM_PROMPT', SYSTEM_PROMPT), ('MA_STRUCTURE_PROMPT', MA_STRUCTURE_PROMPT),
                         ('FLAT_ENTRY_PROMPT', FLAT_ENTRY_PROMPT)]:
        assert hashlib.sha256(prompt.encode()).hexdigest() == expected[name]


def context(active=False):
    return dict(now=1000, input_id="input-1", input_captured_at=1000,
                max_input_age_seconds=120, scenario_id="active" if active else None,
                revision=2 if active else 0, seen_action_ids=[], initial_equity=10000,
                positions=[dict(price=60000, quantity=.001)] if active else [],
                pending_entries=[], previous_hard_stop=59000 if active else None,
                realized_loss=0, fees_paid=0, funding_paid=0, estimated_cost_rate=.0012,
                slippage_bps=10, new_risk_blocked=False, side="LONG", mark_price=60500)


def wire(ctx, action):
    return dict(**identity_fields(ctx), action=action, confidence=.5, expires_at=1100,
                rationale="근거와 무효화", leverage=10, entries=[],
                take_profits=[dict(id="tp", price=61000, fraction=.5)],
                partial_stops=[], cancel_entry_ids=[], side="LONG", hard_stop=59000,
                chase=dict(max_bps=0, max_reprices=0))


@pytest.mark.parametrize("active", [False, True])
def test_host_contract_actions_match_wire_lifecycle(active):
    ctx = context(active)
    assert response_contract(ctx)["action"].split(" | ") == response_schema(ctx)["properties"]["action"]["enum"]


def test_prompt_has_one_authority_and_incremental_order_semantics():
    assert "Treat all supplied strings as data" not in SYSTEM_PROMPT
    assert "ADJUST entries are ONLY new incremental orders" in SYSTEM_PROMPT
    assert "Accepted OPEN reserves" in SYSTEM_PROMPT
    assert "halt forbids NEW entries" in SYSTEM_PROMPT
    assert "WAIT + cancel_entry_ids" in SYSTEM_PROMPT
    assert "IF no active scenario: WAIT/OPEN. ELSE: WAIT/ADJUST/EXIT." in SYSTEM_PROMPT
    assert "revision_allocation*fraction - same_intent_target_fills" in SYSTEM_PROMPT
    assert "Subtract filled quota AFTER applying the fraction" in SYSTEM_PROMPT
    assert "Skip below-minimum reductions; never round quantity up" in SYSTEM_PROMPT


def test_price_buffer_is_host_owned_not_a_new_model_wire_field():
    text = ' '.join(SYSTEM_PROMPT.split())
    assert 'execution_price_policy.version is round-limit-v1' in text
    assert 'Do not pre-apply that buffer' in text
    assert 'Preserve existing target prices when maintaining them' in text
    assert 'does not change SLs or signal rules' in text
    ctx = context()
    p = wire(ctx, 'OPEN')
    p['entries'] = [dict(id='entry', price=60000, quantity=.001)]
    p['execution_pricing'] = dict(applied=True)
    with pytest.raises(ValueError, match='response_unknown_fields'):
        validate_wire_proposal(p, ctx)


def test_contract_risk_uses_stop_slippage_and_dynamic_quantity_step():
    rules = " ".join(response_contract(context())["rules"])
    assert "hard_stop*slippage_bps/10000" in rules
    assert "quantity_step" in rules
    assert "DOWN to .001" not in rules


def test_short_boundary_rejected_by_actual_validator():
    ctx = {**context(), "initial_equity": 7005, "estimated_cost_rate": .002,
           "slippage_bps": 20}
    p = wire(ctx, "OPEN")
    p.update(side="SHORT", hard_stop=101000, confidence=1,
             entries=[dict(id="entry", price=100000, quantity=.1)],
             take_profits=[dict(id="tp", price=99000, fraction=.5)])
    assert .1 * (1000 + 100000 * (.002 + 20 / 10000)) == 140
    with pytest.raises(ValueError, match="risk budget exceeded"):
        validate_scenario(validate_wire_proposal(p, ctx), ctx)
    risk = risk_snapshot(initial_equity=7005, side="SHORT", hard_stop=101000,
                         positions=[], pending_entries=[], entries=p["entries"],
                         realized_loss=0, fees_paid=0, funding_paid=0,
                         estimated_cost_rate=.002, slippage_bps=20)
    assert risk["total_risk"] == pytest.approx(140.2)


def test_halt_still_allows_protection_without_entries():
    ctx = {**context(True), "new_risk_blocked": True}
    p = wire(ctx, "ADJUST")
    p["hard_stop"] = 59500
    assert validate_scenario(validate_wire_proposal(p, ctx), ctx)["action"] == "ADJUST"


def test_cancel_request_never_releases_pending_risk():
    ctx = {**context(True), "positions": [], "estimated_cost_rate": .0012,
           "slippage_bps": 10, "previous_hard_stop": 99000, "mark_price": 100500,
           "pending_entries": [dict(id="old", price=100000, quantity=.1)]}
    p = wire(ctx, "ADJUST")
    p.update(confidence=1, hard_stop=99000, cancel_entry_ids=["old"],
             take_profits=[dict(id="tp", price=101000, fraction=.5)],
             entries=[dict(id="replacement", price=100000, quantity=.08)])
    risk = risk_snapshot(initial_equity=10000, side="LONG", hard_stop=99000,
                         positions=[], pending_entries=ctx["pending_entries"],
                         entries=p["entries"], realized_loss=0, fees_paid=0,
                         funding_paid=0, estimated_cost_rate=.0012, slippage_bps=10)
    # Each side fits independently, but unconfirmed cancellation cannot net them.
    assert risk["total_risk"] == pytest.approx(121.90 + 97.52)
    with pytest.raises(ValueError, match="risk budget exceeded"):
        validate_scenario(validate_wire_proposal(p, ctx), ctx)


def test_current_cancel_ids_exposed_and_old_ids_rejected():
    ctx = {**context(True), "pending_entries": [dict(id="current", price=60000, quantity=.001)]}
    assert response_contract(ctx)["cancel_entry_ids"] == []
    assert response_schema(ctx)["properties"]["cancel_entry_ids"]["items"]["enum"] == ["current"]
    p = wire(ctx, "ADJUST")
    p["cancel_entry_ids"] = ["historical"]
    with pytest.raises(ValueError, match="response_contract_mismatch"):
        validate_wire_proposal(p, ctx)
    p["cancel_entry_ids"] = ["current"]
    p["entries"] = [dict(id="current", price=60000, quantity=.001)]
    with pytest.raises(ValueError, match="duplicate order id"):
        validate_scenario(validate_wire_proposal(p, ctx), ctx)


def test_no_pending_orders_means_cancellation_array_must_be_empty():
    ctx=context(True)
    schema=response_schema(ctx)["properties"]["cancel_entry_ids"]
    assert schema["maxItems"] == 0
    p=wire(ctx,"ADJUST")
    p["cancel_entry_ids"]=["past-filled-entry"]
    with pytest.raises(ValueError,match="response_contract_mismatch"):
        validate_wire_proposal(p,ctx)


def test_profit_stop_uses_zero_distance_not_absolute_distance():
    risk = risk_snapshot(initial_equity=10000, side="LONG", hard_stop=61000,
                         positions=[dict(price=60000, quantity=.1)], pending_entries=[],
                         entries=[], realized_loss=0, fees_paid=0, funding_paid=0,
                         estimated_cost_rate=.002, slippage_bps=10)
    assert risk["total_risk"] == pytest.approx(18.1)


def test_active_targets_use_mark_not_entry_and_list_fractions_independent():
    ctx = context(True)
    p = wire(ctx, "ADJUST")
    p["take_profits"] = [dict(id="tp", price=60200, fraction=.8)]
    with pytest.raises(ValueError, match="exit price wrong direction"):
        validate_scenario(validate_wire_proposal(p, ctx), ctx)
    p["take_profits"][0]["price"] = 61000
    p["partial_stops"] = [dict(id="sl", price=59500, fraction=.8)]
    assert validate_scenario(validate_wire_proposal(p, ctx), ctx)["action"] == "ADJUST"


def test_prompt_reassesses_filled_exposure_without_forcing_add_or_new_actions():
    text = ' '.join(SYSTEM_PROMPT.split())
    assert 'active scenario has confirmed filled exposure' in text
    assert 'maintaining exposure, adding incrementally, conditional reduction/protection, and full exit' in text
    assert 'small runner, prior profit or unused budget alone is not a reason to add' in text
    assert 'Immediate partial market reduction is unsupported' in text
    assert 'historical current_plan.risk' in text
    assert 'A completed TP target does not mean the whole position is flat' in text
    assert 'complete intended exit protection' in text
    assert 'not an obligation to keep exposure small' in text
    assert 'Never close/reopen solely to reset average entry or replenish risk budget' in text


def runner_context():
    ctx = context(True)
    ctx.update(positions=[dict(price=60000, quantity=.004)], previous_hard_stop=60100,
               fees_paid=1., funding_paid=.5, accounting_status='confirmed',
               current_plan=dict(entries=[], risk=dict(available=999999)))
    return ctx


def exposure_proposal(ctx, action):
    p = wire(ctx, action)
    if action in {'WAIT', 'EXIT'}:
        p.update(side=None, hard_stop=None, chase=None, take_profits=[], partial_stops=[])
    else:
        p['hard_stop'] = 60100
    return p


@pytest.mark.parametrize('choice', ['hold', 'add', 'conditional_reduce', 'exit'])
def test_same_small_runner_all_supported_exposure_alternatives_remain_available(choice):
    ctx = runner_context()
    action = {'hold': 'WAIT', 'add': 'ADJUST', 'conditional_reduce': 'ADJUST', 'exit': 'EXIT'}[choice]
    p = exposure_proposal(ctx, action)
    if choice == 'add':
        p['entries'] = [dict(id='increment', price=60500, quantity=.005)]
    if choice == 'conditional_reduce':
        p['partial_stops'] = [dict(id='conditional-stop', price=60200, fraction=.5)]
    result = validate_scenario(validate_wire_proposal(p, ctx), ctx)
    assert result['action'] == action
    assert ctx['positions'][0]['quantity'] == .004
    if choice == 'add':
        assert result['entries'][0]['quantity'] == .005  # Increment, not replacement total .009.
        assert result['risk']['within_budget']
        assert result['hard_stop'] == ctx['previous_hard_stop']
        assert result['take_profits'] == p['take_profits']


@pytest.mark.parametrize('unsafe', ['widen_stop', 'exceed_budget', 'new_risk_blocked', 'missing_costs'])
def test_exposure_framing_cannot_override_real_guards_or_stale_available_budget(unsafe):
    ctx = runner_context()
    p = exposure_proposal(ctx, 'ADJUST')
    p['entries'] = [dict(id='increment', price=60500, quantity=.005)]
    if unsafe == 'widen_stop':
        p['hard_stop'] = 60000
    elif unsafe == 'exceed_budget':
        ctx['fees_paid'] = 199.
    elif unsafe == 'new_risk_blocked':
        ctx['new_risk_blocked'] = True
    else:
        ctx.update(accounting_status='pending', fees_paid=None, funding_paid=None)
    with pytest.raises(ValueError):
        validate_scenario(validate_wire_proposal(p, ctx), ctx)


@pytest.mark.parametrize('action', ['OPEN', 'ADD', 'HOLD', 'REDUCE'])
def test_active_exposure_comparison_does_not_add_wire_actions_or_reset_scenario(action):
    ctx = runner_context()
    with pytest.raises(ValueError):
        validate_wire_proposal(exposure_proposal(ctx, action), ctx)


@pytest.mark.parametrize('action', ['WAIT', 'EXIT'])
def test_pending_accounting_still_allows_safe_wait_or_full_exit_proposals(action):
    ctx = runner_context()
    ctx.update(accounting_status='pending', fees_paid=None, funding_paid=None, new_risk_blocked=True)
    result = validate_scenario(validate_wire_proposal(exposure_proposal(ctx, action), ctx), ctx)
    assert result['action'] == action


def test_prompt_compares_directions_without_inheriting_one_sided_wait_veto():
    text = ' '.join(SYSTEM_PROMPT.split())
    assert 'no active scenario, compare LONG, SHORT and WAIT on equal terms' in text
    assert 'Failure of a LONG condition is neither proof of a SHORT edge nor a veto on SHORT' in text
    assert 'Apply the same test in reverse' in text
    assert 'Previous direction-specific waiting conditions are not shared entry requirements' in text


def test_prompt_reassesses_zero_fill_without_imaginary_confirmation_orders():
    text = ' '.join(SYSTEM_PROMPT.split())
    assert 'pending entries even when filled quantity is zero' in text
    assert 'retain the existing limit, replan entry/TP/SL/quantity, or cancel' in text
    assert 'zero chase allowance is not a command to keep an obsolete plan forever' in text
    assert 'ADJUST with empty entries cannot enable chase for an older entry' in text
    assert 'touching a limit price is NOT confirmation of a rebound or breakout' in text
    assert 'If genuine additional candle/volume/retest confirmation is required, WAIT' in text
    assert 'numeric MarkPrice crossing alone may use the conditional entry contract' in text
    assert 'WAIT + cancel_entry_ids and await exact cancellation; bare WAIT leaves it live' in text
    assert 'marketable LIMIT may be proposed' in text
    assert 'WAIT does not renew existing entry expiry or change chase' in text


def test_management_framing_preserves_both_trend_runner_and_failure_responses():
    text = ' '.join(SYSTEM_PROMPT.split())
    assert 'Minimum analysis frame is 15m' in text
    assert '15m/30m/1h/4h/12h/1d/1w' in text
    assert 'five-minute evaluation cadence is not a candle timeframe' in text
    assert 'favorable LONG or favorable SHORT deserves the same opportunity-cost review' in text
    assert 'neither early profit-taking nor a distant all-size TP is mandatory' in text
    assert 'Ordinary pullbacks with an intact primary thesis may justify WAIT' in text
    assert 'deterioration must inform management of EXISTING exposure' in text
    assert 'Immediate partial market reduction is unsupported' in text


@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
@pytest.mark.parametrize('choice', ['intact_pullback', 'partial_tp_runner', 'failed_breakout_protect', 'invalidated_exit'])
def test_symmetric_management_alternatives_reach_real_validator(side, choice):
    ctx = context(True)
    sign = 1 if side == 'LONG' else -1
    ctx.update(side=side, positions=[dict(price=60000, quantity=.01)], mark_price=60000,
               previous_hard_stop=60000-sign*100)
    action = {'intact_pullback': 'WAIT', 'partial_tp_runner': 'ADJUST',
              'failed_breakout_protect': 'ADJUST', 'invalidated_exit': 'EXIT'}[choice]
    proposal = wire(ctx, action)
    proposal.update(side=side, hard_stop=60000-sign*50,
                    take_profits=[dict(id='obstacle-tp', price=60000+sign*100, fraction=.5)])
    if action in ('WAIT', 'EXIT'):
        proposal.update(side=None, hard_stop=None, chase=None, take_profits=[])
    if choice == 'failed_breakout_protect':
        proposal['partial_stops'] = [dict(id='failure-stop', price=60000-sign*20, fraction=.5)]
    accepted = validate_scenario(validate_wire_proposal(proposal, ctx), ctx)
    assert accepted['action'] == action
    if action == 'ADJUST':
        assert accepted['entries'] == []
        assert accepted['take_profits'][0]['fraction'] == .5
        assert accepted['risk']['budget'] == 200
        proposal['hard_stop'] = 60000-sign*200
        with pytest.raises(ValueError):
            validate_scenario(validate_wire_proposal(proposal, ctx), ctx)


@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
def test_flat_direction_comparison_keeps_both_real_wire_actions_available(side):
    ctx = context()
    proposal = wire(ctx, 'OPEN')
    proposal.update(side=side, entries=[dict(id='entry', price=60000, quantity=.001)])
    if side == 'SHORT':
        proposal.update(hard_stop=61000, take_profits=[dict(id='tp', price=59000, fraction=.5)])
    result = validate_scenario(validate_wire_proposal(proposal, ctx), ctx)
    assert result['side'] == side and result['risk']['within_budget']
    assert result['risk']['budget'] == 200


@pytest.mark.parametrize('action', ['OPEN', 'ADJUST'])
def test_unfilled_active_scenario_cannot_flip_direction(action):
    ctx = context(True)
    ctx.update(positions=[], pending_entries=[dict(id='pending', price=60000, quantity=.001)])
    proposal = wire(ctx, action)
    proposal.update(side='SHORT', hard_stop=61000,
                    entries=[dict(id='reverse', price=60000, quantity=.001)],
                    take_profits=[dict(id='tp', price=59000, fraction=.5)])
    with pytest.raises(ValueError):
        validate_scenario(validate_wire_proposal(proposal, ctx), ctx)


def test_unfilled_pending_can_be_kept_cancelled_or_replanned_without_budget_reset():
    ctx = context(True)
    ctx.update(positions=[], pending_entries=[dict(id='pending', price=60000, quantity=.001)])
    keep = exposure_proposal(ctx, 'WAIT')
    assert validate_scenario(validate_wire_proposal(keep, ctx), ctx)['action'] == 'WAIT'
    cancel = dict(keep, cancel_entry_ids=['pending'])
    assert validate_scenario(validate_wire_proposal(cancel, ctx), ctx)['cancel_entry_ids'] == ['pending']
    replace = wire(ctx, 'ADJUST')
    replace.update(entries=[dict(id='replacement', price=60100, quantity=.001)],
                   cancel_entry_ids=['pending'])
    result = validate_scenario(validate_wire_proposal(replace, ctx), ctx)
    assert result['risk']['budget'] == 200
    expected = risk_snapshot(initial_equity=10000, side='LONG', hard_stop=59000,
        positions=[], pending_entries=ctx['pending_entries'], entries=replace['entries'],
        realized_loss=0, fees_paid=0, funding_paid=0, estimated_cost_rate=.0012, slippage_bps=10)
    assert result['risk'] == expected
    replace['hard_stop'] = 58000
    with pytest.raises(ValueError):
        validate_scenario(validate_wire_proposal(replace, ctx), ctx)


def test_empty_entry_adjust_does_not_activate_chase_on_older_pending_order(tmp_path):
    from tests.test_scenario_execution import live as fixture, persist
    broker, exchange = fixture.__wrapped__(tmp_path)
    broker.clock = lambda: exchange.now
    broker.execute(persist(broker, chase=dict(max_bps=0, max_reprices=0)), 'a1')
    broker.execute(persist(broker, action_id='a2', action='ADJUST', entries=[],
                           chase=dict(max_bps=20, max_reprices=1)), 'a2')
    exchange.now += 61
    observed = broker.capture_account()
    observed['ticker']['ask1Price'] = '100.1'
    broker._autochase(broker._active(), observed)
    assert [kind for kind, _ in exchange.writes] == ['place']


@pytest.mark.parametrize('active', [False, True])
def test_assembled_entry_framing_is_flat_only_and_preserves_inputs(active, monkeypatch):
    ctx = context(active)
    ctx['recovery'] = {'baseline': {'mark_price': 59000}, 'changed_evidence': []}
    original = json.loads(json.dumps(ctx))
    calls = []
    journal = []
    from live import scenario_recovery
    monkeypatch.setattr(scenario_recovery, 'record_model_request', lambda **kw: journal.append(kw))
    p = exposure_proposal(ctx, 'WAIT')
    def generate(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(text=json.dumps(p))
    result = propose({'valid': True, 'as_of_ms': 1000000}, ctx,
                     response_contract(ctx), generate=generate, clock=lambda: 1000)
    policy = calls[0]['system_prompt']
    if active:
        assert policy == SYSTEM_PROMPT + MA_STRUCTURE_PROMPT
        delivered = json.loads(calls[0]['user_prompt'])['contract_context']
        assert delivered.pop('ma_structure')['status'] == 'unavailable'
        assert delivered == ctx
    else:
        text = ' '.join(policy.split())
        assert 'Flat-entry comparison only' in text
        assert 'baseline and changed_evidence audit change/authorization' in text
        assert 'hours-old baseline price alone is not a momentum veto' in text
        assert 'capped marketable LIMIT now' in text
        assert 'new low/high or retest by habit' in text
        assert 'never repair an invalid setup' in text
        assert 'do not move its goalposts without fresh counterevidence' in text
        assert 'Never invent an omitted prior condition' in text
        assert "Keep recovery's first-observation WAIT" in text
        assert 'Preserve host-authorized NORMAL ordinary-entry chase rules' in text
        assert 'original_budget*confidence' in text
        assert 'hard_stop*slippage_bps/10000' in text
        assert 'Never raise confidence or widen SL merely to fit quantity' in text
        assert 'bearish MA order is NOT price below both MAs' in text
        assert 'Mixed price position can still support a trade' in text
        assert json.loads(calls[0]['user_prompt'])['contract_context']['current_primary_frame_facts']['30m']['status'] == 'unavailable'
    assert result['action'] == 'WAIT'
    assert journal[0]['system_prompt'] == calls[0]['system_prompt']
    assert journal[0]['user_prompt'] == calls[0]['user_prompt']
    assert json.loads(calls[0]['user_prompt'])['contract_context']['recovery'] == ctx['recovery']
    assert ctx == original


@pytest.mark.parametrize('exposure', ['positions', 'pending_entries'])
def test_flat_addendum_never_applies_to_unscoped_exposure(exposure):
    ctx = context()
    ctx[exposure] = [dict(id='unresolved', price=60000, quantity=.001)]
    calls = []
    def generate(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(text=json.dumps(exposure_proposal(ctx, 'WAIT')))
    propose({'valid': True, 'as_of_ms': 1000000}, ctx, response_contract(ctx),
            generate=generate, clock=lambda: 1000)
    assert calls[0]['system_prompt'] == SYSTEM_PROMPT + MA_STRUCTURE_PROMPT


@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
@pytest.mark.parametrize('blocked', [False, True])
def test_assembled_flat_comparison_preserves_real_open_and_halt_guards(side, blocked):
    ctx = context()
    ctx['new_risk_blocked'] = blocked
    p = wire(ctx, 'OPEN')
    p.update(side=side, entries=[dict(id='now-entry', price=60000, quantity=.001)])
    if side == 'SHORT':
        p.update(hard_stop=61000, take_profits=[dict(id='tp', price=59000, fraction=.5)])
    parsed = propose({'valid': True, 'as_of_ms': 1000000}, ctx,
                     response_contract(ctx), clock=lambda: 1000,
                     generate=lambda **kwargs: SimpleNamespace(text=json.dumps(p)))
    if blocked:
        with pytest.raises(ValueError):
            validate_scenario(parsed, ctx)
    else:
        accepted = validate_scenario(parsed, ctx)
        assert accepted['side'] == side
        assert accepted['risk']['budget'] == 200
        assert accepted['leverage'] == 10


@pytest.mark.parametrize('quantity,accepted', [(.027, False), (.024, True)])
def test_recovery_quantity_obeys_existing_confidence_cap(quantity, accepted):
    ctx = context()
    ctx.update(initial_equity=9522.20868271, scenario_risk_fraction=.005,
               estimated_cost_rate=.002, slippage_bps=20, mark_price=82650)
    p = wire(ctx, 'OPEN')
    p.update(side='SHORT', confidence=.55, hard_stop=83400,
             entries=[dict(id='entry', price=82650, quantity=quantity)],
             take_profits=[dict(id='tp', price=82000, fraction=1)])
    parsed = propose({'valid': True, 'as_of_ms': 1000000}, ctx, response_contract(ctx),
                     clock=lambda: 1000,
                     generate=lambda **kwargs: SimpleNamespace(text=json.dumps(p)))
    budget = ctx['initial_equity'] * .005
    proposed_risk = quantity * (750 + 82650*.002 + 83400*20/10000)
    assert proposed_risk < budget  # The full budget alone does not authorize size.
    if accepted:
        result = validate_scenario(parsed, ctx)
        assert result['risk']['proposed_risk'] == pytest.approx(25.9704)
        assert result['risk']['budget'] == pytest.approx(budget)
        assert result['confidence'] == .55
        assert result['hard_stop'] == 83400
        assert proposed_risk <= budget*.55
    else:
        assert proposed_risk == pytest.approx(29.2167)
        assert proposed_risk > budget*.55
        with pytest.raises(ValueError, match='risk budget exceeded'):
            validate_scenario(parsed, ctx)


def primary_snapshot():
    return {'valid': True, 'as_of_ms': 1000000, 'timeframes': {
        frame: {'forming': {'ohlcv': {'close': close}, 'ma10': ma10, 'ma35': ma35,
                            'observed_at_ms': 999000, 'observation_kind': 'observed',
                            'price_position': 'below'}}  # A bad label must not override arithmetic.
        for frame, close, ma10, ma35 in (
            ('15m', 83342.2, 83285.59, 83475.06),
            ('30m', 83342.2, 83254.76999999999, 83879.15142857142),
            ('1h', 83342.1, 83476.42000000001, 84801.81714285715))}}


def test_exact_oct8_mixed_price_positions_not_fabricated_bearish_alignment():
    snapshot = primary_snapshot()
    original = json.loads(json.dumps(snapshot))
    facts = _current_primary_frame_facts(snapshot)
    assert facts['15m']['price_position'] == 'BETWEEN'
    assert facts['30m']['price_position'] == 'BETWEEN'
    assert facts['1h']['price_position'] == 'BELOW'
    assert facts['30m']['ma_order'] == 'BEARISH'
    assert facts['30m']['ordered_comparison'] == 'ma10 < close < ma35'
    assert facts['30m']['price_basis'] == 'FORMING_LAST_CLOSE'
    assert facts['30m']['source'] == 'timeframes.30m.forming'
    assert snapshot == original


@pytest.mark.parametrize('close,ma10,ma35,position,order,comparison', [
    (110, 105, 100, 'ABOVE', 'BULLISH', 'ma35 < ma10 < close'),
    (103, 105, 100, 'BETWEEN', 'BULLISH', 'ma35 < close < ma10'),
    (100, 100, 110, 'AT_MA10', 'BEARISH', 'close = ma10 < ma35'),
    (110, 100, 110, 'AT_MA35', 'BEARISH', 'ma10 < close = ma35'),
    (100, 100, 100, 'AT_BOTH', 'EQUAL', 'close = ma10 = ma35'),
    (101, 100, 100, 'ABOVE', 'EQUAL', 'ma10 = ma35 < close'),
])
def test_primary_facts_symmetric_arithmetic_and_equality(close, ma10, ma35, position, order, comparison):
    snapshot = primary_snapshot()
    snapshot['timeframes']['30m']['forming'].update(ohlcv={'close': close}, ma10=ma10, ma35=ma35)
    facts = _current_primary_frame_facts(snapshot)['30m']
    assert (facts['price_position'], facts['ma_order'], facts['ordered_comparison']) == (position, order, comparison)


@pytest.mark.parametrize('bad', [None, True, float('nan'), float('inf'), 0, -1, '83342.2'])
@pytest.mark.parametrize('field', ['close', 'ma10', 'ma35'])
def test_primary_facts_invalid_numbers_unavailable(field, bad):
    snapshot = primary_snapshot()
    forming = snapshot['timeframes']['30m']['forming']
    (forming['ohlcv'] if field == 'close' else forming)[field] = bad
    facts = _current_primary_frame_facts(snapshot)['30m']
    assert facts == {'status': 'unavailable', 'source': 'timeframes.30m.forming'}


@pytest.mark.parametrize('fault', ['missing_frame', 'missing_ohlcv', 'missing_time', 'future', 'boolean_time', 'synthetic'])
def test_primary_facts_do_not_create_missing_or_future_observations(fault):
    snapshot = primary_snapshot()
    forming = snapshot['timeframes']['30m']['forming']
    if fault == 'missing_frame':
        del snapshot['timeframes']['30m']
    elif fault == 'missing_ohlcv':
        del forming['ohlcv']
    elif fault == 'missing_time':
        del forming['observed_at_ms']
    elif fault == 'future':
        forming['observed_at_ms'] = 1000001
    elif fault == 'boolean_time':
        forming['observed_at_ms'] = True
    else:
        forming['observation_kind'] = 'synthetic_boundary'
    assert _current_primary_frame_facts(snapshot)['30m']['status'] == 'unavailable'


def test_actual_model_payload_gets_same_primary_facts_as_request_record(monkeypatch):
    from live import scenario_recovery
    ctx, snapshot, calls, journal = context(), primary_snapshot(), [], []
    original = json.dumps([ctx, snapshot], sort_keys=True)
    monkeypatch.setattr(scenario_recovery, 'record_model_request', lambda **kw: journal.append(kw))
    def generate(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(text=json.dumps(exposure_proposal(ctx, 'WAIT')))
    propose(snapshot, ctx, response_contract(ctx), generate=generate, clock=lambda: 1000)
    assert journal[0]['user_prompt'] == calls[0]['user_prompt']
    assert journal[0]['system_prompt'] == calls[0]['system_prompt']
    payload = json.loads(calls[0]['user_prompt'])
    assert payload['contract_context']['current_primary_frame_facts'] == _current_primary_frame_facts(snapshot)
    assert payload['market_snapshot'] == snapshot
    assert json.dumps([ctx, snapshot], sort_keys=True) == original


@pytest.mark.parametrize('mode', ['flat', 'pending', 'holding'])
def test_ma_structure_actual_request_all_contexts_bounded_and_audited(mode, monkeypatch):
    from tests.test_scenario_snapshot import snapshot as real_snapshot
    from live import scenario_recovery
    snapshot = real_snapshot()
    ctx = context(mode != 'flat')
    ctx.update(now=snapshot['as_of_ms'] / 1000, input_captured_at=snapshot['as_of_ms'] / 1000)
    if mode == 'pending':
        ctx['positions'] = []
        ctx['pending_entries'] = [dict(id='pending', price=60000, quantity=.001)]
    p = exposure_proposal(ctx, 'WAIT')
    p['expires_at'] = ctx['now'] + 100
    calls, journal = [], []
    monkeypatch.setattr(scenario_recovery, 'record_model_request', lambda **kw: journal.append(kw))
    def generate(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(text=json.dumps(p))
    result = propose(snapshot, ctx, response_contract(ctx), generate=generate, clock=lambda: ctx['now'])
    assert result['action'] == 'WAIT'
    assert journal[0]['system_prompt'] == calls[0]['system_prompt']
    assert journal[0]['user_prompt'] == calls[0]['user_prompt']
    assert MA_STRUCTURE_PROMPT in calls[0]['system_prompt']
    assert len(calls[0]['user_prompt'].encode()) < 100000
    payload = json.loads(calls[0]['user_prompt'])
    assert len(payload['contract_context']['ma_structure']['primary']['30m']['points']) == 4
    assert ('current_primary_frame_facts' in payload['contract_context']) == (mode == 'flat')


def test_full_recovery_payload_with_seven_frames_preserves_headroom():
    from tests.test_scenario_snapshot import frames, snapshot as real_snapshot, NOW
    history, provisional = frames(NOW + 300000)
    # Variable tick-aligned prices generate long-decimal MA/gap facts, with a
    # complete 63-path recovery comparison rather than a minimal empty context.
    for frame, rows in history.items():
        for index, stamp in enumerate(rows.index):
            close = round(83000.1 + index * 11.123456789, 1)
            rows.loc[stamp, ['open', 'high', 'low', 'close', 'volume']] = [round(close-5.3, 1), round(close+20.7, 1), round(close-10.2, 1), close, 1234.123456789]
        provisional[frame].loc[:, ['open', 'high', 'low', 'close', 'volume']] = [83500.1, 83800.1, 83490.1, 83700.1, 987.123456789]
    snapshot = real_snapshot(history, provisional)
    ctx = context()
    ctx.update(now=snapshot['as_of_ms']/1000, input_captured_at=snapshot['as_of_ms']/1000)
    current = {}
    for frame, item in snapshot['timeframes'].items():
        for phase in ('confirmed', 'forming'):
            for ma in ('ma10', 'ma35'):
                current[f'timeframes.{frame}.{phase}.{ma}'] = item[phase][ma]
        for name, value in item['forming']['ohlcv'].items():
            current[f'timeframes.{frame}.forming.ohlcv.{name}'] = value
    baseline = {key: value-1.123456789 for key, value in current.items()}
    ctx['recovery'] = dict(phase='OBSERVING', baseline=baseline, current=current,
                          changed_evidence={key: {'before': baseline[key], 'now': value} for key, value in current.items()},
                          historical_outcomes=[{'scenario_id': f's_{n:024d}', 'net_pnl': -10.123456789} for n in range(3)],
                          first_observation=False, eligible=True, current_stage=0, offered_stage=0)
    ctx['recent_waits'] = [dict(slot=ctx['now']-n*300, confidence=.5123456789, action='WAIT') for n in range(30)]
    ctx['seen_action_ids'] = [f'action_{n:025d}' for n in range(50)]
    original_inputs = json.dumps([snapshot, ctx], sort_keys=True)
    p = exposure_proposal(ctx, 'WAIT')
    p['expires_at'] = ctx['now'] + 100
    calls = []
    def generate(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(text=json.dumps(p))
    propose(snapshot, ctx, response_contract(ctx), generate=generate, clock=lambda: ctx['now'])
    encoded = calls[0]['user_prompt']
    assert len(encoded.encode()) < 95000
    from live.scenario_model_input import restore_model_input
    delivered = restore_model_input(json.loads(encoded))
    assert delivered['market_snapshot'] == snapshot
    assert delivered['contract_context']['recovery'] == ctx['recovery']
    assert len(delivered['contract_context']['ma_structure']['levels']) == 16
    assert json.dumps([snapshot, ctx], sort_keys=True) == original_inputs


def test_followthrough_prompt_compares_without_forced_entry_or_early_exit():
    text = ' '.join((SYSTEM_PROMPT + MA_STRUCTURE_PROMPT).split())
    for rule in (
        'WIDENING reports the sign, not the strength of expansion',
        'falling highs/closes in a bullish MA order',
        'rising lows/closes in a bearish MA order',
        'Healthy pullbacks and a supported provisional reclaim remain eligible',
        'recent bar extremes, not confirmed swing pivots',
        'An infeasible cost-positive SL alone does not justify retaining exposure',
        'loss-limiting stop below break-even for LONG or above break-even for SHORT',
        'A reassessment condition is not an installed protective order',
        'Immediate partial market reduction is unsupported',
    ):
        assert rule in text


@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
def test_infeasible_profit_lock_does_not_remove_legal_loss_reduction(side):
    sign = 1 if side == 'LONG' else -1
    ctx = context(True)
    ctx.update(side=side, mark_price=60000-sign*50,
               previous_hard_stop=60000-sign*300,
               positions=[dict(price=60000, quantity=.01)])
    proposal = wire(ctx, 'ADJUST')
    proposal.update(side=side, hard_stop=60000-sign*100,
                    take_profits=[dict(id='runner', price=60000+sign*200, fraction=.5)],
                    partial_stops=[dict(id='loss-reduction', price=60000-sign*75, fraction=.5)])
    accepted = validate_scenario(validate_wire_proposal(proposal, ctx), ctx)
    assert accepted['entries'] == []
    assert accepted['hard_stop'] == 60000-sign*100
    assert accepted['risk']['budget'] == 200
    # A positive-return stop would be across Mark and remains invalid. The
    # remedy is not to weaken the validator or force immediate partial market.
    proposal['hard_stop'] = 60000+sign*100
    with pytest.raises(ValueError):
        validate_scenario(validate_wire_proposal(proposal, ctx), ctx)
