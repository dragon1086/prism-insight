"""Native research decisions, separated from delayed simulated broker fills.

MAIN mirrors research/SHADOW signals, NOT deployed DEMO C1 behavior. No direct
cash, funding, or fill writes. SWING retains its native 4h cross and 1d filter.
"""
from __future__ import annotations

from decimal import Decimal, ROUND_FLOOR

from backtest.engine import BE_TRAIL_ACTIVATE_R, LIQ_MONITOR_FRAC, REENTRY_COOLDOWN_BARS, SL_REENTRY_COOLDOWN_BARS
from backtest.intrabar_broker import EntryIntent, LOT
from core.actions import ActivateBETrail, ClearBreachFlag, ForceReduce, UpdateStop
from core.confidence_allocation import AllocationRequest, POLICIES, allocate, grade_agreement
from core.entries import CooldownState, evaluate_entry_with_reason
from core.exits import BarView, ExitContext, PositionView, evaluate_exits
from core.portfolio_risk import ActualPosition
from core import swing
from engine.signal import check_exit_signal, generate_signal
from engine.sizing import approx_liq_price

HALF_HOUR = 1_800_000
TTL = 5_400_000


def _lots(qty):
    return int((Decimal(str(qty)) / Decimal(str(LOT))).to_integral_value(rounding=ROUND_FLOOR))


