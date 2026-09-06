"""Durably retire owned backup stops only while flat, with terminal readback.

This is cancellation bookkeeping, never execution/PnL evidence. A missing order,
cancel ACK, API timeout, or local handle removal does not release the fence.
Native child orders are not adopted or cancelled by symbol or price matching.
"""
from __future__ import annotations

import math

from live import tracking
from live.exchange_snapshot import read_complete

_KEY = "stop_retirements_v1"
_TERMINAL = {"Cancelled", "Filled", "Rejected", "Deactivated", "PartiallyFilledCanceled"}


def _queue(conn, mode):
    value = tracking.get_meta(conn, _KEY, mode)
    if value is None:
        return []
    if (not isinstance(value, list) or len(value) > 64
            or any(not isinstance(x, str) or not x or x != x.strip() for x in value)
            or len(set(value)) != len(value)):
        raise ValueError("invalid_stop_retirement_queue")
    return value


def _change_queue(conn, mode, order_id, *, remove=False, terminal_row=None):
    # Do not commit a caller's unrelated transaction or submit before durability.
    if conn.in_transaction:
        raise ValueError("stop_retirement_requires_transaction_boundary")
    conn.execute("BEGIN IMMEDIATE")
    try:
        values = _queue(conn, mode)
        if remove:
            values = [x for x in values if x != order_id]
        elif order_id not in values:
            if len(values) >= 64:
                raise ValueError("stop_retirement_queue_full")
            values.append(order_id)
        tracking.set_meta(conn, _KEY, values, mode, commit=False)
        if remove and terminal_row is not None:
            intent_key = "swing_stop_intent" if mode == "swing" else "stop_submission_intent"
            intent = tracking.get_meta(conn, intent_key, mode)
            if (isinstance(intent, dict) and intent.get("link_id")
                    and intent["link_id"] == terminal_row.get("orderLinkId")
                    and intent.get("order_id") in (None, order_id)):
                tracking.set_meta(conn, intent_key, {**intent, "status": "SETTLED",
                                  "order_id": order_id}, mode, commit=False)
                if mode != "swing" and tracking.get_meta(conn, "stop_creation_link", mode) == intent["link_id"]:
                    tracking.set_meta(conn, "stop_creation_link", None, mode, commit=False)
            handle_key = "swing_sl_order_id" if mode == "swing" else "sl_order_id"
            if tracking.get_meta(conn, handle_key, mode) == order_id:
                tracking.set_meta(conn, handle_key, "" if mode == "swing" else None,
                                  mode, commit=False)
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def _history(call, order_id=None, *, order_link_id=None):
    cursor = None
    seen = set()
    matched = None
    for _ in range(20):
        identity = {"orderId": order_id} if order_id else {"orderLinkId": order_link_id}
        reply = call("get_order_history", category="linear", symbol="BTCUSDT",
                     **identity, limit=50, **({"cursor": cursor} if cursor else {}))
        if not isinstance(reply, dict) or reply.get("retCode") != 0:
            return None
        result = reply.get("result")
        if not isinstance(result, dict) or not isinstance(result.get("list"), list):
            return None
        for row in result["list"]:
            if not isinstance(row, dict):
                return None
            if ((row.get("orderId") == order_id if order_id else
                 row.get("orderLinkId") == order_link_id)):
                if matched is not None and matched != row:
                    return None
                matched = row
        cursor = result.get("nextPageCursor")
        if not cursor:
            break
        if not isinstance(cursor, str) or cursor in seen:
            return None
        seen.add(cursor)
    else:
        return None
    if not matched:
        return None
    try:
        trigger = float(matched["triggerPrice"])
        valid = (isinstance(matched.get("orderId"), str) and bool(matched["orderId"])
                 and matched.get("symbol") == "BTCUSDT" and matched.get("positionIdx") == 0
                 and matched.get("reduceOnly") is True
                 and matched.get("side") in {"Buy", "Sell"}
                 and matched.get("orderType") == "Market"
                 and math.isfinite(trigger) and trigger > 0)
    except (KeyError, TypeError, ValueError):
        return None
    return matched if valid else None


def _safe_to_cancel(conn, mode, call):
    pending_key = "swing_entry_pending" if mode == "swing" else "pending_order"
    if tracking.get_meta(conn, pending_key, mode):
        return False
    orders = read_complete(call, "get_open_orders", category="linear", symbol="BTCUSDT")
    if orders is None:
        return False
    for row in orders["result"]["list"]:
        if (row.get("symbol") != "BTCUSDT" or row.get("positionIdx") != 0
                or row.get("reduceOnly") is not True):
            return False
    # Read positions AFTER excluding entry remainders so a just-completed
    # entry is not mistaken for the flat observation from before its fill.
    reply = read_complete(call, "get_positions", category="linear", symbol="BTCUSDT")
    if reply is None:
        return False
    for row in reply["result"]["list"]:
        if (row.get("symbol") != "BTCUSDT" or row.get("positionIdx") != 0
                or isinstance(row.get("size"), bool)):
            return False
        try:
            size = float(row["size"])
        except (KeyError, TypeError, ValueError):
            return False
        if not math.isfinite(size) or size != 0:
            return False
    return True


def reconcile_stop_retirements(conn, mode, call) -> bool:
    """True iff every queued owned stop has exact terminal evidence.

    Cancellation can be retried by exact ID, but only after a fresh flat read.
    Unresolved cleanup blocks new entries; it must never block protection of
    exposure that already exists. Network calls run outside SQLite transactions.
    """
    try:
        values = _queue(conn, mode)
        if conn.in_transaction:
            return False
        intent_key = "swing_stop_intent" if mode == "swing" else "stop_submission_intent"
        intent = tracking.get_meta(conn, intent_key, mode)
        if (isinstance(intent, dict) and intent.get("submitted") and intent.get("link_id")
                and intent.get("status") not in {"CONFIRMED", "CONFIRMED_OWNED", "SETTLED"}
                and _safe_to_cancel(conn, mode, call)):
            # Recover a lost ACK by the persisted exact submission link, never
            # by side/price similarity. Absence is not cancellation evidence.
            recovered = _history(call, order_link_id=intent["link_id"])
            if recovered is None or intent.get("order_id") not in (None, recovered["orderId"]):
                return False
            _change_queue(conn, mode, recovered["orderId"])
            values = _queue(conn, mode)
        for order_id in values:
            try:
                row = _history(call, order_id)
                if row is None:
                    continue
                if row.get("orderStatus") not in _TERMINAL:
                    if row.get("orderStatus") not in {"New", "Untriggered", "PartiallyFilled"}:
                        continue
                    if not _safe_to_cancel(conn, mode, call):
                        continue
                    try:
                        call("cancel_order", category="linear", symbol="BTCUSDT", orderId=order_id)
                    except Exception:
                        # A lost cancel ACK can still be settled by the readback.
                        pass
                    row = _history(call, order_id)
                if row is not None and row.get("orderStatus") in _TERMINAL:
                    _change_queue(conn, mode, order_id, remove=True, terminal_row=row)
            except Exception:
                # Preserve durable identity; never log raw broker error payloads.
                continue
        return not _queue(conn, mode)
    except Exception:
        return False


def request_stop_retirement(conn, mode, call, order_id: str) -> bool:
    """Persist an already-owned stop ID before any cancellation attempt."""
    if not isinstance(order_id, str) or not order_id or order_id != order_id.strip():
        return False
    try:
        _change_queue(conn, mode, order_id)
    except Exception:
        return False
    return reconcile_stop_retirements(conn, mode, call)
