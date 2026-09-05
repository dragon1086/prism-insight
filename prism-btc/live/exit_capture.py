"""Fail-open exit snapshots to the existing ClickStack JSONL shipper.

Snapshots are observations, never order ACK/fill confirmations. No credentials,
exchange response bodies or raw order IDs are accepted by this interface.
"""
from __future__ import annotations

import hashlib
import logging
import math
import sys
from pathlib import Path

from live import tracking

log = logging.getLogger(__name__)


def _number(value):
    try:
        number = float(value)
        return number if math.isfinite(number) else None
    except (TypeError, ValueError):
        return None


def capture(conn, *, mode, timestamp, mark_price, stage, pending=None, price_bar_time=None):
    """Capture every local position; failure cannot change trading decisions."""
    try:
        root = str(Path(__file__).resolve().parents[2])
        if root not in sys.path:
            sys.path.append(root)
        from observability.events import emit_event
        from live import demo

        equity = tracking.latest_equity(conn, mode)
        if pending is None:
            pending = bool(tracking.get_meta(conn, "pending_order", mode))
        logical = (tracking.get_meta(conn, "swing_entry_logical_capital", mode)
                   if mode == "swing" else equity)
        for pos in tracking.load_open_positions(conn, mode):
            identity = f"{mode}:{pos.id}:{pos.entry_time}:{pos.tranche_index}"
            position_id = hashlib.sha256(identity.encode()).hexdigest()[:32]
            event_id = hashlib.sha256(
                f"{position_id}:{timestamp}:{stage}:v1".encode()).hexdigest()[:32]
            mark = _number(mark_price)
            qty, entry = _number(pos.qty), _number(pos.entry_price)
            capital = _number(logical)
            sign = 1 if pos.side == "long" else -1
            gross = sign * qty * (mark-entry) if None not in (qty, mark, entry) else None
            attrs = {
                "exit_schema_version": 1, "mode": mode, "stage": stage,
                "source": "local_ledger", "execution_status": "NOT_CONFIRMED",
                "strategy": "swing_ma35_v1" if mode == "swing" else "main_ma_trail_v1",
                "side": pos.side, "quantity": qty, "entry_price": entry,
                "mark_price": mark, "stop_price": _number(pos.sl_price),
                "observed_at": timestamp, "price_bar_time": price_bar_time,
                "price_source": "protection_bar_close_not_live_mark",
                "initial_risk": _number(pos.initial_risk),
                "logical_capital": capital,
                "logical_capital_source": "entry_snapshot" if mode == "swing" else "latest_ledger",
                "gross_pnl_estimate": gross,
                "gross_return_on_capital": gross/capital if gross is not None and capital and capital>0 else None,
                "effective_exposure": qty*mark/capital if None not in (qty,mark) and capital and capital>0 else None,
                "configured_exchange_leverage": None,
                "net_pnl": None, "net_pnl_status": "MISSING_FEES_AND_FUNDING",
                "trailing_active": bool(pos.trailing_active),
                "breakeven_active": bool(pos.be_stop_set), "pending_entry": pending,
                "trail_timeframe": None if mode == "swing" else demo.TRAILING_TF,
                "activation_r": None if mode == "swing" else demo.BE_TRAIL_ACTIVATE_R,
                "initial_risk_r": gross/pos.initial_risk if gross is not None and pos.initial_risk>0 else None,
                "exchange_stop_confirmed": None,
            }
            if emit_event("btc.exit.snapshot", service="prism-btc", market="CRYPTO",
                          ticker="BTCUSDT", event_id=event_id, trace_id=position_id,
                          position_id=position_id, attributes=attrs) is None:
                log.warning("btc exit capture spool append failed")
    except Exception:  # Observability is never an execution dependency.
        log.warning("btc exit capture unavailable", exc_info=False)
