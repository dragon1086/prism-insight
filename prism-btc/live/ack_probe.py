"""Collect Bybit demo order acknowledgement latency without seeking fills.

The probe is deliberately narrower than the strategy runner:

* Bybit demo credentials/session only.
* BTCUSDT limit + PostOnly orders only; no market or reduce orders.
* Refuse to run when any BTC position or open order exists.
* Place 0.001 BTC five percent away from market, cancel immediately, then
  verify that both the position and open-order lists are empty.
* Stop after 36 clean cycles or 72 hours, whichever comes first.

One clean cycle yields separate ``probe_submit`` and ``probe_cancel``
``SUBMIT_TO_ACK`` rows in ``btc_execution_samples``.  It does not measure fill
latency and must never be used to change live execution automatically.
"""

from __future__ import annotations

import argparse
import json
import secrets
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Any

from live import tracking
from live.demo import (
    DemoAdapter,
    _CATEGORY,
    _POSITION_IDX,
    _SYMBOL,
    _f,
    _order_id,
    _pstr,
    _qstr,
    _result_list,
)

STATE_KEY = "ack_probe_v1"
DEFAULT_TARGET_CYCLES = 36
DEFAULT_DURATION_HOURS = 72
PROBE_DISTANCE_FRAC = 0.05
PROBE_QTY = 0.001


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat()


