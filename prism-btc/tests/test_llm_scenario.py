"""Offline scenario contract tests: no profitability or broker claims."""
from copy import deepcopy

import pytest

from core.llm_scenario import (
    ScenarioValidationError, quantity_for_risk, risk_snapshot,
    update_circuit_breaker, validate_scenario,
)


def sample():
    return ({"schema_version": 1, "scenario_id": "s1", "revision": 1,
             "input_id": "i1", "action_id": "a1", "action": "OPEN",
             "side": "LONG", "confidence": 0.6, "expires_at": 1200,
             "hard_stop": 99000, "entries": [{"id": "e1", "price": 100000, "quantity": 0.05}],
             "take_profits": [{"id": "tp1", "price": 102000, "fraction": 0.5}],
             "partial_stops": [], "chase": {"max_bps": 20, "max_reprices": 1},
             "rationale": "30m/1h breakout", "leverage": 10},
            {"now": 1000, "input_id": "i1", "input_captured_at": 990,
             "max_input_age_seconds": 120, "scenario_id": None, "revision": 0,
             "seen_action_ids": [], "initial_equity": 10000, "positions": [],
             "pending_entries": [], "previous_hard_stop": None, "realized_loss": 0,
             "fees_paid": 0, "funding_paid": 0, "estimated_cost_rate": 0.0012,
             "slippage_bps": 10, "new_risk_blocked": False})


def active():
    p, c = sample()
    p.update(action="ADJUST", entries=[])
    c.update(scenario_id="s1", side="LONG", previous_hard_stop=99000,
             mark_price=101000, positions=[{"price": 100000, "quantity": 0.05}])
    return p, c


def test_valid_open_is_pure():
    p, c = sample()
    before = deepcopy((p, c))
    out = validate_scenario(p, c)
    assert out["risk"]["budget"] == 200
    assert out["risk"]["total_risk"] == pytest.approx(60.95)
    assert (p, c) == before


@pytest.mark.parametrize("field,value", [("confidence", True), ("confidence", float("nan")),
    ("confidence", 2), ("revision", True), ("schema_version", True),
    ("hard_stop", float("inf")), ("hard_stop", 100001), ("expires_at", 999),
    ("expires_at", 5000), ("leverage", 20), ("leverage", True),
    ("action", "BUY"), ("input_id", "stale"), ("revision", 0)])
def test_bad_scalar(field, value):
    p, c = sample()
    p[field] = value
    with pytest.raises(ScenarioValidationError):
        validate_scenario(p, c)


@pytest.mark.parametrize("field,value", [("input_captured_at", 800),
    ("input_captured_at", 1001), ("seen_action_ids", ["a1"]),
    ("initial_equity", True), ("new_risk_blocked", True), ("fees_paid", -1)])
def test_bad_context(field, value):
    p, c = sample()
    c[field] = value
    with pytest.raises(ScenarioValidationError):
        validate_scenario(p, c)


def test_no_profit_recycling_and_all_pending_risk():
    r = risk_snapshot(initial_equity=10000, side="LONG", hard_stop=100,
        positions=[{"price": 90, "quantity": 10}, {"price": 110, "quantity": 10}],
        pending_entries=[{"price": 120, "quantity": 2}], entries=[],
        realized_loss=50, fees_paid=10, funding_paid=5, estimated_cost_rate=0,
        slippage_bps=0)
    assert r["total_risk"] == 205
    assert not r["within_budget"]


def test_split_entries_combined_risk_rejected():
    p, c = sample()
    p["entries"] = [{"id": str(i), "price": 100000, "quantity": 0.05} for i in range(4)]
    with pytest.raises(ScenarioValidationError):
        validate_scenario(p, c)


@pytest.mark.parametrize("kind", ["duplicate", "fractions", "partial", "chase", "unknown"])
def test_invalid_orders(kind):
    p, c = sample()
    if kind == "duplicate":
        p["take_profits"][0]["id"] = "e1"
    elif kind == "fractions":
        p["take_profits"][0]["fraction"] = 1.01
    elif kind == "partial":
        p["partial_stops"] = [{"id": "sl1", "price": 98000, "fraction": 0.5}]
    elif kind == "chase":
        p["chase"]["max_reprices"] = 4
    else:
        p["invented"] = 1
    with pytest.raises(ScenarioValidationError):
        validate_scenario(p, c)


