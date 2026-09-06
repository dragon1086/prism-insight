"""Exact swing entry recovery. An ACK or current position never proves a fill.

Only a terminal exact parent plus complete execution history may create a local
receipt. Partial still-working parents retain the pending intent; known matching
exposure may be protected, but no additional entry is authorized.
"""
from __future__ import annotations

import math
import time
import pandas as pd

from live import tracking
from live.exchange_snapshot import read_complete

MODE = "swing"


def recover(backend) -> bool:
    pending = tracking.get_meta(backend.conn, "swing_entry_pending", MODE)
    if not pending:
        return True
    try:
        return _recover(backend, pending)
    except (KeyError, TypeError, ValueError, OverflowError):
        return False


def _recover(backend, pending):
    query = ({"orderId": pending["order_id"]} if pending.get("order_id")
             else {"orderLinkId": pending["link_id"]})
    parents = backend._complete_identity_rows("get_order_history", "orderId", **query)
    if not parents or len(parents) != 1:
        return False
    parent = parents[0]
    requested = float(pending["qty"])
    cumulative = float(parent["cumExecQty"])
    leaves = float(parent["leavesQty"])
    if (parent.get("orderLinkId") != pending["link_id"]
            or (pending.get("order_id") and parent["orderId"] != pending["order_id"])
            or parent.get("symbol") != "BTCUSDT" or parent.get("positionIdx") != 0
            or parent.get("side") != ("Buy" if pending["side"] == "long" else "Sell")
            or parent.get("reduceOnly") is not False
            or parent.get("orderType") != pending.get("order_type", "Market")
            or not math.isclose(float(parent["qty"]), requested, rel_tol=0, abs_tol=1e-9)
            or not all(math.isfinite(x) for x in (requested, cumulative, leaves))
            or requested <= 0 or not 0 <= cumulative <= requested or leaves < 0):
        return False
    if pending.get("order_type") == "Limit" and not math.isclose(
            float(parent["price"]), float(pending["limit_price"]), rel_tol=0, abs_tol=1e-8):
        return False
    status = parent.get("orderStatus")
    terminal = status in ("Filled", "Cancelled", "Rejected", "PartiallyFilledCanceled")
    if status == "Filled" and not math.isclose(cumulative, requested, rel_tol=0, abs_tol=1e-9):
        return False
    if terminal:
        if leaves != 0:
            return False
    elif status not in ("New", "PartiallyFilled") or not math.isclose(
            cumulative + leaves, requested, rel_tol=0, abs_tol=1e-9):
        return False
    pending = {**pending, "order_id": parent["orderId"]}
    tracking.set_meta(backend.conn, "swing_entry_pending", pending, MODE)
    executions = backend._complete_identity_rows("get_executions", "execId", orderId=parent["orderId"])
    response = read_complete(backend._call, "get_positions", category="linear", symbol="BTCUSDT")
    if response is None:
        return False
    positions = response["result"]["list"]
    if len(positions) > 1:
        return False
    actual = 0.0
    if positions:
        row = positions[0]
        actual = float(row["size"])
        if (row.get("symbol") != "BTCUSDT" or row.get("positionIdx") != 0
                or not math.isfinite(actual) or actual < 0
                or (actual > 0 and row.get("side") != parent["side"])):
            return False
    local = tracking.load_open_positions(backend.conn, MODE)
    if cumulative == 0:
        if not terminal or executions != [] or actual != 0 or local:
            return False
        with backend.conn:
            tracking.set_meta(backend.conn, "swing_entry_receipt:" + pending["link_id"],
                              {"order_id": parent["orderId"], "qty": 0, "terminal": status}, MODE, commit=False)
            tracking.set_meta(backend.conn, "swing_entry_pending", None, MODE, commit=False)
        return True
    proof = backend._entry_execution_snapshot(pending)
    if (not proof or "fee" not in proof or "execution_time_ms" not in proof
            or not math.isclose(proof["qty"], cumulative, rel_tol=0, abs_tol=1e-9)):
        return False
    if actual == 0 and terminal and not local:
        return _recover_closed(backend, pending, parent)
    if not math.isclose(actual, cumulative, rel_tol=0, abs_tol=1e-9):
        return False
    tracking.set_meta(backend.conn, "swing_native_entry", pending, MODE)
    protection = backend._ensure_entry_stop(pending["side"], actual, pending["stop_price"])
    if not protection.confirmed or not terminal:
        return False
    receipt_key = "swing_entry_receipt:" + pending["link_id"]
    receipt = tracking.get_meta(backend.conn, receipt_key, MODE)
    if local:
        if (len(local) != 1 or not receipt or receipt.get("position_id") != local[0].id
                or not math.isclose(local[0].qty, actual, rel_tol=0, abs_tol=1e-9)):
            return False
    else:
        if receipt:
            return False  # A closed receipt is never resurrected.
        context = pending.get("entry_context")
        if not isinstance(context, dict):
            return False
        fill, stop = proof["price"], pending["stop_price"]
        pos = tracking.PositionRow(
            side=pending["side"], entry_price=fill, qty=actual,
            leverage=context["leverage"], sl_price=stop,
            tp1_price=0., tp2_price=0., tp3_price=0., liq_price=0.,
            entry_time=str(pd.to_datetime(proof["execution_time_ms"], unit="ms", utc=True)),
            tranche_index=0, entry_bar_idx=context["entry_bar_idx"],
            initial_risk=abs(fill-stop)*actual, entry_fee=proof["fee"], initial_qty=actual, mode=MODE)
        from live.shared_entry_coordinator import entry_receipt_update, commit_entry_receipt
        reservation_update = entry_receipt_update(backend, "swing", pending, actual)
        with backend.conn:
            tracking.save_position(backend.conn, pos, commit=False)
            tracking.set_meta(backend.conn, receipt_key,
                              {"order_id": parent["orderId"], "position_id": pos.id, "qty": actual},
                              MODE, commit=False)
            for key in ("swing_entry_logical_capital", "swing_entry_account_equity"):
                tracking.set_meta(backend.conn, key, context["logical_capital"], MODE, commit=False)
            if tracking.get_meta(backend.conn, "swing_strategy_nav_v1", MODE) is None:
                tracking.set_meta(backend.conn, "swing_strategy_nav_v1", context["logical_capital"], MODE, commit=False)
            tracking.set_meta(backend.conn, "swing_entry_pending", None, MODE, commit=False)
            commit_entry_receipt(reservation_update, backend.conn)
        return True
    tracking.set_meta(backend.conn, "swing_entry_pending", None, MODE)
    return True


