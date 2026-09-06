"""Deterministic hand-ledger fixtures; no real market or performance queries."""
import math

import numpy as np
import pytest

from backtest.portfolio_replay import (
    BAR, DAY, DailyFeatures, Decision, PortfolioPolicy, _Order, _Replay,
    run_portfolio,
)


def feature(ts=0, atr=1.):
    return DailyFeatures(ts, 100., 99., atr, 1., 110., 90., 105., 95., 100.)


def bars(n=12, price=100., start=0):
    return np.array([[start + i * BAR, price, price, price, price]
                     for i in range(n)], dtype=float)


def run(data=None, callbacks=None, features=None, funding=(), **kwargs):
    data = bars() if data is None else data
    events = []
    policy = PortfolioPolicy(start_ms=int(data[0, 0]),
                             end_ms=int(data[-1, 0]) + BAR, **kwargs)
    summary = run_portfolio(data, funding, features or [feature()],
                            callbacks or {"T": lambda held, f: Decision(1, 1.)},
                            policy, events.append)
    return summary, events


def test_cash_constant_and_hash_repeatable():
    a, events = run(mode="cash", funding=[(0, .01), (BAR, -.01)])
    b, _ = run(mode="cash", funding=[(0, .01), (BAR, -.01)])
    assert a.daily_nav[-1][1] == 10000
    assert a.metrics["fees"] == a.metrics["funding"] == 0
    assert a.hashes == b.hashes
    assert a.counters["funding_events"] == 2
    assert events[0]["mark_price"] is None


@pytest.mark.parametrize("direction", [-1, 1])
def test_hand_entry_and_funding_ledger(direction):
    result, events = run(callbacks={"T": lambda held, f: Decision(direction, .1)},
                         funding=[(BAR * 2, .001)])
    entry = next(e for e in events if e["kind"] == "fill")
    q, fill = entry["quantity"], entry["price"]
    fee = q * fill * .00055
    funding = direction * q * 100 * .001
    assert result.metrics["fees"] == pytest.approx(fee)
    assert result.metrics["funding"] == pytest.approx(funding)
    assert result.metrics["final_nav"] == pytest.approx(
        10000 - fee - funding + direction * q * (100 - fill))
    assert entry["ts_ms"] == BAR


def test_shared_opposites_add_gross_not_net():
    result, _ = run(callbacks={"T": lambda h, f: Decision(1, .5),
                               "B": lambda h, f: Decision(-1, .5)},
                         features=[feature(atr=.1)])
    assert result.metrics["gross_max"] > .99
    assert result.metrics["net_abs_max"] < .501
    assert result.metrics["simultaneous_fraction"] > .8
    assert sum(p["quantity"] * 100 for p in result.final_positions.values()) <= (
        result.metrics["final_nav"] + 1e-8)


def test_heat_budget_resizes_quantity():
    result, _ = run(features=[feature(atr=10)])
    q = result.final_positions["T"]["quantity"]
    assert 0 < q < 10
    assert q * (20 + 100 * .00105) <= .02 * result.metrics["final_nav"]


def test_pending_competition_and_atomic_partial():
    result, events = run(callbacks={"T": lambda h, f: Decision(1, .8),
                                    "B": lambda h, f: Decision(-1, .8)},
                         features=[feature(atr=.1)], partial_entries=True)
    entries = [e for e in events if e.get("action") == "entry"]
    assert {e["sleeve"] for e in entries} == {"T", "B"}
    assert result.metrics["gross_max"] <= 1 + 1e-10
    assert result.counters["resized"] > 0
    assert all(e["remaining_lots"] >= 0 for e in entries)


def test_post_cost_one_lot_rejection():
    # One lot costs exactly initial NAV before costs, but cannot fit after them.
    result, _ = run(bars(price=10_000_000), mode="bh", initial_cash=10_000.)
    assert result.counters["fills"] == 0
    assert result.metrics["final_nav"] == 10000


