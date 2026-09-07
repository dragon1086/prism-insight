"""Adapter-only synthetic tests: native decisions never book market fills."""
from dataclasses import replace

import pytest

from backtest import native_intrabar_strategy as strategy
from backtest.intrabar_broker import Broker as SimBroker, Config, PositionView, EntryIntent
from core.actions import BookPartial, ChargeFunding, ClosePosition, ForceReduce, UpdateStop
from core.entries import EntryInputs
from core import swing
from engine.regime import RegimeSnapshot, TFState
from engine.signal import Signal

T = 1_800_000 * 1000


def context():
    states = {tf: TFState("up", "above_all", 120., 100., 120., 2.)
              for tf in ("30m", "1h", "4h", "12h", "1d", "1w")}
    row = dict(open=120., high=122., low=118., close=120., ma10=120., ma35=100., atr14=2.)
    return {"snapshot": RegimeSnapshot(states, 100., "synthetic"),
        "entry_inputs": {"long": EntryInputs(120., 2., 114., 115.), "short": EntryInputs(120., 2., 126., 125.)},
        "latest4h": dict(row), "previous4h": {**row, "ma10": 99.}, "latest1d": dict(row),
        "trailing_ma12h": None, "prior_close30m": 120., "last4hclose_ts": T,
        "feature_cutoff_ms": T}


class Bundle:
    def __init__(self):
        self.data = context()
        self.reads = []

    def context(self, ts):
        self.reads.append(ts)
        return self.data

    def regime_at(self, ts):
        return {"label": "up_normal"}


class Broker:
    def __init__(self, positions=()):
        self.held, self.pending, self.closed_trades = list(positions), [], []
        self.last = self.mark = 120.
        self.nav = 10_000.
        self.requests, self.amendments, self.reductions, self.resets = [], [], {}, []
        self.cancellations = []

    def positions(self, lane=None):
        return tuple(p for p in self.held if lane is None or p.lane == lane)

    def pending_entries(self, lane=None):
        return tuple(p for p in self.pending if lane is None or p["lane"] == lane)

    def pending_reservations(self):
        return ()

    def pending_reductions(self, pid):
        return self.reductions.get(pid, 0)

    def cancel(self, pid, ts, reason):
        self.cancellations.append((pid, ts, reason))

    def reduce(self, pid, lots, ts, reason):
        self.reductions[pid] = lots

    def close_parent(self, pid, ts, reason):
        self.reductions[pid] = next(p.lots for p in self.held if p.id == pid)

    def amend_stop(self, pid, stop, ts, reason):
        self.amendments.append((pid, stop, ts))

    def reset_window(self, pid):
        self.resets.append(pid)

    def submit(self, intent, ts):
        self.requests.append(intent)
        return intent.lots


def position(**overrides):
    base = PositionView("p", "main", "long", 9, 100., 90., 110., 10, 0, T - 100,
                        9, .09, False, True, False, 101., 99.)
    return replace(base, **overrides)


def test_first_cursor_primes_and_only_closed_four_hour_allows_main():
    data, broker = Bundle(), Broker()
    adapter = strategy.NativeStrategies(data)
    adapter.on_boundary(broker, T)
    assert not broker.requests
    adapter.on_boundary(broker, T + 300_000)
    assert data.reads == [T]
    adapter.on_boundary(broker, T + strategy.HALF_HOUR)
    assert not broker.requests
    data.data["last4hclose_ts"] += 8 * strategy.HALF_HOUR
    adapter.on_boundary(broker, T + 8 * strategy.HALF_HOUR)
    assert len(broker.requests) == 1
    intent = broker.requests[0]
    assert intent.leverage == 10 and intent.limit == 120.
    assert intent.meta["source_profile"] == "RESEARCH_MAIN_NOT_DEMO"
    assert intent.meta["feature_cutoff_ms"] == T
    assert intent.expires_at == T + 8 * strategy.HALF_HOUR + strategy.TTL


