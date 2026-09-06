"""Pure, offline M2 execution-model diagnostics; never a trading authority.

OHLC does not identify entry/TP/SL chronology, liquidity, or actual fills. This
module marks modeling ambiguity and fee sensitivity without rewriting the
backtest, producing strategy returns, or asserting improved profitability.
"""
from __future__ import annotations

from dataclasses import dataclass
import math

from core.exits import PositionView


CONTRACT_VERSION = "tpsl-execution-diagnostic-v1"


@dataclass(frozen=True)
class OHLCBar:
    open: float
    high: float
    low: float
    close: float


def _number(value, name, *, positive=True):
    if (isinstance(value, bool) or not isinstance(value, (int, float))
            or not math.isfinite(value) or (positive and value <= 0)):
        raise ValueError(name)
    return value


def audit_exit_bar(
    position: PositionView,
    bar: OHLCBar,
    *,
    entered_this_bar: bool = False,
    be_trail_activate_r: float = 1.5,
) -> dict:
    """Audit declared active levels, not a reconstructed intrabar price path.

    Caller supplies the stop/TP levels applicable to its model. An adverse-open
    reference is illustrative, not an execution price. NO_FLAGGED_AMBIGUITY
    does not certify a fill, the strategy, costs, or absence of other model bugs.
    """
    if not isinstance(position, PositionView) or not isinstance(bar, OHLCBar):
        raise ValueError("position_and_bar_types")
    if position.side not in {"long", "short"} or type(entered_this_bar) is not bool:
        raise ValueError("side_or_entry_activation")
    if any(type(getattr(position, field)) is not bool for field in ("tp1_hit", "trailing_active")):
        raise ValueError("state_flags_must_be_boolean")
    for field in ("entry_price", "qty", "sl_price", "tp1_price"):
        _number(getattr(position, field), field)
    for field in ("open", "high", "low", "close"):
        _number(getattr(bar, field), field)
    if not bar.low <= min(bar.open, bar.close) <= max(bar.open, bar.close) <= bar.high:
        raise ValueError("invalid_ohlc_order")
    _number(be_trail_activate_r, "be_trail_activate_r")
    long = position.side == "long"
    stop_touched = bar.low <= position.sl_price if long else bar.high >= position.sl_price
    tp_touched = (not position.tp1_hit and
                  (bar.high >= position.tp1_price if long else bar.low <= position.tp1_price))
    distance = abs(position.tp1_price - position.entry_price)
    activation_distance = be_trail_activate_r * distance
    be_level = position.entry_price + (1 if long else -1) * activation_distance
    for field, value in (("distance", distance), ("activation_distance", activation_distance), ("be_level", be_level)):
        _number(value, field, positive=False)
    be_reached = (not position.trailing_active and distance > 0 and
                  (bar.high >= be_level if long else bar.low <= be_level))
    codes = []
    adverse_open = None
    if not entered_this_bar and (bar.open < position.sl_price if long else bar.open > position.sl_price):
        codes.append("STOP_GAP_ADVERSE_OPEN")
        adverse_open = bar.open
        if not bar.low <= position.sl_price <= bar.high:
            codes.append("STOP_LEVEL_OUTSIDE_BAR")
    if entered_this_bar and tp_touched:
        codes.append("AMBIGUOUS_ENTRY_BAR_TP")
    if entered_this_bar and be_reached:
        codes.append("AMBIGUOUS_ENTRY_BAR_BE")
    if stop_touched and tp_touched:
        codes.append("AMBIGUOUS_TP_SL_ORDER")
    be_stop = max(position.sl_price, position.entry_price) if long else min(position.sl_price, position.entry_price)
    if be_reached and (bar.low <= be_stop if long else bar.high >= be_stop):
        codes.append("AMBIGUOUS_BE_ACTIVATION_ORDER")
    return {
        "contract_version": CONTRACT_VERSION,
        "kind": "OFFLINE_EXECUTION_MODEL_DIAGNOSTIC",
        "verdict": "REVIEW_REQUIRED" if codes else "NO_FLAGGED_AMBIGUITY",
        "side": position.side,
        "entered_this_bar": entered_this_bar,
        "reason_codes": codes,
        "tp_sl_convention": "SL_BEFORE_TP",
        "chronology": "UNKNOWN",
        "adverse_open_reference": adverse_open,
        "reference_is_execution": False,
        "execution_observed": False,
        "profitability_evidence": False,
    }


def compare_tp_fee_roles(*, qty: float, price: float, maker_rate: float, taker_rate: float) -> dict:
    """Compare declared fee rates on equal notional; infer no maker/taker fill."""
    _number(qty, "qty")
    _number(price, "price")
    _number(maker_rate, "maker_rate", positive=False)
    _number(taker_rate, "taker_rate", positive=False)
    notional = qty * price
    maker_fee, taker_fee = notional * maker_rate, notional * taker_rate
    difference = taker_fee - maker_fee
    for name, value in (("notional", notional), ("maker_fee", maker_fee),
                        ("taker_fee", taker_fee), ("difference", difference)):
        _number(value, name, positive=False)
    return {"contract_version": CONTRACT_VERSION, "notional": notional,
            "maker_fee": maker_fee, "taker_fee": taker_fee,
            "taker_minus_maker": difference, "actual_fee_role": "UNKNOWN",
            "profitability_evidence": False}