def _parse_iso(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


def _load_state(conn: sqlite3.Connection) -> dict[str, Any] | None:
    value = tracking.get_meta(conn, STATE_KEY, "demo")
    return value if isinstance(value, dict) else None


def _save_state(conn: sqlite3.Connection, state: dict[str, Any]) -> None:
    tracking.set_meta(conn, STATE_KEY, state, "demo")


def start_probe(
    conn: sqlite3.Connection,
    *,
    now: datetime | None = None,
    target_cycles: int = DEFAULT_TARGET_CYCLES,
    duration_hours: int = DEFAULT_DURATION_HOURS,
    restart: bool = False,
) -> dict[str, Any]:
    """Arm an idempotent demo probe window; this function sends no order."""
    current = _load_state(conn)
    if current and current.get("status") == "active" and not restart:
        return get_probe_status(conn, now=now)
    if target_cycles < 1 or duration_hours < 1:
        raise ValueError("target_cycles and duration_hours must be positive")
    started = now or _utcnow()
    state = {
        "version": 1,
        "status": "active",
        "started_at": _iso(started),
        "deadline_at": _iso(started + timedelta(hours=duration_hours)),
        "target_cycles": int(target_cycles),
        "attempts": 0,
        "completed_cycles": 0,
        "skip_count": 0,
        "last_run_at": None,
        "last_result": "armed",
        "ended_at": None,
    }
    _save_state(conn, state)
    tracking.log_event(
        conn,
        "probe",
        f"ACK probe armed: target={target_cycles}, hours={duration_hours}",
        mode="demo",
    )
    return get_probe_status(conn, now=started)


def _sample_counts(conn: sqlite3.Connection, started_at: str) -> dict[str, int]:
    rows = conn.execute(
        "SELECT operation, COUNT(*) AS n FROM btc_execution_samples "
        "WHERE mode='demo' AND operation LIKE 'probe_%' AND completed_at>=? "
        "GROUP BY operation ORDER BY operation",
        (started_at,),
    ).fetchall()
    return {str(row["operation"]): int(row["n"]) for row in rows}


def get_probe_status(
    conn: sqlite3.Connection, *, now: datetime | None = None
) -> dict[str, Any]:
    """Return a secret-free operational status for logs and final reporting."""
    state = _load_state(conn)
    if state is None:
        return {
            "probe_status": "inactive",
            "completed_cycles": 0,
            "target_cycles": DEFAULT_TARGET_CYCLES,
            "sample_counts": {},
        }
    effective_now = now or _utcnow()
    remaining_seconds = max(
        0, int((_parse_iso(str(state["deadline_at"])) - effective_now).total_seconds())
    )
    return {
        "probe_status": str(state.get("status") or "unknown"),
        "started_at": state.get("started_at"),
        "deadline_at": state.get("deadline_at"),
        "remaining_seconds": remaining_seconds,
        "target_cycles": int(state.get("target_cycles") or 0),
        "attempts": int(state.get("attempts") or 0),
        "completed_cycles": int(state.get("completed_cycles") or 0),
        "skip_count": int(state.get("skip_count") or 0),
        "last_run_at": state.get("last_run_at"),
        "last_result": state.get("last_result"),
        "ended_at": state.get("ended_at"),
        "sample_counts": _sample_counts(conn, str(state["started_at"])),
    }


def _finalize_state(
    conn: sqlite3.Connection,
    state: dict[str, Any],
    *,
    status: str,
    result: str,
    now: datetime,
) -> None:
    state["status"] = status
    state["last_run_at"] = _iso(now)
    state["last_result"] = result
    state["ended_at"] = _iso(now)
    _save_state(conn, state)


def _result(
    conn: sqlite3.Connection,
    state: dict[str, Any],
    status: str,
    *,
    now: datetime,
    **extra: Any,
) -> dict[str, Any]:
    public = get_probe_status(conn, now=now)
    return {"status": status, **public, **extra}


def _has_position(response: dict[str, Any]) -> bool:
    return any(_f(row.get("size")) > 0 for row in _result_list(response))


def _has_open_orders(response: dict[str, Any]) -> bool:
    return bool(_result_list(response))


def _fetch_clean_account(adapter: DemoAdapter) -> tuple[bool | None, bool | None]:
    positions = adapter._call("get_positions", category=_CATEGORY, symbol=_SYMBOL)
    if positions is None:
        return None, None
    orders = adapter._call(
        "get_open_orders", category=_CATEGORY, symbol=_SYMBOL, openOnly=0
    )
    if orders is None:
        return None, None
    return _has_position(positions), _has_open_orders(orders)


def _probe_price(adapter: DemoAdapter, side: str) -> float | None:
    response = adapter._call("get_tickers", category=_CATEGORY, symbol=_SYMBOL)
    if response is None:
        return None
    rows = _result_list(response)
    if not rows:
        return None
    ticker = rows[0]
    last = _f(ticker.get("lastPrice"))
    if last <= 0:
        return None
    if side == "Buy":
        reference = _f(ticker.get("bid1Price"), last) or last
        raw = min(last, reference) * (1.0 - PROBE_DISTANCE_FRAC)
    else:
        reference = _f(ticker.get("ask1Price"), last) or last
        raw = max(last, reference) * (1.0 + PROBE_DISTANCE_FRAC)
    return float(_pstr(raw))


def _log_result(conn: sqlite3.Connection, message: str, *, level: str = "info") -> None:
    tracking.log_event(conn, "probe", message, level=level, mode="demo")


def run_probe_once(
    conn: sqlite3.Connection,
    *,
    session: Any | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Run one armed probe cycle and return only secret-free fields."""
    current = now or _utcnow()
    state = _load_state(conn)
    if state is None:
        return _result(conn, {}, "inactive", now=current)
    if state.get("status") != "active":
        return _result(conn, state, str(state.get("status") or "inactive"), now=current)

    if current >= _parse_iso(str(state["deadline_at"])):
        _finalize_state(
            conn, state, status="expired", result="deadline_reached", now=current
        )
        _log_result(conn, "ACK probe expired at 72-hour deadline")
        return _result(conn, state, "expired", now=current)

    target = int(state.get("target_cycles") or DEFAULT_TARGET_CYCLES)
    if int(state.get("completed_cycles") or 0) >= target:
        _finalize_state(
            conn, state, status="complete", result="target_reached", now=current
        )
        return _result(conn, state, "complete", now=current)

    adapter = DemoAdapter(conn, {}, [], [], mode="demo")
    if session is not None:
        adapter.sess = session
        adapter._sess_err = None
    if adapter.sess is None:
        state["last_run_at"] = _iso(current)
        state["last_result"] = "session_unavailable"
        state["skip_count"] = int(state.get("skip_count") or 0) + 1
        _save_state(conn, state)
        _log_result(conn, "ACK probe skipped: demo session unavailable", level="error")
        return _result(conn, state, "session_unavailable", now=current)

    has_position, has_orders = _fetch_clean_account(adapter)
    if has_position is None or has_orders is None:
        state["last_run_at"] = _iso(current)
        state["last_result"] = "precheck_failed"
        state["skip_count"] = int(state.get("skip_count") or 0) + 1
        _save_state(conn, state)
        _log_result(conn, "ACK probe skipped: account precheck unknown", level="error")
        return _result(conn, state, "precheck_failed", now=current)
    if has_position or has_orders:
        result = "skipped_position" if has_position else "skipped_open_orders"
        state["last_run_at"] = _iso(current)
        state["last_result"] = result
        state["skip_count"] = int(state.get("skip_count") or 0) + 1
        _save_state(conn, state)
        _log_result(conn, f"ACK probe safely skipped: {result}")
        return _result(conn, state, result, now=current)

    attempt = int(state.get("attempts") or 0)
    side = "Buy" if attempt % 2 == 0 else "Sell"
    price = _probe_price(adapter, side)
    if price is None:
        state["last_run_at"] = _iso(current)
        state["last_result"] = "ticker_failed"
        state["skip_count"] = int(state.get("skip_count") or 0) + 1
        _save_state(conn, state)
        _log_result(conn, "ACK probe skipped: ticker unavailable", level="error")
        return _result(conn, state, "ticker_failed", now=current)

    state["attempts"] = attempt + 1
    state["last_run_at"] = _iso(current)
    state["last_result"] = "submitting"
    _save_state(conn, state)
    link_id = f"prism-ack-{current:%m%d%H%M}-{secrets.token_hex(3)}"
    response = adapter._call(
        "place_order",
        category=_CATEGORY,
        symbol=_SYMBOL,
        side=side,
        orderType="Limit",
        qty=_qstr(PROBE_QTY),
        price=_pstr(price),
        timeInForce="PostOnly",
        positionIdx=_POSITION_IDX,
        orderLinkId=link_id,
        _telemetry={
            "operation": "probe_submit",
            "details": {
                "side": side,
                "order_type": "Limit",
                "time_in_force": "PostOnly",
                "reduce_only": False,
                "qty": PROBE_QTY,
                "price": price,
                "reason_code": "demo_ack_probe",
            },
        },
    )
    order_id = _order_id(response)
    if not order_id:
        state["last_result"] = "submit_failed"
        _save_state(conn, state)
        _log_result(conn, "ACK probe submit failed; no cancel required", level="error")
        return _result(
            conn, state, "submit_failed", now=current, side=side, price=price
        )

    adapter._call(
        "cancel_order",
        category=_CATEGORY,
        symbol=_SYMBOL,
        orderId=order_id,
        _telemetry={
            "operation": "probe_cancel",
            "details": {"reason_code": "demo_ack_probe_immediate_cancel"},
        },
    )

    has_position, has_orders = _fetch_clean_account(adapter)
    if has_orders:
        # The regular call already retried once.  One final targeted cancel is
        # safer than cancel-all, which could affect an unrelated strategy order.
        adapter._call(
            "cancel_order",
            category=_CATEGORY,
            symbol=_SYMBOL,
            orderId=order_id,
            _telemetry={
                "operation": "probe_emergency_cancel",
                "details": {"reason_code": "probe_order_still_open"},
            },
        )
        has_position, has_orders = _fetch_clean_account(adapter)

    if has_position is None or has_orders is None:
        _finalize_state(
            conn,
            state,
            status="halted",
            result="postcheck_unknown",
            now=current,
        )
        _log_result(conn, "ACK probe halted: post-cancel state unknown", level="error")
        return _result(conn, state, "halted_postcheck_unknown", now=current)
    if has_position:
        _finalize_state(
            conn,
            state,
            status="halted",
            result="position_detected",
            now=current,
        )
        _log_result(conn, "ACK probe halted: demo position detected", level="error")
        return _result(conn, state, "halted_position_detected", now=current)
    if has_orders:
        _finalize_state(
            conn,
            state,
            status="halted",
            result="order_still_open",
            now=current,
        )
        _log_result(conn, "ACK probe halted: probe order still open", level="error")
        return _result(conn, state, "halted_order_still_open", now=current)

    state["completed_cycles"] = int(state.get("completed_cycles") or 0) + 1
    state["last_result"] = "clean"
    if int(state["completed_cycles"]) >= target:
        state["status"] = "complete"
        state["ended_at"] = _iso(current)
    _save_state(conn, state)
    _log_result(
        conn,
        f"ACK probe clean cycle {state['completed_cycles']}/{target}: {side}",
    )
    return _result(conn, state, "clean", now=current, side=side, price=price)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("start", "run", "status"))
    parser.add_argument("--root-db", default=None)
    parser.add_argument("--target-cycles", type=int, default=DEFAULT_TARGET_CYCLES)
    parser.add_argument("--duration-hours", type=int, default=DEFAULT_DURATION_HOURS)
    parser.add_argument(
        "--restart",
        action="store_true",
        help="replace an existing probe window; never implied by cron",
    )
    args = parser.parse_args(argv)

    conn = tracking.get_connection(args.root_db)
    tracking.ensure_schema(conn)
    try:
        if args.action == "start":
            result = start_probe(
                conn,
                target_cycles=args.target_cycles,
                duration_hours=args.duration_hours,
                restart=args.restart,
            )
        elif args.action == "run":
            result = run_probe_once(conn)
        else:
            result = get_probe_status(conn)
    finally:
        conn.close()
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    if result.get("probe_status") == "halted":
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
