"""Hand-computed synthetic execution/accounting regressions; no market claims."""
from dataclasses import replace

import pytest

from backtest.execution_replay import (EntryRequest, FundingEvent, PriceEvent,
                                      ReplayConfig, run_replay)
from core.scalp import Target


def config(**kw):
    base = ReplayConfig(1000, 1, 0, 0, 0, 0, 0, 1000, 10000, 100, 200, 3600)
    return replace(base, **kw)


def entry(**kw):
    base = EntryRequest(0, "long", 4, 90, 110, 10000)
    return replace(base, **kw)


def test_tp_half_stop_remainder_nav_and_fees():
    result = run_replay([PriceEvent(0, 100), PriceEvent(1000, 111),
                         PriceEvent(2000, 85)], entry(),
                        config(maker_fee=-0.001, taker_fee=0.002))
    assert [(f.reason, f.lots, f.price) for f in result.fills] == [
        ("entry", 4, 100), ("tp1", 2, 110), ("stop", 2, 85)]
    assert result.total_fees == pytest.approx(0.8 - 0.22 + 0.34)
    assert result.cash == pytest.approx(1000 + 20 - 30 - 0.92)
    assert result.nav_final == result.cash
    assert result.remaining_lots == 0
    assert result.status == "closed"
    assert result.max_drawdown > 0


def test_unrealized_nav_drawdown_not_cash_only_no_horizon_close():
    result = run_replay([PriceEvent(0, 100), PriceEvent(1000, 105),
                         PriceEvent(2000, 95)], entry(), config())
    assert len(result.fills) == 1
    assert result.cash == 1000
    assert result.unrealized_pnl == -20
    assert result.nav_final == 980
    assert result.max_drawdown == pytest.approx(40 / 1020)
    assert result.status == "open"


@pytest.mark.parametrize("side,stop,tp,sign", [("long", 90, 110, 1),
                                              ("short", 110, 90, -1)])
def test_funding_timestamp_boundaries_signed_and_deduplicated(side, stop, tp, sign):
    funding = [FundingEvent(0, .01, 100), FundingEvent(500, .01, 100),
               FundingEvent(500, .01, 100), FundingEvent(1000, -.005, 100)]
    result = run_replay([PriceEvent(0, 100), PriceEvent(1000, 100)],
                        entry(side=side, stop_price=stop, tp1_price=tp),
                        config(), funding)
    assert [f.lots for f in result.funding] == [0, 4, 4]
    assert result.total_funding == pytest.approx(sign * 2)
    assert result.cash == pytest.approx(1000 - sign * 2)
    assert len([p for p in result.nav if p.reason == "funding"]) == 3


def test_funding_before_same_time_exit_uses_held_quantity():
    result = run_replay([PriceEvent(0, 100), PriceEvent(1000, 80)], entry(),
                        config(), [FundingEvent(1000, .01, 80)])
    assert result.funding[0].lots == 4
    assert result.total_funding == pytest.approx(3.2)
    assert result.cash == pytest.approx(916.8)


def test_partial_entry_native_stop_latches_cancels_entry_and_shares_capacity():
    result = run_replay([PriceEvent(0, 100, 2), PriceEvent(1000, 85, 1),
                         PriceEvent(2000, 120, 1), PriceEvent(3000, 100, 9)],
                        entry(), config())
    assert [(f.reason, f.lots) for f in result.fills] == [
        ("entry", 2), ("stop", 1), ("stop", 1)]
    assert result.entered_lots == 2
    assert result.remaining_lots == 0


def test_first_entry_beyond_stop_rejected_without_unprotected_fallback():
    result = run_replay([PriceEvent(0, 85, 2)], entry(), config())
    assert result.entered_lots == result.remaining_lots == 0
    assert result.status == "rejected"
    assert not result.fills
    assert "ENTRY_GEOMETRY_REJECTED" in result.flags


def test_partial_tp_repeated_snapshots_never_repeat_original_half():
    result = run_replay([PriceEvent(0, 100), PriceEvent(1000, 111, 1),
                         PriceEvent(2000, 111, 1), PriceEvent(3000, 112, 5),
                         PriceEvent(4000, 80)], entry(), config())
    assert sum(f.lots for f in result.fills if f.reason == "tp1") == 2
    assert sum(f.lots for f in result.fills if f.reason == "stop") == 2


def test_further_targets_share_one_event_capacity():
    result = run_replay([PriceEvent(0, 100), PriceEvent(1000, 130, 2),
                         PriceEvent(2000, 130, 1)],
                        entry(further_targets=(Target(120, 1),)), config())
    assert [(f.reason, f.lots) for f in result.fills[1:]] == [("tp1", 2), ("tp2", 1)]
    assert result.remaining_lots == 1


@pytest.mark.parametrize("side,stop,tp,touch,through", [
    ("long", 90, 110, 100, 99), ("short", 110, 90, 100, 101)])