def test_main_native_helper_and_allocation_common_capital(monkeypatch):
    data, broker = Bundle(), Broker()
    captured = {}
    original = strategy.evaluate_entry_with_reason

    def evaluate(sig, equity, tranche, **kwargs):
        captured.update(equity=equity, tranche=tranche, **kwargs)
        return original(sig, equity, tranche, **kwargs)

    monkeypatch.setattr(strategy, "evaluate_entry_with_reason", evaluate)
    strategy.NativeStrategies(data)._enter_main(broker, T, data.data)
    assert captured["equity"] == broker.nav
    assert captured["inputs"] is data.data["entry_inputs"]["long"]
    assert captured["current_price"] == data.data["prior_close30m"]
    native = original(strategy.generate_signal(data.data["snapshot"]), broker.nav, 0,
                      inputs=captured["inputs"])
    assert broker.requests[0].stop == native.intent.sizing.sl_price
    assert broker.requests[0].lots * .001 <= native.intent.sizing.qty


def test_no_direct_cash_fill_and_no_double_funding(monkeypatch):
    broker = Broker([position()])
    observed = {}

    def actions(pos, bar, ctx):
        observed.update(bar=bar, ctx=ctx)
        return [ClosePosition(90., "sl"), BookPartial(1 / 3, 110., "maker", "tp1"), ChargeFunding(99.)]

    monkeypatch.setattr(strategy, "evaluate_exits", actions)
    strategy.NativeStrategies(Bundle())._manage_main(broker, T, context())
    assert observed["ctx"].funding_due is False
    assert observed["bar"].high == observed["bar"].low == observed["bar"].close == broker.last
    assert not broker.requests and not broker.reductions and not broker.amendments
    assert broker.held[0].lots == 9 and broker.nav == 10_000.


def test_be_uses_post_entry_window_not_preentry_30m_high():
    data = Bundle()
    data.data["latest4h"]["high"] = 1000.
    broker = Broker([position(window_high=110.)])
    broker.last = 101.
    adapter = strategy.NativeStrategies(data)
    adapter._manage_main(broker, T, data.data)
    assert not broker.amendments
    broker.held = [position(window_high=116.)]
    adapter._manage_main(broker, T + strategy.HALF_HOUR, data.data)
    assert broker.amendments == [("p", 100., T + strategy.HALF_HOUR)]
    assert broker.held[0].stop == 90.  # Request only, no synchronous ACK/fill.


def test_unchanged_stop_never_requested(monkeypatch):
    broker = Broker([position()])
    monkeypatch.setattr(strategy, "evaluate_exits", lambda *args: [UpdateStop(90.), UpdateStop(89.)])
    strategy.NativeStrategies(Bundle())._manage_main(broker, T, context())
    assert not broker.amendments


@pytest.mark.parametrize("lots,expected", [(1, 1), (2, 1), (3, 1), (9, 4)])
def test_signal_reduction_quantized_and_last_lot_fully_reduced(monkeypatch, lots, expected):
    broker = Broker([position(lots=lots)])
    monkeypatch.setattr(strategy, "check_exit_signal", lambda *args: Signal("long", 10., "test", "reduce"))
    adapter = strategy.NativeStrategies(Bundle())
    adapter._manage_main(broker, T, context())
    assert broker.reductions["p"] == expected
    adapter._manage_main(broker, T + strategy.HALF_HOUR, context())
    assert broker.reductions["p"] == expected


def test_force_reduce_intent_and_breach_flag(monkeypatch):
    broker = Broker([position(lots=1)])
    monkeypatch.setattr(strategy, "evaluate_exits", lambda *args: [ForceReduce(.5, 1., -999., True)])
    adapter = strategy.NativeStrategies(Bundle())
    adapter._manage_main(broker, T, context())
    assert broker.reductions == {"p": 1}
    assert adapter._states["p"]["breach"] is True
    assert broker.held[0].lots == 1


