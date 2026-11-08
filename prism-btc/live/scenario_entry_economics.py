"""Fresh OPEN economics only; never changes prices, sizing or held protection."""
from decimal import Decimal
import math

MINIMUM_RATIO = '1.2'


def policy():
    return dict(version=1, minimum_ratio=float(MINIMUM_RATIO),
                missing_fee_basis='HOST_COST_RESERVE_STRESS_NOT_ACTUAL_FEES')


def _number(value):
    if value is None or isinstance(value, bool):
        raise ValueError('missing_number')
    result = Decimal(str(value))
    if not result.is_finite():
        raise ValueError('nonfinite_number')
    return result


def assess(plan, context, projection):
    supplied = context.get('entry_economics')
    if (supplied is None or plan.get('action') != 'OPEN' or context.get('scenario_id')
            or context.get('positions') or context.get('pending_entries')):
        return None
    result = dict(status='UNKNOWN', minimum_ratio=float(MINIMUM_RATIO), ratio=None,
                  basis='UNKNOWN', scope='FRESH_FLAT_OPEN_CONDITIONAL_PATH_NOT_EXPECTANCY',
                  actual_future_fees=None, actual_future_funding=None)
    try:
        if not isinstance(supplied, dict) or type(supplied.get('version')) is not int or supplied != policy():
            raise ValueError('invalid_policy')
        payoff = projection.get('whole_plan_payoff', {})
        if payoff.get('status') == 'AVAILABLE':
            reward, loss = map(_number, (payoff['net_reward'], payoff['full_stop_loss']))
            basis = 'VERIFIED_HISTORICAL_TAKER_MAX_FUNDING_SLIPPAGE_EXCLUDED'
        else:
            # Reuse the already computed total risk reserve; never call it an observed fee.
            quantity = _number(projection['quantity'])
            loss = quantity * _number(projection['weighted_risk_per_btc'])
            reserve = loss - abs(_number(projection['gross_at_full_stop']))
            if quantity <= 0 or reserve < 0:
                raise ValueError('invalid_reserve')
            path = next(p for p in projection['joint_exit_paths']['paths'] if 'TP_STOP' in p[0])
            reward = _number(path[2]) - reserve
            basis = 'HOST_COST_RESERVE_STRESS_NOT_ACTUAL_FEES'
        if loss <= 0:
            raise ValueError('nonpositive_loss')
        ratio = reward / loss
        value = float(ratio)
        if not math.isfinite(value):
            raise ValueError('nonfinite_ratio')
        result.update(status='PASS' if ratio >= _number(MINIMUM_RATIO) else 'REJECTED',
                      ratio=value, basis=basis, conditional_reward=float(reward), full_stop_loss=float(loss))
    except (KeyError, TypeError, ValueError, ArithmeticError, StopIteration):
        pass
    return result
