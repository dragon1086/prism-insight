import pytest
from analysis.scenario_dataset import candles,digest


def test_bounded_pagination_no_future_rows():
    calls=[]
    def get(path,params):
        calls.append(params)
        return [["60000","100","102","99","101","4"],["0","99","101","98","100","3"]]
    result=candles('1',0,120000,get=get)
    assert [r['open_time'] for r in result]==[0,60000]
    assert calls[0]['end']==119999


def test_unrequested_future_candle_rejected():
    with pytest.raises(ValueError,match='outside_request'):
        candles('1',0,60000,get=lambda *a:[["60000","1","1","1","1","1"]])


def test_hash_is_order_independent_not_value_independent():
    assert digest({'a':1,'b':2})==digest({'b':2,'a':1})
    assert digest({'a':1})!=digest({'a':2})


def test_new_bundle_includes_weekly_prefix_and_all_seven_warmups():
    import pandas as pd
    from analysis.scenario_dataset import create_bundle, INTERVALS
    from engine.scenario_snapshot import candle_start
    start = int(pd.Timestamp('2026-10-11T12:00:00Z').timestamp()*1000)
    end = start+300_000
    calls = []
    durations = {'1': 60_000, **{v[0]: v[1] for v in INTERVALS.values()}}
    def get(path, params):
        calls.append((path, params))
        if path.endswith('/funding/history'):
            return [dict(symbol='BTCUSDT', fundingRateTimestamp=str(t), fundingRate='0.0001')
                    for t in range((params['startTime']+28_799_999)//28_800_000*28_800_000,
                                   params['endTime']+1, 28_800_000)]
        step = durations[params['interval']]
        last = candle_start(params['end'], step)
        return [[str(t), '100', '101', '99', '100', '1']
                for t in range(last, max(params['start']-1, last-1000*step), -step)]
    bundle = create_bundle(start, end, get=get)
    monday = candle_start(start, 604_800_000)
    assert bundle['source'][0]['open_time'] == monday
    assert bundle['mark'][0]['open_time'] == monday
    assert list(bundle['warmup']) == ['15m', '30m', '1h', '4h', '12h', '1d', '1w']
    weekly_calls = [p for _, p in calls if p.get('interval') == 'W']
    assert weekly_calls[0]['start'] == monday-60*604_800_000
    assert all(candle_start(row['open_time'], 604_800_000) == row['open_time']
               for row in bundle['warmup']['1w'])
    funding_call = next(p for path, p in calls if path.endswith('/funding/history'))
    assert funding_call['startTime'] == monday-86_400_000
