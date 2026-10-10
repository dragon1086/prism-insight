"""Arithmetic and timing contract; no live model or exchange calls."""
import copy
import json

import pytest

from engine.scenario_ma_context import build_ma_structure_context
from engine.scenario_snapshot import TIMEFRAME_MS, candle_start


NOW = 2_000_000_000_000


def point(frame, offset, close=100, fast=105, slow=95, confirmed=True):
    duration = TIMEFRAME_MS[frame]
    start = candle_start(NOW, duration) - (offset - 1) * duration
    return dict(open_time_ms=start, observed_at_ms=NOW - 1000,
                open=close, high=close + 1, low=close - 1, close=close,
                ma10=fast, ma35=slow, is_confirmed=confirmed)


def snapshot(frame='30m', before=None, after=None):
    before = before or point(frame, 2)
    after = after or point(frame, 1, confirmed=False)
    return dict(as_of_ms=NOW, timeframes={frame: {'ma_path': {
        'version': 1, 'confirmed_points': [before], 'forming_point': after}}})


def build(s):
    return build_ma_structure_context(s, {'now': NOW / 1000, 'mark_price': 100})


@pytest.mark.parametrize('previous,current,old_order,new_order', [
    ((95, 105), (105, 95), 'BEARISH', 'BULLISH'),
    ((105, 95), (95, 105), 'BULLISH', 'BEARISH'),
    ((100, 100), (105, 95), 'EQUAL', 'BULLISH'),
])
def test_order_cross_not_automatic_price_break(previous, current, old_order, new_order):
    s = snapshot(before=point('30m', 2, fast=previous[0], slow=previous[1]),
                 after=point('30m', 1, fast=current[0], slow=current[1], confirmed=False))
    transition = build(s)['primary']['30m']['transitions'][0]
    assert (transition['from_ma_order'], transition['to_ma_order']) == (old_order, new_order)
    assert transition['close_change_price'] == 0
    assert transition['provisional'] is True


@pytest.mark.parametrize('close,expected', [(90, 'BELOW'), (110, 'ABOVE')])
def test_band_entry_preserves_direction(close, expected):
    s = snapshot(before=point('30m', 2, close=close))
    t = build(s)['primary']['30m']['transitions'][0]
    assert (t['from_position'], t['to_position']) == (expected, 'BETWEEN')
    assert t['close_change_price'] == 100 - close


@pytest.mark.parametrize('fast,slow,state', [(108, 92, 'WIDENING'), (102, 98, 'NARROWING'), (105, 95, 'UNCHANGED')])
def test_raw_gap_not_price_denominator(fast, slow, state):
    s = snapshot(after=point('30m', 1, close=110, fast=fast, slow=slow, confirmed=False))
    assert build(s)['primary']['30m']['transitions'][0]['gap_state'] == state


def test_legacy_exact_join_and_unknown_history_no_mutation():
    p = point('30m', 2)
    forming = point('30m', 1, confirmed=False)
    s = dict(as_of_ms=NOW, timeframes={'30m': {
        'confirmed': {k: p[k] for k in ('open_time_ms', 'ma10', 'ma35')},
        'recent_confirmed_bars': [{k: v for k, v in p.items() if k not in ('ma10', 'ma35')}],
        'forming': {**forming, 'ohlcv': {k: forming[k] for k in ('open', 'high', 'low', 'close')}}}})
    original = copy.deepcopy(s)
    facts = build(s)['primary']['30m']
    assert facts['history_limited'] is True
    assert len(facts['points']) == 2
    assert facts['confirmed_gap'] is facts['forming_gap'] is None
    assert s == original
    s['timeframes']['30m']['recent_confirmed_bars'][0]['open_time_ms'] -= TIMEFRAME_MS['30m']
    assert len(build(s)['primary']['30m']['points']) == 1


