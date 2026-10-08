"""Contemporaneous MA path facts, not inferred trades or profitability."""
import json

import pandas as pd
import pytest

from tests.test_scenario_snapshot import NOW, frames, snapshot


def replace_closes(frame, prices):
    result = frame.copy(deep=True)
    result['close'] = prices
    result['open'] = prices
    result['high'] = result['close'] + 1
    result['low'] = result['close'] - 1
    return result


@pytest.mark.parametrize('direction', [1, -1])
def test_path_uses_each_bar_ma_and_shows_both_crossing_directions(direction):
    h, p = frames(NOW+300000)
    prices = [200-direction*i for i in range(50)]
    prices[-1] = 500 if direction == 1 else 10
    h['30m'] = replace_closes(h['30m'], prices)
    before = h['30m'].copy(deep=True)
    result = snapshot(h, p)['timeframes']['30m']['ma_path']
    points = result['confirmed_points']
    assert len(points) == 3
    assert points[-2]['ma_order'] == ('BEARISH' if direction == 1 else 'BULLISH')
    assert points[-1]['ma_order'] == ('BULLISH' if direction == 1 else 'BEARISH')
    for point, end in zip(points, (48, 49, 50)):
        assert point['ma10'] == pytest.approx(sum(prices[end-10:end])/10)
        assert point['ma35'] == pytest.approx(sum(prices[end-35:end])/35)
        assert point['close'] == prices[end-1]
        assert point['is_confirmed'] is True
    pd.testing.assert_frame_equal(h['30m'], before)


def test_raw_constant_gap_is_not_normalized_narrowing():
    h, p = frames(NOW+300000)
    h['30m'] = replace_closes(h['30m'], list(range(100, 150)))
    fact = snapshot(h, p)['timeframes']['30m']
    gap = fact['ma_path']['confirmed_gap']
    assert gap['absolute_price'] == 12.5
    assert gap['change_price'] == 0
    assert gap['state'] == 'UNCHANGED'
    assert gap['consecutive_narrowing_bars'] == 0
    assert fact['confirmed']['convergence_bars'] > 0  # Original normalized meaning unchanged.


def test_compression_then_forming_widening_preserves_confirmed_duration():
    h, p = frames(NOW+300000)
    p['30m'] = replace_closes(p['30m'], [120])
    path = snapshot(h, p)['timeframes']['30m']['ma_path']
    confirmed, forming = path['confirmed_gap'], path['forming_gap']
    assert confirmed['state'] == 'UNCHANGED'
    assert confirmed['compression_bars'] == 16  # First SMA35 at historical bar35.
    assert confirmed['count_lower_bounds']['compression_bars'] is True
    assert confirmed['compression_threshold'] == .0015
    assert forming['state'] == 'WIDENING'
    assert forming['preceding_confirmed_compression_bars'] == 16
    assert forming['provisional'] is True
    assert path['duration_ms'] == 1800000
    assert path['forming_point']['observed_at_ms'] == NOW+300000
    assert path['available_components'] == ['confirmed', 'forming']
    assert path['source_history_count'] == 50


def test_forming_reversal_does_not_rewrite_confirmed_narrowing_counts():
    h, p = frames(NOW+300000)
    h['30m'] = replace_closes(h['30m'], list(range(100, 140))+[139]*10)
    a = snapshot(h, p)['timeframes']['30m']['ma_path']
    p['30m'] = replace_closes(p['30m'], [300])
    b = snapshot(h, p)['timeframes']['30m']['ma_path']
    assert a['confirmed_points'] == b['confirmed_points']
    assert a['confirmed_gap'] == b['confirmed_gap']
    assert b['confirmed_gap']['consecutive_narrowing_bars'] > 0
    assert b['forming_gap']['preceding_confirmed_narrowing_bars'] == b['confirmed_gap']['consecutive_narrowing_bars']
    assert b['forming_gap']['state'] == 'WIDENING'


def test_confirmed_widening_retains_preceding_narrowing_not_current_duration():
    h, p = frames(NOW+300000)
    h['30m'] = replace_closes(h['30m'], list(range(100, 140))+[139]*9+[300])
    gap = snapshot(h, p)['timeframes']['30m']['ma_path']['confirmed_gap']
    assert gap['state'] == 'WIDENING'
    assert gap['consecutive_narrowing_bars'] == 0
    assert gap['preceding_narrowing_bars'] == 9
    assert gap['count_lower_bounds']['preceding_narrowing_bars'] is False


