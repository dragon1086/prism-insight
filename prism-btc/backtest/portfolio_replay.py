"""Bounded, offline shared-collateral replay; hypothetical paths, not exchange parity.

Bars are UTC milliseconds/open/high/low/close. Funding coverage is the strict
loader's responsibility; this engine additionally rejects duplicates and future
marks. Only daily NAV is retained. The optional sink consumes event dictionaries
synchronously and must stream them rather than retain the complete event history.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import math
import struct
from typing import Callable, Mapping

BAR = 300_000
DAY = 86_400_000
LOT = .001
SLEEVES = ("T", "B", "R")
TRACE_DAYS = frozenset(int(datetime.fromisoformat(d).replace(
    tzinfo=timezone.utc).timestamp() * 1000) for d in
    ("2022-04-02", "2023-01-02", "2025-01-02"))


@dataclass(frozen=True)
class DailyFeatures:
    available_at: int
    sma20: float
    sma60: float
    atr14: float
    std20: float
    prior_high20: float
    prior_low20: float
    prior_high10: float
    prior_low10: float
    close: float


@dataclass(frozen=True)
class Decision:
    direction: int
    weight: float

    def __post_init__(self):
        if self.direction not in (-1, 0, 1) or isinstance(self.direction, bool):
            raise ValueError("direction must be -1, 0, or 1")
        if not math.isfinite(self.weight) or not 0 <= self.weight <= 1:
            raise ValueError("weight must be finite and in [0,1]")


@dataclass(frozen=True)
class PortfolioPolicy:
    start_ms: int
    end_ms: int
    initial_cash: float = 10_000.
    fee_rate: float = .00055
    slippage: float = .0005
    delay_ms: int = BAR
    path: str = "OHLC"
    partial_entries: bool = False
    scale: float = 1.
    mode: str = "strategies"
    resource_check: Callable[[dict], None] | None = None

    def __post_init__(self):
        if (type(self.start_ms) is not int or type(self.end_ms) is not int
                or self.start_ms < 0 or self.end_ms <= self.start_ms
                or self.start_ms % BAR or self.end_ms % BAR):
            raise ValueError("period must be positive, ordered and bar aligned")
        if not math.isfinite(self.initial_cash) or self.initial_cash <= 0:
            raise ValueError("initial_cash must be positive")
        if self.path not in ("OHLC", "OLHC") or self.mode not in (
                "strategies", "bh", "cash"):
            raise ValueError("unknown path or mode")
        if self.delay_ms not in (BAR, 6 * BAR):
            raise ValueError("delay must be 5 or 30 minutes")
        if any(not math.isfinite(x) or not 0 <= x < 1
               for x in (self.fee_rate, self.slippage)):
            raise ValueError("invalid execution costs")
        if not math.isfinite(self.scale) or not 0 <= self.scale <= 1:
            raise ValueError("scale must be in [0,1]")


@dataclass(frozen=True)
class PortfolioSummary:
    daily_nav: tuple[tuple[int, float], ...]
    metrics: dict
    counters: dict
    hashes: dict
    final_positions: dict


class PortfolioReplayError(ValueError):
    """Financial invalidity with the last ledger preserved for the runner."""

    def __init__(self, message, state):
        super().__init__(message)
        self.state = state


@dataclass
class _Position:
    direction: int = 0
    lots: int = 0
    entry: float = 0.
    stop: float = 0.
    blocked_until: int = 0
    realized: float = 0.
    fees: float = 0.
    funding: float = 0.
    campaign_net: float = 0.
    completed: int = 0
    wins: int = 0
    losses: int = 0
    win_pnl: float = 0.
    loss_pnl: float = 0.


@dataclass
class _Order:
    direction: int
    target_lots: int
    remaining: int
    eligible: int
    atr: float
    risk_budget: float
    weight: float
    first_eligible: int | None = None
    waiting_exit: bool = False
    target_notional: float = 0.


def _lots(quantity):
    return max(0, math.floor(quantity / LOT + 1e-10))


def _row(row, funding=False):
    if isinstance(row, Mapping):
        ts = row.get("timestamp", row.get("ts_ms", row.get("funding_time",
                                                                    row.get("open_time"))))
        vals = ((row.get("funding_rate", row.get("rate")),) if funding else
                tuple(row[k] for k in ("open", "high", "low", "close")))
    else:
        ts, *vals = row[:2 if funding else 5]
    if not math.isfinite(float(ts)) or int(ts) != ts:
        raise ValueError("noninteger timestamp")
    vals = tuple(float(x) for x in vals)
    if any(not math.isfinite(x) for x in vals):
        raise ValueError("nonfinite input")
    return (int(ts), *vals)


class _Replay:
    def __init__(self, policy, callbacks, sink):
        self.p = policy
        self.callbacks = callbacks
        self.sink = sink
        self.pos = [_Position() for _ in SLEEVES]
        self.orders = [None, None, None]
        self.cash = self.peak = policy.initial_cash
        self.fees = self.funding = self.realized = self.turnover = 0.
        self.mdd = self.gross_max = self.net_max = self.giveback_max = 0.
        self.gross_breach_max = self.heat_breach_max = 0.
        self.breach = False
        self.breach_duration = self.exposed_ms = self.simultaneous_ms = 0.
        self.underwater_start = None
        self.peak_ts = policy.start_ms
        self.underwater_ms = 0.
        self.last_ts = policy.start_ms
        self.last_occupied = 0
        self.daily = []
        self.digest = hashlib.sha256()
        self.trace_counts = {}
        self.counts = dict(nodes=0, fills=0, funding_events=0, stops=0,
                           reservations=0, cancellations=0, rejected=0,
                           resized=0, decisions=0, forced_reductions=0,
                           breach_changes=0, trace_truncated=0,
                           completed_campaigns=0)

    def emit(self, kind, ts, **data):
        row = dict(kind=kind, ts_ms=ts, **data)
        self.digest.update(json.dumps(row, sort_keys=True, separators=(",", ":"),
                                      allow_nan=False).encode())
        if self.sink is not None:
            self.sink(row)

    def totals(self, price):
        unrealized = gross = heat = net = giveback = 0.
        for s in self.pos:
            if not s.lots:
                continue
            q = s.lots * LOT
            unrealized += s.direction * q * (price - s.entry)
            gross += q * price
            net += s.direction * q * price
            if self.p.mode != "bh":
                heat += q * (max(0., s.direction * (s.entry - s.stop))
                             + price * (self.p.fee_rate + self.p.slippage))
                giveback += q * max(0., s.direction * (price - s.stop))
        return self.cash + unrealized, gross, heat, net, giveback

    def sample(self, ts, price):
        nav, gross, heat, net, giveback = self.totals(price)
        if not math.isfinite(nav) or nav <= 0:
            self.orders[:] = [None] * 3
            raise PortfolioReplayError(f"nonpositive NAV at {ts}: {nav}; cash={self.cash}",
                dict(ts_ms=ts, nav=nav, cash=self.cash, fees=self.fees,
                     funding=self.funding, realized_pnl=self.realized,
                     positions={name: vars(s).copy() for name, s in zip(SLEEVES, self.pos)},
                     counters=dict(self.counts), event_digest=self.digest.hexdigest()))
        identity = self.p.initial_cash + self.realized - self.fees - self.funding
        if not math.isclose(self.cash, identity, rel_tol=1e-10, abs_tol=1e-8):
            raise ArithmeticError("shared cash accounting identity")
        dt = max(0, ts - self.last_ts)
        self.exposed_ms += dt * (self.last_occupied > 0)
        self.simultaneous_ms += dt * (self.last_occupied > 1)
        self.breach_duration += dt * self.breach
        self.last_ts = ts
        self.last_occupied = sum(s.lots > 0 for s in self.pos)
        if nav >= self.peak:
            if self.underwater_start is not None:
                self.underwater_ms = max(self.underwater_ms, ts - self.underwater_start)
            self.peak = nav
            self.peak_ts = ts
            self.underwater_start = None
        else:
            if self.underwater_start is None:
                self.underwater_start = self.peak_ts
            self.underwater_ms = max(self.underwater_ms, ts - self.underwater_start)
        self.mdd = max(self.mdd, 1 - nav / self.peak)
        self.gross_max = max(self.gross_max, gross / nav)
        self.net_max = max(self.net_max, abs(net) / nav)
        self.giveback_max = max(self.giveback_max, giveback / nav)
        gb, hb = max(0., gross / nav - 1), max(0., heat / nav - .02)
        self.gross_breach_max = max(self.gross_breach_max, gb)
        self.heat_breach_max = max(self.heat_breach_max, hb)
        breach = gb > 1e-10 or hb > 1e-10
        if breach != self.breach:
            self.breach = breach
            self.counts["breach_changes"] += 1
            self.emit("breach", ts, active=breach, nav=nav, gross=gross, heat=heat)
        self.counts["nodes"] += 1
        self.digest.update(struct.pack("!qdddd", ts, price, nav, gross, heat))
        day = ts // DAY * DAY
        if day in TRACE_DAYS:
            n = self.trace_counts.get(day, 0)
            self.trace_counts[day] = n + 1
            if n < 5000:
                self.emit("audit", ts, price=price, nav=nav, cash=self.cash,
                          gross=gross, heat=heat,
                          positions=[(s.direction, s.lots, s.entry, s.stop)
                                     for s in self.pos])
            else:
                self.counts["trace_truncated"] += 1
        return nav

    def cancel(self, i, ts, reason):
        order = self.orders[i]
        if order is not None:
            self.counts["cancellations"] += 1
            self.emit("cancel", ts, sleeve=SLEEVES[i], reason=reason,
                      remaining_lots=order.remaining)
            self.orders[i] = None

    def reduce(self, i, lots, price, ts, reason):
        s = self.pos[i]
        lots = min(lots, s.lots)
        if not lots:
            return
        fill = price * (1 - s.direction * self.p.slippage)
        fee = lots * LOT * fill * self.p.fee_rate
        pnl = s.direction * lots * LOT * (fill - s.entry)
        self.cash += pnl - fee
        self.realized += pnl
        self.fees += fee
        self.turnover += lots * LOT * fill
        s.realized += pnl
        s.fees += fee
        s.campaign_net += pnl - fee
        s.lots -= lots
        self.counts["fills"] += 1
        self.emit("fill", ts, sleeve=SLEEVES[i], action=reason, direction=-s.direction,
                  lots=lots, quantity=lots * LOT, price=fill, market_price=price,
                  fee=fee, realized_pnl=pnl, cash=self.cash)
        if not s.lots:
            s.completed += 1
            self.counts["completed_campaigns"] += 1
            if s.campaign_net > 0:
                s.wins += 1
                s.win_pnl += s.campaign_net
            else:
                s.loss_pnl += s.campaign_net
                s.losses += s.campaign_net < 0
            s.direction = 0
            s.entry = s.stop = s.campaign_net = 0.
        self.sample(ts, price)

    def stop(self, i, price, ts):
        self.cancel(i, ts, "stop")
        self.pos[i].blocked_until = (ts // DAY + 1) * DAY
        self.counts["stops"] += 1
        self.reduce(i, self.pos[i].lots, price, ts, "stop")

    def reservation(self, i, price, lots=None):
        order = self.orders[i]
        if order is None:
            return 0., 0.
        n = order.remaining if lots is None else lots
        if n <= 0 or order.waiting_exit:
            return 0., 0.
        fill = price * (1 + order.direction * self.p.slippage)
        s = self.pos[i]
        stop = s.stop if s.lots and s.direction == order.direction else (
            fill - order.direction * 2 * order.atr)
        heat = (0. if self.p.mode == "bh" else n * LOT * (
            max(0., order.direction * (fill - stop))
            + fill * self.p.fee_rate + price * (self.p.fee_rate + self.p.slippage)))
        # A short's adverse sell fill is below the known mark, but its gross
        # exposure is not. Both reservation and post-cost admission call here.
        return n * LOT * max(price, fill), heat

    def reserve(self, price, ts):
        nav, gross, heat, _, _ = self.totals(price)
        for i, order in enumerate(self.orders):
            if order is None:
                continue
            if order.target_notional:
                order.target_lots = min(order.target_lots, _lots(order.target_notional / price))
                held = self.pos[i].lots if self.pos[i].direction == order.direction else 0
                order.remaining = min(order.remaining, max(0, order.target_lots - held))
            if ts >= order.eligible and order.first_eligible is None:
                order.first_eligible = ts
            if order.first_eligible is not None and self.p.partial_entries and (
                    ts >= order.first_eligible + 6 * BAR):
                self.cancel(i, ts, "ttl")
                continue
            if order.remaining <= 0:
                continue
            if order.waiting_exit:
                continue
            if gross > nav + 1e-8 or heat > .02 * nav + 1e-8:
                self.cancel(i, ts, "cap_breach")
                continue
            rg, rh = self.reservation(i, price, 1)
            # Per-sleeve allocation is frozen at the daily decision, while
            # aggregate collateral is always current marked NAV.
            s = self.pos[i]
            sh = (0. if self.p.mode == "bh" or not s.lots else s.lots * LOT * (
                max(0., s.direction * (s.entry - s.stop))
                + price * (self.p.fee_rate + self.p.slippage)))
            room = (min((nav - gross) / rg, (.02 * nav - heat) / rh,
                        (order.risk_budget - sh) / rh) if rh else (nav - gross) / rg)
            allowed = max(0, min(order.remaining, math.floor(room + 1e-9)))
            if allowed < order.remaining:
                self.counts["resized"] += 1
                order.remaining = allowed
                if allowed == 0:
                    self.counts["rejected"] += 1
            if not allowed:
                # Preserve delayed reduction/reversal intent, not a zero entry.
                if not s.lots or (s.direction == order.direction and
                                   s.lots <= order.target_lots):
                    self.cancel(i, ts, "no_capacity")
                    continue
            self.counts["reservations"] += 1
            gross += allowed * rg
            heat += allowed * rh

    def force_caps(self, price, ts):
        nav, gross, heat, _, _ = self.totals(price)
        if gross <= nav + 1e-8 and heat <= .02 * nav + 1e-8:
            return
        for i, order in enumerate(self.orders):
            if order is not None and order.remaining:
                self.cancel(i, ts, "cap_breach")
        for i, s in enumerate(self.pos):
            if not s.lots:
                continue
            while s.lots:
                nav, gross, heat, _, _ = self.totals(price)
                if gross <= nav + 1e-8 and heat <= .02 * nav + 1e-8:
                    return
                unit_heat = (0. if self.p.mode == "bh" else LOT * (
                    max(0., s.direction * (s.entry - s.stop))
                    + price * (self.p.fee_rate + self.p.slippage)))
                n = max(1, math.ceil((gross - nav) / (price * LOT) - 1e-9),
                        math.ceil((heat - .02 * nav) / unit_heat - 1e-9)
                        if unit_heat else 0)
                self.counts["forced_reductions"] += 1
                self.reduce(i, min(n, s.lots), price, ts, "forced")

    def decisions(self, feature, price, ts):
        nav = self.totals(price)[0]
        for i, name in enumerate(SLEEVES):
            self.cancel(i, ts, "daily_target")
            s = self.pos[i]
            callback = self.callbacks.get(name)
            decision = callback(s.direction, feature) if callback else Decision(0, 0.)
            if not isinstance(decision, Decision):
                raise ValueError("callbacks must return Decision")
            self.counts["decisions"] += 1
            direction = decision.direction if ts >= s.blocked_until else 0
            weight = decision.weight * self.p.scale
            target = _lots(weight * nav / price) if direction else 0
            atr = float(feature.atr14)
            if not math.isfinite(atr) or atr <= 0:
                raise ValueError("ATR must be finite and positive")
            remaining = max(0, target - (s.lots if s.direction == direction else 0))
            if target or s.lots:
                self.orders[i] = _Order(direction, target, remaining,
                                         ts + self.p.delay_ms, atr,
                                         weight * .02 * nav, weight,
                                         waiting_exit=bool(s.lots and direction
                                                           and direction != s.direction),
                                         target_notional=weight * nav if direction else 0.)
                if self.orders[i].waiting_exit:
                    self.emit("wait_exit", ts, sleeve=name, target_lots=target,
                              reserved_lots=0)

    def admissible(self, i, n, price):
        s, order = self.pos[i], self.orders[i]
        fill = price * (1 + order.direction * self.p.slippage)
        total = s.lots + n
        entry = (s.entry * s.lots + fill * n) / total
        stop = s.stop if s.lots else fill - order.direction * 2 * order.atr
        if self.p.mode != "bh" and (stop <= 0 or order.direction * (price - stop) <= 0):
            if stop <= 0:
                raise ValueError("nonpositive protective stop")
            return False
        nav, gross, heat, _, _ = self.totals(price)
        nav -= n * LOT * (abs(fill - price) + fill * self.p.fee_rate)
        if nav <= 0:
            return False
        gross += n * LOT * price
        old_heat = (0. if not s.lots or self.p.mode == "bh" else s.lots * LOT * (
            max(0., s.direction * (s.entry - s.stop))
            + price * (self.p.fee_rate + self.p.slippage)))
        new_heat = (0. if self.p.mode == "bh" else total * LOT * (
            max(0., order.direction * (entry - stop))
            + price * (self.p.fee_rate + self.p.slippage)))
        heat += new_heat - old_heat
        own_pending_heat = 0.
        for j, other in enumerate(self.orders):
            if other is not None:
                rg, rh = self.reservation(j, price, other.remaining - n if j == i
                                           else other.remaining)
                gross += rg
                heat += rh
                if j == i:
                    own_pending_heat = rh
        return (gross <= nav + 1e-8 and heat <= .02 * nav + 1e-8
                and (self.p.mode == "bh" or new_heat + own_pending_heat <=
                     min(order.risk_budget, order.weight * .02 * nav) + 1e-8))

    def entries(self, price, ts):
        for i, order in enumerate(self.orders):
            if order is None or ts < order.eligible or order.remaining <= 0:
                continue
            if order.first_eligible is None:
                order.first_eligible = ts
            n = (min(order.remaining, max(1, order.remaining // 2))
                 if self.p.partial_entries else order.remaining)
            # Drop unfillable reserved tail before finding the maximum
            # post-cost admissible lot. Pending and filled risk move atomically.
            original = order.remaining
            while n and not self.admissible(i, n, price):
                if order.remaining > n:
                    order.remaining -= 1
                else:
                    n -= 1
                    order.remaining = n
            if order.remaining < original:
                self.counts["resized"] += 1
            if not n:
                self.counts["rejected"] += 1
                self.cancel(i, ts, "post_cost_capacity")
                continue
            s = self.pos[i]
            fill = price * (1 + order.direction * self.p.slippage)
            fee = n * LOT * fill * self.p.fee_rate
            if not s.lots:
                s.direction = order.direction
                s.stop = 0. if self.p.mode == "bh" else fill - s.direction * 2 * order.atr
                if self.p.mode != "bh" and s.stop <= 0:
                    raise ValueError("nonpositive protective stop")
            s.entry = (s.entry * s.lots + fill * n) / (s.lots + n)
            s.lots += n
            s.fees += fee
            s.campaign_net -= fee
            self.fees += fee
            self.cash -= fee
            self.turnover += n * LOT * fill
            order.remaining -= n
            self.counts["fills"] += 1
            self.emit("fill", ts, sleeve=SLEEVES[i], action="entry", direction=s.direction,
                      lots=n, quantity=n * LOT, price=fill, market_price=price, fee=fee,
                      realized_pnl=0., cash=self.cash, stop=s.stop,
                      remaining_lots=order.remaining)
            self.sample(ts, price)
            if self.breach:
                raise ArithmeticError("entry created cap breach")
            if not order.remaining:
                self.orders[i] = None


def run_portfolio(bars, funding, daily_features, decision_callbacks,
                  policy: PortfolioPolicy, sink=None) -> PortfolioSummary:
    """Replay one continuous period on ONE NAV; callbacks never own held state.

    Funding may include rows outside the period, but must be strictly ordered.
    Bars may include warmup rows to provide the last closed funding mark. At
    startup with no prior close and no position, funding is recorded with zero
    quantity and no invented mark. ``resource_check`` may raise to stop safely.
    """
    e = _Replay(policy, decision_callbacks, sink)
    features = (daily_features if isinstance(daily_features, Mapping) else
                {f.available_at: f for f in daily_features})
    fund = iter(funding)
    next_fund = _row(next(fund), True) if len(funding) else None
    prev_fund = -1
    prev_ts = None
    last_close = None
    seen = 0
    final_price = None
    for raw in bars:
        ts, op, hi, lo, close = _row(raw)
        if ts % BAR or (prev_ts is not None and ts != prev_ts + BAR):
            raise ValueError("bars must be contiguous on the five-minute grid")
        if not 0 < lo <= min(op, close) <= max(op, close) <= hi:
            raise ValueError("invalid OHLC")
        prev_ts = ts
        if ts < policy.start_ms:
            last_close = (ts + BAR, close)
            continue
        if ts >= policy.end_ms:
            break
        if ts != policy.start_ms + seen * BAR:
            raise ValueError("missing period bars")
        seen += 1
        if policy.resource_check is not None:
            policy.resource_check(dict(ts_ms=ts, cash=e.cash,
                event_digest=e.digest.hexdigest(), counters=dict(e.counts)))
        while next_fund is not None and next_fund[0] <= ts:
            ft, rate = next_fund
            if ft <= prev_fund or ft % BAR or abs(rate) >= 1:
                raise ValueError("invalid or duplicate funding")
            prev_fund = ft
            if ft >= policy.start_ms:
                if ft != ts:
                    raise ValueError("funding event missed its open")
                if last_close is None and any(s.lots for s in e.pos):
                    raise ValueError("funding mark unavailable")
                if last_close is not None and last_close[0] > ft:
                    raise ValueError("future funding mark")
                mark = last_close[1] if last_close else None
                charges = []
                for s in e.pos:
                    payment = s.direction * s.lots * LOT * (mark or 0.) * rate
                    s.funding += payment
                    s.campaign_net -= payment
                    e.funding += payment
                    e.cash -= payment
                    charges.append(payment)
                e.counts["funding_events"] += 1
                e.emit("funding", ts, rate=rate, mark_price=mark,
                       payments=charges, cash=e.cash)
                e.sample(ts, mark if mark is not None else op)
            try:
                next_fund = _row(next(fund), True)
            except StopIteration:
                next_fund = None
        e.sample(ts, op)
        if policy.mode != "bh":
            for i, s in enumerate(e.pos):
                if s.lots and s.direction * (op - s.stop) <= 0:
                    e.stop(i, op, ts)
        if policy.mode == "strategies" and ts % DAY == 0 and ts in features:
            feature = features[ts]
            if feature.available_at != ts:
                raise ValueError("feature availability mismatch")
            e.decisions(feature, op, ts)
        elif policy.mode == "bh" and ts == policy.start_ms:
            e.orders[0] = _Order(1, _lots(policy.scale * e.cash / op),
                                 _lots(policy.scale * e.cash / op),
                                 ts + policy.delay_ms, 0., 0., policy.scale,
                                 target_notional=policy.scale * e.cash)
        e.reserve(op, ts)
        e.force_caps(op, ts)
        for i, order in enumerate(e.orders):
            if order is None or ts < order.eligible:
                continue
            s = e.pos[i]
            reduction = (s.lots if s.direction != order.direction else
                         max(0, s.lots - order.target_lots))
            e.reduce(i, reduction, op, ts, "voluntary")
            if order.waiting_exit and not s.lots:
                order.waiting_exit = False
                e.reserve(op, ts)
            if not order.remaining:
                e.orders[i] = None
        e.entries(op, ts)
        e.sample(ts, op)
        points = ((hi, lo, close) if policy.path == "OHLC" else (lo, hi, close))
        a, at = op, ts
        for b, offset in zip(points, (100_000, 200_000, 299_999)):
            bt = ts + offset
            if policy.mode != "bh" and a != b:
                crossings = [(s.stop, i) for i, s in enumerate(e.pos) if s.lots
                             and s.direction * (a - s.stop) > 0
                             and s.direction * (b - s.stop) <= 0]
                crossings.sort(key=lambda x: ((x[0] - a) / (b - a), x[1]))
                for stop, i in crossings:
                    when = at + round((bt - at) * (stop - a) / (b - a))
                    e.sample(when, stop)
                    e.stop(i, stop, when)
            e.sample(bt, b)
            a, at = b, bt
        last_close = (ts + BAR, close)
        final_price = close
        if (ts + BAR) % DAY == 0 or ts + BAR == policy.end_ms:
            e.daily.append((ts + BAR, e.totals(close)[0]))
    if seen != (policy.end_ms - policy.start_ms) // BAR:
        raise ValueError("incomplete period")
    nav = e.sample(policy.end_ms - 1, final_price)
    duration = policy.end_ms - policy.start_ms
    attribution = {name: dict(realized_pnl=s.realized, fees=s.fees, funding=s.funding,
        unrealized_pnl=s.direction * s.lots * LOT * (final_price - s.entry),
        completed_campaigns=s.completed, wins=s.wins, losses=s.losses, win_pnl=s.win_pnl,
        loss_pnl=s.loss_pnl) for name, s in zip(SLEEVES, e.pos)}
    wins, losses = sum(s.wins for s in e.pos), sum(s.losses for s in e.pos)
    win_pnl, loss_pnl = sum(s.win_pnl for s in e.pos), -sum(s.loss_pnl for s in e.pos)
    metrics = dict(start_ms=policy.start_ms, end_ms=policy.end_ms,
        initial_nav=policy.initial_cash, final_nav=nav, mtm_mdd=e.mdd,
        fees=e.fees, funding=e.funding, realized_pnl=e.realized,
        completed_campaigns=e.counts["completed_campaigns"],
        winning_campaigns=wins, losing_campaigns=losses,
        profit_factor=win_pnl / loss_pnl if loss_pnl else None,
        payoff_ratio=(win_pnl / wins) / (loss_pnl / losses) if wins and losses else None,
        turnover=e.turnover / policy.initial_cash, gross_max=e.gross_max,
        net_abs_max=e.net_max, giveback_max=e.giveback_max,
        exposure_fraction=e.exposed_ms / duration,
        simultaneous_fraction=e.simultaneous_ms / duration,
        underwater_ms=e.underwater_ms, breach_duration_ms=e.breach_duration,
        max_gross_breach=e.gross_breach_max, max_heat_breach=e.heat_breach_max,
        sleeve_attribution=attribution)
    final = {name: dict(direction=s.direction, lots=s.lots, quantity=s.lots * LOT,
        average_entry=s.entry, stop=s.stop, blocked_until=s.blocked_until,
        pending_lots=e.orders[i].remaining if e.orders[i] else 0)
        for i, (name, s) in enumerate(zip(SLEEVES, e.pos))}
    return PortfolioSummary(tuple(e.daily), metrics, dict(e.counts),
        dict(events=e.digest.hexdigest(), daily_nav=hashlib.sha256(json.dumps(
            e.daily, separators=(",", ":")).encode()).hexdigest()), final)