def test_future_missing_synthetic_and_duplicate_observations_excluded():
    s = snapshot()
    path = s['timeframes']['30m']['ma_path']
    path['forming_point']['observed_at_ms'] = NOW + 1
    assert len(build(s)['primary']['30m']['points']) == 1
    path['confirmed_points'] *= 2
    assert build(s)['primary']['30m']['status'] == 'unavailable'
    assert build_ma_structure_context(s, {'now': NOW / 1000 - 1})['status'] == 'unavailable'


def test_higher_frame_levels_sorted_paired_and_basis_explicit():
    s = snapshot()
    for frame in ('4h', '12h', '1d', '1w'):
        s['timeframes'][frame] = snapshot(frame)['timeframes'][frame]
    result = build(s)
    assert len(result['long_upward_obstacles']) == 8
    assert len(result['short_downward_obstacles']) == 8
    assert len(result['levels']) == 16
    assert set(result['long_upward_obstacles'] + result['short_downward_obstacles']) == set(result['levels'])
    assert result['levels_price_basis'] == 'LAST_TRADE_OHLC_SMA'
    assert result['reference']['basis_difference_not_adjusted'] is True
    assert result['reference']['kind'] == 'CONTEXT_MARK_PRICE'
    levels = [result['levels'][key] for key in result['higher_frames']['4h']['level_ids']]
    assert levels[0]['same_line_group'] == levels[2]['same_line_group']
    assert levels[0]['as_of_ms'] != levels[2]['as_of_ms']
    assert {p['is_confirmed'] for p in levels} == {True, False}
    assert levels[0]['distance_price'] == 5
    assert len(json.dumps(result)) < 25000


def test_canonical_ma_labels_keep_line_phase_and_value_bound():
    s = snapshot('12h', before=point('12h', 2, close=84000, fast=84056.08, slow=84385.5),
                 after=point('12h', 1, close=84000, fast=83702, slow=84283.47, confirmed=False))
    original = copy.deepcopy(s)
    levels = build(s)['levels']
    expected = {
        '12h.ma10.confirmed': ('12h MA10 confirmed', 84056.08, True),
        '12h.ma35.confirmed': ('12h MA35 confirmed', 84385.5, True),
        '12h.ma10.forming': ('12h MA10 forming', 83702, False),
        '12h.ma35.forming': ('12h MA35 forming', 84283.47, False),
    }
    for key, (label, price, confirmed) in expected.items():
        assert levels[key]['label'] == label
        assert levels[key]['price'] == price
        assert levels[key]['is_confirmed'] is confirmed
        assert levels[key]['ma'] == key.split('.')[1]
    assert s == original


def test_equal_ma_prices_do_not_merge_canonical_identities():
    levels = build(snapshot('12h', before=point('12h', 2, fast=100, slow=100)))['levels']
    assert levels['12h.ma10.confirmed']['price'] == levels['12h.ma35.confirmed']['price']
    assert levels['12h.ma10.confirmed']['label'] != levels['12h.ma35.confirmed']['label']


def test_equal_levels_and_invalid_reference_do_not_fabricate_distances():
    s = snapshot('4h', before=point('4h', 2, fast=100), after=point('4h', 1, fast=100, confirmed=False))
    assert len(build(s)['equal_reference_levels']) == 2
    result = build_ma_structure_context(s, {'now': NOW / 1000, 'mark_price': float('nan')})
    assert result['reference'] is None
    assert result['long_upward_obstacles'] == []
    assert 'distance_price' not in result['levels'][result['higher_frames']['4h']['level_ids'][0]]
    assert result['higher_frames']['12h']['status'] == 'unavailable'


