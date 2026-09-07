"""Research-only scheduled execution; OHLC touches are not exchange fills."""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_FLOOR
from math import isfinite
from numbers import Real

import pandas as pd

from core.portfolio_risk import ActualPosition, ProposedEntry, evaluate_portfolio_entry


def _lots(qty):
    return int((Decimal(str(qty)) / Decimal("0.001")).to_integral_value(rounding=ROUND_FLOOR))


@dataclass(frozen=True)
class SplitConfig:
    mode: str = "single"
    ambiguous_order: str = "fill_first"

    def __post_init__(self):
        if self.mode not in ("single", "timed", "confirmed", "probe"):
            raise ValueError("split mode")
        if self.ambiguous_order not in ("fill_first", "stop_first"):
            raise ValueError("ambiguous order")


class SplitExecution:
    """One run-local controller. A parent owns exactly one Position lifecycle."""

    def __init__(self, config, hook):
        if not isinstance(config, SplitConfig):
            raise ValueError("execution_config must be SplitConfig")
        self.config, self.hook = config, hook
        self.parent = None
        self.owners = {}
        self.seen_trades = 0

    def emit(self, event, time, parent=None, **fields):
        p = self.parent if parent is None else parent
        value = {"event": event, "bar_time": str(time), "synthetic": True,
                 "parent_id": None if p is None else p["id"], **fields}
        if p is not None:
            value.update(parent_qty=p["target"], filled_qty=p["filled"],
                         remaining_qty=sum(p["children"]), cancelled_qty=p["cancelled"],
                         reserved_notional=sum(p["children"]) * p["po"].limit_price,
                         reserved_stop_risk=sum(p["children"]) * p["distance"])
        if self.hook is not None:
            self.hook(value)

    def accept(self, state, time):
        po = state.pending_order
        if po is None or self.parent is not None:
            return
        qty = po.sizing.qty
        values = (qty, po.limit_price, po.sizing.sl_price, po.sizing.leverage,
                  po.sizing.tp1_price, po.sizing.tp2_price, po.sizing.tp3_price,
                  po.sizing.liq_price, po.initial_risk)
        if any(isinstance(x, bool) or not isinstance(x, Real) or not isfinite(x) or x <= 0
               for x in values):
            raise ValueError("invalid split geometry")
        if (po.side not in ("long", "short") or type(po.tranche_index) is not int
                or po.tranche_index not in (0, 1, 2)
                or po.sizing.tranche_index != po.tranche_index):
            raise ValueError("invalid split identity")
        sign = 1 if po.side == "long" else -1
        sz = po.sizing
        if (sign*(po.limit_price-sz.sl_price) <= 0
                or sign*(sz.sl_price-sz.liq_price) <= 0
                or sign*(sz.tp1_price-po.limit_price) <= 0
                or sign*(sz.tp2_price-sz.tp1_price) <= 0
                or sign*(sz.tp3_price-sz.tp2_price) <= 0):
            raise ValueError("invalid split protective geometry")
        if po.sizing.leverage != 10:
            raise ValueError("split research requires fixed 10x")
        lots = _lots(qty)
        if lots < 3:
            self.emit("parent_rejected", time, reason="unrepresentable_split")
            state.pending_order = None
            return
        target = lots / 1000
        first = lots*4//10
        second = lots*3//10
        children = [first / 1000, second / 1000, (lots-first-second) / 1000]
        if self.config.mode == "single":
            children = [target, 0., 0.]
        self.parent = {"id": f"{time}|{po.side}|{po.tranche_index}", "po": po,
                       "first": time + pd.Timedelta(minutes=30), "target": target,
                       "children": children, "filled": 0., "cancelled": 0.,
                       "distance": abs(po.limit_price-po.sizing.sl_price), "pos": None,
                       "released": set()}
        self.emit("parent_accepted", time, side=po.side, tranche_index=po.tranche_index,
                  limit_price=po.limit_price, sl_price=po.sizing.sl_price,
                  frozen_notional=target*po.limit_price,
                  frozen_stop_risk=target*self.parent["distance"])
        if self.config.mode == "probe":
            self.trim(sum(children[1:]), time, "probe_control")
        self.pending_view(state)

    def pending_view(self, state):
        from copy import deepcopy
        p = self.parent
        if p is None:
            state.pending_order = None
            return
        po = deepcopy(p["po"])
        po.sizing.qty = sum(p["children"])
        po.initial_risk = po.sizing.qty * p["distance"]
        state.pending_order = po

    def trim(self, qty, time, reason):
        p = self.parent
        for idx in (2, 1, 0):
            take = min(qty, p["children"][idx])
            p["children"][idx] = round(p["children"][idx]-take, 12)
            p["cancelled"] += take
            qty -= take
        self.emit("cancel", time, reason=reason)

    def finish(self, state, time, reason):
        if self.parent is None:
            return
        self.trim(sum(self.parent["children"]), time, reason)
        self.emit("parent_terminal", time, reason=reason)
        self.parent = None
        state.pending_order = None

    def reconcile(self, state, time):
        p = self.parent
        if p is not None and p["pos"] is not None:
            pos = p["pos"]
            if (not any(x is pos for x in state.positions) or pos.qty < p["filled"]-1e-12
                    or pos.sl_price != p["po"].sizing.sl_price
                    or pos.tp1_hit or pos.had_forced_reduce or pos.be_stop_set):
                self.finish(state, time, "protection_or_reduction")
        for trade in state.trade_logs[self.seen_trades:]:
            key = (trade.entry_time, trade.side, trade.tranche_index)
            p = self.owners[key]
            self.emit("trade_closed", time, p, trade_id=trade.trade_id,
                      net_pnl=trade.net_pnl, entry_time=trade.entry_time,
                      side=trade.side, tranche_index=trade.tranche_index,
                      exit_reason=trade.exit_reason)
        self.seen_trades = len(state.trade_logs)

    def before_bar(self, state, time, idx, bar, snapshot, prior_close, exit_checker):
        from backtest.engine import Position, MAKER_FEE
        self.reconcile(state, time)
        p = self.parent
        if p is None:
            return
        elapsed = int((time-p["first"]) / pd.Timedelta(minutes=30))
        if time >= p["first"] + pd.Timedelta(minutes=90):
            self.finish(state, time, "ttl")
            return
        if elapsed < 0:
            return
        po = p["po"]
        action = None if snapshot is None else exit_checker(snapshot, po.side).exit_action
        if action not in ("hold",):
            self.finish(state, time, "unknown_or_flow_change")
            return
        slot = 0 if self.config.mode == "single" else elapsed
        if self.config.mode != "single":
            for old in range(min(elapsed, 3)):
                if p["children"][old]:
                    qty = p["children"][old]
                    p["children"][old] = 0.
                    p["cancelled"] += qty
                    self.emit("child_expiry", time, child_id=f'{p["id"]}:{old}')
        if self.config.mode == "confirmed" and slot > 0 and p["pos"] is None:
            self.finish(state, time, "probe_missing")
            return
        if slot > 2 or p["children"][slot] <= 0:
            if not sum(p["children"]):
                self.finish(state, time, "schedule_complete")
            else:
                self.pending_view(state)
            return
        if self.config.mode == "confirmed" and slot > 0:
            favorable = (prior_close is not None and isfinite(prior_close)
                         and (prior_close-po.limit_price)*(1 if po.side == "long" else -1) > 0)
            if not favorable:
                qty = p["children"][slot]
                p["children"][slot] = 0.
                p["cancelled"] += qty
                self.emit("child_expiry", time, child_id=f'{p["id"]}:{slot}',
                          reason="probe_or_confirmation_missing")
                if not sum(p["children"]):
                    self.finish(state, time, "schedule_complete")
                else:
                    self.pending_view(state)
                return
        # Marked NAV is known at this bar's open. Reserve fees for all remaining
        # children; no current high/low/close enters sizing or confirmation.
        mark = float(bar["open"])
        nav = state.equity + sum((mark-x.entry_price)*x.qty*(1 if x.side == "long" else -1)
                                 for x in state.positions)
        positions = tuple(ActualPosition("main", x.side, x.qty, x.entry_price, mark,
                                         x.sl_price, True) for x in state.positions)
        remaining = round(sum(p["children"]), 12)
        def allowed(lots):
            qty = lots/1000
            if qty <= 0:
                return False
            return evaluate_portfolio_entry(
                capital=nav-qty*po.limit_price*MAKER_FEE, positions=positions,
                pending_entries=(), proposed=ProposedEntry("main", po.side, qty,
                    po.limit_price, po.sizing.sl_price, po.limit_price)).allowed
        lo, hi = 0, _lots(remaining)
        while lo < hi:
            mid = (lo+hi+1)//2
            if allowed(mid):
                lo = mid
            else:
                hi = mid-1
        if lo/1000 < remaining-1e-12:
            self.trim(remaining-lo/1000, time, "fresh_nav_cap")
        qty = p["children"][slot]
        if qty <= 0:
            if not sum(p["children"]):
                self.finish(state, time, "risk_cap")
            else:
                self.pending_view(state)
            return
        if slot not in p["released"]:
            p["released"].add(slot)
            self.emit("child_release", time, child_id=f'{p["id"]}:{slot}', qty=qty)
        touched = float(bar["low"]) <= po.limit_price <= float(bar["high"])
        ambiguous = touched and any(
            (float(bar["low"]) <= x.sl_price if x.side == "long" else float(bar["high"]) >= x.sl_price)
            or (not x.tp1_hit and (float(bar["high"]) >= x.tp1_price if x.side == "long"
                                  else float(bar["low"]) <= x.tp1_price))
            for x in state.positions)
        if ambiguous:
            self.emit("ambiguous", time, ordering=self.config.ambiguous_order)
            if self.config.ambiguous_order == "stop_first":
                self.finish(state, time, "ambiguous_protection_first")
                return
        if touched:
            fee = qty*po.limit_price*MAKER_FEE
            state.equity -= fee
            state.total_fees += fee
            pos = p["pos"]
            if pos is None:
                sz = po.sizing
                pos = Position(po.side, po.limit_price, qty, sz.leverage, sz.sl_price,
                               sz.tp1_price, sz.tp2_price, sz.tp3_price, sz.liq_price,
                               str(time), po.tranche_index, idx, qty*p["distance"],
                               entry_fee=fee, initial_qty=qty)
                key = (pos.entry_time, pos.side, pos.tranche_index)
                if key in self.owners:
                    raise AssertionError("duplicate parent lifecycle")
                self.owners[key] = p
                p["pos"] = pos
                state.positions.append(pos)
            else:
                pos.qty += qty
                pos.initial_qty += qty
                pos.initial_risk += qty*p["distance"]
                pos.entry_fee += fee
            p["filled"] += qty
            p["children"][slot] = 0.
            self.emit("child_fill", time, child_id=f'{p["id"]}:{slot}', qty=qty,
                      price=po.limit_price, fee=fee, sl_price=pos.sl_price,
                      protection_from_first_fill=True)
        if not sum(p["children"]):
            self.finish(state, time, "schedule_complete")
        else:
            self.pending_view(state)
