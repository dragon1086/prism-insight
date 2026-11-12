"""Live lane (S5): real positions that mirror the recorder's virtual trades, one account slot per account.

Runs right after the signal recorder, per configured account. Every order is a next-open reserved
order, like the research (entry and exit at the next session's open). Per account:

1. Reconcile yesterday's orders with the broker's holdings: a buy with no holding is REJECTED (the
   subscriber account's 2x deposit rule, or any broker refusal) and only recorded; a sell still
   held is placed again.
2. Exit: when the mirrored virtual trade has an exit signal (STOP/TARGET/TIME/NOPROG/TRAIL), sell
   the whole holding at the next open. A position whose virtual trade is stuck (missing bars, lost
   trade) is sold anyway ``timeout_days`` after its fill (LIVE_TIMEOUT): never an open-ended hold.
3. Entry: today's new virtual trades become buy candidates; ``plan_entries`` keeps the lane inside
   its slots (KR one at a time, US three sectors). Amount = slot amount / slots, scaled 2/3 for a
   -3x product so exposure matches the -2x research. Only families whose payoff was validated in
   the one-slot study (S18/S19) order; the other KR recorders stay virtual.

Nothing here runs unless ``INVERSE_LANE_LIVE_MARKETS`` names the market (tools/run_inverse_signals.py).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field, replace
from datetime import date
from typing import Callable, Mapping, Optional, Sequence

from prism_inverse.app.cycle import trace_id
from prism_inverse.app.signals_cycle import FAMILY_MAP, us_family
from prism_inverse.domain.lane import (CLOSED, OPEN, ORDERED_BUY, ORDERED_SELL, REJECTED, LaneRules, LivePosition,
                                       Product, plan_entries)
from prism_inverse.domain.virtual import PENDING_ENTRY, VirtualTrade
from prism_inverse.ports import Broker, EventSink, LiveStore, Unavailable

LIVE_VERSION = "inverse-live-v1"
KR_PRODUCTS = {"251340": Product("251340", -1), "114800": Product("114800", -1), "252670": Product("252670", -2)}
KR_LIVE_FAMILIES = ("KQ_X10", "KQ_FR", "K200_SEMI")   # the S18 three (payoff 2.51) the S19 one-slot design was sized on


def kr_product_map() -> dict[str, Product]:
    return {fam: KR_PRODUCTS[FAMILY_MAP[fam][1]] for fam in KR_LIVE_FAMILIES}


def us_product_map(inverse: Mapping[str, Product]) -> dict[str, Product]:
    return {us_family(t): p for t, p in inverse.items()}


@dataclass
class LiveResult:
    actions: list = field(default_factory=list)      # human-readable lines for the log
    positions: list = field(default_factory=list)    # LivePosition after this run (all accounts)


def run_live(market: str, day: date, trades: Sequence[VirtualTrade], product_map: Mapping[str, Product],
             rules: LaneRules, brokers: Sequence[Broker], store: LiveStore, events: EventSink,
             ref_price: Callable[[Product], Optional[float]] = lambda p: None) -> LiveResult:
    """``trades``: the market's virtual trades after today's recorder run. ``ref_price``: last close of a
    product, needed for a US reserved buy (limit order); None skips the entry and records why."""
    out, by_id = LiveResult(), {t.trade_id: t for t in trades}
    for broker in brokers:
        positions = {p.position_id: p for p in store.live_positions(market) if p.account == broker.name}

        def save(p: LivePosition, event: str, **extra) -> None:
            positions[p.position_id] = p
            store.save_live_position(p)
            events.emit(event, market=market, ticker=p.product, trace_id=trace_id(market, p.trade_id), position_id=p.position_id,
                        attributes={**{k: (v.isoformat() if isinstance(v, date) else v) for k, v in asdict(p).items()},
                                    "policy_version": LIVE_VERSION, "live": True, **extra})
            out.actions.append(f"{broker.name} {p.product} {p.status} {p.exit_reason or ''} {p.error or ''}".rstrip())

        for p in list(positions.values()):                     # 1. reconcile earlier orders (never today's: they fill at the next open)
            if p.status not in (ORDERED_BUY, ORDERED_SELL) or (p.status == ORDERED_BUY and p.opened >= day) \
                    or (p.status == ORDERED_SELL and p.exit_ordered is not None and p.exit_ordered >= day):
                continue
            try:
                held = broker.holding(p.product)
            except Unavailable as exc:
                save(replace(p, error=f"HOLDING_UNAVAILABLE: {exc}"), "inverse.live_unreconciled")
                continue
            if p.status == ORDERED_BUY:
                if held and held[0] > 0:
                    save(replace(p, status=OPEN, qty=held[0], entry_price=held[1], filled=day, error=None), "inverse.live_fill")
                else:
                    save(replace(p, status=REJECTED, closed=day, error="NO_FILL"), "inverse.live_reject")
            elif not held or held[0] == 0:
                save(replace(p, status=CLOSED, closed=day, error=None), "inverse.live_exit")

        for p in list(positions.values()):                     # 2. exits follow the mirrored virtual trade
            if p.status not in (OPEN, ORDERED_SELL) or (p.status == ORDERED_SELL and p.exit_ordered == day):
                continue
            t = by_id.get(p.trade_id)
            timed_out = p.filled is not None and (day - p.filled).days >= rules.timeout_days
            if p.status == OPEN and not timed_out:
                if t is None:
                    save(replace(p, error="MISSING_TRADE"), "inverse.live_unreconciled")   # hold until the timeout; never sell blind
                    continue
                if t.exit_signal_date is None:
                    continue
            reason = p.exit_reason or (t.exit_reason if t and t.exit_signal_date else None) or ("LIVE_TIMEOUT" if timed_out else "UNKNOWN")
            r = broker.sell_next_open(p.product, None, product_map.get(p.family, Product(p.product, -1)).exchange)   # whole holding
            save(replace(p, status=ORDERED_SELL, exit_reason=reason, exit_ordered=day, exit_order_no=r.order_no if r.ok else p.exit_order_no,
                         error=None if r.ok else f"SELL_FAILED: {r.message}"), "inverse.live_order", side="SELL", ok=r.ok)

        cands = [(t, product_map[t.family]) for t in trades
                 if t.status == PENDING_ENTRY and t.signal_date == day and t.family in product_map]
        for t, prod in plan_entries(rules, list(positions.values()), cands, lambda c: (c[0].underlying, c[0].family)):
            pid = f"{t.trade_id}@{broker.name}"
            if pid in positions:
                continue                                       # a re-run of the same day never orders twice
            amount = broker.slot_amount / rules.slots * prod.size_factor
            ref = ref_price(prod)
            if market == "US" and ref is None:
                save(LivePosition(pid, broker.name, market, t.trade_id, t.family, prod.code, status=REJECTED, opened=day,
                                  closed=day, error="MISSING_REF_PRICE"), "inverse.live_reject")
                continue
            r = broker.buy_next_open(prod.code, amount, prod.exchange, ref)
            save(LivePosition(pid, broker.name, market, t.trade_id, t.family, prod.code,
                              status=ORDERED_BUY if r.ok else REJECTED, opened=day, order_no=r.order_no,
                              closed=None if r.ok else day, error=None if r.ok else f"BUY_FAILED: {r.message}"),
                 "inverse.live_order", side="BUY", ok=r.ok, amount=amount, multiplier=prod.multiplier)
        out.positions.extend(positions.values())
    return out
