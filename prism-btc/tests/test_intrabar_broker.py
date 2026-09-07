"""Hand-calculated kernel tests, with no broker/network dependency."""
from dataclasses import replace

import pytest

from backtest.intrabar_broker import Broker, Config, EntryIntent, ReplayInsolventError, run_replay


def cfg(**kw):
    return Config(**({"maker_fee": 0, "taker_fee": 0, "slippage": 0, "spread": 0,
                      "participation": 1} | kw))


def broker(**kw):
    b = Broker(cfg(**kw))
    b.price = b.mark = 101
    b.capacity = 1000
    return b


def intent(pid="a", **kw):
    return EntryIntent(**({"parent_id": pid, "lane": "main", "side": "long", "lots": 10,
                          "limit": 100., "stop": 90., "tp1": 110., "leverage": 10,
                          "tranche": 0, "expires_at": 10_000_000} | kw))


def advance(b, ts, price, funds=None):
    b.segment(ts, price, price, [] if funds is None else funds)


def fills(b, entry=None):
    return [e for e in b.events if e["kind"] == "fill" and (entry is None or e["entry"] == entry)]


def filled(**kw):
    b = broker(**kw)
    b.submit(intent(), 0)
    advance(b, 5000, 101)
    advance(b, 10_000, 99)
    return b


def test_activation_prevents_prior_segment_low_fill():
    b = broker(entry_latency_ms=20_000)
    b.submit(intent(), 0)
    advance(b, 10_000, 99)
    assert not fills(b)
    advance(b, 20_000, 101)
    assert not fills(b)
    advance(b, 30_000, 99)
    assert fills(b)[0]["ts"] == 25_500


def test_strict_one_tick_not_touch():
    b = broker()
    b.submit(intent(), 0)
    advance(b, 5000, 101)
    advance(b, 6000, 100)
    assert not fills(b)
    advance(b, 7000, 99.9)
    assert fills(b)[0]["lots"] == 10


def test_post_only_marketable_reject_at_activation():
    b = broker()
    b.submit(intent(), 0)
    advance(b, 5000, 99)
    assert not fills(b)
    assert any(e.get("reason") == "post_only_marketable" for e in b.events)


def test_funding_exact_before_and_after_fill_uses_mark():
    b = broker()
    b.submit(intent(), 0)
    advance(b, 5000, 101)
    # Strict 99.9 crossing occurs at 7750. Funding at exact fill time is before fill.
    b.segment(10_000, 99, 200, [(7749, .01), (7750, .01), (7751, .01)])
    e = [e for e in b.events if e["kind"] == "funding"]
    assert len(e) == 1
    assert e[0]["ts"] == 7751
    assert e[0]["amount"] == pytest.approx(.01 * (101 + 99 * 2751 / 5000) * .01)
    assert b.cash == pytest.approx(10_000 - e[0]["amount"])


@pytest.mark.parametrize("ack,expected", [(6000, 0), (9000, 10)])
def test_cancel_crossing_race_keeps_reservation_until_ack(ack, expected):
    b = broker(cancel_latency_ms=ack - 5000)
    b.submit(intent(), 0)
    advance(b, 5000, 101)
    b.cancel("a", 5000, "test")
    assert b.pending_entries()[0]["lots"] == 10
    advance(b, 10_000, 99)
    assert sum(e["lots"] for e in fills(b, True)) == expected
    assert not b.pending_entries()


def test_gap_stop_uses_current_price_not_stop():
    b = filled(slippage=.01)
    b.ts = 20_000
    b.price = b.mark = 80
    b._process()
    assert fills(b, False)[0]["price"] == pytest.approx(79.2)
    assert not b.positions()
    assert b.closed_trades[0]["net_pnl"] == pytest.approx(-.208)


