from copy import deepcopy

import pytest

from core.llm_scenario import ScenarioValidationError
from live.scenario_risk_allocation import allocate_entry_risk, validate_allocation_prices


def context():
    return dict(initial_equity=10000, scenario_risk_fraction=.005,
                positions=[], pending_entries=[], realized_loss=0, fees_paid=0,
                funding_paid=0, estimated_cost_rate=.002, slippage_bps=20,
                quantity_step=.001, minimum_quantity=.001, minimum_notional=5,
                price_tick=.1)


def proposal():
    return dict(action="OPEN", side="LONG", confidence=.75, hard_stop=82410,
                entries=[dict(id="entry", price=83100, allocation_fraction=1)],
                cancel_entry_ids=[], take_profits=[])


def test_regression_is_hypothetical_allocation_not_rewritten_model_response():
    p = proposal()
    original = deepcopy(p)
    plan, receipt = allocate_entry_risk(p, context())
    assert p == original
    assert plan["entries"][0]["quantity"] == .014
    assert receipt["new_risk_ceiling"] == 15
    assert receipt["entries"][0]["computed_risk"] == pytest.approx(14.29428)
    validate_allocation_prices(plan, receipt, context())


@pytest.mark.parametrize("side,stop", [("LONG", 82410), ("SHORT", 83790)])
def test_authorized_chase_reserved_for_entire_batch(side, stop):
    p, c = proposal(), context()
    c["scenario_risk_fraction"] = .02
    p.update(side=side, hard_stop=stop, chase=dict(max_bps=50, max_reprices=3))
    p["entries"][0]["allocation_fraction"] = .1
    plan, receipt = allocate_entry_risk(p, c)
    row = receipt["entries"][0]
    assert row["allocation_budget"] == 15
    assert row["worst_chase_risk"] <= 15
    assert row["computed_risk"] < row["worst_chase_risk"]
    assert plan["entries"][0]["quantity"] < .014
    assert plan["chase"] == p["chase"]
    # Any sequence of partial fills at allowed limits is bounded by the same
    # original full-batch reserve, without resetting the allocation after fills.
    from live.scenario_risk_allocation import _unit_risk
    prices = [83100, 83300, 83515.5] if side == "LONG" else [83100, 82900, 82684.5]
    weighted = sum(float(_unit_risk(plan, c, price)) * plan["entries"][0]["quantity"] * fraction
                   for price, fraction in zip(prices, [.2, .3, .5]))
    assert weighted <= row["worst_chase_risk"] <= 15


def test_effective_chase_anchor_revalidated_and_permission_never_expanded():
    p, c = proposal(), context()
    c["quantity_step"] = c["minimum_quantity"] = .000001
    p["chase"] = dict(max_bps=50, max_reprices=1)
    plan, receipt = allocate_entry_risk(p, c)
    shifted = deepcopy(plan)
    shifted["entries"][0]["price"] += 7.3
    with pytest.raises(ScenarioValidationError, match="allocation_budget_exceeded"):
        validate_allocation_prices(shifted, receipt, c)
    changed = deepcopy(plan)
    changed["chase"]["max_reprices"] = 2
    with pytest.raises(ScenarioValidationError, match="allocation_model_decision_changed"):
        validate_allocation_prices(changed, receipt, c)


def test_unused_chase_bps_does_not_shrink_size():
    p, c = proposal(), context()
    base, _ = allocate_entry_risk(p, c)
    p["chase"] = dict(max_bps=50, max_reprices=0)
    plan, receipt = allocate_entry_risk(p, c)
    assert plan["entries"] == base["entries"]
    assert receipt["entries"][0]["computed_risk"] == receipt["entries"][0]["worst_chase_risk"]


@pytest.mark.parametrize("side,stop", [("LONG", 82410), ("SHORT", 83790)])
def test_multi_entry_does_not_fill_unused_allocation(side, stop):
    p = proposal()
    p.update(side=side, hard_stop=stop)
    p["entries"] = [dict(id=str(i), price=83100, allocation_fraction=.2) for i in range(2)]
    plan, r = allocate_entry_risk(p, context())
    assert sum(e["quantity"] for e in plan["entries"]) < .02
    assert r["allocation_fraction_sum"] == .4


