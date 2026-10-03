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