def test_passive_entry_requires_strict_trade_through(side, stop, tp, touch, through):
    result = run_replay([PriceEvent(0, touch), PriceEvent(1000, through)],
                        entry(side=side, stop_price=stop, tp1_price=tp, limit_price=100),
                        config())
    assert len(result.fills) == 1
    assert result.fills[0].ts_ms == 1000
    assert result.fills[0].price == 100


def test_tp_touch_does_not_fill():
    result = run_replay([PriceEvent(0, 100), PriceEvent(1000, 110)], entry(), config())
    assert len(result.fills) == 1


def test_partial_entry_timeout_freezes_actual_even_half():
    result = run_replay([PriceEvent(0, 100, 2), PriceEvent(1000, 111)],
                        entry(expiry_ms=1000), config())
    assert result.entered_lots == 2
    assert [(f.reason, f.lots) for f in result.fills] == [("entry", 2), ("tp1", 1)]
    assert "ENTRY_EXPIRED" in result.flags


@pytest.mark.parametrize("lots,targets", [(1, ()), (2, (Target(120, 2),))])
def test_incompatible_timeout_keeps_stop_suppresses_tp(lots, targets):
    result = run_replay([PriceEvent(0, 100, lots), PriceEvent(1000, 130),
                         PriceEvent(2000, 80)],
                        entry(expiry_ms=1000, further_targets=targets), config())
    assert not any(f.reason.startswith("tp") for f in result.fills)
    assert result.fills[-1].reason == "stop"
    assert "ENTRY_QUANTITY_OR_TARGETS_REQUIRE_RECONCILIATION_NO_TP" in result.flags


def test_entry_expiry_is_wall_clock_and_latency_does_not_extend_it():
    result = run_replay([PriceEvent(0, 100), PriceEvent(10000, 100)],
                        entry(expiry_ms=1000), config(entry_latency_ms=2000))
    assert not result.fills
    assert result.status == "expired"


def test_no_pre_entry_extreme_reuse():
    result = run_replay([PriceEvent(0, 200), PriceEvent(1000, 100),
                         PriceEvent(2000, 101)],
                        entry(submitted_ms=1000), config(early_distance=10))
    assert result.remaining_lots == 4
    assert all(p.stop_price == 90 for p in result.nav)


def test_amendment_latency_old_stop_protects_until_activation():
    result = run_replay([PriceEvent(0, 100), PriceEvent(1000, 109),
                         PriceEvent(1500, 95), PriceEvent(1999, 95),
                         PriceEvent(3000, 94)], entry(),
                        config(early_distance=10, amend_latency_ms=2000))
    assert result.fills[-1].reason == "stop"
    assert result.fills[-1].ts_ms == 3000
    assert next(p for p in result.nav if p.ts_ms == 1999).stop_price == 90
    assert result.nav[-1].stop_price == 99


def test_old_stop_breach_during_amendment_wait():
    result = run_replay([PriceEvent(0, 100), PriceEvent(1000, 109),
                         PriceEvent(1500, 85)], entry(),
                        config(early_distance=10, amend_latency_ms=2000))
    assert result.fills[-1].ts_ms == 1500
    assert result.fills[-1].price == 85


def test_zero_latency_amendment_not_retroactive_same_event():
    result = run_replay([PriceEvent(0, 100), PriceEvent(1000, 109)],
                        entry(), config(early_distance=10))
    assert result.nav[-1].stop_price == 90


def test_check_cadence_does_not_delay_native_stop():
    result = run_replay([PriceEvent(0, 100), PriceEvent(10, 85)], entry(),
                        config(check_interval_ms=100000))
    assert result.fills[-1].ts_ms == 10


def test_holding_exit_at_next_available_check_not_invented_timestamp():
    result = run_replay([PriceEvent(0, 100), PriceEvent(900, 101),
                         PriceEvent(2500, 102)], entry(),
                        config(max_hold_seconds=1, check_interval_ms=2000))
    assert result.fills[-1].reason == "holding_deadline"
    assert result.fills[-1].ts_ms == 2500


@pytest.mark.parametrize("side,stop,tp,last,expected", [
    ("long", 90, 110, 80, 79.2), ("short", 110, 90, 120, 121.2)])
def test_market_entry_and_gap_stop_adverse_slippage(side, stop, tp, last, expected):
    result = run_replay([PriceEvent(0, 100), PriceEvent(1000, last)],
                        entry(side=side, stop_price=stop, tp1_price=tp),
                        config(market_slippage_bps=100))
    assert result.fills[0].price == (101 if side == "long" else 99)
    assert result.fills[-1].price == pytest.approx(expected)


def test_gross_cap_rejects_full_request_even_if_partial_liquidity():
    result = run_replay([PriceEvent(0, 100, 1)], entry(), config(max_gross_notional=399))
    assert result.status == "rejected"
    assert not result.fills


