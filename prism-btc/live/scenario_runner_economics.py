"""Optional, pure holding-review facts; never an order or risk-budget authority."""
from decimal import Decimal, InvalidOperation, ROUND_CEILING, ROUND_FLOOR
import math


def _number(value):
    if value is None or isinstance(value, bool):
        raise ValueError('missing_number')
    result = Decimal(str(value))
    if not result.is_finite():
        raise ValueError('nonfinite_number')
    return result


def runner_economics(*, accounting, side, mark_price, hard_stop, observed_at, now,
                     pending_entries, initial_equity, estimated_cost_rate, slippage_bps,
                     price_tick=None):
    """Project confirmed remaining lots only. Unknown never becomes zero.

    The existing cost rate is a conservative future allowance, not an observed
    fee tier. Already-recorded costs occur once; unknown future funding is excluded.
    These facts never change original risk accounting, even after realized profit.
    """
    unavailable = dict(status='unavailable', reason='incomplete_holding_evidence')
    try:
        if (accounting.get('status') != 'confirmed' or
                accounting.get('accounting_complete') is not True or
                side not in ('LONG', 'SHORT') or pending_entries != []):
            return unavailable
        stamp, current = _number(observed_at), _number(now)
        if not 0 <= current-stamp <= 120:
            return dict(status='unavailable', reason='stale_or_future_account_evidence')
        mark, stop, equity = map(_number, (mark_price, hard_stop, initial_equity))
        rate, slip = map(_number, (estimated_cost_rate, slippage_bps))
        if min(mark, stop, equity) <= 0 or not 0 <= rate <= 1 or not 0 <= slip <= 10000:
            return unavailable
        gross, fees, funding, net = (_number(accounting.get(k)) for k in
                                     ('gross_pnl','fees','funding_net','net_pnl'))
        if abs(gross-fees+funding-net) > Decimal('0.0000001'):
            return dict(status='unavailable', reason='accounting_equation_mismatch')
        lots = accounting.get('positions')
        if not isinstance(lots, list) or not lots or len(lots) > 100:
            return unavailable
        quantity = entry_value = at_mark = at_stop = Decimal(0)
        sign = 1 if side == 'LONG' else -1
        for lot in lots:
            price, qty = _number(lot.get('price')), _number(lot.get('quantity'))
            if price <= 0 or qty <= 0:
                return unavailable
            quantity += qty
            entry_value += price*qty
            at_mark += sign*(mark-price)*qty
            at_stop += sign*(stop-price)*qty
        allowance = quantity*stop*(rate+slip/10000)
        values = dict(observed_at=stamp, remaining_quantity=quantity,
            realized_gross=gross, recorded_fees=fees, recorded_funding_net=funding,
            realized_net_including_recorded_costs=net,
            remaining_gross_at_mark=at_mark, remaining_gross_at_stop=at_stop,
            scenario_net_at_mark_before_future_costs=net+at_mark,
            scenario_net_at_stop_before_future_costs=net+at_stop,
            estimated_future_cost_allowance_at_stop=allowance,
            scenario_net_at_stop_after_estimated_costs=net+at_stop-allowance,
            scenario_net_at_stop_pct_of_start_equity=(net+at_stop-allowance)/equity*100,
            giveback_from_mark_to_stop_before_future_costs=max(Decimal(0),at_mark-at_stop))
        values = {k: float(v) for k,v in values.items()}
        if not all(math.isfinite(v) for v in values.values()):
            return unavailable
        return dict(status='available', scope='confirmed_remaining_lots_no_pending_entries',
                    future_funding_included=False, budget_replenishment_allowed=False,
                    estimates_not_guaranteed=True,
                    future_cost_basis='existing_conservative_cost_rate_plus_stop_slippage_allowance',
                    cost_positive_stop=_cost_positive_stop(net=net, quantity=quantity,
                        entry_value=entry_value, sign=sign, cost_rate=rate+slip/10000,
                        tick=price_tick, mark=mark, stop=stop),
                    **values)
    except (AttributeError, KeyError, TypeError, ValueError, InvalidOperation, OverflowError):
        return unavailable


def _cost_positive_stop(*, net, quantity, entry_value, sign, cost_rate, tick, mark, stop):
    """A cost-positive boundary, not an executable order or noise-buffer verdict.

    Whole-scenario recorded net already includes entry/realized costs once.
    Projected exit costs use the existing conservative allowance, not a fee tier.
    The first profitable tick is a lower boundary for LONG and upper for SHORT.
    Existing tighter stops are retained; an infeasible boundary is never forced.
    """
    unavailable = dict(status='unavailable', reason='invalid_tick_or_cost_boundary')
    try:
        tick = _number(tick)
        if tick <= 0 or cost_rate >= 1:
            return unavailable
        slope = quantity*(sign-cost_rate)
        boundary = (sign*entry_value-net)/slope
        rounding = ROUND_CEILING if sign == 1 else ROUND_FLOOR
        price = (boundary/tick).to_integral_value(rounding=rounding)*tick
        if sign == 1:
            price = max(tick,price)
        def estimate(value):
            return net+sign*(quantity*value-entry_value)-quantity*value*cost_rate
        # An exact break-even tick is not a strictly positive profit target.
        if estimate(price) <= 0:
            price += sign*tick
        if price <= 0 or estimate(price) <= 0:
            return unavailable
        candidate = max(stop,price) if sign == 1 else min(stop,price)
        valid_side = candidate < mark if sign == 1 else candidate > mark
        values = dict(break_even_stop_price=boundary,
            minimum_positive_stop_price=price,
            estimated_scenario_net_at_minimum_positive_stop=estimate(price))
        values = {k:float(v) for k,v in values.items()}
        if not all(math.isfinite(v) for v in values.values()):
            return unavailable
        return dict(status='available', **values,
            feasible_without_widening=valid_side,
            feasible_stop_price=float(candidate) if valid_side else None,
            reason='cost_positive_side_available' if valid_side else 'would_cross_or_touch_mark',
            structural_noise_buffer_evaluated=False,
            future_funding_included=False, estimates_not_guaranteed=True)
    except (TypeError,ValueError,InvalidOperation,OverflowError):
        return unavailable