def test_halted_overbudget_account_can_tighten_and_exit():
    p, c = active()
    c.update(new_risk_blocked=True, realized_loss=300)
    p["hard_stop"] = 100500
    assert validate_scenario(p, c)["risk"]["within_budget"] is False
    p.update(action="EXIT", take_profits=[], partial_stops=[])
    for key in ("hard_stop", "side", "chase"):
        p.pop(key)
    assert validate_scenario(p, c)["action"] == "EXIT"


def test_widening_or_side_change_rejected():
    p, c = active()
    p["hard_stop"] = 98000
    with pytest.raises(ScenarioValidationError):
        validate_scenario(p, c)
    p["hard_stop"] = 99000
    p["side"] = "SHORT"
    with pytest.raises(ScenarioValidationError):
        validate_scenario(p, c)


def test_short_and_partial_stop():
    p, c = sample()
    p.update(side="SHORT", hard_stop=101000,
        take_profits=[{"id": "tp", "price": 98000, "fraction": 0.5}],
        partial_stops=[{"id": "sl", "price": 100500, "fraction": 0.5}])
    assert validate_scenario(p, c)["side"] == "SHORT"


def test_wait_cancellation_halted_allowed_but_does_not_release_risk():
    p, c = active()
    c.update(new_risk_blocked=True, pending_entries=[{"id": "old", "price": 100000, "quantity": 0.01}])
    p.update(action="WAIT", take_profits=[], cancel_entry_ids=["old"])
    for key in ("hard_stop", "side", "chase"):
        p.pop(key)
    assert validate_scenario(p, c)["cancel_entry_ids"] == ["old"]
    p["cancel_entry_ids"] = ["unknown"]
    with pytest.raises(ScenarioValidationError):
        validate_scenario(p, c)


def test_confidence_changes_quantity_not_leverage():
    args = dict(available_risk=200, entry_price=100000, hard_stop=99000,
                side="LONG", estimated_cost_rate=0.0012, slippage_bps=10)
    assert quantity_for_risk(allocation_fraction=0.5, **args) == pytest.approx(0.082)
    assert quantity_for_risk(allocation_fraction=1, **args) == pytest.approx(0.164)


def test_three_losses_latch_idempotent_across_day_and_win():
    state = {}
    for i in range(3):
        state = update_circuit_breaker(state, day="2026-10-03", day_start_equity=10000,
            daily_net_pnl=-30, completed_scenario_id=str(i), completed_net_pnl=-10)
    assert state["blocked"] and state["consecutive_losses"] == 3
    again = update_circuit_breaker(state, day="2026-10-04", day_start_equity=10000,
        daily_net_pnl=0, completed_scenario_id="2", completed_net_pnl=-10)
    assert again["consecutive_losses"] == 3 and again["blocked"]
    assert state["completed_ids"] == ["0", "1", "2"]


def test_daily_loss_latch_includes_open_account_pnl():
    state = update_circuit_breaker({}, day="2026-10-03", day_start_equity=10000,
                                   daily_net_pnl=-400)
    assert state["blocked"] and state["reasons"] == ["daily_loss"]


@pytest.mark.parametrize("action", ["WAIT", "EXIT"])
@pytest.mark.parametrize("field,value", [("hard_stop", 98000), ("hard_stop", 99000),
    ("side", "SHORT"), ("side", "LONG"), ("chase", {"max_bps": 0, "max_reprices": 0})])
def test_non_modifying_actions_forbid_protection_fields(action, field, value):
    p, c = active()
    p.update(action=action, take_profits=[], cancel_entry_ids=["pending"])
    c["pending_entries"] = [{"id": "pending", "price": 100000, "quantity": 0.01}]
    for key in ("hard_stop", "side", "chase"):
        p.pop(key)
    p[field] = value
    before = deepcopy(c)
    with pytest.raises(ScenarioValidationError, match="cannot change protection"):
        validate_scenario(p, c)
    assert c == before
    # Rejection cannot poison the prior stop used by a later ADJUST.
    next_plan, _ = active()
    next_plan["hard_stop"] = 98500
    with pytest.raises(ScenarioValidationError, match="stop widening"):
        validate_scenario(next_plan, c)


def test_malformed_context_does_not_echo_exception_details():
    p, c = sample()
    c["seen_action_ids"] = None
    with pytest.raises(ScenarioValidationError) as error:
        validate_scenario(p, c)
    assert str(error.value) == "incomplete or malformed scenario/context"


def test_huge_integer_rejected_as_validation_error():
    p, c = sample()
    p["hard_stop"] = 10 ** 1000
    with pytest.raises(ScenarioValidationError):
        validate_scenario(p, c)
