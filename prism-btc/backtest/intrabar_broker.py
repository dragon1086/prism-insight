"""Offline, integer-lot execution simulator; never imports a live broker.

OHLC paths are assumptions, not reconstructed ticks. At a timestamp funding is
charged first, then timer ACKs, existing stops, reductions/TP, and new entries.
Signal callbacks see only the preceding completed bars. Quote capacity is shared
across the entire 5-minute interval and comes from the PREVIOUS bar's volume.
"""
from __future__ import annotations

import heapq
import math
from dataclasses import dataclass, field, replace
from datetime import datetime

LOT = 0.001
TICK = 0.1
BAR_MS = 300_000


class ReplayInsolventError(RuntimeError):
    """NAV reached zero: this replay is invalid, not a completed profit result."""


def _tick(price, up):
    return (math.ceil(price / TICK - 1e-9) if up else math.floor(price / TICK + 1e-9)) / 10


def _finite(value, name, positive=False):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f"invalid {name}")
    if positive and value <= 0:
        raise ValueError(f"invalid {name}")
    return float(value)


def _ms(value):
    if isinstance(value, bool):
        raise ValueError("invalid timestamp")
    if isinstance(value, (int, float)):
        if not math.isfinite(value) or int(value) != value:
            raise ValueError("invalid timestamp")
        return int(value)
    if isinstance(value, str):
        value = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return int(value.timestamp() * 1000)


