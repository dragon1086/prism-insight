"""Synthetic sizing invariants only; no broker or profitability assertions."""
from dataclasses import FrozenInstanceError, replace

import pytest

from core.confidence_allocation import (
    AllocationRequest, LEVERAGE, POLICIES, allocate, grade_agreement,
)
from core.portfolio_risk import ActualPosition, PendingEntry, ProposedEntry, evaluate_portfolio_entry


def request(**changes):
    base = AllocationRequest("main", "long", 10000.0, 100000.0, 99000.0,
                             0, "high", "graded", (), (), 100.0)
    return replace(base, **changes)


@pytest.mark.parametrize("side,direction", [("long", "up"), ("short", "down")])
@pytest.mark.parametrize("count,grade", [(0, "low"), (1, "low"), (2, "medium"), (3, "high")])
def test_ordinal_grade(side, direction, count, grade):
    trends = dict(zip(("1h", "4h", "1d"), [direction] * count + ["flat"] * (3-count)))
    assert grade_agreement(side, trends) == grade


@pytest.mark.parametrize("trends", [{}, {"1h": "up", "4h": "up"},
                                   {"1h": "up", "4h": True, "1d": "up"}, None])
def test_unknown_context(trends):
    assert grade_agreement("long", trends) == "unknown"


@pytest.mark.parametrize("lane", ["main", "swing"])
@pytest.mark.parametrize("side,stop", [("long", 99000.0), ("short", 101000.0)])
def test_fixed_leverage_same_stop_different_grade(lane, side, stop):
    decisions = [allocate(request(lane=lane, side=side, stop=stop, grade=grade))
                 for grade in ("low", "medium", "high")]
    assert 0 < decisions[0].qty < decisions[1].qty < decisions[2].qty
    assert [d.target_margin_fraction for d in decisions] == [0.1, 0.2, 0.3]
    for d in decisions:
        assert d.target_notional == 10000 * d.target_margin_fraction * LEVERAGE[lane]
        assert d.actual_margin_fraction == pytest.approx(d.qty * 100000 / LEVERAGE[lane] / 10000)


@pytest.mark.parametrize("policy,fractions", [
    ("fixed_low", (0.1, 0.1, 0.1)), ("uniform", (0.2, 0.2, 0.2)),
    ("fixed_high", (0.3, 0.3, 0.3)), ("graded", (0.1, 0.2, 0.3)),
    ("reversed", (0.3, 0.2, 0.1)),
])
def test_candidate_controls(policy, fractions):
    assert tuple(allocate(request(policy=policy, grade=g)).target_margin_fraction
                 for g in ("low", "medium", "high")) == fractions
    assert allocate(request(policy=policy, grade="unknown")).qty == 0


def test_different_stop_same_target_until_heat_cap():
    normal = allocate(request(stop=99000))
    wider = allocate(request(stop=98000))
    capped = allocate(request(stop=90000))
    assert normal.target_notional == wider.target_notional == capped.target_notional
    assert normal.qty == wider.qty
    assert capped.qty < normal.qty
    assert capped.entry_heat <= 500
    assert "lane_heat_cap" in capped.reasons


def test_legacy_risk_ceiling_only_reduces_and_rounds_down():
    result = allocate(request(legacy_qty=0.02799))
    assert result.qty == 0.027
    assert "legacy_qty_cap" in result.reasons
    assert allocate(request(legacy_qty=0.00099)).qty == 0


@pytest.mark.parametrize("side", ["long", "short"])
def test_main_cumulative_tranches_and_no_later_tier_top_up(side):
    stop = 99000 if side == "long" else 101000
    first = allocate(request(side=side, stop=stop))
    position = ActualPosition("main", side, first.qty, 100000, 100000, stop, True)
    second = allocate(request(side=side, stop=stop, tranche_index=1, positions=(position,)))
    assert second.qty == pytest.approx(0.09)
    last = allocate(request(side=side, stop=stop, tranche_index=2,
                            positions=(replace(position, qty=first.qty+second.qty),)))
    assert last.qty == pytest.approx(0.09)
    low_first = replace(position, qty=allocate(request(side=side, stop=stop, grade="low")).qty)
    upgrade = allocate(request(side=side, stop=stop, tranche_index=1, positions=(low_first,)))
    assert upgrade.qty <= 0.09  # cannot refill old 40% leg at a new higher grade
    assert allocate(request(side=side, stop=stop, grade="low", tranche_index=1,
                            positions=(position,))).qty == 0