def test_partial_stop_latches_across_rebound_and_capacity_never_minted():
    b = filled()
    b.capacity = 3
    advance(b, 20_000, 80)
    assert b.positions()[0].lots == 7
    assert b.positions()[0].closing
    advance(b, 30_000, 120)
    assert b.positions()[0].lots == 7
    b.capacity = 7
    b._process()
    assert not b.positions()
    assert all(e["reason"] == "stop" for e in fills(b, False))


def test_shared_capacity_not_reset_by_multiple_quotes_or_orders():
    b = broker()
    b.capacity = 4
    b.submit(intent(), 0)
    advance(b, 5000, 101)
    advance(b, 10_000, 99)
    advance(b, 20_000, 98)
    assert sum(e["lots"] for e in fills(b)) == 4
    assert b.capacity == 0
    assert b.pending_entries()[0]["lots"] == 6


def test_late_entry_during_stop_cancel_joins_same_latched_parent():
    b = broker(cancel_latency_ms=10_000)
    b.capacity = 4
    b.submit(intent(), 0)
    advance(b, 5000, 101)
    advance(b, 10_000, 99)
    b.capacity = 4
    advance(b, 11_000, 89)
    assert not b.positions()
    assert not b.closed_trades  # Six entry lots remain live until ACK.
    b.capacity = 6
    advance(b, 12_000, 89)
    assert b.positions()[0].lots == 6
    assert b.positions()[0].closing
    b.capacity = 6
    advance(b, 13_000, 95)
    assert not b.positions()
    assert len(b.closed_trades) == 1
    assert b.closed_trades[0]["filled_lots"] == b.closed_trades[0]["exited_lots"] == 10


def test_tp_quota_only_after_entry_settled_and_exact_integer_third():
    b = broker()
    b.capacity = 4
    b.submit(intent(), 0)
    advance(b, 5000, 101)
    advance(b, 10_000, 99)
    b.capacity = 50
    advance(b, 20_000, 120)
    assert not fills(b, False)
    b.cancel("a", 20_000, "settle")
    advance(b, 22_000, 120)
    assert fills(b, False)[0]["lots"] == 1
    assert b.positions()[0].tp1_complete
    advance(b, 30_000, 80)
    assert sum(e["lots"] for e in fills(b, False)) == 4


def test_one_lot_tp_skips_no_minted_dust():
    b = broker()
    b.submit(intent(lots=1), 0)
    advance(b, 5000, 101)
    advance(b, 10_000, 99)
    advance(b, 20_000, 120)
    assert not fills(b, False)
    assert b.positions()[0].tp1_complete
    b.reduce("a", 1, 20_000, "last_lot")
    advance(b, 25_000, 120)
    assert not b.positions()


def test_amend_delay_old_stop_and_late_ack_cannot_loosen():
    b = filled(amend_latency_ms=10_000)
    b.amend_stop("a", 99, 10_000)
    advance(b, 15_000, 95)
    assert b.positions()[0].stop == 90
    b.amend_stop("a", 97, 15_000)
    advance(b, 20_000, 101)
    assert b.positions()[0].stop == 99
    advance(b, 25_000, 101)
    assert b.positions()[0].stop == 99


def test_fixed_leverage_and_invalid_lots_prices():
    for change in ({"lots": True}, {"lots": 1.5}, {"limit": float("nan")}, {"stop": 105}, {"leverage": 5}):
        with pytest.raises(ValueError):
            broker().submit(intent(**change), 0)


def test_shared_risk_clips_pending_and_preserves_margin():
    b = broker(taker_fee=.01)
    b.price = b.mark = 10_000
    a = intent(lots=1_000_000, limit=9990., stop=9000., tp1=11000.)
    accepted = b.submit(a, 0)
    assert 0 < accepted < a.lots
    assert b.snapshot()["heat"] <= 500
    assert b.snapshot()["margin"] <= b.nav
    swing = intent("s", lane="swing", leverage=5, order_type="ioc", limit=10010., stop=9900., tp1=None, lots=1_000_000)
    b.submit(swing, 0)
    s = b.snapshot()
    assert s["heat"] <= 650
    assert s["lanes"]["swing"]["heat"] <= 150
    assert s["gross"] <= 8 * s["nav"]
    assert s["margin"] <= s["nav"]
    assert len(b.pending_reservations()) == 2


