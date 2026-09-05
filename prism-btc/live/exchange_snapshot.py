"""Complete position/order reads: a cursor is continuation, not failure.

Bybit may repeat a symbol position on the terminal page. Deduplicate only
stable identical risk fields; contradictions, cycles and failures stay unknown.
"""
from __future__ import annotations


def read_complete(call, method, *, max_pages=20, **params):
    if method not in ("get_positions", "get_open_orders"):
        raise ValueError("unsupported snapshot method")
    rows = {}
    seen_cursors = set()
    cursor = None
    template = None
    fields = (("side", "size", "avgPrice", "stopLoss", "takeProfit")
              if method == "get_positions" else
              ("side", "qty", "leavesQty", "price", "triggerPrice", "reduceOnly", "orderStatus"))
    for _ in range(max_pages):
        try:
            response = call(method, **params, **({"cursor": cursor} if cursor else {}))
        except Exception:
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
            if key in rows and any(rows[key].get(f) != row.get(f) for f in fields):
                return None
            rows[key] = row
        cursor = result.get("nextPageCursor")
        if not cursor:
            return {**template, "result": {**result, "list": list(rows.values()), "nextPageCursor": ""}}
        if not isinstance(cursor, str) or cursor in seen_cursors:
            return None
        seen_cursors.add(cursor)
    return None
