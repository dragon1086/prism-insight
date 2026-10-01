"""Row-level loop protection for pyramided holdings (KR/US hardstop + trend-exit).

The batch evaluates every holding row with its own stop and sells
floor(snapshot_qty / remaining_rows) shares per closed row (#288). The intraday
loops use the same rule so a pyramided ticker is never left without intraday
protection. Rows of different accounts are separate positions.
"""
from __future__ import annotations

import os
from typing import Any


def pending_kr_enabled() -> bool:
    return os.environ.get("POSITION_PENDING_KR_ENABLED", "false").strip().lower() in {
        "1", "true", "yes", "on"}


def pyramid_protectable(market: str, trader: Any) -> bool:
    """Row-level loop exits only where the batch would sell a fractional quantity now."""
    if market == "KR":
        # The broker-first pending batch blocks pyramided exits pending fill reconciliation.
        return not pending_kr_enabled()
    try:
        # A queued US order cannot carry a partial quantity (batch FIX 1 -> full exit).
        return bool(trader.is_market_open())
    except Exception:
        return False


def group_rows_by_account(rows: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    """One position per (ticker, account), rows in entry order."""
    groups: dict[Any, list[dict[str, Any]]] = {}
    for row in sorted(rows, key=lambda r: r.get("id") or 0):
        groups.setdefault(row.get("account_key"), []).append(row)
    return list(groups.values())


def new_split_state(row_count: int) -> dict[str, Any]:
    return {"remaining": int(row_count), "snapshot": None, "ordered": 0}


def split_sell_quantity(split: dict[str, Any], live_qty: int) -> int:
    """floor(available / remaining_rows) from a once-per-run broker snapshot (#288 FIX 2)."""
    if split["snapshot"] is None:
        split["snapshot"] = max(int(live_qty or 0), 0)
    available = max(split["snapshot"] - split["ordered"], 0)
    remaining = split["remaining"]
    quantity = available if remaining <= 1 else available // remaining
    split["ordered"] += quantity
    return quantity