def test_common_margin_beats_independent_wallets():
    b = broker(taker_fee=.001)
    b.price = b.mark = 10000
    b.submit(intent(lots=7000, limit=9999., stop=9998., tp1=11000.), 0)
    n = b.submit(intent("s", lane="swing", leverage=5, order_type="ioc", lots=5000,
                        limit=10001., stop=10000., tp1=None), 0)
    assert n < 1500
    assert b.snapshot()["margin"] <= b.nav


@pytest.mark.parametrize("side,limit,stop,tp", [("long", 100, 90, 110), ("short", 102, 110, 90)])
def test_conflicting_pending_parent_rejected(side, limit, stop, tp):
    b = broker()
    b.submit(intent(), 0)
    assert b.submit(intent("b", side=side, limit=limit, stop=stop, tp1=tp), 0) == 0


def test_ioc_partial_cancel_and_limit_cap():
    b = broker(slippage=.001)
    b.capacity = 3
    b.submit(intent("s", lane="swing", leverage=5, order_type="ioc", limit=102., tp1=None), 0)
    advance(b, 5000, 101)
    assert fills(b)[0]["lots"] == 3
    assert fills(b)[0]["price"] == pytest.approx(101.2)
    assert not b.pending_entries()
    c = broker(slippage=.02)
    c.submit(intent("s", lane="swing", leverage=5, order_type="ioc", limit=102., tp1=None), 0)
    advance(c, 5000, 101)
    assert not fills(c)
    assert not c.pending_entries()


def bar(ts, **kw):
    return {"ts": ts, "open": 101., "high": 102., "low": 99., "close": 101., "volume": 1., **kw}


def test_replay_prefix_invariance_and_open_end_no_fake_close():
    bars = [bar(0, prior_volume=1), bar(300_000), bar(600_000)]
    def signal(b, ts):
        if ts == 0:
            b.submit(intent(), ts)
    a = run_replay(bars[:2], [], None, cfg(), signal)
    z = run_replay(bars, [], None, cfg(), signal)
    assert [e for e in a.events if e["kind"] != "replay_end"] == [e for e in z.events if e["ts"] < 600_000 and e["kind"] != "replay_end"]
    assert a.positions() and not a.closed_trades
    assert a.events[-1]["disposition"] == "OPEN_AT_END"
    assert a.snapshot()["nav"] == pytest.approx(a.cash + .01 * (a.mark - 100))


def test_first_volume_missing_zero_no_minted_capacity():
    b = run_replay([bar(0)], [], None, cfg(), lambda b, ts: b.submit(intent(), ts))
    assert not fills(b)


def test_current_bar_volume_does_not_change_current_fills():
    def signal(b, ts):
        if ts == 0:
            b.submit(intent(), ts)
    a = run_replay([bar(0, prior_volume=.004, volume=0)], [], None, cfg(), signal)
    z = run_replay([bar(0, prior_volume=.004, volume=10000)], [], None, cfg(), signal)
    assert fills(a) == fills(z)
    assert fills(a)[0]["lots"] == 4


def test_boundary_funding_uses_gap_mark_before_old_stop():
    bars = [bar(0, prior_volume=1), bar(300_000, open=80., high=85., low=75., close=80.)]
    marks = [bar(0), bar(300_000, open=70., high=85., low=65., close=80.)]
    b = run_replay(bars, [{"ts": 300_000, "rate": .01}], marks, cfg(), lambda b, ts: b.submit(intent(), ts) if ts == 0 else None)
    e = [e for e in b.events if e["ts"] == 300_000]
    assert e[0]["kind"] == "funding"
    assert e[0]["mark"] == 70
    assert fills(b, False)[0]["price"] == 80