def test_adjust_counts_positions_pending_cancellations_and_consumed_at_new_stop():
    p, c = proposal(), context()
    p.update(action="ADJUST", hard_stop=82500, cancel_entry_ids=["old"])
    c.update(positions=[dict(price=83000, quantity=.01)],
             pending_entries=[dict(id="old", price=83100, quantity=.01)],
             realized_loss=20, fees_paid=3, funding_paid=1)
    _, r = allocate_entry_risk(p, c)
    assert r["reserved_risk_at_proposed_stop"] == pytest.approx(41.622)
    assert r["new_risk_ceiling"] == pytest.approx(8.378)
    assert r["cancellation_requests_release_risk"] is False


@pytest.mark.parametrize("value", [-1, float("nan"), float("inf"), True, "0.5", 1.1])
@pytest.mark.parametrize("field", ["confidence", "allocation_fraction"])
def test_bad_numbers_rejected(value, field):
    p = proposal()
    (p if field == "confidence" else p["entries"][0])[field] = value
    with pytest.raises(ScenarioValidationError):
        allocate_entry_risk(p, context())


def test_sum_over_one_and_quantity_ambiguity_rejected():
    p = proposal()
    p["entries"].append(dict(id="second", price=83200, allocation_fraction=.1))
    with pytest.raises(ScenarioValidationError):
        allocate_entry_risk(p, context())
    p = proposal()
    p["entries"][0]["quantity"] = .01
    with pytest.raises(ScenarioValidationError):
        allocate_entry_risk(p, context())


@pytest.mark.parametrize("change", [{"confidence": 0}, {"confidence": .00001}, {"hard_stop": 82410.01}])
def test_zero_tiny_and_off_tick_reject(change):
    p = proposal()
    p.update(change)
    with pytest.raises(ScenarioValidationError):
        allocate_entry_risk(p, context())


def test_price_change_rejects_per_entry_cap_without_reallocation():
    p = proposal()
    plan, r = allocate_entry_risk(p, context())
    plan["entries"][0]["price"] += 100
    before = deepcopy(plan)
    with pytest.raises(ScenarioValidationError, match="allocation_budget"):
        validate_allocation_prices(plan, r, context())
    assert plan == before


def test_decimal_step_and_no_entries_preserve_plan():
    p, c = proposal(), context()
    c.update(quantity_step=.003, minimum_quantity=.003)
    plan, r = allocate_entry_risk(p, c)
    assert plan["entries"][0]["quantity"] == .012
    validate_allocation_prices(plan, r, c)
    p.update(action="WAIT", side=None, hard_stop=None, entries=[])
    plan, r = allocate_entry_risk(p, c)
    assert plan == p and r["entries"] == []


@pytest.mark.parametrize("field,value", [("hard_stop", 82400), ("side", "SHORT"), ("confidence", 1)])
def test_post_price_check_never_rewrites_model_decision(field, value):
    plan, r = allocate_entry_risk(proposal(), context())
    plan[field] = value
    with pytest.raises(ScenarioValidationError, match="model_decision_changed"):
        validate_allocation_prices(plan, r, context())


@pytest.mark.parametrize("field", ["quantity_step", "price_tick", "minimum_quantity", "minimum_notional"])
@pytest.mark.parametrize("value", [0, -1, True, float("inf"), float("nan")])
def test_invalid_filters(field, value):
    c = context()
    c[field] = value
    with pytest.raises(ScenarioValidationError):
        allocate_entry_risk(proposal(), c)


def test_pending_reservation_is_not_released_by_cancel_request():
    p, c = proposal(), context()
    c["pending_entries"] = [dict(id="old", price=83100, quantity=.04)]
    p["cancel_entry_ids"] = ["old"]
    _, reserved = allocate_entry_risk(p, c)
    c["pending_entries"] = []
    _, released = allocate_entry_risk(p, c)
    assert reserved["new_risk_ceiling"] < released["new_risk_ceiling"]


def test_multi_order_individual_cap_cannot_borrow_unused_other_allocation():
    p = proposal()
    p["entries"] = [dict(id="a", price=83100, allocation_fraction=.5),
                    dict(id="b", price=83100, allocation_fraction=.1)]
    plan, r = allocate_entry_risk(p, context())
    plan["entries"][0]["price"] += 100
    with pytest.raises(ScenarioValidationError, match="allocation_budget"):
        validate_allocation_prices(plan, r, context())


def test_missing_allocation_and_malformed_row_are_not_defaulted():
    for rows in [[dict(id="e", price=83100)], [None]]:
        p = proposal()
        p["entries"] = rows
        with pytest.raises(ScenarioValidationError):
            allocate_entry_risk(p, context())
