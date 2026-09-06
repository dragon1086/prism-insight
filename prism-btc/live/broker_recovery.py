"""Exchange-only recovery: no market candles, indicators, entries or cursors.

Reuse the adapters' authoritative snapshot and exact execution settlement
paths. An unknown snapshot must raise, never be interpreted as a flat account.
"""
from live import tracking
from live.order_retirement import reconcile_stop_retirements
from live.shared_entry_coordinator import mutation_lock


class RecoveryPending(RuntimeError):
    """Controlled operator-facing reason; never contains a broker payload."""


def _recover_closed_main_entry(adapter, pending, now):
    from live.closed_entry_recovery import closed_entry_proof
    from live.shadow import bar_index_for
    import math
    import pandas as pd

    if (tracking.load_open_positions(adapter.conn, adapter.mode)
            or int(pending.get("tranche_index", -1)) != 0):
        return False
    proof = closed_entry_proof(adapter._call, pending, adapter._entry_parent_matches,
                               now_ms=int(now.value // 1_000_000))
    if proof is None:
        return False
    # History pagination may take time. Reconfirm flat before the atomic receipt;
    # the shared lock blocks our entries, not external/manual account activity.
    current = adapter._sync_state(str(now))
    if (current["position"] is not None
            or any(order.get("reduce_only") is not True for order in current["open_orders"])
            or tracking.load_open_positions(adapter.conn, adapter.mode)):
        return False
    risk = float(pending["initial_risk"])
    if not math.isfinite(risk) or risk <= 0:
        return False
    ids = adapter._get_meta("closed_before_adoption_execution_ids_v1", [])
    if not isinstance(ids, list) or any(not isinstance(i, str) or not i for i in ids):
        return False
    key = "main_entry_receipt:" + pending["link_id"]
    if adapter._get_meta(key, None):
        return False  # A contradictory pending after atomic receipt needs review.
    if set(ids).intersection(proof["execution_ids"]):
        return False
    counter = int(adapter._get_meta("trade_id_counter", 0))
    exit_time = str(pd.to_datetime(proof["exit_time_ms"], unit="ms", utc=True))
    trade = tracking.TradeRow(
        trade_id=counter, side=pending["side"],
        entry_time=str(pd.to_datetime(proof["entry_time_ms"], unit="ms", utc=True)),
        entry_price=proof["entry_price"], exit_time=exit_time, exit_price=proof["exit_price"],
        qty=proof["qty"], leverage=float(pending["sizing_leverage"]),
        sl_price=float(pending["sizing_sl_price"]), exit_reason="native_sl_before_adoption",
        r_multiple=proof["net_pnl"] / risk, fee_paid=proof["open_fee"] + proof["close_fee"],
        funding_paid=proof["funding_paid"], tranche_index=0,
        liq_price=float(pending["sizing_liq_price"]), net_pnl=proof["net_pnl"],
        gross_pnl=proof["gross_pnl"], gross_r_multiple=proof["gross_pnl"] / risk,
        num_legs=1, mode=adapter.mode,
    )
    close_bars = adapter._get_meta("last_close_bar", {"long": -10_000, "short": -10_000})
    close_bars[pending["side"]] = bar_index_for(proof["exit_time_ms"])
    was_sl = adapter._get_meta("last_close_was_sl", {"long": False, "short": False})
    was_sl[pending["side"]] = True
    receipt = {**proof, "order_id": pending["order_id"], "link_id": pending["link_id"],
               "closed": True, "trade_id": counter, "status": "CLOSED_BEFORE_ADOPTION"}
    from live.shared_entry_coordinator import entry_receipt_update, commit_entry_receipt
    reservation_update = entry_receipt_update(adapter, "main", pending, proof["qty"])
    with adapter.conn:
        tracking.record_trade(adapter.conn, trade, commit=False)
        for meta_key, value in {
            key: receipt, "pending_order": None, "trade_id_counter": counter + 1,
            "closed_before_adoption_execution_ids_v1": sorted(set(ids) | set(proof["execution_ids"])),
            "last_close_bar": close_bars, "last_close_was_sl": was_sl,
            "last_entry_resolution": receipt,
        }.items():
            tracking.set_meta(adapter.conn, meta_key, value, adapter.mode, commit=False)
        commit_entry_receipt(reservation_update, adapter.conn)
    return True


def _recover_main_entry(adapter, pending, position, open_orders, now):
    """Resolve already-submitted entries without running a strategy bar.

    Expiry uses actual submission/ACK time, not an invented market candle. Exact
    terminal execution proof is still required before releasing an entry fence.
    """
    if not pending:
        return True
    if adapter._entry_receipt_recorded(pending):
        if not adapter._complete_native_postfill(pending, position):
            return False
        adapter._set_meta("pending_order", None)
        return True
    if adapter._entry_terminal_zero(pending):
        adapter._set_meta("last_entry_resolution", {
            "status": "TERMINAL_ZERO_FILL", "order_id": pending.get("order_id"),
        })
        adapter._set_meta("pending_order", None)
        return True
    if position is None and pending.get("link_id") and _recover_closed_main_entry(adapter, pending, now):
        return True
    active = any(
        (pending.get("order_id") and row.get("order_id") == pending["order_id"])
        or (pending.get("link_id") and row.get("order_link_id") == pending["link_id"])
        for row in open_orders
    )
    if position and position["side"] == pending["side"] and not active and pending.get("link_id"):
        local = tracking.load_open_positions(adapter.conn, adapter.mode)
        previous = sum(p.qty for p in local if p.side == pending["side"])
        delta = position["qty"] - previous
        if delta > 0 and adapter._entry_fill_confirmed(pending, delta):
            from live.shadow import bar_index_for
            # This records the actual confirmation time, not a fabricated OHLC.
            pos = tracking.PositionRow(
                side=position["side"], entry_price=position["entry_price"], qty=delta,
                leverage=position["leverage"], sl_price=float(pending["sizing_sl_price"]),
                tp1_price=float(pending["sizing_tp1_price"]),
                tp2_price=float(pending["sizing_tp2_price"]),
                tp3_price=float(pending["sizing_tp3_price"]),
                liq_price=position["liq_price"] or float(pending["sizing_liq_price"]),
                entry_time=str(now), tranche_index=int(pending["tranche_index"]),
                entry_bar_idx=bar_index_for(int(now.value // 1_000_000)),
                initial_risk=float(pending["initial_risk"]), initial_qty=delta, mode=adapter.mode,
            )
            adapter._save_native_entry_position(pending, pos, position["qty"])
            if adapter._complete_native_postfill(pending, position):
                adapter._set_meta("pending_order", None)
                return True
            return False
    from backtest.engine import ENTRY_ORDER_EXPIRY_BARS
    submitted = int(pending.get("submitted_at_ms") or
                    int(pending.get("latency_ack_wall_ns") or 0) // 1_000_000)
    if not submitted and pending.get("link_id"):
        rows = adapter._reduction_pages("get_order_history", pending)
        matches = [row for row in rows or [] if adapter._entry_parent_matches(row, pending)]
        times = {int(row.get("createdTime") or 0) for row in matches}
        if len(times) == 1:
            submitted = times.pop()
    age = int(now.value // 1_000_000) - submitted
    if pending.get("cancel_requested") or (submitted > 0 and age >= ENTRY_ORDER_EXPIRY_BARS * 30 * 60_000):
        adapter._request_entry_cancel(pending)
    return False


def reconcile_main(adapter, now):
    with mutation_lock(adapter.conn):
        return _reconcile_main(adapter, now)


def _reconcile_main(adapter, now):
    if adapter.sess is None:
        raise RecoveryPending("demo broker session unavailable")
    retired = reconcile_stop_retirements(adapter.conn, adapter.mode, adapter._call)
    snap = adapter._sync_state(str(now))
    position = snap["position"]
    pending = adapter._get_meta("pending_order", None)
    adapter._recover_entry_identity(pending, snap["open_orders"])
    local = tracking.load_open_positions(adapter.conn, adapter.mode)
    # Native-entry and TP children may fill between any two market ticks.
    adapter._protect_tp_exposure(position, local)
    adapter._protect_pending_entry(pending, position, local)
    if position:
        stops = [p.sl_price for p in local if p.side == position["side"]]
        if pending and pending.get("side") == position["side"]:
            stops.append(float(pending["sizing_sl_price"]))
        if stops:
            stop = max(stops) if position["side"] == "long" else min(stops)
            if not adapter._ensure_stop(position["side"], position["qty"], stop).confirmed:
                raise RecoveryPending("demo broker stop not confirmed")
        else:
            raise RecoveryPending("demo exposure has no owned stop intent; intervention required")
    adapter._resume_reduce()
    entry_ready = _recover_main_entry(adapter, pending, position, snap["open_orders"], now)
    settled = adapter._settle_tp(position)
    # Reporting is deliberately after protection; no strategy/cursor advancement.
    if not entry_ready:
        raise RecoveryPending("demo entry recovery pending")
    adapter._record_closed_trades(str(now))
    if not settled:
        raise RecoveryPending("demo TP settlement pending")
    if not retired:
        retired = reconcile_stop_retirements(adapter.conn, adapter.mode, adapter._call)
    if not retired:
        raise RecoveryPending("demo stop retirement pending")


def reconcile_swing(conn, main_mode, now, backend=None):
    with mutation_lock(conn):
        return _reconcile_swing(conn, main_mode, now, backend)


def _reconcile_swing(conn, main_mode, now, backend=None):
    from live import swing

    positions = tracking.load_open_positions(conn, swing.MODE)
    pending = tracking.get_meta(conn, "swing_entry_pending", swing.MODE)
    owned = any(tracking.get_meta(conn, key, swing.MODE) for key in (
        "swing_native_entry", "swing_stop_intent", "swing_sl_order_id", "swing_close_pending",
        "stop_retirements_v1",
    ))
    if not positions and not pending and not owned:
        return
    if backend is None:
        sess, _ = swing._make_swing_session()
        if sess is None:
            if not pending and not owned:
                # Legacy virtual lane has no broker-owned order/entry metadata.
                return
            raise RecoveryPending("swing broker unavailable; preserve exposure")
        backend = swing.ExchangeBackend(conn, sess)
    if backend.name != "exchange":
        raise RecoveryPending("broker recovery requires an exchange backend")
    retired = reconcile_stop_retirements(conn, swing.MODE, backend._call)
    try:
        recovered = backend.recover_pending_entry()
    except Exception:  # protection of existing rows must survive pending recovery errors
        recovered = False
    for pos in tracking.load_open_positions(conn, swing.MODE):
        # ExchangeBackend.check_stop ignores bar; None prevents accidental
        # fallback to virtual OHLC-based fills if its implementation changes.
        fill = backend.check_stop(pos, None)
        if not backend.last_protection_confirmed:
            raise RecoveryPending("swing protection or settlement unconfirmed")
        if fill is not None:
            equity = tracking.latest_equity(conn, swing.MODE)
            if equity is None:
                raise RecoveryPending("swing settlement has no recorded equity")
            counter = int(tracking.get_meta(conn, "trade_id_counter", swing.MODE) or 0)
            _, counter = swing._close_position(
                conn, backend, pos, fill, swing.TAKER_FEE, "swing_sl", str(now),
                equity, counter, main_mode,
            )
            tracking.set_meta(conn, "trade_id_counter", counter, swing.MODE)
    if not recovered:
        raise RecoveryPending("swing entry recovery pending")
    if not retired:
        raise RecoveryPending("swing stop retirement pending")