def test_closed_breakout_preserves_preceding_compression_after_forming_widens():
    h, p = frames(NOW+300000)
    h['30m'] = replace_closes(h['30m'], [100]*49+[120])
    p['30m'] = replace_closes(p['30m'], [130])
    path = snapshot(h, p)['timeframes']['30m']['ma_path']
    gap = path['confirmed_gap']
    assert gap['state'] == 'WIDENING'
    assert gap['compression_bars'] == 0
    assert gap['preceding_compression_bars'] == 15
    assert gap['count_lower_bounds']['preceding_compression_bars'] is True
    assert path['forming_gap']['state'] == 'WIDENING'
    assert path['forming_gap']['preceding_confirmed_compression_bars'] == 0


def test_equality_and_bounded_json():
    result = snapshot()
    for fact in result['timeframes'].values():
        path = fact['ma_path']
        assert len(path['confirmed_points']) <= 3
        assert all(p['price_position']=='AT_BOTH' and p['ma_order']=='EQUAL' for p in path['confirmed_points'])
        assert len(json.dumps(path, allow_nan=False).encode()) < 3000
    json.dumps(result, allow_nan=False)


@pytest.mark.parametrize('fault', ['missing', 'stale', 'future'])
def test_missing_forming_does_not_destroy_valid_confirmed_path(fault):
    h, p = frames(NOW+300000)
    kwargs = {}
    if fault == 'missing':
        del p['30m']
    else:
        kwargs['observed'] = NOW+300000+(-120001 if fault=='stale' else 1)
    result = snapshot(h, p, **kwargs)
    path = result['timeframes']['30m']['ma_path']
    assert not result['valid']
    assert path['status'] == 'available'
    assert path['available_components'] == ['confirmed']
    assert path['forming_point'] is None and path['forming_gap'] is None


@pytest.mark.parametrize('fault', ['stale', 'gap', 'short', 'invalid'])
def test_invalid_confirmed_path_is_unavailable(fault):
    h, p = frames(NOW+300000)
    if fault == 'stale':
        h['30m'] = h['30m'].iloc[:-1]
    elif fault == 'gap':
        h['30m'] = h['30m'].drop(h['30m'].index[20])
    elif fault == 'short':
        h['30m'] = h['30m'].tail(35)
    else:
        h['30m'].iloc[-1, h['30m'].columns.get_loc('close')] = float('nan')
    path = snapshot(h, p)['timeframes']['30m']['ma_path']
    assert path['status'] == 'unavailable'
    assert path['confirmed_points'] == []


def test_future_history_cannot_change_path():
    h, p = frames(NOW+300000)
    original = snapshot(h, p)['timeframes']['30m']['ma_path']
    future = p['30m'].copy(deep=True)*100
    future.index += pd.Timedelta(days=1)
    h['30m'] = pd.concat([h['30m'], p['30m']*50, future])
    assert snapshot(h, p)['timeframes']['30m']['ma_path'] == original


def test_minimum_36_history_preserves_validity_without_faking_third_ma_point():
    h, p = frames(NOW+300000)
    h['30m'] = h['30m'].tail(36)
    result = snapshot(h, p)
    path = result['timeframes']['30m']['ma_path']
    assert result['valid']
    assert len(path['confirmed_points']) == 2
    assert path['valid_confirmed_ma_points'] == 2
    assert all(point['close_time_ms'] <= path['as_of_ms'] for point in path['confirmed_points'])


def test_later_closed_bar_cannot_rewrite_earlier_point_ma():
    h, p = frames(NOW+300000)
    h['30m'] = replace_closes(h['30m'], list(range(100, 150)))
    earlier = snapshot(h, p)['timeframes']['30m']['ma_path']['confirmed_points'][:2]
    h['30m'] = replace_closes(h['30m'], list(range(100, 149))+[300])
    assert snapshot(h, p)['timeframes']['30m']['ma_path']['confirmed_points'][:2] == earlier
