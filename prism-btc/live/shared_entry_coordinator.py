"""Opt-in same-host entry admission; never changes strategy sizing or protection.

Gross is valued at a fresh broker mark, NOT a worst-case execution ceiling.
Heat uses adverse-fill bounds (buy ceiling / sell floor); favorable short fills
can exceed mark-valued gross. Native stops can slip. Neither bound promises a
maximum realized loss. Both demo wallets must be bound to verified distinct UIDs.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from functools import wraps
import hashlib
import math
import os
from pathlib import Path
import threading
import time

from core.portfolio_risk import ProposedEntry
from live import tracking
from live.entry_reservations import EntryReservationStore, LockBusy, execution_mutex
from live.exchange_snapshot import read_complete

PREFIX = "BTC_SHARED_ENTRY_"
_held = threading.local()


def requested():
    return os.environ.get(PREFIX + "ENABLED", "false").lower() not in ("false", "0")


@dataclass(frozen=True)
class Config:
    heat: float
    slippage: float
    main_uid: str
    swing_uid: str


def configuration():
    if not requested():
        return None
    if os.environ.get(PREFIX + "ENABLED", "").lower() not in ("true", "1"):
        raise ValueError("invalid_shared_entry_enabled")
    values = [float(os.environ[PREFIX + key]) for key in ("COMBINED_HEAT", "SLIPPAGE")]
    if not all(math.isfinite(v) and 0 < v < 1 for v in values):
        raise ValueError("invalid_shared_entry_limits")
    uids = [os.environ[PREFIX + lane + "_UID"] for lane in ("MAIN", "SWING")]
    if any(not uid.isdigit() or int(uid) <= 0 for uid in uids) or uids[0] == uids[1]:
        raise ValueError("distinct_account_uid_required")
    return Config(*values, *uids)


def database_path(conn):
    rows = conn.execute("PRAGMA database_list").fetchall()
    path = next((r[2] for r in rows if r[1] == "main"), "")
    if not path:
        raise ValueError("durable_shared_database_required")
    return Path(path).resolve(strict=True)


@contextmanager
def mutation_lock(conn):
    """Reentrant only within this thread and PID, nonblocking across processes."""
    rows = conn.execute("PRAGMA database_list").fetchall()
    if not any(r[1] == "main" and r[2] for r in rows):
        # Isolated in-memory fixtures have no cross-process shared state. The
        # risk coordinator itself still refuses such a non-durable database.
        yield
        return
    path = str(database_path(conn)) + ".btc-execution.lock"
    key = (os.getpid(), path)
    held = getattr(_held, "keys", set())
    if key in held:
        yield
        return
    with execution_mutex(path):
        _held.keys = held | {key}
        try:
            yield
        finally:
            _held.keys = held


def serialized(method):
    @wraps(method)
    def wrapped(self, *args, **kwargs):
        try:
            with mutation_lock(self.conn):
                return method(self, *args, **kwargs)
        except LockBusy:
            tracking.log_event(self.conn, "execution_busy", "shared execution active; skip stale work",
                               mode=getattr(self, "mode", "swing"))
            return None
    return wrapped


def _positive(value):
    if isinstance(value, bool):
        raise ValueError("invalid_broker_number")
    number = float(value)
    if not math.isfinite(number) or number <= 0:
        raise ValueError("invalid_broker_number")
    return number


def _nonnegative(value):
    if isinstance(value, bool):
        raise ValueError("invalid_broker_number")
    number = float(value)
    if not math.isfinite(number) or number < 0:
        raise ValueError("invalid_broker_number")
    return number


def _call(session, method, **kwargs):
    response = getattr(session, method)(**kwargs)
    if not isinstance(response, dict) or response.get("retCode") != 0:
        raise ValueError("broker_snapshot_unavailable")
    return response


def _sessions(adapter, lane):
    # Lazy imports avoid demo <-> swing module initialization cycles.
    from live.demo import _make_session
    from live.swing import _make_swing_session
    other, error = (_make_swing_session() if lane == "main" else _make_session())
    if other is None or error:
        raise ValueError("dual_account_snapshot_unavailable")
    return {lane: adapter.sess, "swing" if lane == "main" else "main": other}


@dataclass(frozen=True)
class LaneSnapshot:
    lane: str
    qty: float
    side: str | None
    entry: float
    mark: float
    stop: float
    orders: tuple


def snapshot(adapter, lane, config, store):
    sessions = _sessions(adapter, lane)
    started = time.monotonic()
    for name in ("main", "swing"):
        identity = _call(sessions[name], "get_api_key_information")["result"].get("userID")
        if str(identity) != (config.main_uid if name == "main" else config.swing_uid):
            raise ValueError("account_uid_mismatch")
    confirmations = reconcile_reservations(store, sessions, apply=False)
    result = []
    capital = None
    for name in ("main", "swing"):
        session = sessions[name]
        def call(method, **kwargs):
            return _call(session, method, **kwargs)
        if name == "main":
            wallet = call("get_wallet_balance", accountType="UNIFIED")["result"]["list"]
            if len(wallet) != 1:
                raise ValueError("capital_unknown")
            capital = _positive(wallet[0]["totalEquity"])
        positions = read_complete(call, "get_positions", category="linear", symbol="BTCUSDT")
        orders = read_complete(call, "get_open_orders", category="linear", symbol="BTCUSDT")
        tickers = call("get_tickers", category="linear", symbol="BTCUSDT")["result"]["list"]
        if positions is None or orders is None or len(tickers) != 1 or tickers[0].get("symbol") != "BTCUSDT":
            raise ValueError("snapshot_unknown")
        mark = _positive(tickers[0]["markPrice"])
        rows = positions["result"]["list"]
        if len(rows) > 1:
            raise ValueError("unsupported_hedge_position")
        qty, entry, stop, side = 0., 0., 0., None
        if rows:
            row = rows[0]
            qty = _nonnegative(row["size"])
            if (row.get("symbol") != "BTCUSDT" or row.get("positionIdx") != 0
                    or not math.isfinite(qty) or qty < 0):
                raise ValueError("invalid_position")
            if qty:
                if row["side"] not in ("Buy", "Sell"):
                    raise ValueError("invalid_position_side")
                side = "long" if row["side"] == "Buy" else "short"
                entry = _positive(row["avgPrice"])
                local = tracking.load_open_positions(adapter.conn, "demo" if name == "main" else "swing")
                if (any(p.side != side for p in local)
                        or not math.isclose(sum(p.qty for p in local), qty, rel_tol=0, abs_tol=1e-9)):
                    raise ValueError("unreconciled_or_manual_position")
                candidates = []
                for order in orders["result"]["list"]:
                    if (order.get("symbol") == "BTCUSDT" and order.get("positionIdx") == 0
                            and order.get("reduceOnly") is True and order.get("orderType") == "Market"
                            and order.get("orderStatus") == "Untriggered"
                            and order.get("stopOrderType") in ("StopLoss", "PartialStopLoss", "Stop")
                            and order.get("side") == ("Sell" if side == "long" else "Buy")
                            and order.get("triggerBy") == "LastPrice"
                            and order.get("triggerDirection") == (2 if side == "long" else 1)
                            and _positive(order["qty"]) >= qty):
                        candidates.append(_positive(order["triggerPrice"]))
                if not candidates:
                    raise ValueError("protection_unconfirmed")
                stop = max(candidates) if side == "long" else min(candidates)
        if qty == 0 and tracking.load_open_positions(adapter.conn, "demo" if name == "main" else "swing"):
            raise ValueError("unsettled_local_position")
        result.append(LaneSnapshot(name, qty, side, entry, mark, stop,
                                   tuple(dict(o) for o in orders["result"]["list"])))
    known = {old.order_link_id: (old, update, parent) for old, update, parent in confirmations}
    for item in result:
        mode = "demo" if item.lane == "main" else "swing"
        pending = tracking.get_meta(adapter.conn, "pending_order" if item.lane == "main" else "swing_entry_pending", mode)
        if pending and pending.get("link_id") not in known:
            raise ValueError("unreserved_legacy_entry_intent")
        for key in (("pending_reduce", "stop_submission_intent") if item.lane == "main"
                    else ("swing_close_pending", "swing_stop_intent")):
            intent = tracking.get_meta(adapter.conn, key, mode)
            if intent and (("stop" not in key) or (intent.get("submitted") and intent.get("status") not in
                                                  ("CONFIRMED", "CONFIRMED_OWNED", "SETTLED"))):
                raise ValueError("unresolved_lane_lifecycle")
        for order in item.orders:
            if order.get("reduceOnly") is True:
                continue
            proof = known.get(order.get("orderLinkId"))
            if not proof:
                raise ValueError("unreconciled_or_manual_entry_order")
            old, update, parent = proof
            if (old.lane != item.lane or order.get("orderId") != parent["orderId"]
                    or order.get("symbol") != "BTCUSDT" or order.get("positionIdx") != 0
                    or order.get("side") != parent["side"] or order.get("orderType") != "Limit"
                    or order.get("reduceOnly") is not False
                    or order.get("orderStatus") != parent["orderStatus"]
                    or any(not math.isclose(float(order[field]), float(parent[field]), rel_tol=0, abs_tol=1e-9)
                           for field in ("qty", "leavesQty", "cumExecQty", "price"))):
                raise ValueError("open_order_history_incoherent")
        for old, update, parent in confirmations:
            if old.lane != item.lane or update["confirmed_filled_qty"] <= old.confirmed_filled_qty:
                continue
            closed_receipt = tracking.get_meta(adapter.conn,
                                               ("main_entry_receipt:" if item.lane == "main" else "swing_entry_receipt:")
                                               + old.order_link_id, mode) or {}
            if (item.qty == 0 and closed_receipt.get("closed") is True
                    and closed_receipt.get("order_id") == parent["orderId"]
                    and closed_receipt.get("qty") == update["confirmed_filled_qty"]
                    and update["state"] in ("FILLED", "CANCELLED_CONFIRMED")):
                continue
            if item.qty < update["confirmed_filled_qty"] - old.confirmed_filled_qty:
                raise ValueError("filled_exposure_not_reconciled")
            if item.lane == "main":
                receipt = (pending or {}).get("fill_receipt") or {}
                native = tracking.get_meta(adapter.conn, "native_entry_intent", mode) or {}
                owned = receipt.get("order_id") == parent["orderId"] or (
                    native.get("link_id") == old.order_link_id and native.get("order_id") == parent["orderId"]
                    and bool(native.get("position_ids")))
            else:
                receipt = tracking.get_meta(adapter.conn, "swing_entry_receipt:" + old.order_link_id, mode) or {}
                owned = receipt.get("order_id") == parent["orderId"] and bool(receipt.get("position_id"))
            if not owned:
                raise ValueError("fill_receipt_unconfirmed")
    if time.monotonic() - started > 10:
        raise ValueError("snapshot_too_old")
    for _, update, _ in confirmations:
        store.update(**update)
    return capital, tuple(result)


def _pages(session, method, identity, **params):
    rows, cursors, cursor = {}, set(), None
    for _ in range(20):
        result = _call(session, method, category="linear", symbol="BTCUSDT", limit=50,
                       **params, **({"cursor": cursor} if cursor else {}))["result"]
        if not isinstance(result.get("list"), list):
            raise ValueError("history_unknown")
        for row in result["list"]:
            key = row[identity]
            if not isinstance(key, str) or not key or (key in rows and rows[key] != row):
                raise ValueError("history_identity_conflict")
            rows[key] = row
        cursor = result.get("nextPageCursor")
        if not cursor:
            return tuple(rows.values())
        if not isinstance(cursor, str) or cursor in cursors:
            raise ValueError("history_cursor_conflict")
        cursors.add(cursor)
    raise ValueError("history_incomplete")


def reconcile_reservations(store, sessions, *, apply=True, only_link=None):
    confirmations = []
    for reservation in store.active():
        if only_link is not None and reservation.order_link_id != only_link:
            continue
        session = sessions[reservation.lane]
        parents = _pages(session, "get_order_history", "orderId", orderLinkId=reservation.order_link_id)
        if len(parents) != 1:
            raise ValueError("reservation_parent_unknown")
        parent = parents[0]
        if (parent.get("orderLinkId") != reservation.order_link_id
                or (reservation.order_id and parent["orderId"] != reservation.order_id)
                or parent.get("symbol") != "BTCUSDT" or parent.get("positionIdx") != 0
                or parent.get("side") != ("Buy" if reservation.side == "long" else "Sell")
                or parent.get("reduceOnly") is not False or parent.get("orderType") != "Limit"
                or not math.isclose(_positive(parent["price"]),
                                    reservation.price_bound if reservation.side == "long" else reservation.min_fill_price,
                                    rel_tol=0, abs_tol=1e-8)
                or not math.isclose(_positive(parent["qty"]), reservation.requested_qty, rel_tol=0, abs_tol=1e-9)):
            raise ValueError("reservation_parent_conflict")
        cumulative = _nonnegative(parent["cumExecQty"])
        leaves = _nonnegative(parent["leavesQty"])
        if (not all(math.isfinite(v) for v in (cumulative, leaves))
                or not 0 <= cumulative <= reservation.requested_qty or leaves < 0):
            raise ValueError("reservation_quantity_conflict")
        executions = _pages(session, "get_executions", "execId", orderId=parent["orderId"])
        filled = 0.
        for execution in executions:
            if (execution.get("orderId") != parent["orderId"]
                    or execution.get("orderLinkId") not in (None, "", reservation.order_link_id)
                    or execution.get("symbol") != "BTCUSDT" or execution.get("side") != parent["side"]
                    or execution.get("execType") != "Trade"):
                raise ValueError("reservation_execution_conflict")
            filled += _positive(execution["execQty"])
            _positive(execution["execPrice"])
        if not math.isclose(filled, cumulative, rel_tol=0, abs_tol=1e-9):
            raise ValueError("reservation_execution_incomplete")
        status = parent.get("orderStatus")
        terminal = status in ("Filled", "Cancelled", "Rejected", "PartiallyFilledCanceled")
        if terminal:
            if leaves != 0 or (status == "Filled" and not math.isclose(filled, reservation.requested_qty, rel_tol=0, abs_tol=1e-9)):
                raise ValueError("reservation_terminal_conflict")
            state = "FILLED" if status == "Filled" else "CANCELLED_CONFIRMED"
        else:
            if status not in ("New", "PartiallyFilled") or not math.isclose(
                    cumulative + leaves, reservation.requested_qty, rel_tol=0, abs_tol=1e-9):
                raise ValueError("reservation_remaining_conflict")
            state = "PARTIAL" if filled else "ACK"
        update = dict(intent_id=reservation.intent_id, order_link_id=reservation.order_link_id,
                      state=state, confirmed_filled_qty=filled, order_id=parent["orderId"],
                      evidence_ref=":".join((parent["orderId"], status, str(filled), str(leaves))))
        confirmations.append((reservation, update, parent))
    if apply:
        for _, update, _ in confirmations:
            store.update(**update)
    return tuple(confirmations)


def entry_receipt_update(adapter, lane, pending, qty):
    """Join an ALREADY execution-proven receipt to its funded reservation.

    Only receipt writers may call this: main after _entry_fill_confirmed, swing
    after complete exact-parent/execution proof. This is not an order authorizer
    or a substitute for broker evidence. Requiring a second network query here
    would leave a crash window after the first authoritative proof. Full exact
    fills exhaust requested quantity; partial receipts keep every unfilled unit
    reserved until separately confirmed terminal history. No network/UID/equity
    fallback is involved and risk-flag changes do not orphan existing receipts.
    """
    if not adapter.conn.execute("SELECT 1 FROM sqlite_master WHERE name='entry_reservations'").fetchone():
        return None
    store = EntryReservationStore(database_path(adapter.conn))
    rows = [r for r in store.active() if r.order_link_id == pending.get("link_id")]
    if not rows:
        return None
    if len(rows) != 1:
        raise ValueError("receipt_reservation_identity_conflict")
    old = rows[0]
    qty = _positive(qty)
    if math.isclose(qty, old.requested_qty, rel_tol=0, abs_tol=1e-9):
        qty = old.requested_qty
    elif math.isclose(qty, old.confirmed_filled_qty, rel_tol=0, abs_tol=1e-9):
        qty = old.confirmed_filled_qty
    order_id = pending.get("order_id")
    if (old.lane != lane or not order_id or (old.order_id and old.order_id != order_id)
            or not old.confirmed_filled_qty <= qty <= old.requested_qty):
        raise ValueError("receipt_reservation_quantity_conflict")
    state = "FILLED" if qty == old.requested_qty else "PARTIAL"
    update = dict(intent_id=old.intent_id, order_link_id=old.order_link_id,
                  state=state, confirmed_filled_qty=qty, order_id=order_id,
                  evidence_ref=f"local-execution-receipt:{old.order_link_id}:{order_id}:{qty}")
    return store, update


def commit_entry_receipt(update, conn):
    if update is not None:
        store, fields = update
        store.update(**fields, connection=conn)


def prepare(adapter, lane, side, qty, price, stop, context):
    """Called only under mutation_lock, before persistence and broker submission.

    Existing unresolved reservations fail closed, including absence from open
    orders. They are not released by age or guessed from a flat position.
    """
    config = configuration()
    if config is None:
        return None
    if getattr(adapter, "mode", "demo") not in ("demo",):
        raise ValueError("shared_entry_demo_only")
    if not isinstance(context, dict) or not context.get("decision_bar"):
        raise ValueError("deterministic_decision_identity_required")
    store = EntryReservationStore(database_path(adapter.conn))
    capital, lanes = snapshot(adapter, lane, config, store)
    active = store.active()
    existing = next(item for item in lanes if item.lane == lane)
    if ((existing.qty and existing.side != side)
            or any(r.lane == lane and r.side != side for r in active)):
        raise ValueError("same_lane_reversal_not_supported")
    if any(r.lane == lane for r in active):
        raise ValueError("same_lane_entry_already_pending")
    known = {r.order_link_id for r in active}
    for item in lanes:
        if any(o.get("reduceOnly") is not True and o.get("orderLinkId") not in known for o in item.orders):
            raise ValueError("unreconciled_or_manual_entry_order")
    mark = next(item.mark for item in lanes if item.lane == lane)
    # Main remains PostOnly at its original limit, unless outside adverse cap.
    # Swing uses IOC Limit; a SELL floor is not an upper fill-price guarantee.
    adverse = mark * (1 + config.slippage if side == "long" else 1 - config.slippage)
    if lane == "main":
        if (side == "long" and price > adverse) or (side == "short" and price < adverse):
            raise ValueError("entry_outside_slippage_bound")
        adverse = price
    else:
        from decimal import Decimal, ROUND_FLOOR, ROUND_CEILING
        adverse = float(Decimal(str(adverse)).quantize(Decimal(".1"), rounding=
                         ROUND_FLOOR if side == "long" else ROUND_CEILING))
    sign = 1 if side == "long" else -1
    if sign * (adverse-stop) <= 0:
        raise ValueError("invalid_adverse_stop")
    gross = {item.lane: item.qty * item.mark for item in lanes}
    heat = {item.lane: max(0., (1 if item.side == "long" else -1) * (item.entry-item.stop)) * item.qty
            for item in lanes}
    for reservation in active:
        pending_mark = next(item.mark for item in lanes if item.lane == reservation.lane)
        gross[reservation.lane] += reservation.remaining_qty * pending_mark
        adverse_price = reservation.price_bound if reservation.side == "long" else reservation.min_fill_price
        heat[reservation.lane] += reservation.remaining_qty * max(
            0., (1 if reservation.side == "long" else -1) * (adverse_price-reservation.stop))
    from core.risk import compute_operating_risk
    from live.shadow import SHADOW_BASE_RISK, SHADOW_DD_THRESHOLD, SHADOW_REDUCED_RISK
    from engine.config import SWING_RISK_PER_TRADE
    from core.leadership import leadership_multipliers
    peak = tracking.peak_equity(adapter.conn, "demo")
    if peak is None:
        peak = capital
    main_budget = compute_operating_risk(capital, _positive(peak), base_risk=SHADOW_BASE_RISK,
                                        dd_threshold=SHADOW_DD_THRESHOLD, reduced_risk=SHADOW_REDUCED_RISK)
    long_mult, short_mult, _ = leadership_multipliers()
    lane_sides = {item.lane: item.side or side for item in lanes}
    main_budget *= long_mult if lane_sides["main"] == "long" else short_mult
    swing_budget = SWING_RISK_PER_TRADE * (long_mult if lane_sides["swing"] == "long" else short_mult)
    if lane == "swing":
        # Existing sizing already spends its lane risk at the hint. Tighten the
        # IOC price rather than increasing risk, resizing, or rejecting every
        # normally-sized candidate merely for reserving optional slippage.
        remaining_heat = min(capital * swing_budget - heat["swing"],
                             capital * config.heat - sum(heat.values()))
        if remaining_heat <= 0:
            raise ValueError("shared_risk_budget_exceeded")
        risk_price = stop + sign * remaining_heat / qty
        adverse = min(adverse, risk_price) if side == "long" else max(adverse, risk_price)
        adverse = float(Decimal(str(adverse)).quantize(Decimal(".1"), rounding=
                         ROUND_FLOOR if side == "long" else ROUND_CEILING))
        if sign * (adverse-stop) <= 0:
            raise ValueError("shared_risk_budget_exceeded")
    gross[lane] += qty * mark
    heat[lane] += qty * sign * (adverse-stop)
    if (sum(gross.values()) > capital * 8 or gross["swing"] > capital * 5
            or sum(heat.values()) > capital * config.heat
            or heat["main"] > capital * main_budget or heat["swing"] > capital * swing_budget):
        raise ValueError("shared_risk_budget_exceeded")
    identity = ":".join((lane, str(context["decision_bar"]), side,
                         str(context.get("lifecycle", "initial")), str(context.get("tranche_index", 0))))
    intent_id = hashlib.sha256(identity.encode()).hexdigest()
    existing = store.get(intent_id)
    if existing:
        raise ValueError("duplicate_shared_intent")
    import uuid
    link = ("entry-" if lane == "main" else "sw-entry-") + uuid.uuid4().hex[:24]
    # Reservation price_bound is storage of snapshot gross valuation only here;
    # do NOT pass it to the strict worst-case portfolio arithmetic helper.
    proposed = ProposedEntry(lane, side, qty, adverse if side == "long" else max(mark, adverse), stop,
                             adverse if side == "short" else None)
    expected_pending = tuple(row.pending_entry() for row in active)
    result = store.reserve(intent_id=intent_id, order_link_id=link, proposed=proposed,
                           eligibility=lambda current_pending: current_pending == expected_pending)
    if not result.created:
        raise ValueError("duplicate_shared_intent")
    store.update(intent_id=intent_id, order_link_id=link, state="SUBMITTED_UNKNOWN", confirmed_filled_qty=0.)
    return {"intent_id": intent_id, "link_id": link, "limit_price": adverse}


def authorize(adapter, lane, side, qty, price, stop, context):
    try:
        return True, prepare(adapter, lane, side, qty, price, stop, context)
    except Exception as exc:
        tracking.log_event(adapter.conn, "entry_blocked", "shared_entry:" + type(exc).__name__ + ":" + str(exc),
                           level="warning", mode=getattr(adapter, "mode", "swing"))
        return False, None
