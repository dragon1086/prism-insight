"""Exact aggregate TP accounting. Allocation is attribution, not tranche 1R proof."""
from decimal import Decimal, InvalidOperation


def _number(value):
    number = Decimal(str(value))
    if not number.is_finite():
        raise ValueError("nonfinite TP value")
    return number


def order_cumulative(row, target):
    """Validate status/cumulative and optional remaining quantity as one schema."""
    try:
        qty, cumulative = _number(target), _number(row["cumExecQty"])
        status = row["orderStatus"]
        if cumulative < 0 or cumulative > qty:
            return None
        if status == "New" and cumulative != 0:
            return None
        if status == "PartiallyFilled" and not 0 < cumulative < qty:
            return None
        if status == "Filled" and cumulative != qty:
            return None
        if status not in {"New", "PartiallyFilled", "Filled", "Cancelled", "Rejected", "PartiallyFilledCanceled"}:
            return None
        if "leavesQty" in row:
            leaves = _number(row["leavesQty"])
            if leaves < 0 or leaves > qty - cumulative:
                return None
            if status in {"New", "PartiallyFilled", "Filled"} and leaves != qty - cumulative:
                return None
        return float(cumulative)
    except (KeyError, TypeError, ValueError, InvalidOperation):
        return None


def allocation_snapshot(positions, qty):
    """Proportional .001 quotas; remainder lots go to ascending position IDs."""
    if any(not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0 for pid, _ in positions):
        raise ValueError("invalid TP position ID")
    rows = sorted((pid, _number(size)) for pid, size in positions)
    target = _number(qty)
    units = int(target * 1000)
    total = sum(size for _, size in rows)
    if (not rows or total <= 0 or units <= 0 or target > total
            or target * 1000 != units or len({pid for pid, _ in rows}) != len(rows)
            or any(size <= 0 or abs(size - size.quantize(Decimal(".001"))) > Decimal("1e-9")
                   for _, size in rows)):
        raise ValueError("invalid TP allocation")
    quotas = [int(units * size / total) for _, size in rows]
    for index in range(units - sum(quotas)):
        quotas[index] += 1
    return [dict(id=pid, initial_qty=float(size), quota=quota / 1000)
            for (pid, size), quota in zip(rows, quotas)]


def consumed(allocations, qty):
    left = _number(qty)
    if left < 0:
        raise ValueError("negative TP fill")
    result = {}
    for item in allocations:
        quota = _number(item["quota"])
        initial = _number(item["initial_qty"])
        if (quota < 0 or initial < quota or item["id"] in result
                or not isinstance(item["id"], int) or item["id"] <= 0):
            raise ValueError("invalid allocation")
        amount = min(left, quota)
        result[item["id"]] = amount
        left -= amount
    if left > Decimal("0.000000001"):
        raise ValueError("overfilled TP")
    return result


def settlement(intent, rows, exchange_qty, positions):
    """Return incremental allocations and exact execution cursor, or UNKNOWN.

    Full snapshots must retain every previous execution unchanged. Any other
    local/exchange reduction or concurrent addition requires separate recovery.
    """
    try:
        if rows is None or not intent.get("order_id"):
            return None
        executions = {}
        for row in rows:
            if (not row.get("execId") or row.get("execType") != "Trade"
                    or row.get("symbol") != "BTCUSDT"
                    or row.get("side") != ("Sell" if intent["side"] == "long" else "Buy")
                    or row.get("orderId") != intent["order_id"]
                    or row.get("orderLinkId") != intent["link_id"]):
                return None
            qty, price, fee = (_number(row[key]) for key in ("execQty", "execPrice", "execFee"))
            if qty <= 0 or price <= 0:
                return None
            value = [str(qty), str(price), str(fee)]
            if row["execId"] in executions and executions[row["execId"]] != value:
                return None
            executions[row["execId"]] = value
        previous = intent.get("executions", {})
        if any(executions.get(key) != value for key, value in previous.items()):
            return None
        old_qty = sum((_number(value[0]) for value in previous.values()), Decimal(0))
        qty = sum((_number(value[0]) for value in executions.values()), Decimal(0))
        if qty > _number(intent["qty"]):
            return None
        allocation = intent["allocations"]
        before, after = consumed(allocation, old_qty), consumed(allocation, qty)
        current = dict(positions)
        if len(current) != len(positions):
            return None
        expected_ids = {item["id"] for item in allocation
                        if _number(item["initial_qty"]) - before[item["id"]] > 0}
        if set(current) != expected_ids:
            return None
        for item in allocation:
            if abs(_number(current.get(item["id"], 0)) - (_number(item["initial_qty"]) - before[item["id"]])) > Decimal("1e-9"):
                return None
        expected = sum((_number(item["initial_qty"]) for item in allocation), Decimal(0)) - qty
        if abs(_number(exchange_qty) - expected) > Decimal("1e-9"):
            return None
        return {pid: float(after[pid] - before[pid]) for pid in after}, executions
    except (KeyError, TypeError, ValueError, InvalidOperation, ArithmeticError):
        return None
