"""Every KIS order request as a ledger event (fail-open, observation only).

All real and demo order TRs — buy, sell, amend/cancel and reserved orders, KR
and US — pass through ``trading.kis_auth._url_fetch``.  Recording there covers
the batch, hard-stop, trend-exit, re-entry, add, pending-reserved and
fill-chaser paths with one hook, so the ledger shows what was actually sent to
the broker and how the broker answered.  ``entry.executed``/``exit.executed``
are simulator snapshots and cannot tell an accepted order from a rejected one.

An accepted order (``rt_cd == "0"``) is not a fill; fills stay with the
existing reconciliation events.  This module never raises, never retries and
performs no network I/O.  Account numbers are dropped; the account appears
only as the same irreversible ``execution_profile_ref`` hash the trading
context ledger uses.
"""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path
from typing import Any, Mapping

from observability.events import emit_event

EVENT_TYPE = "broker.order_request"

# Keyed by the TR id without its first character, so the live ("T"/"C") and
# demo ("V") variants of one order TR map to the same action.
ORDER_TRS: dict[str, tuple[str, str]] = {
    "TTC0012U": ("KR", "buy"),
    "TTC0011U": ("KR", "sell"),
    "TTC0013U": ("KR", "amend_cancel"),
    "TSC0008U": ("KR", "reserved"),
    "TTT1002U": ("US", "buy"),
    "TTT1006U": ("US", "sell"),
    "TTT1001U": ("US", "sell"),  # demo overseas sell
    "TTT3014U": ("US", "reserved_buy"),
    "TTT3016U": ("US", "reserved_sell"),
    "TTT1004U": ("US", "amend_cancel"),
}

# Account identity is never copied; it is represented by execution_profile_ref.
_DROPPED_PARAMS = frozenset({"CANO", "ACNT_PRDT_CD"})


def order_action(tr_id: Any) -> tuple[str, str] | None:
    """Return ``(market, action)`` for an order TR id, ``None`` for any other TR."""
    return ORDER_TRS.get(str(tr_id or "")[1:])


def execution_profile_ref(account_key: str) -> str:
    """Same hash as ``observability.trading_context.execution_profile_ref``."""
    raw = f"execution-profile|{account_key}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def _body(response: Any) -> Mapping[str, Any]:
    try:
        payload = response.json()
    except Exception:  # noqa: BLE001 - a non-JSON body is still an observable answer
        return {}
    return payload if isinstance(payload, Mapping) else {}


def _order_no(body: Mapping[str, Any]) -> str | None:
    output = body.get("output")
    if isinstance(output, list):
        output = output[0] if output else {}
    if not isinstance(output, Mapping):
        return None
    value = output.get("ODNO") or output.get("odno") or output.get("RSVN_ORD_SEQ")
    return str(value) if value else None


def note_order_request(
    tr_id: str,
    params: Mapping[str, Any] | None,
    *,
    paper: bool,
    account: str | None,
    product: str | None,
    response: Any = None,
    error: BaseException | None = None,
    latency_s: float | None = None,
) -> dict[str, Any] | None:
    """Append one ``broker.order_request`` event; ignore non-order TRs."""
    try:
        action = order_action(tr_id)
        if action is None:
            return None
        market, kind = action
        fields = {
            str(key): str(value)[:40]
            for key, value in (params or {}).items()
            if str(key).upper() not in _DROPPED_PARAMS
        }
        http_status = getattr(response, "status_code", None)
        body = _body(response) if response is not None and http_status == 200 else {}
        rt_cd = body.get("rt_cd")
        if error is not None:
            status = "EXCEPTION"
        elif http_status != 200:
            status = "HTTP_ERROR"
        else:
            status = "ACCEPTED" if str(rt_cd) == "0" else "REJECTED"
        if error is not None:
            message = f"{type(error).__name__}: {str(error)[:150]}"
        elif http_status != 200:
            message = str(getattr(response, "text", "") or "")[:200]
        else:
            message = str(body.get("msg1") or "")[:200]
        environment = "demo" if paper else "prod"
        profile = (
            execution_profile_ref(f"{'vps' if paper else 'prod'}:{account}:{product}")
            if account
            else None
        )
        return emit_event(
            EVENT_TYPE,
            service="prism-kis-orders",
            market=market,
            ticker=fields.get("PDNO"),
            severity="INFO" if status == "ACCEPTED" else "WARNING",
            attributes={
                "tr_id": tr_id,
                "action": kind,
                "environment": environment,
                "status": status,
                "http_status": http_status,
                "rt_cd": rt_cd,
                "msg_cd": body.get("msg_cd"),
                "message": message,
                "order_no": _order_no(body),
                "params": fields,
                "execution_profile_ref": profile,
                "process": Path(sys.argv[0] or "").name or None,
                "latency_s": round(latency_s, 3) if latency_s is not None else None,
            },
        )
    except Exception:  # noqa: BLE001 - observation must never affect an order
        return None


__all__ = ["EVENT_TYPE", "ORDER_TRS", "execution_profile_ref", "note_order_request", "order_action"]
