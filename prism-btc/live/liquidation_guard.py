"""Conservative entry veto, NOT a projection of UTA cross liquidation price.

Use one main-account capital budget for both lanes. Stops can gap; neither this
finite stress nor the isolated-style leverage check is a maximum-loss guarantee.
Only shared demo NEW entries call this module; recovery/reductions do not.
"""
from __future__ import annotations

import math

from engine.config import SWING_MAX_LEVERAGE
from engine.sizing import LIQ_TO_SL_MIN_RATIO

CLOSE_FEE = 0.00055  # Same taker assumption as backtest.engine; not an API fee quote.


def number(value, *, positive=False):
    try:
        result = float(value)
    except (TypeError, ValueError):
        raise ValueError("liquidation_data_unknown") from None
    if isinstance(value, bool) or not math.isfinite(result) or result < 0 or (positive and result == 0):
        raise ValueError("liquidation_data_unknown")
    return result


def capture(call, wallet, position, started):
    """Two additional bounded GETs per eligible lane; wallet/position reused."""
    info = call("get_account_info")["result"]
    if info.get("marginMode") != "REGULAR_MARGIN":
        raise ValueError("liquidation_margin_mode_unsupported")
    for key in ("accountIMRate", "accountMMRate"):
        if number(wallet.get(key)) >= 1:
            raise ValueError("liquidation_account_margin_exhausted")
    margin_balance = number(wallet.get("totalMarginBalance"), positive=True)
    available = number(wallet.get("totalAvailableBalance"), positive=True)
    current_mm = number(wallet.get("totalMaintenanceMargin"))
    initial_margin = number(wallet.get("totalInitialMargin"))
    mm_rate = number(wallet.get("accountMMRate"))
    # Reported collateral bound, not the exact dynamic UTA denominator:
    # available+IM and MM/rate can reflect haircuts absent from raw balance.
    caps = [margin_balance, number(available + initial_margin, positive=True)]
    if current_mm > 0 and mm_rate > 0:
        caps.append(number(current_mm / mm_rate, positive=True))
    margin_balance = min(caps)
    coins = [c for c in wallet.get("coin", []) if c.get("coin") == "USDT"]
    if len(coins) != 1:
        raise ValueError("liquidation_data_unknown")
    fx = number(number(coins[0].get("usdValue"), positive=True) /
                number(coins[0].get("equity"), positive=True), positive=True)
    risks = call("get_risk_limit", category="linear", symbol="BTCUSDT")["result"]
    if risks.get("nextPageCursor") or not isinstance(risks.get("list"), list) or not risks["list"]:
        raise ValueError("liquidation_risk_tier_unknown")
    tiers = []
    for row in risks["list"]:
        if row.get("symbol") != "BTCUSDT":
            raise ValueError("liquidation_risk_tier_unknown")
        limit = number(row.get("riskLimitValue"), positive=True)
        mm = number(row.get("maintenanceMargin"), positive=True)
        im = number(row.get("initialMargin"), positive=True)
        leverage = number(row.get("maxLeverage"), positive=True)
        if not 0 < mm < im <= 1:
            raise ValueError("liquidation_risk_tier_unknown")
        tiers.append((limit, mm, leverage))
    tiers.sort()
    if len({t[0] for t in tiers}) != len(tiers):
        raise ValueError("liquidation_risk_tier_unknown")
    liq = position.get("liqPrice") if position else None
    return {"fx": fx, "tiers": tuple(tiers), "started": started,
            "margin_balance": margin_balance, "available": available, "current_mm": current_mm,
            "liq": number(liq, positive=True) if liq not in (None, "") else None}


def validate(capital, lanes, reservations, lane, side, qty, price, stop, planned_leverage=None):
    """Use remaining pending qty, no hedge netting or profitable-stop credit."""
    capital = number(capital, positive=True)
    total = 0.
    for item in lanes:
        data = item.liquidation
        if not data:
            raise ValueError("liquidation_data_unknown")
        exposures = []
        if item.qty:
            sign = 1 if item.side == "long" else -1
            distance = sign * (item.mark - item.stop)
            if distance <= 0:
                raise ValueError("liquidation_existing_stop_buffer")
            liq = data["liq"]
            if liq is not None and sign * (item.mark - liq) < LIQ_TO_SL_MIN_RATIO * distance:
                raise ValueError("liquidation_existing_stop_buffer")
            exposures.append((item.qty, item.mark, item.stop, item.side))
            if item.lane == "swing":
                # Main totalEquity already includes main UPL, not swing UPL.
                # Debit swing accrued loss once; never credit its profits.
                total += item.qty * max(0., sign * (item.entry - item.mark)) * data["fx"]
        exposures.extend((r.remaining_qty,
                          r.price_bound if r.side == "long" else r.min_fill_price, r.stop, r.side)
                         for r in reservations if r.lane == item.lane and r.remaining_qty > 0)
        if item.lane == lane:
            exposures.append((qty, price, stop, side))
        loss = notional = existing_notional = pending_opening_fee = 0.
        for index, (amount, reference, sl, direction) in enumerate(exposures):
            amount, reference, sl = (number(v, positive=True) for v in (amount, reference, sl))
            if direction not in ("long", "short"):
                raise ValueError("liquidation_data_unknown")
            distance = (1 if direction == "long" else -1) * (reference - sl)
            if distance <= 0:
                raise ValueError("liquidation_existing_stop_buffer")
            loss += amount * distance * LIQ_TO_SL_MIN_RATIO
            # Conservative high value even for shorts; never net opposing lanes.
            notional += amount * max(item.mark, reference, sl + (LIQ_TO_SL_MIN_RATIO - 1) * distance)
            if index == 0 and item.qty:
                existing_notional = notional
            elif not (item.lane == lane and index == len(exposures) - 1):
                pending_opening_fee += amount * max(reference, item.mark, sl) * CLOSE_FEE * data["fx"]
        if not notional:
            continue
        tier = next((t for t in data["tiers"] if notional <= t[0]), None)
        if tier is None:
            raise ValueError("liquidation_risk_tier_unknown")
        _, maintenance, max_leverage = tier
        opening_fee = pending_opening_fee
        if item.lane == lane:
            expected_leverage = SWING_MAX_LEVERAGE if lane == "swing" else number(planned_leverage, positive=True)
            if not 1 <= expected_leverage <= max_leverage:
                raise ValueError("liquidation_planned_stop_buffer")
            new_notional = qty * max(price, item.mark, stop) * data["fx"]
            new_fee = new_notional * CLOSE_FEE
            opening_fee += new_fee
            if new_notional / expected_leverage + new_fee >= data["available"]:
                raise ValueError("liquidation_account_margin_exhausted")
            # Planned leverage is NOT proof that a setter succeeds. This is
            # only an isolated-style bound, not projected cross liquidation.
            distance = abs(price - stop) / price
            if 1 / expected_leverage - maintenance - CLOSE_FEE < LIQ_TO_SL_MIN_RATIO * distance:
                raise ValueError("liquidation_planned_stop_buffer")
        modeled_mm = notional * maintenance * data["fx"]
        extra_mm = max(0., notional - existing_notional) * maintenance * data["fx"]
        required_mm = max(data["current_mm"] + extra_mm, modeled_mm)
        stress_cost = (loss + notional * CLOSE_FEE) * data["fx"] + opening_fee
        if data["margin_balance"] - stress_cost <= required_mm:
            raise ValueError("liquidation_account_margin_exhausted")
        total += stress_cost + modeled_mm
    if not math.isfinite(total) or total >= capital:
        raise ValueError("liquidation_shared_capital_exhausted")
    return total