@pytest.mark.parametrize("stopped,bars", [(False, 8), (True, 16)])
def test_cooldown_uses_actual_exit_elapsed_30m_not_five_minute_or_ack(monkeypatch, stopped, bars):
    broker, data = Broker(), Bundle()
    broker.closed_trades = [{"parent_id": "old", "lane": "main", "side": "long",
        "exit_ms": T + 99_000, "last_exit_fill_ms": T, "stop_triggered": stopped}]
    adapter = strategy.NativeStrategies(data)
    adapter._closed_updates(broker)
    adapter._enter_main(broker, T + bars * strategy.HALF_HOUR - 1, data.data)
    assert not broker.requests
    adapter._enter_main(broker, T + bars * strategy.HALF_HOUR, data.data)
    assert len(broker.requests) == 1


def test_swing_native_cross_day_equal_short_and_no_tp():
    data, broker = Bundle(), Broker()
    data.data["previous4h"].update(ma10=101., ma35=100.)
    data.data["latest4h"].update(ma10=99., ma35=100., close=99.)
    data.data["latest1d"].update(ma10=100., ma35=100.)
    broker.last = 99.
    strategy.NativeStrategies(data, scope="swing")._swing(broker, T, data.data)
    intent = broker.requests[0]
    assert intent.side == "short" and intent.leverage == 5
    assert intent.tp1 is None and intent.order_type == "ioc"
    assert intent.limit == 99 * .999
    assert intent.stop == swing.stop_price("short", 99., 2.)
    assert intent.lots == strategy._lots(swing.compute_swing_sizing(broker.nav, 99., intent.stop).qty)


def test_swing_rule_exit_cannot_reenter_same_boundary():
    data = Bundle()
    broker = Broker([position(lane="swing", side="short", tp1=None)])
    strategy.NativeStrategies(data, scope="swing")._swing(broker, T, data.data)
    assert broker.reductions == {"p": 9}
    assert not broker.requests


def test_pending_and_opposite_lane_block_entry():
    data, broker = Bundle(), Broker()
    broker.pending = [{"parent_id": "pending", "lane": "main", "side": "short"}]
    adapter = strategy.NativeStrategies(data, scope="joint")
    adapter._enter_main(broker, T, data.data)
    adapter._swing(broker, T, data.data)
    assert not broker.requests


def test_confirm_uses_closed_reference_and_hold_not_current_quote(monkeypatch):
    data, broker = Bundle(), Broker()
    intent = EntryIntent("p", "main", "long", 10, 119., 110., 128., 10, 0, T + strategy.TTL)
    adapter = strategy.NativeStrategies(data)
    broker.last = .1
    assert adapter.confirm_callback(broker, [position()], intent, 1, T + 5000)
    data.data["prior_close30m"] = 118.
    broker.last = 1000.
    assert not adapter.confirm_callback(broker, [position()], intent, 1, T + 5000)
    assert not adapter.confirm_callback(broker, [], intent, 1, T + 5000)
    data.data["prior_close30m"] = 120.
    monkeypatch.setattr(strategy, "check_exit_signal", lambda *args: Signal("long", 10., "test", "exit"))
    assert not adapter.confirm_callback(broker, [position()], intent, 1, T + 5000)


def test_joint_priority_explicit(monkeypatch):
    order = []
    adapter = strategy.NativeStrategies(Bundle(), scope="joint", main_first=False)
    adapter._four_cursor = {"main": 0, "swing": 0}
    monkeypatch.setattr(adapter, "_swing", lambda *args: order.append("swing"))
    monkeypatch.setattr(adapter, "_enter_main", lambda *args: order.append("main"))
    adapter.on_boundary(Broker(), T)
    assert order == ["swing", "main"]


