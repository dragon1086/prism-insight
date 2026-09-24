import json

import pandas as pd
import pytest

from cores.market_data.remote_source import encode_result
from cores.report_calculations import compute_report_metrics, render_report_metrics


def captures(n=220):
    dates = pd.bdate_range(end='2026-09-21', periods=n)
    close = pd.Series(range(100, 100 + n), index=dates)
    frame = pd.DataFrame({'Open': close, 'High': close + 5, 'Low': close - 7,
                          'Close': close, 'Volume': 100}, index=dates)
    flow = pd.DataFrame({'기관합계': 2, '외국인합계': 3, '개인': -4}, index=dates)
    flow.attrs['unit'] = 'shares'
    return [{'capability': cap, 'ticker': ticker, 'params': {}, 'response': encode_result(data)}
            for cap, ticker, data in [('price_history', '017670', frame),
                                      ('index_history', '1001', frame),
                                      ('index_history', '2001', frame),
                                      ('investor_flows', '017670', flow)]]


def compute(rows):
    return compute_report_metrics(rows, ticker='017670', reference_date='2026-09-21',
                                  asof_utc='2026-09-21T10:00:00Z')


def facts(result):
    return {f['id']: f for f in result['facts']}


def test_metrics_and_distinct_ranges():
    result = compute(captures())
    f = facts(result)
    assert f['stock.sma.5']['value'] == 317
    assert f['stock.sma.120']['value'] == 259.5
    assert f['stock.range.20']['value'] == {
        'ohlc_high': 324, 'ohlc_low': 293, 'close_high': 319, 'close_low': 300}
    assert f['stock.rsi.14']['value'] == 100
    assert f['stock.latest']['value']['finality'] == 'unconfirmed'
    assert f['index.1001.sma.20']['value'] == 309.5
    json.dumps(result, allow_nan=False)


def test_flow_two_actor_sum_is_not_three_and_current_excluded():
    f = facts(compute(captures()))['flow.5']
    assert f['value'] == {'institution': 10, 'foreign': 15, 'individual': -20,
                          'institution_foreign': 25, 'all_three': 5}
    assert f['period']['end'] == '2026-09-18'


@pytest.mark.parametrize('mutation', ['duplicate', 'future', 'fractional', 'nonfinite'])
def test_invalid_price_fails_closed(mutation):
    rows = captures()
    data = rows[0]['response']['table']['data']
    if mutation == 'duplicate':
        data[-1]['index'] = data[-2]['index']
    elif mutation == 'future':
        data[-1]['index'] = '2026-09-22T00:00:00.000000000'
    elif mutation == 'fractional':
        data[-1]['Volume'] = 1.5
    else:
        data[-1]['Close'] = None
    assert facts(compute(rows))['stock.latest']['status'] == 'MISSING'


def test_insufficient_and_missing_capture():
    f = facts(compute(captures(10)))
    assert f['stock.sma.20']['value'] is None
    assert f['stock.macd.12_26_9']['value'] is None
    assert facts(compute([]))['stock.latest']['status'] == 'MISSING'


def test_timezone_and_duplicate_capture_rejected():
    with pytest.raises(ValueError, match='timezone'):
        compute_report_metrics([], ticker='1', reference_date='2026-09-21',
                               asof_utc='2026-09-21')
    rows = captures()
    rows.append(rows[0])
    assert facts(compute(rows))['stock.latest']['status'] == 'MISSING'


def test_fractional_flow_is_not_truncated_by_dataframe_decoder():
    rows = captures()
    rows[-1]['response']['table']['data'][-2]['개인'] = 0.1
    assert facts(compute(rows))['flow.5']['value'] is None


def test_public_render_has_units_and_no_internal_dump():
    text = render_report_metrics(compute(captures()))
    assert '기관+외국인' in text and '3주체' in text
    assert 'UNKNOWN' not in text and 'sha256' not in text and 'stock.sma' not in text


@pytest.mark.parametrize('symbol,segments,expected', [
    ('017670', [(60, 97041.66666666667), (55, 90730.90909090909), (5, 88020)],
     {5: 88020, 60: 90505, 120: 93773.33333333333}),
    ('011170', [(100, 75232), (20, 61215)], {20: 61215, 120: 72895.83333333333}),
])
def test_previous_report_arithmetic_regressions_synthetic(symbol, segments, expected):
    values = [value for count, value in segments for _ in range(count)]
    dates = pd.bdate_range(end='2026-09-21', periods=len(values))
    frame = pd.DataFrame({col: values for col in ('Open', 'High', 'Low', 'Close')}, index=dates)
    frame['Volume'] = 100
    rows = [{'capability': 'price_history', 'ticker': symbol, 'params': {}, 'response': encode_result(frame)}]
    f = facts(compute_report_metrics(rows, ticker=symbol, reference_date='2026-09-21',
                                    asof_utc='2026-09-21T10:00:00Z'))
    for n, expected_value in expected.items():
        assert f[f'stock.sma.{n}']['value'] == pytest.approx(expected_value)


