import pandas as pd

from engine.scenario_snapshot import TIMEFRAME_MS, build_scenario_snapshot, candle_start
from live.scenario_analysis_frames import analysis_model_view


def market(count=192, gap=False):
    now = 1_800_000_000_000
    duration = TIMEFRAME_MS['30m']
    start = candle_start(now, duration)
    times = [start - i * duration for i in range(count, 0, -1)]
    if gap:
        times.pop(80)
    volumes = [float((t - (start - 192 * duration)) // (48 * duration) + 1) for t in times]
    frame = pd.DataFrame({'open': 100., 'high': 101., 'low': 99., 'close': 100., 'volume': volumes},
                         index=pd.to_datetime(times, unit='ms', utc=True))
    result = build_scenario_snapshot({'30m': frame.tail(99)}, now, volume_history_30m=frame)
    return result, start


def test_four_day_volume_summary_is_closed_compact_and_reaches_model_view():
    result, start = market()
    summary = result['timeframes']['30m']['volume_4d_30m']
    assert summary['medians_btc'] == [1., 2., 3., 4.]
    assert summary['overall_median_btc'] == 2.5
    assert summary['latest_closed_over_overall_median'] == 1.6
    assert summary['p90_btc'] == [1., 2., 3., 4.]
    assert summary['latest_closed_over_median'] == [4., 2., 1.333, 1.]
    assert summary['latest_closed_over_p90'] == [4., 2., 1.333, 1.]
    assert summary['window_end_ms'] == start
    assert len(result['timeframes']['30m']['recent_confirmed_bars']) == 20
    model = analysis_model_view({'market_snapshot': result, 'contract_context': {}})
    assert model['market_snapshot']['timeframes']['30m']['volume_4d_30m'] == summary


def test_short_or_gapped_history_is_never_claimed_as_four_days():
    for count, gap in [(191, False), (193, True)]:
        result, _ = market(count, gap)
        assert 'volume_4d_30m' not in result['timeframes']['30m']


def test_zero_p90_has_unknown_ratio():
    now = 1_800_000_000_000
    duration = TIMEFRAME_MS['30m']
    start = candle_start(now, duration)
    times = [start - i * duration for i in range(192, 0, -1)]
    frame = pd.DataFrame({'open': 100., 'high': 101., 'low': 99., 'close': 100.,
                          'volume': [0.] * 48 + [2.] * 144},
                         index=pd.to_datetime(times, unit='ms', utc=True))
    result = build_scenario_snapshot({'30m': frame.tail(99)}, now, volume_history_30m=frame)
    assert result['timeframes']['30m']['volume_4d_30m']['latest_closed_over_p90'] == [None, 1., 1., 1.]
    assert result['timeframes']['30m']['volume_4d_30m']['latest_closed_over_median'] == [None, 1., 1., 1.]
    frame.iloc[:48, frame.columns.get_loc('volume')] = 1e-320
    result = build_scenario_snapshot({'30m': frame.tail(99)}, now, volume_history_30m=frame)
    assert result['timeframes']['30m']['volume_4d_30m']['latest_closed_over_p90'][0] is None
    frame['volume'] = 0.
    result = build_scenario_snapshot({'30m': frame.tail(99)}, now, volume_history_30m=frame)
    assert result['timeframes']['30m']['volume_4d_30m']['latest_closed_over_overall_median'] is None


def test_optional_summary_falls_back_if_it_exceeds_model_limit(monkeypatch):
    from copy import deepcopy
    from live import scenario_calculated_review as review
    monkeypatch.setattr(review, '_base_model_payload', lambda snap, *args, **kwargs: deepcopy(snap))
    monkeypatch.setattr(review, 'explanation_enabled', lambda context: True)
    monkeypatch.setattr(review, 'project_explanation_input', lambda payload, *args, **kwargs: payload)
    summary = {'medians_btc': [1., 2., 3., 4.]}
    snap = {'timeframes': {'30m': {'volume_4d_30m': summary}}, 'padding': 'x' * 99940}
    payload = review._model_payload(snap, {}, {})
    assert 'volume_4d_30m' not in payload['timeframes']['30m']
    assert snap['timeframes']['30m']['volume_4d_30m'] == summary
    assert len(review._encoded(payload).encode()) <= 100_000


def test_optional_volume_history_does_not_change_existing_market_facts():
    result, _ = market()
    without = {**result, 'timeframes': dict(result['timeframes'])}
    without['timeframes']['30m'] = dict(result['timeframes']['30m'])
    without['timeframes']['30m'].pop('volume_4d_30m')
    now = 1_800_000_000_000
    duration = TIMEFRAME_MS['30m']
    start = candle_start(now, duration)
    times = [start - i * duration for i in range(99, 0, -1)]
    values = [float((t - (start - 192 * duration)) // (48 * duration) + 1) for t in times]
    frame = pd.DataFrame({'open': 100., 'high': 101., 'low': 99., 'close': 100., 'volume': values},
                         index=pd.to_datetime(times, unit='ms', utc=True))
    assert without == build_scenario_snapshot({'30m': frame}, now)


def test_collector_keeps_existing_99_bar_decision_history():
    from live.scenario_preview import collect_snapshot
    now = 1_800_000_300.
    received = {}
    def fetch(tf):
        duration = TIMEFRAME_MS[tf]
        start = candle_start(int(now * 1000), duration)
        count = 193 if tf == '30m' else 100
        rows = [[start - i * duration, 100., 101., 99., 100., float(i % 9 + 1), 0.]
                for i in range(count - 1, -1, -1)]
        received[tf] = rows
        return rows
    result = collect_snapshot(fetch=fetch, clock=lambda: now)
    frame = result['timeframes']['30m']
    assert 'volume_4d_30m' in frame
    rows = received['30m']
    def dataframe(data):
        df = pd.DataFrame(data, columns=['open_time', 'open', 'high', 'low', 'close', 'volume', 'turnover'])
        df.index = pd.to_datetime(df.pop('open_time'), unit='ms', utc=True)
        return df
    base = build_scenario_snapshot({tf: dataframe(values[-100:-1]) for tf, values in received.items()},
        int(now * 1000), provisional_tf_data={tf: dataframe(values[-1:]) for tf, values in received.items()},
        observed_at_ms=int(now * 1000))
    actual = dict(frame)
    actual.pop('volume_4d_30m')
    assert actual == base['timeframes']['30m']


def test_future_volume_cannot_leak_into_four_day_summary():
    now = 1_800_000_000_000
    duration = TIMEFRAME_MS['30m']
    start = candle_start(now, duration)
    times = [start - i * duration for i in range(192, 0, -1)]
    frame = pd.DataFrame({'open': 100., 'high': 101., 'low': 99., 'close': 100., 'volume': 5.},
                         index=pd.to_datetime(times, unit='ms', utc=True))
    before = build_scenario_snapshot({'30m': frame.tail(99)}, now, volume_history_30m=frame)
    future = frame.iloc[-1:].copy()
    future.index = pd.to_datetime([start], unit='ms', utc=True)
    future['volume'] = 1_000_000.
    after = build_scenario_snapshot({'30m': pd.concat([frame.tail(99), future])}, now,
        volume_history_30m=pd.concat([frame, future]))
    assert before['timeframes']['30m']['volume_4d_30m'] == after['timeframes']['30m']['volume_4d_30m']


def test_upper_tail_distinguishes_bursts_with_same_median():
    now = 1_800_000_000_000
    duration = TIMEFRAME_MS['30m']
    start = candle_start(now, duration)
    times = [start - i * duration for i in range(192, 0, -1)]
    frame = pd.DataFrame({'open': 100., 'high': 101., 'low': 99., 'close': 100., 'volume': 1.},
                         index=pd.to_datetime(times, unit='ms', utc=True))
    frame.iloc[-8:, frame.columns.get_loc('volume')] = 10.
    snap = build_scenario_snapshot({'30m': frame.tail(99)}, now, volume_history_30m=frame)
    stats = snap['timeframes']['30m']['volume_4d_30m']
    assert stats['medians_btc'][-1] == stats['medians_btc'][-2] == 1.
    assert stats['p90_btc'][-1] == 10. and stats['p90_btc'][-2] == 1.


def test_four_day_volume_view_is_for_flat_entry_only():
    from copy import deepcopy
    snapshot, _ = market()
    for ctx in ({'scenario_id': 'scenario'}, {'positions': [{'size': 1}]}):
        source = {'market_snapshot': snapshot, 'contract_context': ctx}
        frozen = deepcopy(source)
        result = analysis_model_view(source)
        assert 'volume_4d_30m' not in result['market_snapshot']['timeframes']['30m']
        assert source == frozen
    flat = analysis_model_view({'market_snapshot': snapshot, 'contract_context': {'scenario_id': None, 'positions': []}})
    assert 'volume_4d_30m' in flat['market_snapshot']['timeframes']['30m']