@pytest.mark.parametrize("factory", [
    lambda: PriceEvent(True, 100), lambda: PriceEvent(0, float("nan")),
    lambda: PriceEvent(0, 100, True), lambda: PriceEvent(0, 100, -1),
    lambda: PriceEvent(0, 100, trend_permission=1),
    lambda: FundingEvent(0, float("inf"), 100),
    lambda: FundingEvent(0, 1, 100), lambda: FundingEvent(0, 0, False),
    lambda: entry(lots=True), lambda: entry(side="buy"),
    lambda: entry(expiry_ms=0), lambda: entry(further_targets=[]),
    lambda: entry(stop_price=120), lambda: entry(limit_price="100"),
    lambda: config(taker_fee=-.01), lambda: config(maker_fee=1),
    lambda: config(check_interval_ms=0), lambda: config(amend_latency_ms=True),
    lambda: config(lot_size=0), lambda: config(market_slippage_bps=10000),
])
def test_invalid_dataclass_inputs(factory):
    with pytest.raises(ValueError):
        factory()


@pytest.mark.parametrize("prices,funding", [
    ([], []), ([PriceEvent(1, 100), PriceEvent(0, 100)], []),
    ([PriceEvent(0, 100), PriceEvent(0, 100)], []),
    ([PriceEvent(0, 100), PriceEvent(2, 100)],
     [FundingEvent(1, .01, 100), FundingEvent(1, .02, 100)]),
    ([PriceEvent(0, 100), PriceEvent(2, 100)],
     [FundingEvent(2, .01, 100), FundingEvent(1, .01, 100)]),
    ([PriceEvent(0, 100)], [FundingEvent(1, .01, 100)]),
])
def test_replay_rejects_invalid_sequences(prices, funding):
    with pytest.raises(ValueError):
        run_replay(prices, entry(), config(), funding)


def test_quantity_cash_and_nav_conservation_across_seeded_paths():
    import random

    randomizer = random.Random(726)
    for _ in range(100):
        prices = [PriceEvent(i * 1000, randomizer.uniform(80, 130),
                             randomizer.randint(0, 4), bool(i % 2))
                  for i in range(20)]
        result = run_replay(prices, entry(), config(maker_fee=-.0001, taker_fee=.0005),
                            [FundingEvent(5500, .0001, 101),
                             FundingEvent(12500, -.0002, 103)])
        exits = sum(f.lots for f in result.fills if f.reason != "entry")
        assert result.entered_lots == exits + result.remaining_lots
        assert result.cash == pytest.approx(1000 + sum(f.realized_pnl for f in result.fills)
                                             - result.total_fees - result.total_funding)
        assert result.nav_final == pytest.approx(result.cash + result.unrealized_pnl)
        for event in prices:
            assert sum(f.lots for f in result.fills if f.ts_ms == event.ts_ms) <= event.max_fill_lots
        assert sum(f.lots for f in result.fills if f.reason == "tp1") <= result.entered_lots // 2


def test_nonrepresentable_price_rejected_as_value_error():
    with pytest.raises(ValueError):
        PriceEvent(0, 10 ** 1000)


@pytest.mark.parametrize("side,stop,tp,through", [
    ("long", 90, 110, 111), ("short", 110, 90, 89)])
def test_tp_settles_before_same_event_policy_enables_runner(side, stop, tp, through):
    result = run_replay([PriceEvent(0, 100, trend_permission=True),
                         PriceEvent(1000, through, trend_permission=True),
                         PriceEvent(2000, 100, trend_permission=True)],
                        entry(side=side, stop_price=stop, tp1_price=tp),
                        config(early_distance=10, runner_distance=30))
    assert [(f.reason, f.lots) for f in result.fills] == [("entry", 4), ("tp1", 2)]
    assert result.remaining_lots == 2
    assert result.status == "open"
    assert all(point.stop_price == stop for point in result.nav)


def test_resting_tp_then_holding_exit_share_event_liquidity():
    result = run_replay([PriceEvent(0, 100), PriceEvent(1000, 111, 3),
                         PriceEvent(2000, 112, 3)], entry(),
                        config(max_hold_seconds=1))
    assert [(f.ts_ms, f.reason, f.lots) for f in result.fills] == [
        (0, "entry", 4), (1000, "tp1", 2),
        (1000, "holding_deadline", 1), (2000, "holding_deadline", 1)]


@pytest.mark.parametrize("side,stop,tp,price", [
    ("long", 90, 110, 90), ("long", 90, 110, 89),
    ("long", 90, 110, 110), ("long", 90, 110, 111),
    ("short", 110, 90, 110), ("short", 110, 90, 111),
    ("short", 110, 90, 90), ("short", 110, 90, 89)])
def test_invalid_initial_stop_or_target_geometry_rejects(side, stop, tp, price):
    result = run_replay([PriceEvent(0, price)],
                        entry(side=side, stop_price=stop, tp1_price=tp), config())
    assert result.status == "rejected"
    assert not result.fills
    assert result.cash == 1000
    assert "ENTRY_GEOMETRY_REJECTED" in result.flags


@pytest.mark.parametrize("side,stop,tp", [("long", 90, 101), ("short", 110, 99)])
def test_slippage_crossing_first_target_rejects_before_entry(side, stop, tp):
    result = run_replay([PriceEvent(0, 100)],
                        entry(side=side, stop_price=stop, tp1_price=tp),
                        config(market_slippage_bps=100))
    assert result.status == "rejected"
    assert not result.fills
