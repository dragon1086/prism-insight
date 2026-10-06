"""Offline execution evidence for flexible TP ladders, not profitability proof."""
import json
from decimal import Decimal

import pytest

from core.llm_scenario import ScenarioValidationError, validate_scenario
from tests.test_llm_scenario import sample
from tests.test_scenario_execution import live, persist  # noqa: F401


@pytest.fixture(params=["LONG", "SHORT"])
def ladder(request, monkeypatch):
    broker, exchange = request.getfixturevalue("live")
    side = request.param
    stop = 98 if side == "LONG" else 102
    if side == "SHORT":
        # The shared fake stores an unsigned long size. Adapt only fake fills
        # and observations; production order requests retain their real side.
        original_fill = exchange.fill
        original_positions = exchange.get_positions

        def fill(link, amount):
            order = exchange.orders[link]
            actual_side = order["side"]
            order["side"] = "Buy" if actual_side == "Sell" else "Sell"
            try:
                original_fill(link, amount)
            finally:
                order["side"] = actual_side
            exchange.fills[-1]["side"] = actual_side
            if "native" in exchange.orders:
                exchange.orders["native"]["side"] = "Buy"

        def positions(**kwargs):
            result = original_positions(**kwargs)
            row = result["result"]["list"][0]
            row["side"] = "Sell" if float(exchange.size) else ""
            return result

        monkeypatch.setattr(exchange, "fill", fill)
        monkeypatch.setattr(exchange, "get_positions", positions)
        exchange.stop = str(stop)
        state = json.loads(broker.conn.execute(
            "SELECT body FROM llm_scenario_state WHERE id=1").fetchone()[0])
        state["active"].update(side=side, hard_stop=stop)
        broker.conn.execute("UPDATE llm_scenario_state SET body=? WHERE id=1",
                            (json.dumps(state),))
        broker.conn.commit()
    return broker, exchange, side, stop


def targets(side, fractions=(.15, .25, .6)):
    prices = (103, 105, 108) if side == "LONG" else (97, 95, 92)
    return [dict(id=name, price=price, fraction=fraction)
            for name, price, fraction in zip(("near", "mid", "far"), prices, fractions)]


def open_ladder(ladder, fractions=(.15, .25, .6), filled=.1):
    broker, exchange, side, stop = ladder
    payload = persist(broker, side=side, hard_stop=stop,
                      take_profits=targets(side, fractions))
    broker.execute(payload, "a1")
    exchange.fill(next(iter(exchange.orders)), filled)
    assert broker.reconcile()["protection_confirmed"] is True
    return broker, exchange


def live_targets(broker, intent="a1"):
    return {child["local_id"].partition(":")[2]: child
            for child in broker.children() if child["kind"] == "tp"
            and child["intent_id"] == intent and child["status"] == "LIVE"}


def test_three_flexible_tiers_are_reduce_only_and_keep_full_stop(ladder):
    broker, exchange = open_ladder(ladder)
    rows = live_targets(broker)
    assert {key: Decimal(row["request"]["qty"]) for key, row in rows.items()} == {
        "near": Decimal(".015"), "mid": Decimal(".025"), "far": Decimal(".060")}
    for row in rows.values():
        assert row["request"]["reduceOnly"] is True
        assert row["request"]["side"] == ("Sell" if ladder[2] == "LONG" else "Buy")
    assert sum(Decimal(row["request"]["qty"]) for row in rows.values()) == Decimal(exchange.size)
    assert exchange.orders["native"]["orderStatus"] == "Untriggered"
    assert Decimal(exchange.orders["native"]["qty"]) == Decimal(exchange.size)
    before = list(exchange.writes)
    broker.reconcile()
    assert exchange.writes == before


@pytest.mark.parametrize("fractions,expected", [((1.0,), (".100",)),
                                                 ((.25, .5), (".025", ".050"))])
