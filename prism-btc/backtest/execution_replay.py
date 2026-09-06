"""OFFLINE single-campaign execution scenarios, not an exchange matching engine.

Every execution is simulated. Prices are ordered observations; OHLC paths supplied
by a caller remain hypothetical. None liquidity means unlimited MODEL liquidity.
Passive fills require strict trade-through and execute at the limit, not the
observed better price. There is no queue, orderbook or real trade confirmation.
Native stop activation is an explicit zero-latency entry-protection assumption.
"""
from __future__ import annotations

from dataclasses import dataclass
from math import isfinite

from core.scalp import (ScalpExitPlan, ScalpSnapshot, ScalpTrailPolicy, Target,
                        propose_scalp_exit)


def _number(value: object, name: str, *, positive: bool = False) -> None:
    try:
        finite = isinstance(value, (int, float)) and isfinite(value)
    except OverflowError:
        finite = False
    if (isinstance(value, bool) or not isinstance(value, (int, float))
            or not finite or (positive and value <= 0)):
        raise ValueError(name)


def _integer(value: object, name: str, minimum: int = 0) -> None:
    if type(value) is not int or value < minimum:
        raise ValueError(name)


@dataclass(frozen=True)
class PriceEvent:
    ts_ms: int
    price: float
    max_fill_lots: int | None = None
    trend_permission: bool = False

    def __post_init__(self) -> None:
        _integer(self.ts_ms, "ts_ms")
        _number(self.price, "price", positive=True)
        if self.max_fill_lots is not None:
            _integer(self.max_fill_lots, "max_fill_lots")
        if type(self.trend_permission) is not bool:
            raise ValueError("trend_permission")


@dataclass(frozen=True)
class FundingEvent:
    ts_ms: int
    rate: float
    mark_price: float

    def __post_init__(self) -> None:
        _integer(self.ts_ms, "ts_ms")
        _number(self.rate, "rate")
        _number(self.mark_price, "mark_price", positive=True)
        if abs(self.rate) >= 1:
            raise ValueError("funding_rate_bound")


@dataclass(frozen=True)
class EntryRequest:
    submitted_ms: int
    side: str
    lots: int
    stop_price: float
    tp1_price: float
    expiry_ms: int
    further_targets: tuple[Target, ...] = ()
    limit_price: float | None = None

    def __post_init__(self) -> None:
        _integer(self.submitted_ms, "submitted_ms")
        _integer(self.expiry_ms, "expiry_ms")
        _integer(self.lots, "lots", 1)
        if self.expiry_ms <= self.submitted_ms:
            raise ValueError("expiry_after_submission")
        if self.side not in ("long", "short"):
            raise ValueError("side")
        for name in ("stop_price", "tp1_price"):
            _number(getattr(self, name), name, positive=True)
        if self.limit_price is not None:
            _number(self.limit_price, "limit_price", positive=True)
        if (type(self.further_targets) is not tuple
                or any(not isinstance(t, Target) for t in self.further_targets)):
            raise ValueError("further_targets")
        direction = 1 if self.side == "long" else -1
        if direction * (self.tp1_price - self.stop_price) <= 0:
            raise ValueError("stop_tp_order")
        previous = self.tp1_price
        for target in self.further_targets:
            if direction * (target.price - previous) <= 0:
                raise ValueError("target_order")
            previous = target.price
        if sum(t.lots for t in self.further_targets) > self.lots // 2:
            raise ValueError("target_quota")


