"""Offline adaptive execution, one collateral ledger and synthetic OHLC paths.

No exchange adapter or production imports. Partial liquidity is a bar capacity,
not fresh liquidity at each crossing. All quantities are integer 0.001 BTC lots.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math

from core.scalp import ScalpExitPlan, Target

BAR = 300_000
DAY = 86_400_000
LOT = .001


class AdaptiveReplayError(RuntimeError):
    """Fail-closed financial error with a JSON-safe final evidence snapshot."""

    def __init__(self, message, state):
        super().__init__(message)
        self.state = state


@dataclass(frozen=True)
class AdaptiveConfig:
    start_ms: int
    end_ms: int
    lanes: tuple = ("S", "C")
    profile: str = "F"
    allocation: str = "fixed"
    cost_multiple: int = 1
    timing_ms: int = BAR
    partial: bool = False
    fixed_lots: int | None = None
    initial_cash: float = 10_000.
    path: str = "OHLC"
    resource_check: object = None
    lane_profiles: tuple = ()

    def __post_init__(self):
        if (type(self.start_ms) is not int or type(self.end_ms) is not int
                or self.start_ms < 0 or self.end_ms <= self.start_ms
                or self.start_ms % BAR or self.end_ms % BAR):
            raise ValueError("invalid period")
        if (self.lanes not in (("S",), ("C",), ("S", "C"))
                or self.profile not in ("F", "P", "Q", "H", "K", "U")
                or self.allocation not in ("fixed", "flex")
                or type(self.cost_multiple) is not int or self.cost_multiple not in (1, 2)
                or self.timing_ms not in (BAR, 6 * BAR)
                or type(self.partial) is not bool or self.path not in ("OHLC", "OLHC")
                or (self.resource_check is not None and not callable(self.resource_check))
                or not math.isfinite(self.initial_cash) or self.initial_cash <= 0):
            raise ValueError("invalid policy")
        if (type(self.lane_profiles) is not tuple or
                any(type(pair) is not tuple or len(pair) != 2 or
                    pair[0] not in self.lanes or pair[1] not in ("F", "P", "Q", "H", "K", "U")
                    for pair in self.lane_profiles) or
                (self.lane_profiles and
                 (len(self.lane_profiles) != len(self.lanes) or
                  {pair[0] for pair in self.lane_profiles} != set(self.lanes)))):
            raise ValueError("invalid lane profiles")
        if self.fixed_lots is not None and (
                type(self.fixed_lots) is not int or self.fixed_lots <= 0
                or self.fixed_lots % 4):
            raise ValueError("fixed_lots must be positive multiples of four")

    def profile_for(self, lane):
        return dict(self.lane_profiles).get(lane, self.profile)


def run_adaptive(bars, funding, signals, contexts, config: AdaptiveConfig, sink=None):
    """Return JSON-safe results; emit sparse ledger to a synchronous sink.

    ``contexts`` maps available timestamps to changed lane contexts. Signals and
    contexts must be right-edge available, not candle opening timestamps. The
    caller is responsible for provenance/indicator causality and funding coverage.
    Bars can include warmup before start; all requested period bars are mandatory.

    Partial model: one entry capacity per lane/bar, half remaining rounded down
    to four lots with a four-lot minimum. Separately, all exits share one capacity
    of ceil(opening held lots / 2), minimum one. A newborn position initializes
    this exit capacity once. Crossings never replenish it. Protective exits latch
    the whole remainder; cap reductions latch only their requested reduction.
    Entry TTL begins at first eligibility and expires before fills 30m later.
    """
    c = config
    rows = [tuple(row) for row in bars]
    previous = None
    for row in rows:
        if len(row) != 5 or not all(math.isfinite(float(x)) for x in row):
            raise ValueError("invalid bar")
        t, op, hi, lo, cl = row
        if (int(t) != t or t % BAR or min(op, hi, lo, cl) <= 0
                or lo > min(op, cl) or hi < max(op, cl) or lo > hi
                or (previous is not None and t != previous + BAR)):
            raise ValueError("invalid or noncontiguous bars")
        previous = t
    active = [r for r in rows if c.start_ms <= r[0] < c.end_ms]
    if len(active) != (c.end_ms-c.start_ms)//BAR or active[0][0] != c.start_ms:
        raise ValueError("missing requested bars")
    fs = [tuple(row) for row in funding]
    last = -1
    for row in fs:
        if len(row) != 2 or not all(math.isfinite(float(x)) for x in row):
            raise ValueError("invalid funding")
        if int(row[0]) != row[0] or row[0] <= last or row[0] % BAR:
            raise ValueError("funding must be unique ordered five-minute timestamps")
        if abs(row[1]) >= 1:
            raise ValueError("funding rate magnitude must be below one")
        last = row[0]
    f_map = {int(t): float(rate) for t, rate in fs}
    signal_map = {}
    ids = set()
    for original in signals:
        s = dict(original)
        if (s["signal_id"] in ids or s["lane"] not in ("S", "C")
                or type(s["available_at"]) is not int or s["available_at"] % BAR
                or type(s["direction"]) is not int or s["direction"] not in (-1, 1)
                or type(s["strong"]) is not bool
                or not all(math.isfinite(s[k]) and s[k] > 0
                           for k in ("reference_price", "stop_distance"))
                or s["stop_distance"] >= s["reference_price"]
                or (s["max_hold_ms"] is not None and
                    (type(s["max_hold_ms"]) is not int or s["max_hold_ms"] <= 0))):
            raise ValueError("invalid signal")
        if "risk_share" in s and (type(s["risk_share"]) not in (int, float) or
                not math.isfinite(s["risk_share"]) or not 0 < s["risk_share"] <= 1):
            raise ValueError("invalid risk share")
        if "cancel_if_context_invalid" in s and type(s["cancel_if_context_invalid"]) is not bool:
            raise ValueError("invalid context cancellation flag")
        ids.add(s["signal_id"])
        signal_map.setdefault(s["available_at"], []).append(s)
    for t, lanes in contexts.items():
        if type(t) is not int or t % BAR:
            raise ValueError("context timestamp")
        for lane, value in lanes.items():
            required = {"exit_long", "exit_short", "trend_long", "trend_short"}
            optional_bool = {"allow_entry_long", "allow_entry_short", "phase_exit_long", "phase_exit_short",
                             "phase_valid_long", "phase_valid_short"}
            if (lane not in ("S", "C") or not required <= set(value) or
                    set(value)-required-optional_bool-{"trail_long", "trail_short", "phase_at"}):
                raise ValueError("context fields")
            if any(type(value[k]) is not bool for k in set(value) & (required | optional_bool)):
                raise ValueError("context boolean")
            for key in ("trail_long", "trail_short"):
                if key in value and (type(value[key]) not in (int, float) or
                        not math.isfinite(value[key]) or value[key] <= 0):
                    raise ValueError("context trail")
            if "phase_at" in value and (type(value["phase_at"]) is not int or
                    value["phase_at"] < 0 or value["phase_at"] % BAR or value["phase_at"] > t):
                raise ValueError("context phase timestamp")
            if (any(value.get(k, False) for k in ("phase_exit_long", "phase_exit_short")) or
                    any(value.get(k) is False for k in ("phase_valid_long", "phase_valid_short"))) and "phase_at" not in value:
                raise ValueError("phase exit requires timestamp")

    cash = c.initial_cash
    positions, pending = {}, {}
    context = {}
    for t in sorted(contexts):
        if t < c.start_ms:
            context.update(contexts[t])
    campaigns, entry_fills, daily = [], [], []
    counters = dict(signals=0, accepted=0, rejected_busy=0, rejected_opposite=0,
                    rejected_budget=0, fills=0, partial_exits=0, expired=0,
                    amendments=0, cap_breaches=0, cap_breach_nodes=0,
                    rejected_geometry=0, suppressed_target_plans=0)
    ledger_hash, node_hash = hashlib.sha256(), hashlib.sha256()
    peak = c.initial_cash
    mdd = 0.
    fee_total = funding_total = slip_total = turnover = 0.
    realized_total = 0.
    gross_sum = 0.
    max_gross = max_heat = identity_error = 0.
    exit_capacity = {}
    taker, maker, slip = .00055*c.cost_multiple, .0002*c.cost_multiple, .0005*c.cost_multiple

    def emit(kind, t, **data):
        event = dict(kind=kind, ts=int(t), **data)
        ledger_hash.update(json.dumps(event, sort_keys=True, separators=(",", ":"),
                                      allow_nan=False).encode()+b"\n")
        if sink is not None:
            sink(event)

    def nav(mark):
        return cash + sum(p["lots"]*LOT*p["direction"]*(mark-p["entry"])
                          for p in positions.values())

    def pending_unit_heat(order, mark):
        adverse = mark*(1+order["direction"]*slip)
        p = positions.get(order["lane"])
        stop = p["stop"] if p else adverse-order["direction"]*order["stop_distance"]
        return LOT*(max(0., order["direction"]*(adverse-stop))+mark*(2*taker+slip))

    def order_limits(order, value, other_gross, other_heat):
        # These are whole-order budgets (filled + reserved), never a new share
        # of free capital after subtracting the order's own position a second time.
        share = order["allocation_share"]
        if c.allocation == "fixed":
            current_gross, current_heat = value*share, .02*value*share
        else:
            current_gross = max(0., value-other_gross)*share
            current_heat = max(0., .02*value-other_heat)*share
        return (min(order["gross_budget"], current_gross),
                min(order["heat_budget"], current_heat))

    def risk(mark, include_pending=True, only_lane=None):
        gross = heat = 0.
        for lane, p in positions.items():
            if only_lane is not None and lane != only_lane:
                continue
            q = p["lots"]*LOT
            gross += q*mark
            heat += q*(max(0., p["direction"]*(p["entry"]-p["stop"]))
                       + mark*(taker+slip))
        if include_pending:
            for lane, order in pending.items():
                if only_lane is not None and lane != only_lane:
                    continue
                q = order["remaining"]*LOT
                adverse = mark*(1+order["direction"]*slip)
                gross += q*max(mark, adverse)
                heat += order["remaining"]*pending_unit_heat(order, mark)
        return gross, heat

    def sample(t, mark):
        nonlocal peak, mdd, max_gross, max_heat, identity_error
        value = nav(mark)
        if not math.isfinite(value) or value <= 0:
            raise AdaptiveReplayError("insolvent adaptive replay", dict(
                ts=int(t), mark=mark, cash=cash, positions=positions, pending=pending,
                counters=counters, ledger_hash=ledger_hash.hexdigest()))
        peak = max(peak, value)
        mdd = max(mdd, (peak-value)/peak)
        g, h = risk(mark, False)
        max_gross = max(max_gross, g/value)
        max_heat = max(max_heat, h/value)
        if g > value+1e-8 or h > .02*value+1e-8:
            counters["cap_breach_nodes"] += 1
        identity_error = max(identity_error, abs(cash-(c.initial_cash+realized_total-fee_total-funding_total)))
        if identity_error > 1e-6:
            raise AdaptiveReplayError("cash identity", dict(
                ts=int(t), mark=mark, cash=cash, positions=positions, pending=pending,
                counters=counters, ledger_hash=ledger_hash.hexdigest()))
        for p in positions.values():
            pnl = p["realized_pnl"] + p["lots"]*LOT*p["direction"]*(mark-p["entry"]) - p["fees"]-p["funding"]
            p["peak_net_pnl"] = max(p["peak_net_pnl"], pnl)
            p["giveback"] = max(p["giveback"], p["peak_net_pnl"]-pnl)
            p["extreme"] = max(p["extreme"], mark) if p["direction"] == 1 else min(p["extreme"], mark)
        node_hash.update(f"{int(t)}:{value:.12f}:{g:.12f}:{h:.12f}\n".encode())

    def freeze(lane, t):
        p = positions.get(lane)
        pending.pop(lane, None)
        if p is None or p["frozen"]:
            return
        p["frozen"] = True
        p["initial_lots"] = p["lots"]
        p["initial_risk"] = p["lots"]*LOT*p["stop_distance"]
        profile = c.profile_for(lane)
        if profile in ("Q", "H", "K", "U"):
            d, r = p["direction"], p["stop_distance"]
            furthest = 1.5 if profile in ("H", "K", "U") else .5
            if p["entry"]+d*furthest*r <= 0:
                p["target_plan_suppressed"] = "nonpositive_target_after_partial_entry"
                counters["suppressed_target_plans"] += 1
                emit("target_plan_suppressed", t, lane=lane, reason=p["target_plan_suppressed"])
                emit("entry_frozen", t, lane=lane, lots=p["lots"])
                return
            extras = (Target(p["entry"]+d*1.5*r, p["lots"]//4),) if profile in ("H", "K", "U") else ()
            plan = ScalpExitPlan("long" if d == 1 else "short", p["entry"],
                                 p["lots"], 1, p["entry"]+d*.5*r, extras)
            p["targets"] = [dict(price=x.price, lots=x.lots, filled=0, retired=0) for x in plan.targets]
        emit("entry_frozen", t, lane=lane, lots=p["lots"])

    def cancel_entry(lane, t):
        if lane in pending:
            freeze(lane, t)

    def close(lane, requested, mark, t, reason, limit=False):
        nonlocal cash, fee_total, slip_total, turnover, realized_total
        p = positions.get(lane)
        if p is None:
            return 0
        if lane not in exit_capacity:
            exit_capacity[lane] = max(1, math.ceil(p["lots"]/2)) if c.partial else p["lots"]
        lots = min(requested, p["lots"], exit_capacity[lane])
        if not limit:
            cancel_entry(lane, t)
            p["exit_due"] = int(t)
            p["exit_reason"] = reason
            p["exit_remaining"] = requested
            p["amend"] = None
            if reason != "risk_cap":
                p["targets"] = []
        if not lots:
            return 0
        price = mark if limit else mark*(1-p["direction"]*slip)
        q = lots*LOT
        fee = q*price*(maker if limit else taker)
        pnl = q*p["direction"]*(price-p["entry"])
        cash += pnl-fee
        realized_total += pnl
        p["realized_pnl"] += pnl
        p["fees"] += fee
        fee_total += fee
        slip_total += q*abs(price-mark)
        turnover += q*price
        p["lots"] -= lots
        if not limit:
            p["exit_remaining"] -= lots
            if reason == "risk_cap":
                excess = max(0, sum(x["lots"]-x["filled"]-x["retired"] for x in p["targets"])-p["lots"])
                for target in reversed(p["targets"]):
                    retired = min(excess, target["lots"]-target["filled"]-target["retired"])
                    target["retired"] += retired
                    excess -= retired
                if not p["exit_remaining"]:
                    p["exit_due"] = None
                    p["exit_reason"] = None
        exit_capacity[lane] -= lots
        counters["fills"] += 1
        emit("fill", t, lane=lane, signal_id=p["signal_id"], action="exit", reason=reason,
             lots=lots, price=price, fee=fee, realized_pnl=pnl, remaining_lots=p["lots"], cash=cash)
        if p["lots"] == 0:
            p["closed_at"] = int(t)
            p["end_ms"] = int(t)
            p["status"] = "CLOSED"
            p["exit_reason"] = reason
            p["unrealized_pnl"] = 0.
            p["net_pnl"] = p["realized_pnl"]-p["fees"]-p["funding"]
            p["net_r"] = p["net_pnl"]/p["initial_risk"]
            p["giveback"] = max(p["giveback"], p["peak_net_pnl"]-p["net_pnl"])
            campaigns.append(dict(p))
            positions.pop(lane)
            pending.pop(lane, None)
        elif not limit:
            counters["partial_exits"] += 1
        return lots

    def protective(mark, t, old=None):
        # Segment crossings are processed by caller; this handles gaps/open.
        for lane in tuple(positions):
            p = positions.get(lane)
            if p and p["direction"]*(mark-p["stop"]) <= 0:
                close(lane, p["lots"], mark, t, "stop")

    previous_close = next((float(r[4]) for r in reversed(rows) if r[0] < c.start_ms), None)
    for row in active:
        t, op, hi, lo, cl = int(row[0]), *map(float, row[1:])
        if c.resource_check is not None and (t-c.start_ms) % DAY == 0:
            c.resource_check(dict(ts=t, campaigns=len(campaigns)))
        exit_capacity = {lane: max(1, math.ceil(p["lots"]/2)) if c.partial else p["lots"]
                         for lane, p in positions.items()}
        if t in f_map:
            if positions and previous_close is None:
                raise ValueError("funding has no prior available close")
            for lane, p in positions.items():
                charge = p["lots"]*LOT*p["direction"]*previous_close*f_map[t]
                cash -= charge
                p["funding"] += charge
                funding_total += charge
                emit("funding", t, lane=lane, amount=charge, mark=previous_close, rate=f_map[t], cash=cash)
            if previous_close is not None:
                sample(t, previous_close)
        sample(t, op)
        protective(op, t)
        for lane in tuple(positions):
            p = positions.get(lane)
            if p is None:
                continue
            if p["exit_due"] is not None and p["exit_due"] <= t:
                close(lane, p.get("exit_remaining", p["lots"]), op, t, p["exit_reason"])
                continue
            if p["amend"] and p["amend"][0] <= t:
                p["stop"] = max(p["stop"], p["amend"][1]) if p["direction"] == 1 else min(p["stop"], p["amend"][1])
                p["amend"] = None
                counters["amendments"] += 1
                emit("stop_amended", t, lane=lane, stop=p["stop"])
                if p["direction"]*(op-p["stop"]) <= 0:
                    close(lane, p["lots"], op, t, "amended_stop_crossed")
        for lane, p in list(positions.items()):
            if p["exit_due"] is None:
                for index, target in enumerate(p["targets"]):
                    if p["direction"]*(op-target["price"]) > 0:
                        target["filled"] += close(lane, target["lots"]-target["filled"]-target["retired"],
                                                  target["price"], t, f"tp{index+1}", True)
        context.update(contexts.get(t, {}))
        for lane, order in list(pending.items()):
            side = "long" if order["direction"] == 1 else "short"
            if (order.get("cancel_if_context_invalid", False) and
                    context.get(lane, {}).get("allow_entry_"+side) is False):
                emit("entry_cancelled", t, lane=lane, signal_id=order["signal_id"],
                     reason="context_invalid", cancelled_lots=order["remaining"])
                freeze(lane, t)
        # Drift caps: reduce at the next bar open, with the same partial capacity.
        g, h = risk(op, False)
        if g > nav(op)+1e-8 or h > .02*nav(op)+1e-8:
            counters["cap_breaches"] += 1
            for lane in tuple(positions):
                if g <= nav(op)+1e-8 and h <= .02*nav(op)+1e-8:
                    break
                p = positions[lane]
                if p["exit_due"] is not None:
                    continue
                unit_cost = LOT*op*(slip+taker*(1-p["direction"]*slip))
                unit_heat = LOT*(max(0., p["direction"]*(p["entry"]-p["stop"]))+op*(taker+slip))
                value = nav(op)
                gross_need = max(0., g-value)/max(1e-12, LOT*op-unit_cost)
                heat_need = max(0., h-.02*value)/max(1e-12, unit_heat-.02*unit_cost)
                reduce_lots = min(p["lots"], max(1, math.ceil(max(gross_need, heat_need)-1e-10)))
                close(lane, reduce_lots, op, t, "risk_cap")
                g, h = risk(op, False)
        for lane in tuple(pending):
            if t >= pending[lane]["expires"]:
                counters["expired"] += 1
                freeze(lane, t)
        for lane, p in list(positions.items()):
            if t % c.timing_ms or p["exit_due"] is not None:
                continue
            cx = context.get(lane, {})
            side = "long" if p["direction"] == 1 else "short"
            profile = c.profile_for(lane)
            due = cx.get("exit_"+side, False) or (p["max_hold_ms"] is not None and t >= p["opened_at"]+p["max_hold_ms"])
            phase_due = (profile == "U" and (cx.get("phase_exit_"+side, False) or
                         cx.get("phase_valid_"+side) is False)
                         and cx.get("phase_at", -1) > p["opened_at"])
            due = due or phase_due
            if due:
                p["exit_due"], p["exit_reason"] = t+c.timing_ms, "phase_exit" if phase_due else "rule_or_deadline"
                p["exit_remaining"] = p["lots"]
                cancel_entry(lane, t)
                p["targets"] = []
                continue
            if profile != "F":
                distance = p["stop_distance"]
                tp1 = bool(p["targets"] and p["targets"][0]["filled"] == p["targets"][0]["lots"])
                tp2 = bool(len(p["targets"]) == 2 and p["targets"][1]["filled"] == p["targets"][1]["lots"])
                if profile in ("K", "U") and not tp1:
                    continue
                if profile in ("H", "K") and tp1 and cx.get("trend_"+side, False):
                    distance *= 2 if tp2 else 1.5
                desired = p["extreme"]-p["direction"]*distance
                if profile == "U":
                    if "trail_"+side not in cx:
                        counters["policy_errors"] = counters.get("policy_errors", 0)+1
                        emit("policy_error", t, lane=lane, reason="missing_causal_trail")
                        continue
                    desired = cx["trail_"+side]
                if p["direction"]*(p["extreme"]-p["entry"]) >= p["stop_distance"] or (profile in ("H", "K", "U") and tp1):
                    be = p["entry"]*(1+p["direction"]*(2*taker+slip))
                    desired = max(desired, be) if p["direction"] == 1 else min(desired, be)
                if p["direction"]*(desired-p["stop"]) > 1e-10:
                    # Do not postpone an already queued earlier amendment.
                    if p["amend"] is None:
                        p["amend"] = (t+c.timing_ms, desired)
                        emit("stop_requested", t, lane=lane, stop=desired, eligible_at=t+c.timing_ms)
        for s in sorted(signal_map.get(t, []), key=lambda x: x["lane"] != "S"):
            lane = s["lane"]
            if lane not in c.lanes:
                continue
            counters["signals"] += 1
            if lane in positions or lane in pending:
                counters["rejected_busy"] += 1
                continue
            if any(x["direction"] != s["direction"] for x in list(positions.values())+list(pending.values())):
                counters["rejected_opposite"] += 1
                continue
            g, h = risk(op)
            value = nav(op)
            if c.allocation == "fixed":
                share = 1/len(c.lanes)
                gross_budget = min(value*share, max(0., value-g))
                heat_budget = min(value*.02*share, max(0., value*.02-h))
                if "risk_share" in s:
                    gross_budget *= s["risk_share"]
                    heat_budget *= s["risk_share"]
                    share *= s["risk_share"]
            else:
                share = s.get("risk_share", .5 if lane == "C" and not s["strong"] else 1.)
                gross_budget = max(0., value-g)*share
                heat_budget = max(0., value*.02-h)*share
            per_lot_heat = LOT*(s["stop_distance"]+op*(2*taker+slip))
            lots = int(min(gross_budget/(LOT*op*(1+slip+2*taker)), heat_budget/per_lot_heat))//4*4
            if c.fixed_lots is not None:
                lots = c.fixed_lots if c.fixed_lots <= lots else 0
            if not lots:
                counters["rejected_budget"] += 1
                continue
            pending[lane] = dict(s, remaining=lots, requested=lots, eligible=t+c.timing_ms,
                                 expires=t+c.timing_ms+6*BAR, allocation_share=share,
                                 gross_budget=gross_budget, heat_budget=heat_budget)
            counters["accepted"] += 1
            emit("entry_reserved", t, lane=lane, signal_id=s["signal_id"], lots=lots,
                 gross_budget=gross_budget, heat_budget=heat_budget, allocation_share=share)
        for lane in tuple(pending):
            order = pending[lane]
            side = "long" if order["direction"] == 1 else "short"
            if (order.get("cancel_if_context_invalid", False) and
                    context.get(lane, {}).get("allow_entry_"+side) is False):
                emit("entry_cancelled", t, lane=lane, signal_id=order["signal_id"],
                     reason="context_invalid", cancelled_lots=order["remaining"])
                freeze(lane, t)
                continue
            if t < order["eligible"]:
                continue
            d = order["direction"]
            price = op*(1+d*slip)
            if lane not in positions:
                native_stop = price-d*order["stop_distance"]
                profile = c.profile_for(lane)
                furthest = 1.5 if profile in ("H", "K", "U") else .5
                invalid_target = (profile in ("Q", "H", "K", "U") and
                                  price+d*furthest*order["stop_distance"] <= 0)
                if native_stop <= 0 or d*(op-native_stop) <= 0 or invalid_target:
                    counters["rejected_geometry"] += 1
                    emit("entry_rejected_geometry", t, lane=lane, signal_id=order["signal_id"])
                    pending.pop(lane)
                    continue
            lots = max(4, (order["remaining"]//8)*4) if c.partial else order["remaining"]
            lots = min(lots, order["remaining"])
            # Admission includes all other reservations and post-cost NAV.
            g, h = risk(op)
            old_res_g = order["remaining"]*LOT*max(op, price)
            old_res_h = order["remaining"]*pending_unit_heat(order, op)
            lane_g, lane_h = risk(op, False, lane)
            held = positions.get(lane)
            stop = held["stop"] if held else price-d*order["stop_distance"]
            held_lots = held["lots"] if held else 0
            held_entry = held["entry"] if held else price
            while lots:
                q = lots*LOT
                new_nav = nav(op)-q*(abs(price-op)+price*taker)
                new_g = g-old_res_g+q*op
                aggregate_entry = (held_entry*held_lots+price*lots)/(held_lots+lots)
                aggregate_heat = (held_lots+lots)*LOT*(max(0., d*(aggregate_entry-stop))+op*(taker+slip))
                new_h = h-old_res_h-lane_h+aggregate_heat
                own_g_limit, own_h_limit = order_limits(
                    order, new_nav, g-old_res_g-lane_g, h-old_res_h-lane_h)
                lane_ok = (lane_g+q*op <= own_g_limit+1e-9 and
                           aggregate_heat <= own_h_limit+1e-9)
                if new_g <= new_nav+1e-9 and new_h <= .02*new_nav+1e-9 and lane_ok:
                    break
                lots -= 4
            if c.fixed_lots is not None and not c.partial and lots != order["remaining"]:
                lots = 0
            if not lots:
                freeze(lane, t)
                counters["rejected_budget"] += 1
                continue
            # Clip the tail BEFORE cash/positions are mutated. Its hypothetical
            # future fill uses the unchanged currently native stop, not signal R.
            tail = order["remaining"]-lots
            tail_unit_g = LOT*max(op, price)
            tail_unit_h = LOT*(max(0., d*(price-stop))+op*(2*taker+slip))
            tail_max = min(max(0., new_nav-new_g)/tail_unit_g,
                           max(0., .02*new_nav-new_h)/tail_unit_h)
            tail_max = min(tail_max,
                           max(0., own_g_limit-(lane_g+lots*LOT*op))/tail_unit_g,
                           max(0., own_h_limit-aggregate_heat)/tail_unit_h)
            tail = min(tail, int(tail_max)//4*4)
            if tail != order["remaining"]-lots:
                emit("reservation_reduced", t, lane=lane,
                     cancelled_lots=order["remaining"]-lots-tail)
            order["remaining"] = lots+tail
            fee = lots*LOT*price*taker
            cash -= fee
            fee_total += fee
            slip_total += lots*LOT*abs(price-op)
            turnover += lots*LOT*price
            p = positions.get(lane)
            if p is None:
                p = dict(lane=lane, signal_id=order["signal_id"], direction=d, lots=0,
                         initial_lots=0, entry=price, stop=price-d*order["stop_distance"],
                         first_native_stop=price-d*order["stop_distance"],
                         stop_distance=order["stop_distance"], extreme=price, opened_at=t,
                         start_ms=t, end_ms=None, status="OPEN",
                         max_hold_ms=order["max_hold_ms"], frozen=False, targets=[], amend=None,
                         exit_due=None, exit_reason=None, exit_remaining=0, realized_pnl=0., fees=0., funding=0.,
                         initial_risk=0., peak_net_pnl=0., giveback=0., closed_at=None)
                positions[lane] = p
            old_lots = p["lots"]
            p["entry"] = (p["entry"]*old_lots+price*lots)/(old_lots+lots)
            p["lots"] += lots
            # Further partial fills do not amend protection. Only the queued
            # policy amendment path may tighten this first native stop.
            p["fees"] += fee
            p["initial_lots"] += lots
            p["initial_risk"] = p["initial_lots"]*LOT*p["stop_distance"]
            p["initial_stop_risk_amount"] = p["initial_lots"]*LOT*max(0., d*(p["entry"]-p["first_native_stop"]))
            order["remaining"] -= lots
            counters["fills"] += 1
            fill = dict(ts=t, lane=lane, signal_id=p["signal_id"], lots=lots, price=price)
            entry_fills.append(fill)
            emit("fill", t, lane=lane, signal_id=p["signal_id"], action="entry", lots=lots,
                 price=price, fee=fee, remaining_lots=p["lots"], stop=p["stop"], cash=cash)
            if order["remaining"] == 0:
                freeze(lane, t)
            actual_g, actual_h = risk(op)
            actual_lane_g, actual_lane_h = risk(op, True, lane)
            own_g_limit, own_h_limit = order_limits(
                order, nav(op), actual_g-actual_lane_g, actual_h-actual_lane_h)
            if (actual_g > nav(op)+1e-7 or actual_h > .02*nav(op)+1e-7
                    or actual_lane_g > own_g_limit+1e-7 or actual_lane_h > own_h_limit+1e-7):
                raise AdaptiveReplayError("post-fill reservation cap", dict(
                    ts=t, cash=cash, positions=positions, pending=pending,
                    counters=counters, ledger_hash=ledger_hash.hexdigest()))
            protective(op, t)
        sample(t, op)
        path = (op, hi, lo, cl) if c.path == "OHLC" else (op, lo, hi, cl)
        times = (t, t+100_000, t+200_000, t+BAR-1)
        for i in range(3):
            left, right = path[i:i+2]
            # Crossing order shared across sleeves, refreshed after every fill.
            crossings = []
            for lane, p in positions.items():
                d = p["direction"]
                if d*(left-p["stop"]) > 0 and d*(right-p["stop"]) <= 0:
                    crossings.append((abs((p["stop"]-left)/(right-left)), lane, "stop", p["stop"], -1))
                if p["exit_due"] is None:
                    for index, target in enumerate(p["targets"]):
                        if target["filled"]+target["retired"] < target["lots"] and d*(right-target["price"]) > 0:
                            frac = (0. if d*(left-target["price"]) > 0 else
                                    (target["price"]-left)/(right-left))
                            if d*(left-target["price"]) > 0 or 0 <= frac <= 1:
                                crossings.append((frac, lane, "tp", target["price"], index))
            for frac, lane, kind, price, index in sorted(crossings):
                p = positions.get(lane)
                if p is None:
                    continue
                at = times[i]+int(frac*(times[i+1]-times[i]))
                mark_at = left+frac*(right-left)
                sample(at, mark_at)
                if kind == "stop":
                    close(lane, p["lots"], price, at, "stop")
                elif p["exit_due"] is None and index < len(p["targets"]):
                    target = p["targets"][index]
                    filled = close(lane, target["lots"]-target["filled"]-target["retired"], price, at, f"tp{index+1}", True)
                    target["filled"] += filled
                sample(at, mark_at)
            sample(times[i+1], right)
        gross_sum += risk(cl, False)[0]/nav(cl)
        if (t+BAR) % DAY == 0 or t+BAR == c.end_ms:
            daily.append([t+BAR, nav(cl)])
        previous_close = cl
    final_mark = float(active[-1][4])
    for p in positions.values():
        p["unrealized_pnl"] = p["lots"]*LOT*p["direction"]*(final_mark-p["entry"])
        p["net_pnl"] = p["realized_pnl"]+p["unrealized_pnl"]-p["fees"]-p["funding"]
        p["net_r"] = p["net_pnl"]/p["initial_risk"]
    final = nav(final_mark)
    return dict(metrics=dict(start_ms=c.start_ms, end_ms=c.end_ms,
                             initial_nav=c.initial_cash, final_nav=final,
                             total_return=final/c.initial_cash-1, mtm_mdd=mdd,
                             mean_gross=gross_sum/len(active), fees=fee_total,
                             funding=funding_total, slippage=slip_total,
                             realized_pnl=realized_total,
                             unrealized_pnl=sum(p["unrealized_pnl"] for p in positions.values()),
                             turnover=turnover/c.initial_cash, max_gross=max_gross,
                             max_heat=max_heat, identity_error=identity_error,
                             completed_campaigns=len(campaigns)), counters=counters,
                daily_nav=daily, campaigns=campaigns+list(positions.values()),
                final_positions=dict(positions), entry_fills=entry_fills,
                hashes=dict(ledger=ledger_hash.hexdigest(), nodes=node_hash.hexdigest()),
                scope_flags=dict(offline=True, synthetic_paths=True, auto_activate=False,
                                 profitability_status="INSUFFICIENT", partial_capacity="per_lane_per_bar"))
