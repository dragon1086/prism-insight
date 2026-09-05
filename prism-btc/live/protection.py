"""First-observation stop protection, shared without strategy or ledger changes.

The caller owns durable stop submission identity and observed position sizing.
This does not provide immediate-at-fill protection: attach an exchange-native SL
to the entry for that guarantee. No cancellation is performed here.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, ROUND_CEILING, ROUND_FLOOR
import logging

from live.exchange_snapshot import read_complete

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class ProtectionResult:
    status: str
    order_id: str | None = None
    trigger: float | None = None
    qty: float | None = None
    owned: bool = False

    @property
    def confirmed(self) -> bool:
        return self.status == "CONFIRMED"


def _positive(value):
    try:
        number = Decimal(str(value))
        return number if number.is_finite() and number > 0 else None
    except (InvalidOperation, ValueError):
        return None


def reconcile_stop(call, *, side: str, qty: float, trigger: float,
                   owned_order_id: str | None = None,
                   create_order_link_id: str | None = None,
                   symbol: str = "BTCUSDT", position_idx: int = 0) -> ProtectionResult:
    """Read → no-op/amend/create → readback; ACK alone never confirms coverage.

    Existing unowned stops are never changed. New submission requires a stable
    link ID persisted by the caller before invocation (reuse it after lost ACK).
    Quantization follows BTCUSDT 0.001 quantity / 0.1 price conventions.
    """
    wanted_qty, wanted_trigger = _positive(qty), _positive(trigger)
    if side not in ("long", "short") or wanted_qty is None or wanted_trigger is None:
        return ProtectionResult("INVALID_INPUT", owned_order_id)
    if symbol != "BTCUSDT" or position_idx != 0:
        return ProtectionResult("UNSUPPORTED_POSITION", owned_order_id)
    wanted_qty = wanted_qty.quantize(Decimal(".001"), rounding=ROUND_CEILING)
    rounding = ROUND_CEILING if side == "long" else ROUND_FLOOR
    wanted_trigger = wanted_trigger.quantize(Decimal(".1"), rounding=rounding)
    if wanted_trigger <= 0:
        return ProtectionResult("INVALID_INPUT", owned_order_id)
    close_side, direction = ("Sell", 2) if side == "long" else ("Buy", 1)

    def read():
        response = read_complete(call, "get_open_orders", category="linear", symbol=symbol)
        return response["result"]["list"] if response is not None else None

    def valid(row):
        return (row.get("symbol") == symbol and row.get("positionIdx") == position_idx
                and row.get("side") == close_side and row.get("orderType") == "Market"
                and row.get("reduceOnly") is True and row.get("triggerDirection") == direction
                and row.get("triggerBy") == "LastPrice"
                and row.get("orderStatus") == "Untriggered"
                and row.get("stopOrderType") not in ("TakeProfit", "PartialTakeProfit", "TrailingStop")
                and _positive(row.get("qty")) is not None
                and _positive(row.get("triggerPrice")) is not None)

    def sufficient(row, target):
        actual = _positive(row["triggerPrice"])
        return (_positive(row["qty"]) >= wanted_qty and
                (actual >= target if side == "long" else actual <= target))

    def confirmed(row):
        return ProtectionResult("CONFIRMED", row["orderId"],
                                float(row["triggerPrice"]), float(row["qty"]),
                                owned=(row["orderId"] == owned_order_id or
                                       bool(create_order_link_id and
                                            row.get("orderLinkId") == create_order_link_id)))

    rows = read()
    if rows is None:
        return ProtectionResult("SNAPSHOT_UNKNOWN", owned_order_id)
    candidates = [row for row in rows if valid(row)]
    target = wanted_trigger
    if candidates:
        values = [target] + [_positive(row["triggerPrice"]) for row in candidates]
        target = max(values) if side == "long" else min(values)
        for row in candidates:
            if sufficient(row, target):
                return confirmed(row)
    owned = next((row for row in rows if row["orderId"] == owned_order_id or
                  (create_order_link_id and row.get("orderLinkId") == create_order_link_id)), None)
    if owned is not None and not valid(owned):
        return ProtectionResult("OWNED_STOP_UNVERIFIABLE", owned_order_id)
    if candidates and owned is None:
        return ProtectionResult("UNOWNED_STOP_INSUFFICIENT", candidates[0]["orderId"])

    if owned is not None:
        method = "amend_order"
        params = {"orderId": owned["orderId"], "qty": str(max(wanted_qty, _positive(owned["qty"]))),
                  "triggerPrice": str(target.quantize(Decimal(".1"), rounding=rounding))}
    else:
        # An ambiguous existing conditional reduce order is not proof of absence.
        if any(row.get("reduceOnly") is True and _positive(row.get("triggerPrice")) is not None
               and row.get("stopOrderType") not in ("TakeProfit", "PartialTakeProfit") for row in rows):
            return ProtectionResult("STOP_STATE_AMBIGUOUS", owned_order_id)
        if not create_order_link_id:
            return ProtectionResult("SUBMISSION_ID_REQUIRED", owned_order_id)
        method = "place_order"
        params = {"orderLinkId": create_order_link_id, "side": close_side,
                  "qty": str(wanted_qty), "triggerPrice": str(target),
                  "orderType": "Market", "reduceOnly": True, "timeInForce": "GTC",
                  "triggerDirection": direction, "triggerBy": "LastPrice", "positionIdx": position_idx}
    response = None
    try:
        response = call(method, category="linear", symbol=symbol, **params)
    except Exception as exc:
        # Only subsequent exchange state can settle a lost ACK. Do not log
        # exception payloads, which can contain request/account details.
        log.warning("Protection submission uncertain (%s); checking exchange state", type(exc).__name__)
    acknowledged = isinstance(response, dict) and response.get("retCode") == 0
    result = response.get("result") if acknowledged else None
    oid = (result.get("orderId") if isinstance(result, dict) else None) or (owned["orderId"] if owned else owned_order_id)
    after = read()
    for row in after or []:
        matches = (row["orderId"] == oid if method == "amend_order" else
                   row.get("orderLinkId") == create_order_link_id)
        if matches and valid(row) and sufficient(row, target):
            return confirmed(row)
    return ProtectionResult("ACK_UNCONFIRMED" if acknowledged else "SUBMISSION_UNKNOWN", oid,
                            owned=bool(owned or (acknowledged and oid and method == "place_order")))
