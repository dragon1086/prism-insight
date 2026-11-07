"""Row-level loop protection for pyramided holdings (KR/US hardstop + trend-exit).

The batch evaluates every holding row with its own stop and sells
floor(snapshot_qty / remaining_rows) shares per closed row (#288). The intraday
loops use the same rule so a pyramided ticker is never left without intraday
protection. Rows of different accounts are separate positions.
"""
from __future__ import annotations

import json
import math
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


def new_split_state(row_count: int, rows: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    state = {"remaining": int(row_count), "snapshot": None, "ordered": 0}
    if rows and any(_micro_block(r.get("scenario")) is not None for r in rows):
        state["rows"] = {r.get("id"): r.get("scenario") for r in rows}  # per-row ledger shares
    return state


def split_sell_quantity(split: dict[str, Any], live_qty: int, row_id: Any = None) -> int:
    """Shares for one closed row from a once-per-run broker snapshot (#288 FIX 2).

    Legacy rows: floor(available / remaining_rows). Micro-split rows: that row's ledger
    shares (``row_sell_quantity``); the reason, subscriber denominator and sell fraction are
    left on the state as ``last_reason`` / ``last_denominator`` / ``last_fraction``.
    """
    if split["snapshot"] is None:
        split["snapshot"] = max(int(live_qty or 0), 0)
    available = max(split["snapshot"] - split["ordered"], 0)
    remaining = split["remaining"]
    rows = split.get("rows")
    if rows is not None:
        own = rows.pop(row_id, None)  # an unidentified row is unknown: residual at most
        quantity, split["last_reason"], split["last_denominator"], split["last_fraction"] = row_sell_quantity(
            available, remaining, own, list(rows.values()))
    else:
        quantity = available if remaining <= 1 else available // remaining
    split["ordered"] += quantity
    return quantity


# ── Per-row shares of micro-split pyramid rows (2026-11-08, PYRAMID_ROWS_MICRO_SPLIT_ENABLED) ──
# Rows of different sizes cannot share floor(qty / N). A micro-split row's shares follow from its
# strategy ledger: each leg ordered floor(scaled_cash(slot amount, leg allocation) / leg price)
# shares (strict-budget limit orders, prism_core.micro_split_live). That is the ORDERED quantity;
# a rejected or unfilled order makes it overstate, so a row never sells more than the broker
# remainder left after every other row's ledger shares.
_ROWS_SQL = {
    "KR": "SELECT id, scenario FROM stock_holdings WHERE ticker = ? AND account_key = ? ORDER BY id",
    "US": "SELECT id, scenario FROM us_stock_holdings WHERE ticker = ? AND account_key = ? ORDER BY id",
}


def _micro_block(scenario: Any):
    from prism_core.micro_split_live import record
    if isinstance(scenario, str):
        try:
            scenario = json.loads(scenario or "{}")
        except ValueError:
            return None
    return record(scenario) if isinstance(scenario, dict) else None


def row_ledger_quantity(scenario: Any) -> int | None:
    """Whole shares a micro-split row ordered per its ledger legs; None when not derivable.

    Each leg is sized at ``order_price`` when recorded (the price the broker order used, e.g. the
    US broker re-quote), else at the leg price.
    """
    from prism_core.micro_split_live import scaled_cash
    from prism_core.order_budget import whole_share_quantity
    block = _micro_block(scenario)
    if block is None:
        return None
    try:
        total = 0
        for leg in block["legs"]:
            try:
                cash = scaled_cash(block["unit_amount"], leg["allocation"], block["market"])
            except ValueError:
                cash = 0  # below one currency unit: no order was placed
            # legs store prices as strings
            total += whole_share_quantity(cash, float(leg.get("order_price") or leg["price"]))
        return total
    except (KeyError, TypeError, ValueError, ArithmeticError):
        return None


def row_sell_quantity(available: int, remaining_rows: int, scenario: Any,
                      other_scenarios: list[Any]) -> tuple[int, str, int, float | None]:
    """(shares, reason, subscriber denominator, sell fraction) for closing ONE row of a (ticker, account).

    ``other_scenarios`` are the rows that stay open. No micro-split row involved: the legacy
    floor(available / remaining_rows), denominator N, no fraction. Otherwise each row sells its
    own ledger shares and never another row's: a known row sells min(own, available - others)
    when every other row is known, min(own, available) when one is not; an unknown row sells the
    residual available - others when all others are known, else nothing (fail safe; the last
    row sweeps the remainder). The last row always sells the whole remainder.

    Subscribers sell holding // denominator. While other rows stay open the denominator is at
    least 2 and rounds up (ceil), so a mirror never sells another row's share; 0 shares gives
    denominator 0, which the publishers never send. ``sell_fraction`` = shares / available.
    """
    available = max(int(available or 0), 0)
    if _micro_block(scenario) is None and all(_micro_block(s) is None for s in other_scenarios):
        n = max(int(remaining_rows), 1)
        return (available if n <= 1 else available // n), "LEGACY_EQUAL_SPLIT", n, None
    if not other_scenarios:
        return available, "LAST_ROW_REMAINDER", 1, 1.0
    own = row_ledger_quantity(scenario)
    others = [row_ledger_quantity(s) for s in other_scenarios]
    if None in others:
        quantity, reason = ((0, "PYRAMID_ROW_QTY_UNKNOWN") if own is None
                            else (min(own, available), "PYRAMID_ROW_LEDGER_QTY"))
    else:
        room = max(available - sum(others), 0)
        quantity, reason = ((room, "PYRAMID_ROW_QTY_RESIDUAL") if own is None
                            else (min(own, room), "PYRAMID_ROW_LEDGER_QTY"))
    if quantity <= 0:
        return 0, reason, 0, 0.0
    return quantity, reason, max(2, math.ceil(available / quantity)), round(quantity / available, 4)


def batch_row_sell_quantity(cursor: Any, market: str, stock: dict[str, Any], available: int,
                            remaining_rows: int) -> tuple[int, str, int, float | None]:
    """Batch sell of one row: fresh row scenarios of the (ticker, account), then ``row_sell_quantity``.

    Read before the row is deleted or after (the closed row is excluded by id either way). Any
    micro-split row of the ticker/account selects the ledger path, flag on or off. Never raises:
    an unreadable state sells nothing for a micro-split row (PYRAMID_ROW_QTY_UNKNOWN) and keeps the
    legacy equal split for a legacy row.
    """
    micro = _micro_block(stock.get("scenario")) is not None
    legacy = row_sell_quantity(available, remaining_rows, None, [])
    try:
        if stock.get("id") is None or not stock.get("account_key"):
            raise ValueError("cannot scope the rows")
        rows = cursor.execute(_ROWS_SQL[str(market).upper()],
                              (stock.get("ticker"), stock.get("account_key"))).fetchall()
        fresh = {row[0]: row[1] for row in rows}
        own = fresh.pop(stock.get("id"), stock.get("scenario"))
        return row_sell_quantity(available, remaining_rows, own, list(fresh.values()))
    except Exception as error:  # noqa: BLE001 - one row never stops the batch's other sells
        import logging
        logging.getLogger(__name__).warning("[PYRAMID_ROW] %s row quantity unavailable (%s): %s",
                                            stock.get("ticker"), "micro" if micro else "legacy", error)
        return (0, "PYRAMID_ROW_QTY_UNKNOWN", 0, 0.0) if micro else legacy