class NativeStrategies:
    def __init__(self, bundle, scope="main", main_mode="single", allocation="graded",
                 sink=None, main_first=True):
        if scope not in ("main", "swing", "joint") or main_mode not in ("single", "confirmed"):
            raise ValueError("invalid strategy scope or execution mode")
        if allocation not in POLICIES or type(main_first) is not bool:
            raise ValueError("invalid allocation or priority")
        self.bundle, self.scope, self.main_mode = bundle, scope, main_mode
        self.allocation, self.sink, self.main_first = allocation, sink, main_first
        self.decisions = []
        self._last_boundary = None
        self._four_cursor = {"main": None, "swing": None}
        self._states, self._closed = {}, 0
        self._cooldown = {}
        self._serial = 0

    def _audit(self, lane, ts, context, reason, **fields):
        row = {"kind": "strategy_decision", "ts": ts, "lane": lane,
               "source_profile": "RESEARCH_MAIN_NOT_DEMO" if lane == "main" else "NATIVE_SWING",
               "feature_cutoff_ms": context["feature_cutoff_ms"],
               "last4hclose_ts": context["last4hclose_ts"],
               "regime": self.bundle.regime_at(ts)["label"], "reason": reason, **fields}
        self.decisions.append(row)
        if self.sink:
            self.sink(dict(row))

    def _closed_updates(self, broker):
        for trade in broker.closed_trades[self._closed:]:
            if trade["lane"] == "main":
                exited = trade.get("last_exit_fill_ms", trade["exit_ms"])
                reason = trade.get("stop_kind") or trade.get("exit_reason")
                stopped = reason == "sl" or (reason != "be" and trade.get("stop_triggered", False))
                prior = self._cooldown.get(trade["side"])
                if prior is None or exited > prior[0]:
                    self._cooldown[trade["side"]] = (exited, stopped)
                elif exited == prior[0]:
                    self._cooldown[trade["side"]] = (exited, stopped or prior[1])
            self._states.pop(trade["parent_id"], None)
        self._closed = len(broker.closed_trades)

    def on_boundary(self, broker, ts_ms):
        if type(ts_ms) is not int or ts_ms < 0:
            raise ValueError("integer timestamp required")
        if ts_ms % HALF_HOUR:
            return
        if self._last_boundary is not None and ts_ms <= self._last_boundary:
            raise ValueError("duplicate or nonmonotonic strategy boundary")
        self._last_boundary = ts_ms
        self._closed_updates(broker)
        context = self.bundle.context(ts_ms)
        lanes = ("main", "swing") if self.main_first else ("swing", "main")
        for lane in lanes:
            if self.scope not in (lane, "joint"):
                continue
            if lane == "main":
                self._manage_main(broker, ts_ms, context)
            closed = context["last4hclose_ts"]
            if closed is None or closed == self._four_cursor[lane]:
                continue
            priming = self._four_cursor[lane] is None
            self._four_cursor[lane] = closed
            if priming:
                self._audit(lane, ts_ms, context, "cursor_prime")
                continue
            if lane == "main":
                self._enter_main(broker, ts_ms, context)
            else:
                self._swing(broker, ts_ms, context)

    def _manage_main(self, broker, ts, context):
        snapshot = context["snapshot"]
        for pending in broker.pending_entries("main"):
            if snapshot is None or check_exit_signal(snapshot, pending["side"]).exit_action != "hold":
                broker.cancel(pending["parent_id"], ts, "unknown_or_flow_change")
        for pos in broker.positions("main"):
            state = self._states.setdefault(pos.id, {"trailing": False, "be": False, "breach": False})
            if pos.closing:
                continue
            view = PositionView(pos.side, pos.entry, pos.lots * LOT, pos.stop, pos.tp1,
                approx_liq_price(pos.entry, 10, pos.side), state["trailing"], state["be"],
                pos.tp1_complete, state["breach"])
            actions = evaluate_exits(view, BarView(ts // HALF_HOUR, broker.last, broker.last, broker.last),
                ExitContext(False, 0., True, context["trailing_ma12h"], BE_TRAIL_ACTIVATE_R, LIQ_MONITOR_FRAC))
            sign = 1 if pos.side == "long" else -1
            target = pos.stop
            for action in actions:
                if isinstance(action, ForceReduce) and not broker.pending_reductions(pos.id):
                    self._reduce(broker, pos, max(1, pos.lots // 2), ts, "liq_forced_reduce")
                    state["breach"] = True
                elif isinstance(action, ClearBreachFlag):
                    state["breach"] = False
                elif isinstance(action, UpdateStop) and sign * (action.new_stop - target) > 0:
                    target = action.new_stop
                elif isinstance(action, ActivateBETrail):
                    state["trailing"] = state["be"] = True
                # ClosePosition/BookPartial are never interpreted as fills.
            risk_distance = abs(pos.tp1 - pos.entry)
            favorable = pos.window_high - pos.entry if sign > 0 else pos.entry - pos.window_low
            if not state["trailing"] and risk_distance > 0 and favorable >= BE_TRAIL_ACTIVATE_R * risk_distance:
                state["trailing"] = state["be"] = True
                if sign * (pos.entry - target) > 0:
                    target = pos.entry
            if sign * (target - pos.stop) > 0:
                broker.amend_stop(pos.id, target, ts, "be_trail")
            if snapshot is not None:
                signal = check_exit_signal(snapshot, pos.side)
                if signal.exit_action == "exit" or (signal.exit_action == "reduce"
                        and not broker.pending_reductions(pos.id)):
                    lots = pos.lots if signal.exit_action == "exit" else max(1, pos.lots // 2)
                    self._reduce(broker, pos, lots, ts, "signal_" + signal.exit_action)
            broker.reset_window(pos.id)

    @staticmethod
    def _reduce(broker, pos, lots, ts, reason):
        if lots >= pos.lots:
            broker.close_parent(pos.id, ts, reason)
        else:
            broker.reduce(pos.id, lots, ts, reason)

    def _enter_main(self, broker, ts, context):
        snapshot = context["snapshot"]
        if snapshot is None:
            self._audit("main", ts, context, "missing_snapshot")
            return
        positions = broker.positions()
        if broker.pending_entries("main") or any(broker.pending_reductions(p.id) or p.closing for p in positions):
            self._audit("main", ts, context, "unresolved_entry_or_reduction")
            return
        signal = generate_signal(snapshot)
        side = signal.side
        if side == "none":
            self._audit("main", ts, context, "signal_none")
            return
        if any(p.side != side for p in positions) or any(p["side"] != side for p in broker.pending_entries()):
            self._audit("main", ts, context, "opposing_exposure", side=side)
            return
        same = [p for p in positions if p.lane == "main" and p.side == side]
        tranche = len(same)
        derived = context["entry_inputs"][side]
        if derived is None:
            self._audit("main", ts, context, "missing_entry_inputs", side=side)
            return
        cooldown = None
        if side in self._cooldown:
            exited, stopped = self._cooldown[side]
            cooldown = CooldownState((ts - exited) // HALF_HOUR,
                SL_REENTRY_COOLDOWN_BARS if stopped else REENTRY_COOLDOWN_BARS)
        capital = float(broker.nav)
        evaluated = evaluate_entry_with_reason(signal, capital, tranche, inputs=derived, cooldown=cooldown,
            avg_entry=sum(p.entry for p in same) / len(same) if same else None,
            current_price=derived.entry_price)
        if evaluated.intent is None:
            self._audit("main", ts, context, evaluated.reason, side=side, tranche=tranche)
            return
        native = evaluated.intent
        grade = grade_agreement(side, {tf: state.trend for tf, state in snapshot.tf_states.items()})
        allocation = allocate(AllocationRequest("main", side, capital, native.limit_price,
            native.sizing.sl_price, tranche, grade, self.allocation,
            tuple(ActualPosition(p.lane, p.side, p.lots * LOT, p.entry, float(broker.mark), p.stop, True)
                  for p in positions), broker.pending_reservations(), native.sizing.qty))
        lots = _lots(allocation.qty)
        if lots <= 0:
            self._audit("main", ts, context, "allocation_zero", side=side, grade=grade,
                        allocation_reasons=list(allocation.reasons))
            return
        self._submit(broker, ts, context, "main", side, lots, native.limit_price, native.sizing.sl_price,
                     native.sizing.tp1_price, tranche, grade, native.sizing.qty)

    def _swing(self, broker, ts, context):
        four, previous, day = context["latest4h"], context["previous4h"], context["latest1d"]
        if any(row is None or any(row.get(k) is None for k in ("ma10", "ma35", "atr14", "close"))
               for row in (four, previous, day)):
            self._audit("swing", ts, context, "missing_native_features")
            return
        held = broker.positions("swing")
        if held:
            for pos in held:
                if swing.rule_exit_due(pos.side, four["close"], four["ma35"]) and not broker.pending_reductions(pos.id):
                    broker.close_parent(pos.id, ts, "swing_rule_exit")
            return  # No same-boundary rule exit and entry.
        if broker.pending_entries("swing"):
            return
        cross = swing.detect_cross(previous["ma10"], previous["ma35"], four["ma10"], four["ma35"])
        side = swing.entry_side(cross, day["ma10"], day["ma35"], four["close"], four["ma35"])
        if side is None:
            self._audit("swing", ts, context, "signal_none")
            return
        mains = [p.side for p in broker.positions("main")] + [p["side"] for p in broker.pending_entries("main")]
        if swing.conflicts_with_main(side, mains):
            self._audit("swing", ts, context, "opposing_main", side=side)
            return
        entry = float(broker.last)
        stop = swing.stop_price(side, entry, four["atr14"])
        sized = swing.compute_swing_sizing(float(broker.nav), entry, stop)
        lots = _lots(sized.qty)
        if sized.rejected or lots <= 0 or stop <= 0:
            self._audit("swing", ts, context, "native_sizing_reject", side=side)
            return
        limit = entry * (1.001 if side == "long" else .999)
        self._submit(broker, ts, context, "swing", side, lots, limit, stop, None, 0, "native_cross", sized.qty)

    def _submit(self, broker, ts, context, lane, side, lots, limit, stop, tp, tranche, grade, legacy_qty):
        self._serial += 1
        parent = f"{lane}-{ts}-{self._serial}"
        meta = {"decision_ts": ts, "feature_cutoff_ms": context["feature_cutoff_ms"],
                "grade": grade, "regime": self.bundle.regime_at(ts)["label"], "initial_sl": stop,
                "source_profile": "RESEARCH_MAIN_NOT_DEMO" if lane == "main" else "NATIVE_SWING"}
        intent = EntryIntent(parent, lane, side, lots, limit, stop, tp, 10 if lane == "main" else 5,
            tranche, ts + TTL, self.main_mode if lane == "main" else "single",
            "post_only" if lane == "main" else "ioc", meta)
        accepted = broker.submit(intent, ts)
        self._audit(lane, ts, context, "submitted" if accepted else "broker_rejected", parent_id=parent,
            side=side, tranche=tranche, grade=grade, legacy_qty=legacy_qty,
            requested_lots=lots, accepted_lots=accepted, initial_sl=stop)

    def confirm_callback(self, broker, positions, intent, child_index, ts_ms):
        if intent.lane != "main" or child_index <= 0:
            return False
        if not any(p.id == intent.parent_id and p.lots > 0 and not p.closing for p in positions):
            return False
        context = self.bundle.context(ts_ms)
        close, snapshot = context["prior_close30m"], context["snapshot"]
        return (snapshot is not None and close is not None
                and (close - intent.limit) * (1 if intent.side == "long" else -1) > 0
                and check_exit_signal(snapshot, intent.side).exit_action == "hold")