@pytest.mark.parametrize('fault', ['stale_confirmed', 'gap', 'stale_forming', 'unavailable'])
def test_stale_or_invalid_higher_history_is_not_current(fault):
    s = snapshot('4h')
    path = s['timeframes']['4h']['ma_path']
    if fault == 'stale_confirmed':
        path['confirmed_points'][0]['open_time_ms'] -= TIMEFRAME_MS['4h']
    elif fault == 'gap':
        path['confirmed_points'].insert(0, point('4h', 4))
    elif fault == 'stale_forming':
        path['forming_point']['observed_at_ms'] = NOW - 120001
    else:
        path['status'] = 'unavailable'
    result = build(s)
    levels = [result['levels'][key] for key in result['higher_frames']['4h']['level_ids']]
    if fault in ('stale_confirmed', 'gap'):
        assert len(levels) == 2 and all(not p['is_confirmed'] for p in levels)
    elif fault == 'stale_forming':
        assert len(levels) == 2 and all(p['is_confirmed'] for p in levels)
    else:
        assert levels == []


def test_mark_reference_never_invents_observation_time():
    result = build(snapshot())
    assert result['reference']['observed_at_seconds'] is None
    assert 'account_captured_at_seconds' not in result['reference']


def test_real_snapshot_producer_consumed_with_lower_bounds():
    from tests.test_scenario_snapshot import snapshot as real_snapshot
    s = real_snapshot()
    result = build_ma_structure_context(s, {'now': s['as_of_ms'] / 1000, 'mark_price': 104})
    for frame in ('15m', '30m', '1h'):
        facts = result['primary'][frame]
        assert len(facts['points']) == 4
        assert len(facts['transitions']) == 3
        assert facts['confirmed_gap']['count_lower_bounds']['compression_bars'] is True
        assert facts['source_history_count'] == 50
    for frame in ('4h', '12h', '1d', '1w'):
        assert len(result['higher_frames'][frame]['level_ids']) == 4


@pytest.mark.parametrize('metadata', [
    {'as_of_ms': NOW + 1}, {'as_of_ms': None}, {'duration_ms': 1},
    {'duration_ms': True}, {'version': 2}, {'version': True},
    {'as_of_ms': NOW - 2000},
])
def test_invalid_path_metadata_never_leaks_aggregate_or_levels(metadata):
    s = snapshot()
    path = s['timeframes']['30m']['ma_path']
    path.update(metadata, confirmed_gap={'compression_bars': 999, 'state': 'WIDENING'})
    facts = build(s)['primary']['30m']
    assert facts['status'] == 'unavailable'
    assert facts['confirmed_gap'] is facts['forming_gap'] is None


@pytest.mark.parametrize('fault', ['future_confirmed', 'duplicate_confirmed', 'future_forming'])
def test_bad_path_point_never_leaves_unverified_aggregate(fault):
    s = snapshot()
    path = s['timeframes']['30m']['ma_path']
    path.update(confirmed_gap={'compression_bars': 999}, forming_gap={'state': 'WIDENING'})
    if fault == 'future_confirmed':
        path['confirmed_points'].insert(0, point('30m', 0))
    elif fault == 'duplicate_confirmed':
        path['confirmed_points'] *= 2
    else:
        path['forming_point']['observed_at_ms'] = NOW + 1
    facts = build(s)['primary']['30m']
    assert facts['confirmed_gap'] is facts['forming_gap'] is None


def test_closed_expansion_retains_preceding_compression_from_producer():
    from tests.test_scenario_snapshot import frames, snapshot as real_snapshot, NOW as SNAPSHOT_NOW
    h, p = frames(SNAPSHOT_NOW + 300000)
    h['30m'].loc[h['30m'].index[-1], ['open', 'high', 'low', 'close']] = [120, 121, 119, 120]
    p['30m'].loc[:, ['open', 'high', 'low', 'close']] = [130, 131, 129, 130]
    s = real_snapshot(h, p)
    facts = build_ma_structure_context(s, {'now': s['as_of_ms'] / 1000})['primary']['30m']
    assert facts['confirmed_gap']['compression_bars'] == 0
    assert facts['confirmed_gap']['preceding_compression_bars'] == 15
    assert facts['confirmed_gap']['count_lower_bounds']['preceding_compression_bars'] is True
    assert facts['forming_gap']['state'] == 'WIDENING'