def test_one_or_two_tiers_do_not_force_extra_targets(ladder, fractions, expected):
    broker, exchange = open_ladder(ladder, fractions)
    rows = live_targets(broker)
    assert [Decimal(row["request"]["qty"]) for row in rows.values()] == [
        Decimal(quantity) for quantity in expected]
    assert len(rows) == len(fractions)
    assert sum(Decimal(row["request"]["qty"]) for row in rows.values()) <= Decimal(exchange.size)
    assert Decimal(exchange.orders["native"]["qty"]) == Decimal(exchange.size)
    assert broker.reconcile()["protection_confirmed"] is True


def test_partial_then_completed_near_tp_is_not_recreated(ladder):
    broker, exchange = open_ladder(ladder)
    near = live_targets(broker)["near"]
    exchange.fill(near["link_id"], .005)
    assert broker.reconcile()["protection_confirmed"] is True
    assert Decimal(exchange.orders[near["link_id"]]["leavesQty"]) == Decimal(".010")
    exchange.fill(near["link_id"], .010)
    broker.reconcile()
    before = list(exchange.writes)
    broker.reconcile()
    assert exchange.writes == before
    assert set(live_targets(broker)) == {"mid", "far"}
    assert len([c for c in broker.children() if c["kind"] == "tp"]) == 3
    assert Decimal(exchange.size) == Decimal(".085")
    assert Decimal(exchange.orders["native"]["qty"]) == Decimal(".085")


def test_adjust_reallocates_only_remnant_without_restoring_old_quota(ladder):
    broker, exchange = open_ladder(ladder)
    near = live_targets(broker)["near"]
    exchange.fill(near["link_id"], .015)
    broker.reconcile()
    old_live = live_targets(broker)
    payload = persist(broker, action="ADJUST", action_id="a2", entries=[],
                      side=ladder[2], hard_stop=ladder[3],
                      take_profits=targets(ladder[2], (.2, .3, .5)))
    broker.execute(payload, "a2")
    broker.reconcile()
    rows = live_targets(broker, "a2")
    assert {key: Decimal(row["request"]["qty"]) for key, row in rows.items()} == {
        "near": Decimal(".017"), "mid": Decimal(".025"), "far": Decimal(".042")}
    assert all(exchange.orders[row["link_id"]]["orderStatus"] == "Cancelled"
               for row in old_live.values())
    assert exchange.orders[near["link_id"]]["orderStatus"] == "Filled"
    assert sum(Decimal(row["request"]["qty"]) for row in rows.values()) == Decimal(".084")
    # Per-tier flooring leaves .001 under native Full SL, not overclosed or
    # falsely described as exact 100% TP coverage.
    assert Decimal(exchange.orders["native"]["qty"]) == Decimal(".085")
    assert broker.reconcile()["protection_confirmed"] is True


def test_small_fill_skips_subminimum_tier_and_keeps_floor_residual_protected(ladder):
    broker, exchange = open_ladder(ladder, (.1, .2, .7), filled=.007)
    rows = live_targets(broker)
    assert {key: Decimal(row["request"]["qty"]) for key, row in rows.items()} == {
        "mid": Decimal(".001"), "far": Decimal(".004")}
    assert Decimal(exchange.orders["native"]["qty"]) == Decimal(".007")
    assert broker.reconcile()["protection_confirmed"] is True


@pytest.mark.parametrize("fractions", [(.1, .2, .4), (.15, .25, .6), (.5, .3, .3)])
def test_core_preserves_fraction_limit_without_forcing_three_equal_tiers(fractions):
    payload, context = sample()
    payload["take_profits"] = [dict(id=str(index), price=102000 + index * 1000, fraction=fraction)
                               for index, fraction in enumerate(fractions)]
    if sum(fractions) > 1:
        with pytest.raises(ScenarioValidationError, match="exit fractions exceed position"):
            validate_scenario(payload, context)
    else:
        assert validate_scenario(payload, context)["risk"]["budget"] == 200