def test_same_lane_pending_counts_toward_campaign_without_side_netting():
    pending = PendingEntry("main", "short", 0.10, 100000, 101000, 100000)
    result = allocate(request(pending=(pending,)))
    assert result.qty == pytest.approx(0.02)


def test_pending_short_uses_lower_bound_for_heat():
    pending = PendingEntry("main", "short", 0.1, 100000, 101000, 96500)
    result = allocate(request(pending=(pending,), tranche_index=2, stop=90000))
    assert result.qty <= 0.005
    assert "lane_heat_cap" in result.reasons


def test_main_and_swing_heat_are_reserved_and_not_netted():
    positions = (ActualPosition("main", "short", 0.4, 100000, 100000, 101000, True),)
    pending = (PendingEntry("swing", "long", 0.01, 100000, 99000),)
    result = allocate(request(lane="swing", positions=positions, pending=pending, stop=98000))
    assert result.qty <= 0.07
    assert result.entry_heat + 410 <= 650


def test_gross_cap_profitable_stops_do_not_release_gross():
    positions = (ActualPosition("main", "long", 0.799, 90000, 100000, 100000, True),)
    result = allocate(request(lane="swing", positions=positions))
    assert result.qty <= 0.001
    assert "gross_cap" in result.reasons


def test_margin_cap_combines_fixed_lane_leverage():
    positions = (ActualPosition("swing", "long", 0.49, 90000, 100000, 100000, True),)
    result = allocate(request(positions=positions))
    assert result.qty <= 0.02
    assert "margin_cap" in result.reasons


def test_existing_swing_gross_excess_rejects_even_main_entry():
    positions = (ActualPosition("swing", "long", 0.51, 90000, 100000, 100000, True),)
    assert allocate(request(positions=positions)).qty == 0


@pytest.mark.parametrize("field,value", [
    ("equity", 0), ("equity", True), ("price", float("nan")),
    ("price", float("inf")), ("stop", -1), ("stop", 100000),
    ("stop", 101000), ("legacy_qty", False), ("legacy_qty", -1),
    ("tranche_index", True), ("tranche_index", 3), ("lane", "other"),
    ("side", "up"), ("grade", "unknown"), ("policy", "missing"),
    ("pending", None), ("positions", []),
    ("lane", []), ("policy", []), ("equity", 10**1000),
])
def test_invalid_request_fails_closed(field, value):
    decision = allocate(request(**{field: value}))
    assert decision.qty == 0
    assert decision.reasons


@pytest.mark.parametrize("position", [
    ActualPosition("main", "long", 0.01, 100000, 100000, 99000, False),
    ActualPosition("main", "long", True, 100000, 100000, 99000, True),
    ActualPosition("main", "long", 0.01, 100000, float("inf"), 99000, True),
    None,
])
def test_unknown_protection_or_position_rejected(position):
    assert allocate(request(positions=(position,))).qty == 0


@pytest.mark.parametrize("pending", [
    PendingEntry("main", "short", 0.01, 100000, 101000),
    PendingEntry("main", "short", 0.01, 100000, 101000, 100001),
    PendingEntry("main", "long", 0.01, 100000, 101000),
    PendingEntry("main", "long", True, 100000, 99000), None,
])
def test_unknown_or_invalid_pending_rejected(pending):
    assert allocate(request(pending=(pending,))).qty == 0


def test_inputs_and_leverage_immutable():
    original = request()
    allocate(original)
    assert original.stop == 99000
    with pytest.raises(FrozenInstanceError):
        original.stop = 99999
    with pytest.raises(TypeError):
        LEVERAGE["main"] = 20
    with pytest.raises(TypeError):
        POLICIES["graded"] = (1, 1, 1)
    assert dict(LEVERAGE) == {"main": 10, "swing": 5}


@pytest.mark.parametrize("lane", ["main", "swing"])
@pytest.mark.parametrize("side", ["long", "short"])
def test_grid_every_positive_result_satisfies_existing_risk_engine(lane, side):
    for equity in (100, 10000, 1000000):
        for distance in (50, 1000, 10000):
            for grade in ("low", "medium", "high"):
                req = request(lane=lane, side=side, equity=equity, grade=grade,
                              stop=100000-distance if side == "long" else 100000+distance)
                result = allocate(req)
                assert result.qty <= req.legacy_qty
                if result.qty:
                    check = evaluate_portfolio_entry(
                        capital=equity, positions=(), pending_entries=(),
                        proposed=ProposedEntry(lane, side, result.qty, req.price, req.stop,
                                               req.price if side == "short" else None))
                    assert check.allowed
                    assert result.actual_margin_fraction <= 1