def test_real_kernel_be_amend_waits_for_ack_and_never_books_past_stop():
    broker = SimBroker(Config(maker_fee=0., taker_fee=0., slippage=0., spread=0., participation=1.))
    broker.price = broker.mark = 101.
    broker.capacity = 1000
    broker.submit(EntryIntent("p", "main", "long", 10, 100., 90., 110., 10, 0, T + strategy.TTL), 0)
    broker.segment(5000, 101., 101., [])
    broker.segment(10_000, 99., 99., [])
    broker.segment(20_000, 116., 116., [])
    broker.segment(T, 105., 105., [])
    assert broker.positions()[0].stop == 90.
    before_lots = broker.positions()[0].lots
    strategy.NativeStrategies(Bundle())._manage_main(broker, T, context())
    assert broker.positions()[0].stop == 90.
    assert broker.positions()[0].lots == before_lots
    broker.segment(T + 1000, 105., 105., [])
    assert broker.positions()[0].stop == 90.
    broker.segment(T + 2000, 105., 105., [])
    assert broker.positions()[0].stop == 100.
    assert broker.positions()[0].lots == before_lots


def test_future_bundle_change_does_not_rewrite_recorded_decisions():
    data, broker = Bundle(), Broker()
    adapter = strategy.NativeStrategies(data)
    adapter._enter_main(broker, T, data.data)
    before = [dict(row) for row in adapter.decisions]
    data.data = context()
    data.data["snapshot"].alignment_score = -100.
    data.data["prior_close30m"] = 1.
    assert adapter.decisions == before
    assert broker.requests[0].limit == 120.


@pytest.mark.parametrize("kwargs", [{"scope": "live"}, {"main_mode": "market"},
    {"allocation": "new_unvalidated"}, {"main_first": 1}])
def test_invalid_profiles_rejected(kwargs):
    with pytest.raises(ValueError):
        strategy.NativeStrategies(Bundle(), **kwargs)


@pytest.mark.parametrize("mode", ["single", "confirmed"])
@pytest.mark.parametrize("action", ["reduce", "exit", None])
def test_flat_pending_flow_change_or_unknown_requests_cancel(monkeypatch, mode, action):
    broker, data = Broker(), Bundle()
    broker.pending = [{"parent_id": "pending", "lane": "main", "side": "long", "lots": 10}]
    if action is None:
        data.data["snapshot"] = None
    else:
        monkeypatch.setattr(strategy, "check_exit_signal", lambda *args: Signal("long", 0., "test", action))
    strategy.NativeStrategies(data, main_mode=mode)._manage_main(broker, T, data.data)
    assert broker.cancellations == [("pending", T, "unknown_or_flow_change")]


def test_full_signal_exit_supersedes_pending_half_reduce(monkeypatch):
    broker = Broker([position()])
    broker.reductions["p"] = 4
    monkeypatch.setattr(strategy, "check_exit_signal", lambda *args: Signal("long", 0., "test", "exit"))
    strategy.NativeStrategies(Bundle())._manage_main(broker, T, context())
    assert broker.reductions["p"] == 9


def test_late_settlement_cannot_move_cooldown_backwards():
    broker = Broker()
    def trade(pid, ts, stopped):
        return {"parent_id": pid, "lane": "main", "side": "long", "exit_ms": 3000,
                "last_exit_fill_ms": ts, "stop_triggered": stopped,
                "stop_kind": "sl" if stopped else None}
    broker.closed_trades = [trade("new", 2000, True), trade("old", 1000, False), trade("tie", 2000, False)]
    adapter = strategy.NativeStrategies(Bundle())
    adapter._closed_updates(broker)
    assert adapter._cooldown["long"] == (2000, True)


def test_be_stop_keeps_native_normal_cooldown_despite_adverse_fill():
    broker = Broker()
    broker.closed_trades = [{"parent_id": "be", "lane": "main", "side": "long",
        "exit_ms": T + 1000, "last_exit_fill_ms": T, "stop_triggered": True,
        "stop_kind": "be", "exit_reason": "stop", "net_pnl": -1.}]
    adapter = strategy.NativeStrategies(Bundle())
    adapter._closed_updates(broker)
    assert adapter._cooldown["long"] == (T, False)
