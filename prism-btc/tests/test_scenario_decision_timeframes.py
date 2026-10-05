import json
import re

import pandas as pd

from engine.scenario_snapshot import TIMEFRAME_MS, build_scenario_snapshot


def test_decision_timeframes_exclude_five_minutes():
    assert list(TIMEFRAME_MS) == ['15m', '30m', '1h', '4h', '12h', '1d', '1w']


def test_weekly_candles_use_monday_utc_not_epoch_thursday():
    monday = pd.Timestamp('2026-10-05', tz='UTC')
    index = pd.date_range(end=monday-pd.Timedelta(weeks=1), periods=50, freq='7D')
    history = pd.DataFrame(dict(open=100., high=101., low=99., close=100., volume=10.), index=index)
    forming = history.tail(1).copy()
    forming.index = pd.DatetimeIndex([monday])
    now = int((monday+pd.Timedelta(hours=1)).timestamp()*1000)
    result = build_scenario_snapshot({'1w': history}, now, provisional_tf_data={'1w': forming}, observed_at_ms=now)
    week = result['timeframes']['1w']
    assert week['status'] == 'ok'
    assert week['forming']['elapsed_ms'] == 3_600_000
    assert week['forming']['remaining_ms'] == 601_200_000


def test_legacy_five_minute_input_cannot_leak_into_snapshot():
    from tests.test_scenario_snapshot import frames, NOW
    now = NOW+300_000
    h, p = frames(now)
    baseline = build_scenario_snapshot(h, now, provisional_tf_data=p, observed_at_ms=now)
    h['5m'] = h['15m'] * 9999
    p['5m'] = p['15m'] * 9999
    changed = build_scenario_snapshot(h, now, provisional_tf_data=p, observed_at_ms=now)
    assert changed == baseline
    assert not re.search(r'(?<![0-9])5m', json.dumps(changed))


def test_fifteen_minute_freshness_and_input_time_are_required():
    from tests.test_scenario_snapshot import frames, NOW
    from live.scenario_runtime import snapshot_input_time
    now = NOW+300_000
    h, p = frames(now)
    observed = {tf: now for tf in TIMEFRAME_MS}
    observed['15m'] = now-30_000
    result = build_scenario_snapshot(h, now, provisional_tf_data=p, observed_at_by_tf_ms=observed)
    assert result['valid']
    assert snapshot_input_time(result) == (now-30_000)/1000
    observed['15m'] = now-120_001
    assert not build_scenario_snapshot(h, now, provisional_tf_data=p, observed_at_by_tf_ms=observed)['valid']


def test_weekly_replay_uses_same_monday_grid_and_never_fills_missing_prefix():
    from backtest.scenario_data import HistoricalScenarioData
    monday = pd.Timestamp('2026-10-05', tz='UTC')
    index = pd.date_range(monday-pd.Timedelta(hours=40), monday+pd.Timedelta(minutes=30), freq='1min')
    source = pd.DataFrame(dict(open=100., high=101., low=99., close=100., volume=1.), index=index)
    warm_index = pd.date_range(end=monday-pd.Timedelta(weeks=1), periods=50, freq='7D')
    warm = pd.DataFrame(dict(open=100., high=101., low=99., close=100., volume=10.), index=warm_index)
    data = HistoricalScenarioData(source, source_interval_ms=60_000, warmup={'1w': warm})
    before = data.snapshot(int((monday-pd.Timedelta(minutes=5)).timestamp()*1000))
    assert before['timeframes']['1w']['forming'] is None
    at = data.snapshot(int(monday.timestamp()*1000))
    assert at['timeframes']['1w']['forming']['observation_kind'] == 'synthetic_boundary'
    after = data.snapshot(int((monday+pd.Timedelta(minutes=15)).timestamp()*1000))
    assert after['timeframes']['1w']['forming']['ohlcv']['volume'] == 15
    assert after['timeframes']['1w']['forming']['elapsed_ms'] == 900_000
