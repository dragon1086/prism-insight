"""Complete position/order reads: a cursor is continuation, not failure.

Bybit may repeat a symbol position on the terminal page. Deduplicate only
stable identical risk fields; contradictions, cycles and failures stay unknown.
"""
from __future__ import annotations

import math


def _recheck_active_placeholder(call, params, rows, row, result, cursor):
    """Corroborate one active BTC position, never interpret a blank row as flat."""
    empty_fields = ("side", "avgPrice", "stopLoss", "takeProfit", "createdTime",
                    "updatedTime", "positionStatus")
    risk_fields = ("symbol", "positionIdx", "size", *empty_fields)
    if (not cursor or params.get("category") != "linear" or params.get("symbol") != "BTCUSDT"
            or len(rows) != 1 or len(result["list"]) != 1 or result.get("nextPageCursor")
            or row.get("symbol") != "BTCUSDT" or type(row.get("positionIdx")) is not int
            or row["positionIdx"] != 0 or any(row.get(k) != "" for k in empty_fields)):
        return None
    old = next(iter(rows.values()))
    try:
        if (not math.isfinite(float(row["size"])) or float(row["size"]) != 0
                or old.get("symbol") != "BTCUSDT" or type(old.get("positionIdx")) is not int
                or old["positionIdx"] != 0 or old.get("side") not in ("Buy", "Sell")
                or old.get("positionStatus") != "Normal"
                or any(k not in old for k in risk_fields)
                or any(not str(old[k]).isdigit() or int(old[k]) <= 0
                       for k in ("createdTime", "updatedTime"))
                or not all(math.isfinite(float(old[k])) and float(old[k]) > 0
                           for k in ("size", "avgPrice", "createdTime", "updatedTime"))):
            return None
        response = call("get_positions", **params)  # Single independent read, no recursion.
        fresh_rows = response["result"]["list"]
        if response.get("retCode") != 0 or not isinstance(fresh_rows, list) or len(fresh_rows) != 1:
            return None
        fresh = fresh_rows[0]
        if (not isinstance(fresh, dict) or type(fresh.get("positionIdx")) is not int
                or any(k not in fresh or fresh[k] != old[k] for k in risk_fields)):
            return None
        return fresh
    except (KeyError, TypeError, ValueError, OverflowError):
        return None
    except Exception:  # noqa: BLE001 - every transport failure must stay unknown
        return None

def _flat(row):
    try:
        return float(row["size"]) == 0
    except (KeyError, TypeError, ValueError):
        return False


def read_complete(call, method, *, max_pages=20, **params):
    if method not in ("get_positions", "get_open_orders"):
        raise ValueError("unsupported snapshot method")
    rows = {}
    seen_cursors = set()
    cursor = None
    template = None
    fields = (("side", "size", "avgPrice", "stopLoss", "takeProfit")
              if method == "get_positions" else
              ("symbol", "positionIdx", "side", "orderLinkId", "parentOrderLinkId", "qty", "leavesQty", "price", "triggerPrice",
               "reduceOnly", "orderStatus", "orderType", "triggerDirection", "triggerBy", "stopOrderType"))
    for _ in range(max_pages):
        try:
            response = call(method, **params, **({"cursor": cursor} if cursor else {}))
        except Exception:  # noqa: BLE001 - every transport failure must stay unknown
            return None
        if not isinstance(response, dict) or response.get("retCode") != 0:
            return None
        result = response.get("result")
        if not isinstance(result, dict) or not isinstance(result.get("list"), list):
            return None
        template = response
        for row in result["list"]:
            if not isinstance(row, dict):
                return None
            if method == "get_positions":
                key = (row.get("symbol", params.get("symbol")), row.get("positionIdx", 0))
            else:
                key = row.get("orderId")
                if not key:
                    return None
            both_flat = method == "get_positions" and key in rows and _flat(rows[key]) and _flat(row)
            if key in rows and not both_flat and any(rows[key].get(f) != row.get(f) for f in fields):
                fresh = (_recheck_active_placeholder(call, params, rows, row, result, cursor)
                         if method == "get_positions" else None)
                if fresh is None:
                    return None
                rows[key] = fresh
                continue
            rows[key] = row
        cursor = result.get("nextPageCursor")
        if not cursor:
            return {**template, "result": {**result, "list": list(rows.values()), "nextPageCursor": ""}}
        if not isinstance(cursor, str) or cursor in seen_cursors:
            return None
        seen_cursors.add(cursor)
    return None
