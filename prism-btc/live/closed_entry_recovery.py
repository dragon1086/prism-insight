"""Bounded exact evidence for an attached SL closing before local adoption.

No database writes, inferred fills, or broker orders. Missing/contradictory
history, fees, linkage or quantity conservation leaves recovery unknown.
"""
import math


def _rows(call, method, identity, **params):
    found, cursors, cursor = {}, set(), None
    for _ in range(20):
        try:
            response = call(method, category="linear", symbol="BTCUSDT", limit=50,
                            **params, **({"cursor": cursor} if cursor else {}))
        except Exception:
            return None
        if not isinstance(response, dict) or response.get("retCode") != 0:
            return None
        result = response.get("result")
        if not isinstance(result, dict) or not isinstance(result.get("list"), list):
            return None
        for row in result["list"]:
            if not isinstance(row, dict) or not isinstance(row.get(identity), str) or not row[identity]:
                return None
            key = row[identity]
            if key in found and found[key] != row:
                return None
            found[key] = row
        cursor = result.get("nextPageCursor")
        if not cursor:
            return list(found.values())
        if not isinstance(cursor, str) or cursor in cursors:
            return None
        cursors.add(cursor)
    return None


def _number(row, field, positive=False):
    if isinstance(row[field], bool):
        raise ValueError("boolean is not numeric evidence")
    value = float(row[field])
    if not math.isfinite(value) or (positive and value <= 0):
        raise ValueError("invalid numeric evidence")
    return value


def _executions(call, order_id, side, qty, submitted, now_ms, *, closing):
    rows = _rows(call, "get_executions", "execId", orderId=order_id,
                 startTime=submitted, endTime=now_ms)
    if not rows:
        return None
    for row in rows:
        if (row.get("orderId") != order_id or row.get("symbol") != "BTCUSDT"
                or row.get("execType") != "Trade" or row.get("side") != side):
            return None
        size = _number(row, "execQty", True)
        _number(row, "execPrice", True)
        _number(row, "execFee")
        ts = int(row["execTime"])
        if not submitted <= ts <= now_ms:
            return None
        if closing and not math.isclose(_number(row, "closedSize", True), size, abs_tol=1e-9):
            return None
    total = math.fsum(float(row["execQty"]) for row in rows)
    if not math.isclose(total, qty, rel_tol=0, abs_tol=1e-9):
        return None
    return {"qty": total, "price": math.fsum(float(r["execQty"]) * float(r["execPrice"]) for r in rows) / total,
            "fee": math.fsum(float(r["execFee"]) for r in rows),
            "first_ms": min(int(r["execTime"]) for r in rows),
            "last_ms": max(int(r["execTime"]) for r in rows),
            "ids": sorted(r["execId"] for r in rows)}


def closed_entry_proof(call, pending, parent_matches, *, now_ms):
    """Return fully linked economic evidence, or None. Caller proves broker flat.

    Keys: qty, entry_price, exit_price, open_fee, close_fee, entry_time_ms,
    exit_time_ms, net_pnl, gross_pnl, funding_paid, funding_source, execution_ids (close only),
    entry_execution_ids, child_id. funding_paid is the explicit accounting
    residual, as in the existing swing settlement contract, not a funding feed.
    """
    try:
        oid, link = pending.get("order_id"), pending.get("link_id")
        if not oid or not link or pending.get("side") not in {"long", "short"}:
            return None
        parents = _rows(call, "get_order_history", "orderId", orderId=oid)
        if not parents or len(parents) != 1 or not parent_matches(parents[0], pending):
            return None
        parent = parents[0]
        if parent.get("orderStatus") not in {"Filled", "Cancelled", "PartiallyFilledCanceled"}:
            return None
        if _number(parent, "leavesQty") != 0:
            return None
        qty = _number(parent, "cumExecQty", True)
        if qty > _number(parent, "qty", True) + 1e-9:
            return None
        if parent["orderStatus"] == "Filled" and not math.isclose(qty, float(parent["qty"]), abs_tol=1e-9):
            return None
        submitted = int(pending.get("submitted_at_ms") or parent.get("createdTime") or 0)
        if not 0 < submitted <= now_ms or now_ms - submitted >= 24 * 3600_000:
            return None
        side = "Buy" if pending["side"] == "long" else "Sell"
        entry = _executions(call, oid, side, qty, submitted, now_ms, closing=False)
        history = _rows(call, "get_order_history", "orderId", startTime=submitted, endTime=now_ms)
        if entry is None or history is None:
            return None
        linked = [r for r in history if r.get("parentOrderLinkId") == link]
        if len(linked) != 1:
            return None
        child = linked[0]
        close_side = "Sell" if side == "Buy" else "Buy"
        if (child["orderId"] == oid or child.get("symbol") != "BTCUSDT"
                or child.get("positionIdx") != 0 or child.get("side") != close_side
                or child.get("reduceOnly") is not True or child.get("orderType") != "Market"
                or child.get("stopOrderType") not in {"StopLoss", "PartialStopLoss"}
                or child.get("orderStatus") != "Filled"
                or not math.isclose(_number(child, "cumExecQty", True), qty, rel_tol=0, abs_tol=1e-9)):
            return None
        close = _executions(call, child["orderId"], close_side, qty, submitted, now_ms, closing=True)
        if close is None or close["first_ms"] < entry["last_ms"]:
            return None
        pnl_rows = _rows(call, "get_closed_pnl", "orderId", startTime=submitted, endTime=now_ms)
        matches = [r for r in pnl_rows or [] if r["orderId"] == child["orderId"]]
        if pnl_rows is None or len(matches) != 1:
            return None
        pnl = matches[0]
        if (pnl.get("symbol") != "BTCUSDT"
                or pnl.get("side") != close_side or pnl.get("execType") != "Trade"
                or not math.isclose(_number(pnl, "closedSize", True), qty, rel_tol=0, abs_tol=1e-9)
                or not math.isclose(_number(pnl, "qty", True), qty, rel_tol=0, abs_tol=1e-9)
                or not math.isclose(_number(pnl, "avgEntryPrice", True), entry["price"], rel_tol=0, abs_tol=1e-6)
                or not math.isclose(_number(pnl, "avgExitPrice", True), close["price"], rel_tol=0, abs_tol=1e-6)
                or not math.isclose(_number(pnl, "openFee"), entry["fee"], rel_tol=0, abs_tol=1e-6)
                or not math.isclose(_number(pnl, "closeFee"), close["fee"], rel_tol=0, abs_tol=1e-6)):
            return None
        net = _number(pnl, "closedPnl")
        gross = qty * (close["price"] - entry["price"]) * (1 if side == "Buy" else -1)
        return {"qty": qty, "entry_price": entry["price"], "exit_price": close["price"],
                "open_fee": entry["fee"], "close_fee": close["fee"],
                "entry_time_ms": entry["first_ms"], "exit_time_ms": close["last_ms"],
                "net_pnl": net, "gross_pnl": gross,
                "funding_paid": gross - entry["fee"] - close["fee"] - net,
                "funding_source": "settlement_residual",
                "execution_ids": close["ids"], "entry_execution_ids": entry["ids"],
                "child_id": child["orderId"]}
    except (KeyError, TypeError, ValueError, OverflowError):
        return None