def test_flat_rsi_and_band_position_are_explicit():
    rows = captures()
    for row in rows[0]['response']['table']['data']:
        for key in ('Open', 'High', 'Low', 'Close'):
            row[key] = 100
    f = facts(compute(rows))
    assert f['stock.rsi.14']['value'] == 50
    assert f['stock.bollinger.20']['value']['position_pct'] is None
    assert f['stock.macd.12_26_9']['value'] == {'line': 0, 'signal': 0, 'histogram': 0}


def test_future_or_naive_capture_timestamp_rejected():
    for timestamp in ['2026-09-22T01:00:00Z', '2026-09-21']:
        rows = captures()
        rows[0]['response']['attrs']['observed_at'] = timestamp
        assert facts(compute(rows))['stock.latest']['value'] is None


def test_missing_individual_or_wrong_unit_prevents_three_actor_claim():
    rows = captures()
    rows[-1]['response']['attrs']['unit'] = 'KRW'
    assert facts(compute(rows))['flow.5']['value'] is None
    rows = captures()
    rows[-1]['response']['table']['data'][-2]['개인'] = None
    assert facts(compute(rows))['flow.5']['value'] is None


def test_exactly_34_prices_required_for_macd_signal():
    assert facts(compute(captures(33)))['stock.macd.12_26_9']['value'] is None
    assert facts(compute(captures(34)))['stock.macd.12_26_9']['value'] is not None


def test_lossy_integer_schema_price_is_rejected():
    rows = captures()
    rows[0]['response']['table']['data'][-1]['Close'] = 318.5
    assert facts(compute(rows))['stock.latest']['value'] is None


@pytest.mark.parametrize('n', [5, 20])
@pytest.mark.parametrize('prefix', ['stock', 'index.1001', 'index.2001'])
def test_return_intervals_are_not_input_price_count(n, prefix):
    result = compute(captures())
    item = facts(result)[f'{prefix}.return.{n}']
    dates = pd.bdate_range(end='2026-09-21', periods=220).strftime('%Y-%m-%d')
    assert item['horizon_intervals'] == n
    assert item['input_price_count'] == n + 1
    assert item['baseline_price_date'] == dates[-n-1]
    assert item['window_start_date'] == dates[-n]
    assert item['end_date'] == dates[-1]
    assert item['period'] == {'start': dates[-n-1], 'end': dates[-1], 'observations': n + 1}
    assert item['value'] == (319 / (319 - n) - 1) * 100
    assert item['formula'] == f'(close[t]/close[t-{n}]-1)*100'
    line = next(line for line in render_report_metrics(result).splitlines()
                if item['label'] + ':' in line)
    assert f'수익률 {n}관측구간' in line
    assert f'가격 입력 {n + 1}개' in line
    assert f'{n + 1}관측일' not in line


def test_return_dates_use_actual_observations_across_missing_date_without_calendar_inference():
    rows = captures(8)
    data = rows[0]['response']['table']['data']
    del data[-4]
    actual = [pd.Timestamp(row['index']).date().isoformat() for row in data]
    item = facts(compute(rows))['stock.return.5']
    assert item['baseline_price_date'] == actual[-6]
    assert item['window_start_date'] == actual[-5]
    assert item['end_date'] == actual[-1]
    assert item['value'] == (data[-1]['Close'] / data[-6]['Close'] - 1) * 100


def test_short_return_history_does_not_invent_baseline_or_window_dates():
    item = facts(compute(captures(5)))['stock.return.5']
    assert item['status'] == 'MISSING'
    assert item['horizon_intervals'] == 5 and item['input_price_count'] == 5
    assert item['baseline_price_date'] is None and item['window_start_date'] is None
    assert item['end_date'] == '2026-09-21'


@pytest.mark.parametrize('n', [5, 20])
def test_return_and_flow_windows_do_not_claim_identical_dates(n):
    f = facts(compute(captures()))
    price, flow = f[f'stock.return.{n}'], f[f'flow.{n}']
    assert price['end_date'] == '2026-09-21'
    assert flow['period']['end'] == '2026-09-18'
    assert price['window_start_date'] != flow['period']['start']
    assert price['baseline_price_date'] == flow['period']['start']