def _recover_closed(backend, pending, parent):
    """One exact native SL round trip, without ever creating an open row."""
    from live.closed_entry_recovery import closed_entry_proof

    keys = ("orderId", "orderLinkId", "symbol", "positionIdx", "side", "reduceOnly", "orderType", "qty")
    proof = closed_entry_proof(backend._call, pending,
                               lambda row, _: all(row.get(key) == parent.get(key) for key in keys),
                               now_ms=time.time_ns() // 1_000_000)
    context = pending.get("entry_context")
    if proof is None or not isinstance(context, dict):
        return False
    fresh = read_complete(backend._call, "get_positions", category="linear", symbol="BTCUSDT")
    orders = read_complete(backend._call, "get_open_orders", category="linear", symbol="BTCUSDT")
    if (fresh is None or orders is None or len(fresh["result"]["list"]) > 1
            or any(row.get("symbol") != "BTCUSDT" or row.get("positionIdx") != 0
                   or float(row["size"]) != 0 for row in fresh["result"]["list"])
            or any(row.get("reduceOnly") is not True for row in orders["result"]["list"])
            or tracking.load_open_positions(backend.conn, MODE)):
        return False
    receipt_key = "swing_entry_receipt:" + pending["link_id"]
    receipt = tracking.get_meta(backend.conn, receipt_key, MODE)
    if receipt:
        if not (receipt.get("closed") is True and receipt.get("order_id") == parent["orderId"]
                and receipt.get("child_id") == proof["child_id"] and receipt.get("qty") == proof["qty"]):
            return False
        tracking.set_meta(backend.conn, "swing_entry_pending", None, MODE)
        return True
    risk = abs(proof["entry_price"] - pending["stop_price"]) * proof["qty"]
    if not math.isfinite(risk) or risk <= 0:
        return False
    counter = int(tracking.get_meta(backend.conn, "trade_id_counter", MODE) or 0) + 1
    trade = tracking.TradeRow(
        trade_id=counter, side=pending["side"],
        entry_time=str(pd.to_datetime(proof["entry_time_ms"], unit="ms", utc=True)),
        entry_price=proof["entry_price"],
        exit_time=str(pd.to_datetime(proof["exit_time_ms"], unit="ms", utc=True)),
        exit_price=proof["exit_price"], qty=proof["qty"], leverage=context["leverage"],
        sl_price=pending["stop_price"], exit_reason="swing_native_sl_before_adoption",
        r_multiple=proof["net_pnl"] / risk, fee_paid=proof["open_fee"] + proof["close_fee"],
        funding_paid=proof["funding_paid"], tranche_index=0, liq_price=0.,
        net_pnl=proof["net_pnl"], gross_pnl=proof["gross_pnl"],
        gross_r_multiple=proof["gross_pnl"] / risk, mode=MODE)
    nav = tracking.get_meta(backend.conn, "swing_strategy_nav_v1", MODE)
    if nav is None:
        nav = context["logical_capital"]
    if not math.isfinite(float(nav)):
        return False
    from live.shared_entry_coordinator import entry_receipt_update, commit_entry_receipt
    reservation_update = entry_receipt_update(backend, "swing", pending, proof["qty"])
    with backend.conn:
        tracking.record_trade(backend.conn, trade, commit=False)
        tracking.set_meta(backend.conn, receipt_key,
                          {**proof, "order_id": parent["orderId"], "closed": True, "trade_id": counter},
                          MODE, commit=False)
        tracking.set_meta(backend.conn, "trade_id_counter", counter, MODE, commit=False)
        tracking.set_meta(backend.conn, "swing_strategy_nav_v1", float(nav) + proof["net_pnl"], MODE, commit=False)
        tracking.set_meta(backend.conn, "swing_entry_pending", None, MODE, commit=False)
        commit_entry_receipt(reservation_update, backend.conn)
    return True