@dataclass(frozen=True)
class ReplayConfig:
    initial_cash: float
    lot_size: float
    maker_fee: float
    taker_fee: float
    market_slippage_bps: float
    entry_latency_ms: int
    amend_latency_ms: int
    check_interval_ms: int
    max_gross_notional: float
    early_distance: float
    runner_distance: float
    max_hold_seconds: float

    def __post_init__(self) -> None:
        for name in ("initial_cash", "lot_size", "max_gross_notional",
                     "early_distance", "runner_distance", "max_hold_seconds"):
            _number(getattr(self, name), name, positive=True)
        for name in ("maker_fee", "taker_fee", "market_slippage_bps"):
            _number(getattr(self, name), name)
        if abs(self.maker_fee) >= 1 or not 0 <= self.taker_fee < 1:
            raise ValueError("fee_bound")
        if not 0 <= self.market_slippage_bps < 10000:
            raise ValueError("slippage_bound")
        _integer(self.entry_latency_ms, "entry_latency_ms")
        _integer(self.amend_latency_ms, "amend_latency_ms")
        _integer(self.check_interval_ms, "check_interval_ms", 1)


@dataclass(frozen=True)
class SimulatedFill:
    ts_ms: int
    reason: str
    lots: int
    quantity: float
    price: float
    fee: float
    realized_pnl: float


@dataclass(frozen=True)
class FundingCharge:
    ts_ms: int
    rate: float
    mark_price: float
    lots: int
    amount: float


@dataclass(frozen=True)
class NavPoint:
    ts_ms: int
    reason: str
    mark_price: float
    remaining_lots: int
    cash: float
    unrealized_pnl: float
    nav: float
    stop_price: float


@dataclass(frozen=True)
class ReplayResult:
    fills: tuple[SimulatedFill, ...]
    funding: tuple[FundingCharge, ...]
    nav: tuple[NavPoint, ...]
    remaining_lots: int
    entered_lots: int
    cash: float
    unrealized_pnl: float
    nav_final: float
    total_fees: float
    total_funding: float
    max_drawdown: float
    status: str
    flags: tuple[str, ...]