def test_confirmed_target_reserved_no_rollforward():
    b = broker()
    b.confirm_callback = lambda *_: False
    assert b.submit(intent(mode="confirmed"), 0) == 10
    assert b.pending_entries()[0]["lots"] == 10
    advance(b, 5000, 101)
    advance(b, 10_000, 99)
    assert fills(b)[0]["lots"] == 4
    advance(b, 1_805_000, 101)
    advance(b, 3_605_000, 101)
    assert not b.pending_entries()
    assert sum(e["lots"] for e in fills(b, True)) == 4


def test_cash_identity_fees_funding_realized_and_mark_dd():
    b = filled(maker_fee=.0002, taker_fee=.00055)
    b.funding_event(.001)
    advance(b, 20_000, 89)
    s = b.snapshot()
    assert s["cash"] == pytest.approx(10000 + s["realized_price"] - s["fees"] - s["funding"])
    assert s["nav"] == s["cash"]
    assert b.max_drawdown > 0 and b.drawdown_witness


@pytest.mark.parametrize("bars", [[bar(0), bar(300001)], [bar(0, high=90)], [bar(0, volume=True)], [bar(0, low=float("nan"))]])
def test_invalid_bar_data_rejected(bars):
    with pytest.raises(ValueError):
        run_replay(bars, [], None, cfg(), lambda *_: None)


def test_immutable_position_views():
    b = filled()
    p = b.positions()[0]
    with pytest.raises(AttributeError):
        p.lots = 100
    assert replace(p, lots=100).lots == 100
    assert b.positions()[0].lots == 10


def test_exact_cancel_ack_precedes_fill_crossing():
    b = broker(cancel_latency_ms=2750)
    b.submit(intent(), 0)
    advance(b, 5000, 101)
    b.cancel("a", 5000)
    advance(b, 10000, 99)
    assert not fills(b)


def test_short_funding_credit_stop_and_risk_money():
    b = broker()
    b.submit(intent(side="short", limit=102., stop=110., tp1=90.), 0)
    advance(b, 5000, 101)
    advance(b, 10000, 103)
    assert b.positions()[0].initial_risk == pytest.approx(.08)
    b.funding_event(.01)
    assert b.funding == pytest.approx(-.0103)
    advance(b, 20000, 111)
    assert sum(e["lots"] for e in fills(b, False)) == 10
    assert all(e["action"] == "BUY" for e in fills(b, False))


def test_prices_all_exchange_ticks():
    b = broker(slippage=.00055)
    b.submit(intent(limit=100.049, stop=90.001, tp1=110.019), 0)
    advance(b, 5000, 101)
    advance(b, 10000, 99)
    assert b.positions()[0].stop == 90.0
    advance(b, 20000, 80)
    assert all(e["price"] * 10 == round(e["price"] * 10) for e in fills(b))


def test_native_swing_ioc_partial_has_no_tp():
    b = broker()
    b.capacity = 3
    b.submit(intent("s", lane="swing", leverage=5, order_type="ioc", limit=102., tp1=None), 0)
    advance(b, 5000, 101)
    assert b.positions()[0].tp1 is None
    assert not b.pending_entries()
    b.capacity = 100
    advance(b, 10000, 1000)
    assert not fills(b, False)


@pytest.mark.parametrize("side,limit,stop,tp,expected", [("long", 100.09, 90.09, 110.01, 90.), ("short", 102.01, 110.01, 90.01, 110.1)])
def test_stop_normalization_never_tightens(side, limit, stop, tp, expected):
    b = broker()
    b.submit(intent(side=side, limit=limit, stop=stop, tp1=tp), 0)
    event = next(e for e in b.events if e["kind"] == "entry_submit")
    assert event["stop"] == expected
    assert event["original_prices"]["stop"] == stop
    assert b.snapshot()["heat"] <= b.nav * .05


@pytest.mark.parametrize("stop", [90., 89., 90.01])
def test_nonimproving_amend_does_not_cancel_entries(stop):
    b = broker()
    b.capacity = 4
    b.submit(intent(), 0)
    advance(b, 5000, 101)
    advance(b, 10000, 99)
    b.amend_stop("a", stop, 10000)
    assert not [e for e in b.events if e["kind"] in ("cancel_request", "amend_request")]
    assert b.pending_entries()[0]["lots"] == 6