@pytest.mark.parametrize('sign', [1, -1])
def test_price_followthrough_and_small_gap_widening_are_separate_facts(sign):
    before = point('30m', 3, close=100, fast=100 + sign * 5, slow=100 - sign * 5)
    after = point('30m', 2, close=100 - sign, fast=100 + sign * 5.001, slow=100 - sign * 5)
    s = snapshot(before=after)
    s['timeframes']['30m']['ma_path']['confirmed_points'] = [before, after]
    original = copy.deepcopy(s)
    facts = build(s)['primary']['30m']
    t = facts['transitions'][0]
    assert t['gap_state'] == 'WIDENING'
    assert t['gap_change_fraction_of_previous_close'] == pytest.approx(.00001)
    assert t['close_change_price'] == t['high_change_price'] == t['low_change_price'] == -sign
    assert facts['points'][0]['high'] == before['high']
    assert facts['points'][1]['low'] == after['low']
    assert not t['provisional']
    assert s == original


def test_nearby_extrema_reference_confirmed_points_not_forming_or_swing_pivots():
    s = snapshot(before=point('30m', 2, close=102), after=point('30m', 1, close=100, confirmed=False))
    path = s['timeframes']['30m']['ma_path']
    path['confirmed_points'].insert(0, point('30m', 3, close=98))
    result = build(s)
    facts = result['primary']['30m']
    candidates = facts['nearby_confirmed_extrema']
    assert candidates == {'high_above_mark': 1, 'low_below_mark': 0, 'at_mark': []}
    assert facts['points'][1]['high'] == 103  # forming high=101 is deliberately excluded
    assert facts['points'][0]['low'] == 97
    assert facts['points'][1]['is_confirmed']
    assert result['extrema_interpretation'] == 'RECENT_BAR_EXTREMES_NOT_CONFIRMED_SWING_OR_SUPPORT_RESISTANCE'
    assert result['reference']['basis_difference_not_adjusted']
    assert facts['price_basis'] == 'LAST_TRADE_OHLC_SMA'
    assert facts['points'][1]['as_of_ms'] <= NOW


def test_extrema_equal_mark_missing_reference_and_latest_tie_are_explicit():
    s = snapshot(before=point('30m', 2, close=99))
    path = s['timeframes']['30m']['ma_path']
    path['confirmed_points'].insert(0, point('30m', 3, close=99))
    candidates = build(s)['primary']['30m']['nearby_confirmed_extrema']
    assert candidates['high_above_mark'] is None
    assert candidates['low_below_mark'] == 1
    assert candidates['at_mark'] == [{'point_index': 0, 'field': 'high'}, {'point_index': 1, 'field': 'high'}]
    assert build_ma_structure_context(s, {'now': NOW / 1000})['primary']['30m']['nearby_confirmed_extrema'] is None


@pytest.mark.parametrize('fault', ['future', 'gap', 'nan'])
def test_invalid_history_never_becomes_nearby_price_candidate(fault):
    s = snapshot()
    p = s['timeframes']['30m']['ma_path']['confirmed_points'][0]
    if fault == 'future':
        p['open_time_ms'] = candle_start(NOW, TIMEFRAME_MS['30m'])
    elif fault == 'gap':
        p['open_time_ms'] -= TIMEFRAME_MS['30m']
    else:
        p['high'] = float('nan')
    facts = build(s)['primary']['30m']
    assert facts['nearby_confirmed_extrema'] == {'high_above_mark': None, 'low_below_mark': None, 'at_mark': []}


@pytest.mark.parametrize('changes', [{'high': 99}, {'low': 101}, {'open': 102}])
def test_malformed_ohlc_is_not_a_price_obstacle(changes):
    s = snapshot()
    s['timeframes']['30m']['ma_path']['confirmed_points'][0].update(changes)
    facts = build(s)['primary']['30m']
    assert not any(p['is_confirmed'] for p in facts['points'])
    assert facts['nearby_confirmed_extrema'] == {'high_above_mark': None, 'low_below_mark': None, 'at_mark': []}