def run_replay(events: list[PriceEvent] | tuple[PriceEvent, ...],
               entry: EntryRequest, config: ReplayConfig,
               funding_events: list[FundingEvent] | tuple[FundingEvent, ...] = (),
               ) -> ReplayResult:
    """Replay one linear-contract campaign; no portfolio allocator or signal.

    Funding precedes prices at equal timestamps. Stop amendments take effect on
    the NEXT price event at/after latency, never retrospectively. Policy checks
    occur only at supplied price observations; cadence cannot invent data.
    Current stops and latched exits outrank resting TPs. Eligible resting TP
    executions settle before policy checks; separate fill-detection latency is
    not modeled. Targets created by entry fills cannot execute on that event.
    Odd or incompatible partial-entry final quantities retain native protection
    but suppress TP planning, explicitly flagged for reconciliation.
    max_drawdown is peak-to-trough fractional mark-to-market NAV drawdown.
    """
    if not isinstance(entry, EntryRequest) or not isinstance(config, ReplayConfig):
        raise ValueError("request_config_type")
    if not isinstance(events, (list, tuple)) or not events:
        raise ValueError("nonempty_price_events_required")
    if not isinstance(funding_events, (list, tuple)):
        raise ValueError("funding_events_type")
    for i, event in enumerate(events):
        if not isinstance(event, PriceEvent):
            raise ValueError("price_event_type")
        if i and event.ts_ms <= events[i - 1].ts_ms:
            raise ValueError("strictly_ordered_price_timestamps_required")
    unique_funding: list[FundingEvent] = []
    for event in funding_events:
        if not isinstance(event, FundingEvent):
            raise ValueError("funding_event_type")
        if unique_funding and event.ts_ms < unique_funding[-1].ts_ms:
            raise ValueError("ordered_funding_required")
        if unique_funding and event.ts_ms == unique_funding[-1].ts_ms:
            if event != unique_funding[-1]:
                raise ValueError("conflicting_funding_timestamp")
            continue
        if not events[0].ts_ms <= event.ts_ms <= events[-1].ts_ms:
            raise ValueError("funding_outside_price_horizon")
        unique_funding.append(event)

    direction = 1 if entry.side == "long" else -1
    policy = ScalpTrailPolicy(config.early_distance, config.runner_distance,
                              config.max_hold_seconds)
    fills: list[SimulatedFill] = []
    charges: list[FundingCharge] = []
    curve: list[NavPoint] = []
    flags = {"SIMULATED_ONLY", "INSTANT_NATIVE_STOP_ASSUMPTION",
             "NO_ORDERBOOK_OR_QUEUE_MODEL", "NO_FORCED_HORIZON_CLOSE"}
    if any(e.max_fill_lots is None for e in events):
        flags.add("UNLIMITED_MODEL_LIQUIDITY")
    cash = config.initial_cash
    remaining = entered = 0
    average = 0.0
    stop = entry.stop_price
    first_fill_ms: int | None = None
    extreme = 0.0
    entry_done = False
    entry_rejected = False
    plan: ScalpExitPlan | None = None
    tp_filled: list[int] = []
    exit_intent: str | None = None
    amendment: tuple[int, float] | None = None
    next_check = 0
    funding_index = 0
    peak = config.initial_cash
    drawdown = 0.0

    def sample(ts: int, mark: float, reason: str) -> None:
        nonlocal peak, drawdown
        unrealized = direction * (mark - average) * remaining * config.lot_size
        nav = cash + unrealized
        for name, value in (("cash", cash), ("unrealized", unrealized), ("nav", nav)):
            _number(value, "nonfinite_accounting_" + name)
        peak = max(peak, nav)
        drawdown = max(drawdown, (peak - nav) / peak)
        curve.append(NavPoint(ts, reason, mark, remaining, cash, unrealized,
                              nav, stop))

    def fill(ts: int, price: float, lots: int, reason: str, maker: bool,
             mark: float) -> None:
        nonlocal cash, remaining, entered, average, first_fill_ms, extreme
        quantity = lots * config.lot_size
        fee = price * quantity * (config.maker_fee if maker else config.taker_fee)
        realized = 0.0
        if reason == "entry":
            average = (average * entered + price * lots) / (entered + lots)
            entered += lots
            remaining += lots
            if first_fill_ms is None:
                first_fill_ms = ts
                extreme = price
            extreme = max(extreme, price, mark) if direction == 1 else min(extreme, price, mark)
        else:
            realized = direction * (price - average) * quantity
            remaining -= lots
        cash += realized - fee
        fills.append(SimulatedFill(ts, reason, lots, quantity, price, fee, realized))
        sample(ts, mark, "fill:" + reason)

    def freeze_plan() -> None:
        nonlocal plan, tp_filled
        if not entered:
            return
        try:
            plan = ScalpExitPlan(entry.side, average, entered, 1,
                                 entry.tp1_price, entry.further_targets)
            tp_filled = [0] * len(plan.targets)
        except ValueError:
            flags.add("ENTRY_QUANTITY_OR_TARGETS_REQUIRE_RECONCILIATION_NO_TP")

    for event in events:
        ts, mark = event.ts_ms, event.price
        while funding_index < len(unique_funding) and unique_funding[funding_index].ts_ms <= ts:
            funding = unique_funding[funding_index]
            amount = direction * remaining * config.lot_size * funding.mark_price * funding.rate
            cash -= amount
            charges.append(FundingCharge(funding.ts_ms, funding.rate,
                                         funding.mark_price, remaining, amount))
            sample(funding.ts_ms, funding.mark_price, "funding")
            funding_index += 1
        capacity = event.max_fill_lots

        def available(wanted: int) -> int:
            return wanted if capacity is None else min(wanted, capacity)

        def consume(lots: int) -> None:
            nonlocal capacity
            if capacity is not None:
                capacity -= lots

        if amendment is not None and ts >= amendment[0]:
            stop = amendment[1]
            amendment = None
        if not entry_done and ts >= entry.expiry_ms:
            entry_done = True
            freeze_plan()
            flags.add("ENTRY_EXPIRED")
        if remaining and direction * (mark - stop) <= 0:
            exit_intent = "stop"
            entry_done = True
            amendment = None

        entered_this_event = False
        if (not entry_done and ts >= entry.submitted_ms + config.entry_latency_ms
                and ts < entry.expiry_ms and exit_intent is None):
            passive = entry.limit_price is not None
            eligible = not passive or direction * (mark - entry.limit_price) < 0
            if eligible:
                price = (entry.limit_price if passive else
                         mark * (1 + direction * config.market_slippage_bps / 10000))
                # Reserve full requested exposure at the currently executable price.
                gross = max(price, mark) * entry.lots * config.lot_size
                invalid_geometry = (entered == 0 and (
                    direction * (mark - entry.stop_price) <= 0
                    or direction * (price - entry.stop_price) <= 0
                    or direction * (entry.tp1_price - price) <= 0))
                if invalid_geometry:
                    entry_done = entry_rejected = True
                    flags.add("ENTRY_GEOMETRY_REJECTED")
                elif gross > config.max_gross_notional:
                    entry_done = entry_rejected = True
                    flags.add("ENTRY_GROSS_CAP_REJECTED")
                    freeze_plan()
                else:
                    lots = available(entry.lots - entered)
                    if lots:
                        fill(ts, price, lots, "entry", passive, mark)
                        consume(lots)
                        entered_this_event = True
                    if entered == entry.lots:
                        entry_done = True
                        freeze_plan()
        if remaining:
            extreme = max(extreme, mark) if direction == 1 else min(extreme, mark)
            if direction * (mark - stop) <= 0:
                exit_intent = "stop"
                entry_done = True
                amendment = None
            if exit_intent is None and plan is not None and not entered_this_event:
                for index, target in enumerate(plan.targets):
                    if direction * (mark - target.price) <= 0:
                        continue
                    lots = available(min(target.lots - tp_filled[index], remaining))
                    if lots:
                        fill(ts, target.price, lots, "tp" + str(index + 1), True, mark)
                        tp_filled[index] += lots
                        consume(lots)
            # Expiry/holding safety also works for unsupported odd partial plans.
            if (remaining and exit_intent is None and ts >= next_check
                    and first_fill_ms is not None):
                next_check = ts + config.check_interval_ms
                elapsed = (ts - first_fill_ms) / 1000
                if elapsed >= config.max_hold_seconds:
                    exit_intent = "holding_deadline"
                elif plan is not None:
                    snapshot = ScalpSnapshot(remaining, tuple(tp_filled), 0,
                                             stop, mark, extreme, elapsed, True, True)
                    proposal = propose_scalp_exit(plan, snapshot, policy,
                                                  trend_permission=event.trend_permission)
                    if proposal.status == "close":
                        exit_intent = proposal.reason
                    elif proposal.status == "reconcile":
                        flags.add("POLICY_RECONCILIATION")
                    elif amendment is None and proposal.desired_stop != stop:
                        amendment = (ts + config.amend_latency_ms, proposal.desired_stop)
                if exit_intent is not None:
                    entry_done = True
                    amendment = None
            if exit_intent is not None:
                lots = available(remaining)
                if lots:
                    price = mark * (1 - direction * config.market_slippage_bps / 10000)
                    fill(ts, price, lots, exit_intent, False, mark)
                    consume(lots)
        sample(ts, mark, "price")
        if entered - sum(f.lots for f in fills if f.reason != "entry") != remaining:
            raise AssertionError("quantity_conservation")
    status = ("exit_pending" if remaining and exit_intent else
              "open" if remaining else "closed" if entered else
              "rejected" if entry_rejected else "expired" if entry_done else "pending")
    final = curve[-1]
    return ReplayResult(tuple(fills), tuple(charges), tuple(curve), remaining,
                        entered, cash, final.unrealized_pnl, final.nav,
                        sum(f.fee for f in fills), sum(f.amount for f in charges),
                        drawdown, status, tuple(sorted(flags)))