@dataclass(frozen=True)
class Config:
    path: str = "OHLC"
    entry_latency_ms: int = 5000
    cancel_latency_ms: int = 2000
    amend_latency_ms: int = 2000
    market_latency_ms: int = 5000
    maker_fee: float = 0.0002
    taker_fee: float = 0.00055
    slippage: float = 0.0005
    spread: float = 0.0001
    participation: float = 0.01

    def __post_init__(self):
        if self.path not in ("OHLC", "OLHC"):
            raise ValueError("invalid path")
        for name in ("entry_latency_ms", "cancel_latency_ms", "amend_latency_ms", "market_latency_ms"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise ValueError("latencies must be positive integer milliseconds")
        for name in ("maker_fee", "taker_fee", "slippage", "spread", "participation"):
            if not 0 <= _finite(getattr(self, name), name) <= 1:
                raise ValueError(f"invalid {name}")
        if self.slippage + self.spread / 2 >= 1:
            raise ValueError("spread/slippage must leave positive executable sell prices")


@dataclass(frozen=True)
class EntryIntent:
    parent_id: str
    lane: str
    side: str
    lots: int
    limit: float
    stop: float
    tp1: float | None
    leverage: int
    tranche: int
    expires_at: int
    mode: str = "single"
    order_type: str = "post_only"
    meta: dict = field(default_factory=dict)


@dataclass(frozen=True)
class PositionView:
    id: str
    lane: str
    side: str
    lots: int
    entry: float
    stop: float
    tp1: float | None
    leverage: int
    tranche: int
    entry_ms: int
    initial_lots: int
    initial_risk: float
    tp1_complete: bool
    entry_settled: bool
    closing: bool
    window_high: float
    window_low: float


@dataclass
class _Parent:
    intent: EntryIntent
    lots: int = 0
    entry: float = 0.0
    stop: float = 0.0
    entry_ms: int = 0
    filled: int = 0
    exited: int = 0
    realized: float = 0.0
    fees: float = 0.0
    funding: float = 0.0
    initial_risk: float = 0.0
    last_exit_fill_ms: int | None = None
    exit_reason: str | None = None
    tp_quota: int | None = None
    tp_filled: int = 0
    closing: bool = False
    closing_active: bool = False
    closing_reason: str | None = None
    stop_triggered: bool = False
    stop_kind: str | None = None
    final: bool = False
    high: float = 0.0
    low: float = math.inf
    orders: list = field(default_factory=list)
    reductions: list = field(default_factory=list)


class Broker:
    @property
    def last(self):
        return self.price

    @property
    def now(self):
        return self.ts

    @property
    def nav(self):
        return self.snapshot()["nav"]

    def __init__(self, config, initial_cash=10_000, sink=None, valuation_sink=None, confirm_callback=None):
        self.config = config
        self.initial_cash = _finite(initial_cash, "initial_cash", True)
        self.cash = self.initial_cash
        self.realized = self.fees = self.funding = 0.0
        self.ts = 0
        self.price = self.mark = 0.0
        self.capacity = 0
        self.parents = {}
        self.active_parents = {}
        self.bar_open_ts = 0
        self.insolvent = False
        self.events = []
        self.closed_trades = []
        self.sink = sink
        self.valuation_sink = valuation_sink
        self.confirm_callback = confirm_callback
        self._timers = []
        self._timer_seq = self._seq = 0
        self.peak = self.initial_cash
        self.max_drawdown = 0.0
        self.peak_witness = {"ts": 0, "nav": self.peak}
        self.drawdown_witness = None
        self.mark_source = "separate_mark_ohlc"
        self.drift_breaches = 0

    def _event(self, kind, **fields):
        self._seq += 1
        row = {"seq": self._seq, "ts": self.ts, "bar_open_ts": self.bar_open_ts, "kind": kind, **fields}
        self.events.append(row)
        if self.sink:
            self.sink(dict(row))

    def _timer(self, ts, action, pid, data=None):
        self._timer_seq += 1
        heapq.heappush(self._timers, (int(ts), self._timer_seq, action, pid, data))

    def _clock(self, ts):
        ts = _ms(ts)
        if ts != self.ts:
            raise ValueError("command timestamp must equal broker.now; advance quotes via replay")

    @staticmethod
    def _sign(p):
        return 1 if p.intent.side == "long" else -1

    def _pending(self, p):
        return sum(o["remaining"] for o in p.orders if o["state"] not in ("cancelled", "filled", "rejected"))

    def _settle(self, p):
        if not self._pending(p) and p.tp_quota is None:
            p.tp_quota = p.filled // 3 if p.intent.tp1 is not None else 0
            self._event("entry_settled", parent_id=p.intent.parent_id, filled_lots=p.filled, tp1_quota=p.tp_quota)
        if p.lots == 0 and not self._pending(p) and not p.final:
            for r in p.reductions:
                if r["remaining"]:
                    self._event("reduce_cancel", parent_id=p.intent.parent_id,
                                lots=r["remaining"], reason="flat_finalized")
                    r["remaining"] = 0
            p.final = True
            self.active_parents.pop(p.intent.parent_id, None)
            if p.filled:
                row = {"parent_id": p.intent.parent_id, "lane": p.intent.lane, "side": p.intent.side,
                       "entry_ms": p.entry_ms, "exit_ms": self.ts, "filled_lots": p.filled,
                       "last_exit_fill_ms": p.last_exit_fill_ms, "parent_settled_ms": self.ts,
                       "exit_reason": p.exit_reason, "stop_triggered": p.stop_triggered,
                       "stop_kind": p.stop_kind,
                       "initial_risk": p.initial_risk,
                       "exited_lots": p.exited, "realized_price": p.realized,
                       "fees": p.fees, "funding": p.funding,
                       "net_pnl": p.realized - p.fees - p.funding, "meta": dict(p.intent.meta)}
                self.closed_trades.append(row)
                self._event("parent_close", **row)

    def snapshot(self):
        unreal = sum(self._sign(p) * p.lots * LOT * (self.mark - p.entry) for p in self.active_parents.values())
        nav = self.cash + unreal
        gross = margin = heat = reserved = 0.0
        lanes = {name: {"lots": 0, "gross": 0.0, "heat": 0.0, "margin": 0.0} for name in ("main", "swing")}
        for p in self.active_parents.values():
            n = self._pending(p)
            held = p.lots * LOT * self.mark
            pending = n * LOT * max(p.intent.limit, self.mark)
            h = p.lots * LOT * max(0, self._sign(p) * (p.entry - p.stop))
            h += n * LOT * max(0, self._sign(p) * (p.intent.limit - p.intent.stop))
            # Reserve future exit fees for held exposure, and entry+exit fees
            # for planned exposure. Already-paid fees are reflected in cash.
            m = (held + pending) / p.intent.leverage + (held + 2 * pending) * self.config.taker_fee
            gross += held + pending
            margin += m
            heat += h
            reserved += pending
            lane = lanes[p.intent.lane]
            lane["lots"] += p.lots
            lane["gross"] += held + pending
            lane["heat"] += h
            lane["margin"] += m
        return {"ts": self.ts, "bar_open_ts": self.bar_open_ts, "cash": self.cash, "nav": nav, "unrealized": unreal,
                "realized_price": self.realized, "fees": self.fees, "funding": self.funding,
                "gross": gross, "heat": heat, "margin": margin, "reserved": reserved,
                "lanes": lanes, "main_qty": lanes["main"]["lots"] * LOT,
                "swing_qty": lanes["swing"]["lots"] * LOT,
                "max_drawdown": self.max_drawdown, "mark_source": self.mark_source,
                "drift_breaches": self.drift_breaches}

    @staticmethod
    def _feasible(s):
        nav = s["nav"]
        return (nav > 0 and s["margin"] <= nav + 1e-9 and s["gross"] <= nav * 8 + 1e-9
                and s["lanes"]["swing"]["gross"] <= nav * 5 + 1e-9
                and s["heat"] <= nav * .065 + 1e-9
                and s["lanes"]["main"]["heat"] <= nav * .05 + 1e-9
                and s["lanes"]["swing"]["heat"] <= nav * .015 + 1e-9)

    def _value(self):
        s = self.snapshot()
        if not all(math.isfinite(s[k]) for k in ("cash", "nav", "fees", "funding", "realized_price")):
            raise AssertionError("non-finite economic state")
        if not math.isclose(self.cash, self.initial_cash + self.realized - self.fees - self.funding, abs_tol=1e-7):
            raise AssertionError("cash identity violated")
        if any(p.lots < 0 or p.exited > p.filled for p in self.active_parents.values()):
            raise AssertionError("oversold parent")
        if s["nav"] <= 0:
            self.insolvent = True
            self._event("replay_invalid", reason="insolvent", nav=s["nav"])
            raise ReplayInsolventError("NAV is nonpositive; liquidation/borrow modelling required")
        if s["nav"] > self.peak:
            self.peak = s["nav"]
            self.peak_witness = {"ts": self.ts, "nav": self.peak}
        dd = (self.peak - s["nav"]) / self.peak
        if dd > self.max_drawdown:
            self.max_drawdown = dd
            self.drawdown_witness = {"peak": dict(self.peak_witness), "trough": {"ts": self.ts, "nav": s["nav"]}}
        if not self._feasible(s):
            self.drift_breaches += 1
        if self.valuation_sink:
            self.valuation_sink(self.snapshot())

    def positions(self, lane=None):
        return tuple(PositionView(p.intent.parent_id, p.intent.lane, p.intent.side, p.lots,
                                  p.entry, p.stop, p.intent.tp1, p.intent.leverage, p.intent.tranche,
                                  p.entry_ms, p.filled, p.initial_risk,
                                  p.tp_quota is not None and p.tp_filled >= p.tp_quota,
                                  not self._pending(p), p.closing, p.high, p.low)
                     for p in self.active_parents.values() if p.lots and (lane is None or p.intent.lane == lane))

    def pending_entries(self, lane=None):
        return tuple({"parent_id": p.intent.parent_id, "lane": p.intent.lane, "side": p.intent.side,
                      "lots": self._pending(p), "limit": p.intent.limit, "stop": p.intent.stop}
                     for p in self.active_parents.values() if self._pending(p) and (lane is None or p.intent.lane == lane))

    def pending_reservations(self):
        from core.portfolio_risk import PendingEntry
        return tuple(PendingEntry(p.intent.lane, p.intent.side, self._pending(p) * LOT,
                                  max(p.intent.limit, self.mark), p.intent.stop, p.intent.limit)
                     for p in self.active_parents.values() if self._pending(p))

    def pending_reductions(self, pid):
        return sum(r["remaining"] for r in self.parents[pid].reductions if r["remaining"] > 0)

    def reset_window(self, pid):
        p = self.parents[pid]
        p.high = p.low = self.price

    def submit(self, intent, ts):
        self._clock(ts)
        if not isinstance(intent, EntryIntent):
            raise ValueError("EntryIntent required")
        if not isinstance(intent.parent_id, str) or not intent.parent_id or intent.parent_id in self.parents:
            raise ValueError("duplicate or empty parent")
        if intent.lane not in ("main", "swing") or intent.side not in ("long", "short"):
            raise ValueError("invalid lane/side")
        if isinstance(intent.lots, bool) or not isinstance(intent.lots, int) or intent.lots <= 0:
            raise ValueError("positive integer lots required")
        if intent.leverage != (10 if intent.lane == "main" else 5):
            raise ValueError("fixed lane leverage required")
        if intent.mode not in ("single", "confirmed") or intent.order_type not in ("post_only", "ioc"):
            raise ValueError("invalid execution mode")
        if intent.lane == "swing" and (intent.mode != "single" or intent.order_type != "ioc"):
            raise ValueError("swing requires single IOC")
        for key in ("limit", "stop"):
            _finite(getattr(intent, key), key, True)
        if intent.tp1 is not None:
            _finite(intent.tp1, "tp1", True)
        elif intent.lane == "main":
            raise ValueError("main TP1 required")
        if intent.lane == "swing" and intent.tp1 is not None:
            raise ValueError("native swing has no TP1")
        sign = 1 if intent.side == "long" else -1
        original_prices = {"limit": intent.limit, "stop": intent.stop, "tp1": intent.tp1}
        intent = replace(intent, limit=_tick(intent.limit, sign < 0),
                         stop=_tick(intent.stop, sign < 0),
                         tp1=None if intent.tp1 is None else _tick(intent.tp1, sign > 0))
        if sign * (intent.limit - intent.stop) <= 0 or (intent.tp1 is not None and sign * (intent.tp1 - intent.limit) <= 0):
            raise ValueError("invalid protective prices")
        if _ms(intent.expires_at) <= ts or self.mark <= 0:
            raise ValueError("expired entry or absent quote")
        # No lane can hedge or stack another pending parent. Held same-direction
        # parents may be added to; opposite held/pending exposure is rejected.
        if any((p.lots or self._pending(p)) and (p.intent.side != intent.side or
               (p.intent.lane == intent.lane and self._pending(p))) for p in self.active_parents.values()):
            self._event("entry_reject", parent_id=intent.parent_id, reason="exposure_conflict")
            return 0
        intent = replace(intent, meta=dict(intent.meta), expires_at=_ms(intent.expires_at))
        p = _Parent(intent=intent, stop=intent.stop)
        order = {"index": 0, "remaining": intent.lots, "state": "planned", "cancel_requested": False}
        p.orders = [order]
        self.parents[intent.parent_id] = p
        self.active_parents[intent.parent_id] = p
        lo, hi = 0, intent.lots
        while lo < hi:
            mid = (lo + hi + 1) // 2
            order["remaining"] = mid
            if self._feasible(self.snapshot()):
                lo = mid
            else:
                hi = mid - 1
        if lo == 0:
            del self.parents[intent.parent_id]
            del self.active_parents[intent.parent_id]
            self._event("entry_reject", parent_id=intent.parent_id, reason="shared_budget")
            return 0
        p.intent = replace(intent, lots=lo)
        chunks = [lo] if intent.mode == "single" else [lo * 40 // 100, lo * 30 // 100, lo - lo * 40 // 100 - lo * 30 // 100]
        p.orders = [{"index": i, "remaining": n, "state": "planned" if n else "filled", "cancel_requested": False} for i, n in enumerate(chunks)]
        self._event("entry_submit", parent_id=intent.parent_id, lane=intent.lane, side=intent.side,
                    requested_lots=intent.lots, accepted_lots=lo, limit=intent.limit, stop=intent.stop,
                    tp1=intent.tp1, mode=intent.mode, leverage=intent.leverage,
                    original_prices=original_prices,
                    order_type=intent.order_type, expires_at=intent.expires_at,
                    children=[{"index": o["index"], "lots": o["remaining"],
                               "activation_ms": ts + o["index"] * 1_800_000 + self.config.entry_latency_ms}
                              for o in p.orders], meta=dict(intent.meta))
        for o in p.orders:
            if o["remaining"]:
                self._timer(ts + o["index"] * 1_800_000 + self.config.entry_latency_ms, "activate", intent.parent_id, o["index"])
        self._timer(min(intent.expires_at, ts + 5_400_000) if intent.mode == "confirmed" else intent.expires_at,
                    "expire", intent.parent_id)
        self._value()
        return lo

    def _cancel_order(self, p, o, reason):
        if o["state"] in ("filled", "cancelled", "rejected") or o["cancel_requested"]:
            return
        o["cancel_requested"] = True
        self._event("cancel_request", parent_id=p.intent.parent_id, child=o["index"], reason=reason)
        self._timer(self.ts + self.config.cancel_latency_ms, "cancel", p.intent.parent_id, o["index"])

    def cancel(self, pid, ts, reason="strategy"):
        self._clock(ts)
        p = self.parents[pid]
        for o in p.orders:
            self._cancel_order(p, o, reason)

    def reduce(self, pid, lots, ts, reason="strategy"):
        self._clock(ts)
        if isinstance(lots, bool) or not isinstance(lots, int) or lots <= 0:
            raise ValueError("positive integer reduction lots required")
        p = self.parents[pid]
        self.cancel(pid, ts, reason)
        n = min(lots, max(0, p.lots - self.pending_reductions(pid)))
        if n:
            r = {"remaining": n, "active": False, "reason": reason}
            p.reductions.append(r)
            self._timer(ts + self.config.market_latency_ms, "reduce", pid, len(p.reductions) - 1)
            self._event("reduce_request", parent_id=pid, lots=n, reason=reason)
        return n

    def close_parent(self, pid, ts, reason="signal_exit"):
        """Terminal reduce-only latch: include fills arriving before cancel ACK.

        Unlike reduce(), this closes the parent, not merely its current quantity.
        The strategy market-close latency applies; an earlier native stop can
        supersede it and is reported separately via stop_triggered.
        """
        self._clock(ts)
        p = self.parents[pid]
        if p.final or p.closing:
            return
        p.closing = True
        p.closing_reason = reason
        self.cancel(pid, ts, reason)
        self._timer(ts + self.config.market_latency_ms, "close", pid)
        self._event("close_request", parent_id=pid, lots=p.lots, reason=reason)

    def amend_stop(self, pid, price, ts, reason="strategy"):
        self._clock(ts)
        _finite(price, "stop", True)
        p = self.parents[pid]
        price = _tick(price, self._sign(p) < 0)
        if self._sign(p) * (price - p.stop) > 0:
            self.cancel(pid, ts, reason)
            self._timer(ts + self.config.amend_latency_ms, "amend", pid, price)
            self._event("amend_request", parent_id=pid, stop=price, reason=reason)

    def _quote(self, side):
        return _tick(self.price * (1 + side * (self.config.spread / 2 + self.config.slippage)), side > 0)

    def _fill(self, p, n, price, entry, reason, maker=False, order=None):
        n = min(n, self.capacity, n if entry else p.lots)
        if n <= 0:
            return 0
        if entry and self._sign(p) * (price - p.stop) <= 0:
            order["state"] = "rejected"
            self._event("entry_reject", parent_id=p.intent.parent_id, child=order["index"],
                        cancelled_lots=order["remaining"], reason="invalid_actual_sl_geometry",
                        price=price, stop=p.stop)
            self._settle(p)
            return 0
        # An entry can only consume its own already-reserved quantity. Recheck
        # fresh mark/NAV; if drift invalidated admission, cancel rather than lever up.
        if entry and not self._feasible(self.snapshot()):
            self.cancel(p.intent.parent_id, self.ts, "fresh_budget")
            return 0
        if entry:
            # Admission's proxy quote is not a guarantee. Test the actual fill
            # against one post-fee NAV before committing any economic event.
            old_lots, old_entry, old_remaining, old_cash = p.lots, p.entry, order["remaining"], self.cash
            lo, hi = 0, n
            while lo < hi:
                mid = (lo + hi + 1) // 2
                p.lots = old_lots + mid
                p.entry = (old_entry * old_lots + price * mid) / p.lots
                order["remaining"] = old_remaining - mid
                self.cash = old_cash - mid * LOT * price * (self.config.maker_fee if maker else self.config.taker_fee)
                if self._feasible(self.snapshot()):
                    lo = mid
                else:
                    hi = mid - 1
            p.lots, p.entry, order["remaining"], self.cash = old_lots, old_entry, old_remaining, old_cash
            n = lo
            if not n:
                self.cancel(p.intent.parent_id, self.ts, "post_fee_budget")
                return 0
        self.capacity -= n
        fee = n * LOT * price * (self.config.maker_fee if maker else self.config.taker_fee)
        pnl = 0.0
        if entry:
            p.entry = (p.entry * p.lots + price * n) / (p.lots + n)
            if not p.filled:
                p.entry_ms = self.ts
                p.high = p.low = self.price
            p.lots += n
            p.filled += n
            p.initial_risk += n * LOT * abs(price - p.intent.stop)
            order["remaining"] -= n
            if not order["remaining"]:
                order["state"] = "filled"
        else:
            pnl = self._sign(p) * n * LOT * (price - p.entry)
            p.lots -= n
            p.exited += n
            p.last_exit_fill_ms = self.ts
            p.exit_reason = reason
            p.realized += pnl
            self.realized += pnl
        p.fees += fee
        self.fees += fee
        self.cash += pnl - fee
        self._event("fill", parent_id=p.intent.parent_id, lane=p.intent.lane, side=p.intent.side,
                    action="BUY" if (self._sign(p) == 1) == entry else "SELL", entry=entry,
                    lots=n, qty=n * LOT, price=price, fee=fee, realized_price=pnl, reason=reason,
                    child=None if order is None else order["index"], remaining_lots=p.lots,
                    stop=p.stop, mark=self.mark, initial_risk=p.initial_risk,
                    stop_triggered=p.stop_triggered, stop_kind=p.stop_kind, meta=dict(p.intent.meta))
        self._settle(p)
        self._value()
        return n

    def _ack(self, action, pid, data):
        p = self.parents[pid]
        if p.final:
            return
        if action == "activate":
            o = p.orders[data]
            if o["state"] != "planned":
                return
            if data:
                for previous in p.orders[:data]:
                    self._cancel_order(p, previous, "next_slot")
                allowed = p.filled > 0 and not p.closing and self.confirm_callback is not None
                if allowed:
                    allowed = bool(self.confirm_callback(self, self.positions(), p.intent, data, self.ts))
                if not allowed:
                    o["state"] = "cancelled"
                    self._event("child_skip", parent_id=pid, child=data, cancelled_lots=o["remaining"], reason="confirmation")
                    self._settle(p)
                    return
            if o["cancel_requested"] or self.ts >= p.intent.expires_at:
                return
            sign = self._sign(p)
            quote = self.price * (1 + sign * self.config.spread / 2)
            if p.intent.order_type == "post_only" and sign * (p.intent.limit - quote) >= -1e-10:
                o["state"] = "rejected"
                self._event("entry_reject", parent_id=pid, child=data, cancelled_lots=o["remaining"], reason="post_only_marketable")
            else:
                o["state"] = "active"
                self._event("entry_active", parent_id=pid, child=data, remaining_lots=o["remaining"])
            self._settle(p)
        elif action == "cancel":
            o = p.orders[data]
            if o["state"] not in ("filled", "rejected", "cancelled"):
                o["state"] = "cancelled"
                self._event("cancel_ack", parent_id=pid, child=data, cancelled_lots=o["remaining"])
            self._settle(p)
        elif action == "expire":
            self.cancel(pid, self.ts, "expiry")
        elif action == "amend":
            old = p.stop
            if self._sign(p) * (data - old) > 0:
                p.stop = data
            self._event("amend_ack", parent_id=pid, old_stop=old, stop=p.stop)
        elif action == "reduce":
            p.reductions[data]["active"] = True
        elif action == "close":
            p.closing_active = True

    def _process(self):
        # Quote-arrival NAV precedes any fee, funding (caller), TP or SL fill.
        self._value()
        # Timers take precedence over fills at the exact same millisecond.
        while self._timers and self._timers[0][0] <= self.ts:
            _, _, action, pid, data = heapq.heappop(self._timers)
            self._ack(action, pid, data)
        for p in tuple(self.active_parents.values()):
            if p.lots:
                p.high, p.low = max(p.high, self.price), min(p.low, self.price)
                if self._sign(p) * (self.price - p.stop) <= 1e-9 and not p.stop_triggered:
                    p.stop_triggered = p.closing = p.closing_active = True
                    p.closing_reason = "stop"
                    p.stop_kind = "be" if p.intent.lane == "main" and abs(p.stop - p.entry) <= 1e-8 else "sl"
                    self.cancel(p.intent.parent_id, self.ts, "stop")
                    self._event("stop_trigger", parent_id=p.intent.parent_id, stop=p.stop, price=self.price)
            if p.closing_active and p.lots:
                self._fill(p, p.lots, self._quote(-self._sign(p)), False, p.closing_reason)
        for p in tuple(self.active_parents.values()):
            for r in p.reductions:
                if r["active"] and r["remaining"] and not p.closing:
                    executed = self._fill(p, r["remaining"], self._quote(-self._sign(p)), False, r["reason"])
                    r["remaining"] = max(0, r["remaining"] - executed)
                if not p.lots:
                    r["remaining"] = 0
            if (p.lots and not p.closing and p.tp_quota is not None and p.tp_filled < p.tp_quota
                    and self._sign(p) * (self.price - p.intent.tp1) >= TICK - 1e-9):
                p.tp_filled += self._fill(p, p.tp_quota - p.tp_filled, p.intent.tp1, False, "tp1", maker=True)
        for p in tuple(self.active_parents.values()):
            for o in p.orders:
                if o["state"] == "active" and p.intent.order_type == "ioc":
                    px = self._quote(self._sign(p))
                    if self._sign(p) * (px - p.intent.limit) <= 1e-10:
                        self._fill(p, o["remaining"], px, True, "ioc", order=o)
                    if o["remaining"] and o["state"] != "rejected":
                        o["state"] = "cancelled"
                        self._event("cancel_ack", parent_id=p.intent.parent_id, child=o["index"], cancelled_lots=o["remaining"], reason="ioc_remainder")
                    self._settle(p)
                if (o["state"] == "active" and o["remaining"] and p.intent.order_type == "post_only"
                        and self._sign(p) * (p.intent.limit - self.price) >= TICK - 1e-9):
                    self._fill(p, o["remaining"], p.intent.limit, True, "limit", maker=True, order=o)
                # Both IOC and resting fills can arrive with last-trade already
                # through SL even when their execution price is above/below it.
                if p.lots and self._sign(p) * (self.price - p.stop) <= 1e-9 and not p.stop_triggered:
                    p.stop_triggered = p.closing = p.closing_active = True
                    p.closing_reason = "stop"
                    p.stop_kind = "be" if p.intent.lane == "main" and abs(p.stop - p.entry) <= 1e-8 else "sl"
                    self.cancel(p.intent.parent_id, self.ts, "stop")
                    self._event("stop_trigger", parent_id=p.intent.parent_id, stop=p.stop, price=self.price)
                if p.closing_active and p.lots:
                    self._fill(p, p.lots, self._quote(-self._sign(p)), False, p.closing_reason)
        self._value()

    def funding_event(self, rate):
        self._value()
        rate = _finite(rate, "funding rate")
        for p in self.active_parents.values():
            if p.lots:
                cost = self._sign(p) * p.lots * LOT * self.mark * rate
                p.funding += cost
                self.funding += cost
                self.cash -= cost
                self._event("funding", parent_id=p.intent.parent_id, lane=p.intent.lane, lots=p.lots,
                            rate=rate, mark=self.mark, amount=cost)
        self._value()

    def _levels(self):
        for p in self.active_parents.values():
            sign = self._sign(p)
            if p.lots and not p.stop_triggered:
                yield p.stop
                if not p.closing and p.tp_quota is not None and p.tp_filled < p.tp_quota:
                    yield p.intent.tp1 + sign * TICK
            if self.capacity:
                for o in p.orders:
                    if o["state"] == "active" and o["remaining"]:
                        yield p.intent.limit - sign * TICK

    def segment(self, end_ts, end_price, end_mark, funding, defer_end=False):
        start_ts, start_price, start_mark = self.ts, self.price, self.mark
        while self.ts < end_ts:
            nxt = end_ts
            if self._timers:
                nxt = min(nxt, max(self.ts + 1, self._timers[0][0]))
            if funding and funding[0][0] <= end_ts:
                nxt = min(nxt, max(self.ts + 1, funding[0][0]))
            if end_price != self.price:
                for level in self._levels():
                    f = (level - self.price) / (end_price - self.price)
                    if 1e-12 < f <= 1:
                        nxt = min(nxt, self.ts + max(1, math.ceil(f * (end_ts - self.ts))))
            fraction = (nxt - start_ts) / (end_ts - start_ts)
            self.ts = nxt
            self.price = start_price + fraction * (end_price - start_price)
            self.mark = start_mark + fraction * (end_mark - start_mark)
            self._value()
            if defer_end and self.ts == end_ts:
                self._value()
                break
            while funding and funding[0][0] <= self.ts:
                _, rate = funding.pop(0)
                self.funding_event(rate)
            self._process()


def _rows(data):
    if hasattr(data, "to_dict"):
        frame = data
        if not any(c in frame.columns for c in ("ts", "timestamp", "date", "time", "open_time")):
            frame = frame.reset_index()
        data = frame.to_dict("records")
    return list(data)


def _timestamp(row):
    for key in ("ts", "timestamp", "date", "time", "open_time", "index", "fundingRateTimestamp"):
        if key in row:
            return _ms(row[key])
    raise ValueError("missing timestamp")


def _bars(data):
    result = []
    for row in _rows(data):
        r = {"ts": _timestamp(row)}
        for key in ("open", "high", "low", "close"):
            r[key] = _finite(row[key], key, True)
        r["volume"] = _finite(row.get("volume", 0), "volume")
        if "prior_volume" in row:
            r["prior_volume"] = _finite(row["prior_volume"], "prior_volume")
            if r["prior_volume"] < 0:
                raise ValueError("invalid prior_volume")
        if r["volume"] < 0 or r["low"] > min(r["open"], r["close"]) or r["high"] < max(r["open"], r["close"]) or r["low"] > r["high"]:
            raise ValueError("invalid OHLCV")
        if result and r["ts"] != result[-1]["ts"] + BAR_MS:
            raise ValueError("bars must be consecutive 5m opens")
        result.append(r)
    if not result:
        raise ValueError("empty bars")
    return result


def run_replay(bars, funding, mark_bars, config, on_boundary, sink=None, valuation_sink=None, confirm_callback=None, initial_cash=10_000):
    """Replay complete 5m bars; timestamps are OPEN times, funding uses actual ms.

    Mark extrema are phase-paired with trade extrema. No order-book reconstruction,
    liquidation engine, exchange stop-trigger feed, or hidden queue position is
    claimed. End-of-data positions remain open and outstanding ACKs remain pending.
    """
    bars = _bars(bars)
    marks = _bars(mark_bars) if mark_bars is not None else bars
    if len(marks) != len(bars) or any(a["ts"] != b["ts"] for a, b in zip(bars, marks)):
        raise ValueError("mark/trade timestamps must match")
    funds = [(_timestamp(r), _finite(r.get("rate", r.get("fundingRate")), "funding")) for r in _rows(funding)]
    if any(b[0] <= a[0] for a, b in zip(funds, funds[1:])):
        raise ValueError("funding timestamps must be strictly increasing")
    funds = [r for r in funds if bars[0]["ts"] <= r[0] < bars[-1]["ts"] + BAR_MS]
    broker = Broker(config, initial_cash, sink, valuation_sink, confirm_callback)
    broker.mark_source = "trade_ohlc_proxy_no_mark" if mark_bars is None else "separate_mark_ohlc_phase_paired"
    previous_volume = 0.0
    order = ("high", "low", "close") if config.path == "OHLC" else ("low", "high", "close")
    for bar, mark in zip(bars, marks):
        ts = bar["ts"]
        broker.bar_open_ts = ts
        broker.ts, broker.price, broker.mark = ts, bar["open"], mark["open"]
        capacity_volume = bar.get("prior_volume", previous_volume)
        if ts != bars[0]["ts"] and "prior_volume" in bar and not math.isclose(capacity_volume, previous_volume, abs_tol=1e-9):
            raise ValueError("prior_volume does not match preceding completed bar")
        broker.capacity = math.floor(capacity_volume * config.participation / LOT + 1e-12)
        broker._value()
        while funds and funds[0][0] <= ts:
            _, rate = funds.pop(0)
            broker.funding_event(rate)
        broker._process()
        on_boundary(broker, ts)
        for i, key in enumerate(order, 1):
            broker.segment(ts + i * 100_000, bar[key], mark[key], funds, defer_end=i == 3)
        previous_volume = bar["volume"]
    broker._event("replay_end", open_parents=[p.id for p in broker.positions()],
                  pending_parents=[p["parent_id"] for p in broker.pending_entries()],
                  disposition="OPEN_AT_END", **broker.snapshot())
    return broker