@pytest.mark.parametrize("direction,gap,expected", [(1, 90., 89.955), (-1, 110., 110.055)])
def test_gap_stop_fills_adverse_open(direction, gap, expected):
    data = bars(3)
    data[2, 1:] = gap
    result, events = run(data, callbacks={"T": lambda h, f: Decision(direction, .1)})
    stop = next(e for e in events if e.get("action") == "stop")
    assert stop["price"] == pytest.approx(expected)
    assert stop["ts_ms"] == 2 * BAR
    assert result.final_positions["T"]["lots"] == 0


@pytest.mark.parametrize("path,expected_offset", [("OHLC", 100000 + round(100000 * 5.95 / 9)),
                                                   ("OLHC", round(100000 * 1.95 / 5))])
def test_continuous_segment_stop_at_stop_not_extreme(path, expected_offset):
    data = bars(2)
    data[1, 2:4] = (104, 95)
    result, events = run(data, path=path)
    entry = next(e for e in events if e.get("action") == "entry")
    stop = next(e for e in events if e.get("action") == "stop")
    assert stop["price"] == pytest.approx(entry["stop"] * .9995)
    assert stop["ts_ms"] == BAR + expected_offset
    assert result.counters["stops"] == 1


def test_joint_long_short_stop_order_follows_one_path():
    data = bars(2)
    data[1, 2:4] = (105, 95)
    callbacks = {"T": lambda h, f: Decision(1, .5), "B": lambda h, f: Decision(-1, .5)}
    _, ohlc = run(data, callbacks=callbacks)
    _, olhc = run(data, callbacks=callbacks, path="OLHC")
    assert [e["sleeve"] for e in ohlc if e.get("action") == "stop"] == ["B", "T"]
    assert [e["sleeve"] for e in olhc if e.get("action") == "stop"] == ["T", "B"]


def test_no_pre_entry_extreme_lookahead():
    data = bars(3)
    data[0, 3] = 50
    result, _ = run(data)
    assert result.counters["stops"] == 0


