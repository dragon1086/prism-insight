"""Build validated BTC entry-attached SL parameters; no execution or I/O."""
from __future__ import annotations

from decimal import Decimal, InvalidOperation, ROUND_CEILING, ROUND_FLOOR


def native_stop_params(side: str, entry_price: float, stop_price: float,
                       mode: str = "Full") -> dict[str, str]:
    if side not in ("long", "short") or mode not in ("Full", "Partial"):
        raise ValueError("invalid attached stop policy")
    if isinstance(entry_price, bool) or isinstance(stop_price, bool):
        raise ValueError("invalid attached stop price")
    try:
        # Match the existing BTC limit-price wire formatter before side checks.
        entry, stop = Decimal(f"{float(entry_price):.1f}"), Decimal(str(stop_price))
    except (InvalidOperation, ValueError, TypeError, OverflowError) as exc:
        raise ValueError("invalid attached stop price") from exc
    if not entry.is_finite() or not stop.is_finite() or min(entry, stop) <= 0:
        raise ValueError("invalid attached stop price")
    try:
        stop = stop.quantize(Decimal(".1"), rounding=ROUND_CEILING if side == "long" else ROUND_FLOOR)
    except InvalidOperation as exc:
        raise ValueError("invalid attached stop precision") from exc
    if stop <= 0 or (stop >= entry if side == "long" else stop <= entry):
        raise ValueError("attached stop is not on protective side after tick rounding")
    return {"stopLoss": str(stop), "slTriggerBy": "LastPrice",
            "slOrderType": "Market", "tpslMode": mode}