def test_closed_receipt_exit_fill_and_settlement_times_distinct():
    b = broker(cancel_latency_ms=10000)
    b.capacity = 4
    b.submit(intent(), 0)
    advance(b, 5000, 101)
    advance(b, 10000, 99)
    b.capacity = 4
    advance(b, 11000, 89)
    exit_fill = fills(b, False)[-1]
    assert not b.closed_trades
    advance(b, 25000, 99)
    row = b.closed_trades[0]
    assert row["last_exit_fill_ms"] == exit_fill["ts"]
    assert row["parent_settled_ms"] > row["last_exit_fill_ms"]
    assert row["exit_reason"] == "stop"
    assert row["stop_triggered"] is True
    assert row["initial_risk"] == pytest.approx(.04)
    assert exit_fill["initial_risk"] == row["initial_risk"]


def test_terminal_signal_close_drains_late_entry_with_original_reason():
    b = broker(cancel_latency_ms=20000, market_latency_ms=5000)
    b.capacity = 4
    b.submit(intent(), 0)
    advance(b, 5000, 101)
    advance(b, 10000, 99)
    b.close_parent("a", 10000, "signal_exit")
    b.capacity = 4
    advance(b, 15000, 99)
    assert not b.positions() and not b.closed_trades
    b.capacity = 6
    advance(b, 16000, 99)
    assert b.positions()[0].lots == 6
    assert b.positions()[0].closing
    b.capacity = 6
    advance(b, 17000, 99)
    assert not b.positions()
    assert b.closed_trades[0]["filled_lots"] == 10
    assert b.closed_trades[0]["exit_reason"] == "signal_exit"
    assert not b.closed_trades[0]["stop_triggered"]
    assert {e["reason"] for e in fills(b, False)} == {"signal_exit"}


def test_native_stop_can_fire_while_terminal_market_close_waits_latency():
    b = filled(market_latency_ms=20000)
    b.close_parent("a", 10000, "signal_exit")
    advance(b, 20000, 80)
    assert b.closed_trades[0]["stop_triggered"]
    assert b.closed_trades[0]["exit_reason"] == "stop"
    assert b.closed_trades[0]["last_exit_fill_ms"] < 20000


def test_ioc_favorable_gap_invalid_actual_stop_rejects_not_unprotected():
    b = broker()
    b.submit(intent("s", lane="swing", leverage=5, order_type="ioc", limit=102., tp1=None), 0)
    advance(b, 5000, 80)
    assert not b.positions() and not fills(b)
    assert not b.pending_entries()
    assert any(e.get("reason") == "invalid_actual_sl_geometry" for e in b.events)


def test_quote_peak_is_before_tp_price_concession():
    b = filled()
    advance(b, 20000, 110.1)
    assert b.peak == pytest.approx(10000.101)
    assert b.nav == pytest.approx(10000.1007)
    assert b.max_drawdown >= .0003 / 10000.101


@pytest.mark.parametrize("kw", [{"slippage": 1}, {"spread": 1, "slippage": .5}, {"spread": .8, "slippage": .7}])
def test_invalid_executable_price_factors_rejected(kw):
    with pytest.raises(ValueError, match="positive executable"):
        cfg(**kw)


@pytest.mark.parametrize("command", ["submit", "cancel", "reduce", "amend_stop", "close_parent"])
def test_public_command_cannot_jump_time(command):
    b = filled()
    with pytest.raises(ValueError, match="broker.now"):
        if command == "submit":
            b.submit(intent("b"), 11000)
        elif command in ("cancel", "close_parent"):
            getattr(b, command)("a", 11000)
        else:
            getattr(b, command)("a", 1 if command == "reduce" else 95, 11000)
    assert b.now == 10000