def test_partial_ttl_exact_boundary_and_immediate_fixed_stop():
    result, events = run(partial_entries=True, features=[feature(atr=.1)])
    entries = [e for e in events if e.get("action") == "entry"]
    assert len(entries) == 6
    assert max(e["ts_ms"] for e in entries) == 6 * BAR
    assert len({e["stop"] for e in entries}) == 1
    assert any(e["kind"] == "cancel" and e["reason"] == "ttl"
               and e["ts_ms"] == 7 * BAR for e in events)
    assert result.final_positions["T"]["pending_lots"] == 0
    for a, b in zip(entries, entries[1:]):
        assert b["lots"] <= max(1, a["remaining_lots"] // 2)


def test_partial_stop_cancels_remaining_and_does_not_reenter():
    data = bars(12)
    data[1, 3] = 95
    result, events = run(data, partial_entries=True)
    assert sum(e.get("action") == "entry" for e in events) == 1
    assert result.final_positions["T"]["pending_lots"] == 0
    assert result.final_positions["T"]["blocked_until"] == DAY


def test_daily_callback_gets_actual_flat_after_intraday_stop():
    data = bars(290)
    data[2, 3] = 95
    held = []
    def decision(actual, f):
        held.append(actual)
        return Decision(1 if f.available_at == 0 else actual, .5)
    result, _ = run(data, callbacks={"B": decision}, features=[feature(), feature(DAY)])
    assert held == [0, 0]
    assert result.counters["fills"] == 2


def test_midnight_gap_stop_blocks_that_same_daily_signal():
    data = bars(290)
    data[288:, 1:] = 95
    held = []
    def decision(actual, f):
        held.append(actual)
        return Decision(1, .5)
    result, events = run(data, callbacks={"T": decision}, features=[feature(), feature(DAY)],
                         funding=[(DAY, .001)])
    assert held == [0, 0]
    assert result.final_positions["T"]["blocked_until"] == DAY * 2
    boundary = [e for e in events if e["ts_ms"] == DAY]
    assert boundary[0]["kind"] == "funding"
    assert any(e.get("action") == "stop" for e in boundary)
    assert result.final_positions["T"]["lots"] == 0


def test_identical_daily_target_no_fee_and_stop_never_loosened():
    result, events = run(bars(290), features=[feature(), feature(DAY, atr=10)],
                         fee_rate=0., slippage=0.)
    entries = [e for e in events if e.get("action") == "entry"]
    assert len(entries) == 1
    assert result.final_positions["T"]["stop"] == 98


def test_delay30_and_funding_before_first_fill():
    _, events = run(delay_ms=6 * BAR, funding=[(6 * BAR, .01)])
    at_fill = [e for e in events if e["ts_ms"] == 6 * BAR]
    assert at_fill[0]["kind"] == "funding"
    assert at_fill[0]["payments"] == [0., 0., 0.]
    assert next(e for e in at_fill if e.get("action") == "entry")["ts_ms"] == 6 * BAR


def test_gross_drift_forced_reduction_next_open_bh():
    data = bars(4)
    data[2:, 1:] = 80
    result, events = run(data, mode="bh", funding=[(2 * BAR, .01)])
    assert result.counters["forced_reductions"] > 0
    assert any(e.get("action") == "forced" for e in events)
    assert not any(e.get("action") == "stop" for e in events)
    assert result.metrics["gross_max"] > 1
    assert result.final_positions["T"]["quantity"] * 80 <= result.metrics["final_nav"] + 1e-8


def test_intrabar_breach_waits_until_next_open():
    data = bars(4)
    data[2, 2] = 120
    # Short loss expands both notional and consumes NAV.
    result, events = run(data, callbacks={"T": lambda h, f: Decision(-1, 1.)},
                         features=[feature(atr=.01)], slippage=0., fee_rate=0.)
    # Tight native stop fires first, so no unprotected exposure survives.
    assert result.counters["stops"] == 1
    assert not any(e.get("action") == "forced" for e in events)


def test_scale_changes_real_lots_not_output_nav():
    full, _ = run(mode="bh")
    half, _ = run(mode="bh", scale=.5)
    assert half.final_positions["T"]["lots"] < full.final_positions["T"]["lots"]
    assert half.metrics["fees"] < full.metrics["fees"]


def test_prefix_determinism():
    data = bars(290)
    first, events1 = run(data[:288])
    data[289, 2] = 9999
    _, events2 = run(data)
    assert [e for e in events2 if e["ts_ms"] < DAY] == events1
    assert first.daily_nav[0][0] == DAY


@pytest.mark.parametrize("mutation", ["nan", "gap", "ohlc", "funding_duplicate"])
def test_invalid_data_fail_closed(mutation):
    data = bars()
    funding = ()
    if mutation == "nan":
        data[2, 2] = math.nan
    elif mutation == "gap":
        data[2, 0] += BAR
    elif mutation == "ohlc":
        data[2, 3] = 101
    else:
        funding = [(0, 0.), (0, .01)]
    with pytest.raises(ValueError):
        run(data, funding=funding)


def test_nonpositive_nav_raises_and_no_further_orders():
    data = bars(4)
    data[2:, 1:] = .01
    with pytest.raises(ValueError, match="nonpositive NAV"):
        run(data, mode="bh", funding=[(2 * BAR, .5)])


def test_resource_callback_can_stop_at_safe_boundary():
    checkpoints = []
    def guard(state):
        checkpoints.append(state)
        if state["ts_ms"] == BAR * 2:
            raise RuntimeError("INCOMPLETE")
    with pytest.raises(RuntimeError, match="INCOMPLETE"):
        run(resource_check=guard)
    assert checkpoints[-1]["counters"]["fills"] == 1
    assert len(checkpoints[-1]["event_digest"]) == 64


def test_protected_reductions_are_full_even_in_partial_mode():
    e = _Replay(PortfolioPolicy(0, BAR, partial_entries=True), {}, None)
    e.orders[0] = _Order(1, 100, 100, 0, 1., 200., 1.)
    e.entries(100, 0)
    assert e.pos[0].lots == 50
    e.stop(0, 98, 1)
    assert e.pos[0].lots == 0
    assert e.orders[0] is None


def test_full_budget_reversal_waits_exit_then_reallocates():
    def decision(h, f):
        return Decision(1 if f.available_at == 0 else -1, 1.)
    result, events = run(bars(290), callbacks={"T": decision},
                         features=[feature(atr=.1), feature(DAY, atr=.1)],
                         fee_rate=0., slippage=0.)
    assert result.final_positions["T"]["direction"] == -1
    assert result.final_positions["T"]["quantity"] == 100.
    fills = [e for e in events if e.get("kind") == "fill"]
    assert [e["action"] for e in fills] == ["entry", "voluntary", "entry"]
    assert fills[-1]["ts_ms"] == fills[-2]["ts_ms"] == DAY + BAR
    assert any(e["kind"] == "wait_exit" and e["reserved_lots"] == 0 for e in events)
    assert result.counters["completed_campaigns"] == 1


def test_known_open_mark_insolvency_preserves_ledger():
    from backtest.portfolio_replay import PortfolioReplayError
    data = bars(4)
    data[2:, 1:] = .01
    with pytest.raises(PortfolioReplayError) as caught:
        run(data, mode="bh", funding=[(2 * BAR, .5)])
    state = caught.value.state
    assert state["nav"] < 0
    assert state["ts_ms"] == 2 * BAR
    assert state["positions"]["T"]["lots"] > 0
    assert len(state["event_digest"]) == 64


def test_funding_uses_last_closed_not_current_bar_close():
    data = bars(3)
    data[2, 2:] = (110, 100, 110)
    _, events = run(data, mode="bh", funding=[(2 * BAR, .001)])
    event = next(e for e in events if e["kind"] == "funding")
    assert event["mark_price"] == 100


def test_profitable_stop_heat_never_negative_and_giveback_separate():
    e = _Replay(PortfolioPolicy(0, BAR), {}, None)
    s = e.pos[0]
    s.direction, s.lots, s.entry, s.stop = 1, 1000, 100., 105.
    nav, gross, heat, net, giveback = e.totals(110)
    assert heat == pytest.approx(110 * .00105)
    assert giveback == 5
    assert nav == 10010


def test_continuous_year_boundary_keeps_original_position():
    start = 1672444800000  # 2022-12-31 UTC, synthetic only.
    result, events = run(bars(576, start=start), mode="bh")
    assert len(result.daily_nav) == 2
    assert result.daily_nav[0][0] == 1672531200000
    assert sum(e.get("action") == "entry" for e in events) == 1
    assert result.final_positions["T"]["quantity"] > 0


def test_mapping_loader_rows_supported():
    data = [dict(open_time=i * BAR, open=100, high=100, low=100, close=100)
            for i in range(2)]
    result = run_portfolio(data, [dict(funding_time=0, rate=0)], [feature()], {},
                            PortfolioPolicy(0, 2 * BAR, mode="cash"))
    assert result.metrics["final_nav"] == 10000


def test_nonpositive_stop_is_invalid_not_silent_cash():
    with pytest.raises(ValueError, match="nonpositive protective stop"):
        run(features=[feature(atr=100)])


def test_intrabar_mdd_records_extreme_without_any_trace_window():
    data = bars(3)
    data[2, 2] = 120
    result, events = run(data, mode="bh", fee_rate=0., slippage=0.)
    assert result.metrics["mtm_mdd"] == pytest.approx(1 - 10000 / 12000)
    assert not any(e["kind"] == "audit" for e in events)


def test_trace_is_fixed_window_and_bounded():
    start = 1648857600000  # 2022-04-02 UTC
    result, events = run(bars(288, start=start), mode="cash")
    audits = [e for e in events if e["kind"] == "audit"]
    assert 0 < len(audits) <= 5000
    assert all(start <= e["ts_ms"] < start + DAY for e in audits)


def test_pending_price_drift_cannot_double_daily_notional():
    data = bars(3)
    data[1:, 1:] = 200
    result, events = run(data, callbacks={"T": lambda h, f: Decision(1, .1)},
                         features=[feature(atr=.1)], fee_rate=0., slippage=0.)
    entry = next(e for e in events if e.get("action") == "entry")
    assert entry["quantity"] == 5.
    assert result.final_positions["T"]["quantity"] * 200 == 1000.


def test_short_pending_reserves_at_least_known_market_gross():
    e = _Replay(PortfolioPolicy(0, BAR), {}, None)
    e.orders[0] = _Order(-1, 80_000, 80_000, BAR, .1, 200., 1.)
    e.orders[1] = _Order(1, 20_000, 20_000, BAR, .1, 200., 1.)
    e.reserve(100., 0)
    reserved = sum(e.reservation(i, 100.)[0] for i in range(3))
    conservative_gross = sum(o.remaining * .001 * max(
        100., 100. * (1 + o.direction * e.p.slippage))
        for o in e.orders if o is not None)
    assert e.reservation(0, 100.)[0] == 8000.
    assert conservative_gross == pytest.approx(reserved)
    assert conservative_gross <= 10_000. + 1e-8
    assert e.orders[1].remaining < 20_000


def test_post_cost_admission_counts_short_pending_at_market_not_sell_fill():
    e = _Replay(PortfolioPolicy(0, BAR), {}, None)
    e.orders[0] = _Order(1, 1000, 1000, 0, .1, 200., 1.)
    e.orders[1] = _Order(-1, 99_000, 99_000, BAR, .1, 200., 1.)
    # The proposed long leaves gross=100+9900, but its execution costs lower
    # NAV below 10000. Valuing pending shorts at 99.95 would hide the breach.
    assert not e.admissible(0, 1000, 100.)


def test_campaign_loss_count_excludes_flat_breakeven():
    e = _Replay(PortfolioPolicy(0, BAR, fee_rate=0., slippage=0.), {}, None)
    for price in (110., 100., 95.):
        e.orders[0] = _Order(1, 1000, 1000, 0, 1., 200., 1.)
        e.entries(100., 0)
        e.reduce(0, 1000, price, 1, "voluntary")
    s = e.pos[0]
    assert (s.completed, s.wins, s.losses) == (3, 1, 1)
    assert s.win_pnl == 10.
    assert s.loss_pnl == -5.


def test_profit_factor_payoff_are_incremental_completed_campaign_metrics():
    data = bars(866)
    data[288:576, 1:] = 110.
    data[864:, 1:] = 95.
    def decision(h, f):
        return Decision(1 if f.available_at in (0, 2 * DAY) else 0, .1)
    result, _ = run(data, callbacks={"T": decision},
                    features=[feature(i * DAY, atr=10.) for i in range(4)],
                    fee_rate=0., slippage=0.)
    assert result.metrics["winning_campaigns"] == 1
    assert result.metrics["losing_campaigns"] == 1
    pnl = result.metrics["sleeve_attribution"]["T"]
    ratio = pnl["win_pnl"] / -pnl["loss_pnl"]
    assert result.metrics["profit_factor"] == pytest.approx(ratio)
    assert result.metrics["payoff_ratio"] == pytest.approx(ratio)


@pytest.mark.parametrize("direction", [-1, 1])
@pytest.mark.parametrize("new_high", [0., 1.])
def test_underwater_duration_includes_exact_recovery_sample(direction, new_high):
    e = _Replay(PortfolioPolicy(0, BAR, fee_rate=0., slippage=0.), {}, None)
    s = e.pos[0]
    s.direction, s.lots, s.entry, s.stop = direction, 1000, 100., 100. - direction * 10.
    e.sample(0, 100.)
    e.sample(100_000, 100. - direction * 5.)
    assert e.underwater_ms == 100_000
    e.sample(200_000, 100. + direction * new_high)
    assert e.underwater_ms == 200_000
    assert e.underwater_start is None
    assert e.peak_ts == 200_000
    e.sample(250_000, 100. - direction)
    assert e.underwater_start == 200_000
    assert e.underwater_ms == 200_000


def test_underwater_duration_is_zero_without_any_drawdown():
    e = _Replay(PortfolioPolicy(0, BAR), {}, None)
    for ts in (0, 100_000, 200_000):
        e.sample(ts, 100.)
    assert e.underwater_ms == 0
    assert e.underwater_start is None


def test_underwater_duration_includes_unrecovered_final_mark():
    e = _Replay(PortfolioPolicy(0, BAR, fee_rate=0., slippage=0.), {}, None)
    s = e.pos[0]
    s.direction, s.lots, s.entry, s.stop = 1, 1000, 100., 90.
    e.sample(0, 100.)
    e.sample(100_000, 95.)
    e.sample(BAR - 1, 96.)
    assert e.underwater_start == 0
    assert e.underwater_ms == BAR - 1
