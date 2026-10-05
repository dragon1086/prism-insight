"""Optional, pure holding-review facts; never an order or risk-budget authority."""
from decimal import Decimal, InvalidOperation
import math


def _number(value):
    if value is None or isinstance(value, bool):
        raise ValueError('missing_number')
    result = Decimal(str(value))
    if not result.is_finite():
        raise ValueError('nonfinite_number')
    return result


def runner_economics(*, accounting, side, mark_price, hard_stop, observed_at, now,
                     pending_entries, initial_equity, estimated_cost_rate, slippage_bps):
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
        quantity = at_mark = at_stop = Decimal(0)
        sign = 1 if side == 'LONG' else -1
        for lot in lots:
            price, qty = _number(lot.get('price')), _number(lot.get('quantity'))
            if price <= 0 or qty <= 0:
                return unavailable
            quantity += qty
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
                    **values)
    except (AttributeError, KeyError, TypeError, ValueError, InvalidOperation, OverflowError):
        return unavailable