def test_native_swing_rejects_tp():
    with pytest.raises(ValueError, match="no TP1"):
        broker().submit(intent("s", lane="swing", leverage=5, order_type="ioc"), 0)


def test_bar_open_timestamp_distinguishes_close_and_next_gap():
    observed = []
    b = run_replay([bar(0), bar(300000, open=105., high=106., close=105.)], [], None,
                   cfg(), lambda *_: None, valuation_sink=observed.append)
    rows = [r for r in observed if r["ts"] == 300000]
    assert {r["bar_open_ts"] for r in rows} == {0, 300000}
    assert b.events[-1]["bar_open_ts"] == 300000


def test_finalized_parent_removed_from_hot_store_and_pending_reduce_terminal():
    b = filled(market_latency_ms=20000)
    b.reduce("a", 10, 10000, "strategy")
    advance(b, 15000, 80)
    assert b.parents["a"].final
    assert not b.active_parents
    assert b.pending_reductions("a") == 0
    assert any(e["kind"] == "reduce_cancel" for e in b.events)
    advance(b, 30000, 80)
    assert sum(e["lots"] for e in fills(b, False)) == 10


def test_tp_then_queued_reduce_clamps_without_negative_remaining():
    b = filled(market_latency_ms=20000)
    b.reduce("a", 10, 10000, "strategy")
    advance(b, 15000, 111)
    assert fills(b, False)[0]["lots"] == 3
    advance(b, 30000, 111)
    assert sum(e["lots"] for e in fills(b, False)) == 10
    assert b.pending_reductions("a") == 0
    assert all(r["remaining"] == 0 for r in b.parents["a"].reductions)


def test_nonpositive_nav_explicitly_invalidates_replay():
    b = filled()
    b.mark = -1_000_000
    with pytest.raises(ReplayInsolventError):
        b._value()
    assert b.insolvent
    assert b.events[-1]["kind"] == "replay_invalid"


def test_be_stop_kind_fixed_for_native_cooldown():
    b = filled()
    b.amend_stop("a", 100, b.now, "be")
    advance(b, 12000, 101)
    advance(b, 15000, 99)
    assert b.closed_trades[0]["stop_kind"] == "be"
    assert b.closed_trades[0]["stop_triggered"]


def test_non_be_trailing_stop_kind_is_sl():
    b = filled()
    advance(b, 15000, 105)
    b.amend_stop("a", 103, b.now, "trailing")
    advance(b, 17000, 105)
    advance(b, 20000, 102)
    assert b.closed_trades[0]["stop_kind"] == "sl"


def test_ioc_valid_execution_but_last_trade_past_stop_triggers_immediately():
    b = broker(slippage=.001)
    b.submit(intent("s", lane="swing", leverage=5, order_type="ioc", limit=102., stop=100., tp1=None), 0)
    advance(b, 5000, 99.99)
    assert fills(b, True)[0]["price"] > 100
    assert fills(b, False)[0]["ts"] == fills(b, True)[0]["ts"]
    assert b.closed_trades[0]["stop_triggered"]
    assert not b.positions()


@pytest.mark.parametrize("side", ["long", "short"])
@pytest.mark.parametrize("stop,expected", [(99.9, "sl"), (100., "be"), (100.1, "sl")])
def test_exact_entry_only_be_not_one_tick_neighbour(side, stop, expected):
    b = broker()
    if side == "short":
        b.price = b.mark = 99
    b.submit(intent(side=side, limit=100., stop=90. if side == "long" else 110.,
                    tp1=110. if side == "long" else 90.), 0)
    advance(b, 5000, b.price)
    advance(b, 10000, 99. if side == "long" else 101.)
    assert b.positions()[0].entry == 100
    advance(b, 15000, 105. if side == "long" else 95.)
    b.amend_stop("a", stop, b.now, "test")
    advance(b, 17000, b.price)
    advance(b, 20000, 99. if side == "long" else 101.)
    assert b.closed_trades[0]["stop_kind"] == expected
