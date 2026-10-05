"""Prompt specification regressions against the actual economic validator."""
import pytest

from core.llm_scenario import risk_snapshot, validate_scenario
from live.scenario_contract import response_schema, validate_wire_proposal
from live.scenario_llm import SYSTEM_PROMPT
from live.scenario_preview import response_contract
from live.scenario_contract import identity_fields


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
    assert 'If genuine additional confirmation is required, WAIT' in text
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
