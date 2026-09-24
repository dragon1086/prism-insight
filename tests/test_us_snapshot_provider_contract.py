"""Offline real-module provider contract; no provider or order calls."""
import importlib.util
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest


@pytest.fixture
def detector(monkeypatch):
    monkeypatch.setenv('US_DAILY_CACHE_ENABLED', 'false')
    root = Path(__file__).resolve().parents[1]
    original_path = sys.path[:]
    spec = importlib.util.spec_from_file_location(
        'snapshot_contract_detector', root / 'prism-us/cores/us_surge_detector.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, 'get_last_trading_day', lambda day: pd.Timestamp('2026-09-22').date())
    monkeypatch.setattr(module.yf, 'download', lambda *a, **kw: pytest.fail('Unexpected provider call'))
    yield module
    sys.path[:] = original_path


def history(shape='price_first', symbols=('AAA', 'BBB')):
    base = pd.DataFrame({'Open': [10., 11.], 'High': [13., 14.],
                         'Low': [9., 10.], 'Close': [12., 13.],
                         'Volume': [1000000., 2000000.]},
                        index=pd.to_datetime(['2026-09-22', '2026-09-23']))
    if shape == 'flat':
        return base
    frame = pd.concat({symbol: base * (index + 1) for index, symbol in enumerate(symbols)}, axis=1)
    return frame.swaplevel(axis=1) if shape == 'price_first' else frame


@pytest.mark.parametrize('shape', ['price_first', 'ticker_first', 'flat'])
def test_exact_date_and_reuses_one_download(detector, monkeypatch, shape):
    symbols = ['AAA'] if shape == 'flat' else ['AAA', 'BBB']
    raw = history(shape, symbols)
    calls = []
    monkeypatch.setattr(detector.yf, 'download', lambda *a, **kw: calls.append(kw) or raw)
    current = detector.get_snapshot('20260923', symbols)
    current.loc['AAA', 'Close'] = 999
    raw.iloc[0, :] = 999  # The reuse buffer owns a detached copy.
    previous, date = detector.get_previous_snapshot('20260923', symbols)
    assert date == '20260922' and previous.loc['AAA', 'Close'] == 12
    assert len(calls) == 1 and list(previous.index) == symbols
    assert previous.attrs['snapshot_coverage']['status'] == 'COMPLETE'
    json.dumps(previous.attrs, allow_nan=False)


def test_flat_multiple_symbols_is_not_attributed_to_first(detector, monkeypatch):
    monkeypatch.setattr(detector.yf, 'download', lambda *a, **kw: history('flat'))
    for frame in (detector.get_snapshot('20260923', ['AAA', 'BBB']),
                  detector.get_previous_snapshot('20260923', ['AAA', 'BBB'])[0]):
        assert frame.empty
        assert frame.attrs['snapshot_coverage']['reason_counts'] == {'ambiguous_flat_universe': 2}


@pytest.mark.parametrize('requested,expected', [('20260924', '20260924'), ('20260923', '20260922')])
def test_missing_exact_date_is_not_relabelled(detector, monkeypatch, requested, expected):
    raw = history().loc['2026-09-23':]
    monkeypatch.setattr(detector.yf, 'download', lambda *a, **kw: raw)
    result = (detector.get_snapshot(requested, ['AAA', 'BBB']) if requested.endswith('24')
              else detector.get_previous_snapshot(requested, ['AAA', 'BBB'])[0])
    assert result.empty
    assert result.attrs['snapshot_coverage']['requested_date'] == expected
    assert result.attrs['snapshot_coverage']['reason_counts'] == {'missing_exact_date': 2}


@pytest.mark.parametrize('column,value', [('Close', np.nan), ('High', np.inf), ('Open', 0), ('Low', -1), ('Volume', -1)])
def test_partial_invalid_ticker_preserves_valid_rows(detector, monkeypatch, column, value):
    raw = history()
    raw.loc[pd.Timestamp('2026-09-23'), (column, 'BBB')] = value
    monkeypatch.setattr(detector.yf, 'download', lambda *a, **kw: raw)
    result = detector.get_snapshot('20260923', ['AAA', 'BBB'])
    assert list(result.index) == ['AAA'] and result.loc['AAA', 'Amount'] == 26000000
    metadata = result.attrs['snapshot_coverage']
    assert metadata['status'] == 'PARTIAL' and metadata['missing_count'] == 1
    assert metadata['reason_counts'] == {'invalid_ohlcv': 1}
    json.dumps(result.attrs, allow_nan=False)


@pytest.mark.parametrize('variant', ['universe', 'order', 'date', 'expired', 'consumed'])
def test_reuse_rejects_mismatched_or_expired_key(detector, monkeypatch, variant):
    calls = []
    now = [10.]
    monkeypatch.setattr(detector.time, 'monotonic', lambda: now[0])
    monkeypatch.setattr(detector.yf, 'download', lambda *a, **kw: calls.append(a) or history())
    detector.get_snapshot('20260923', ['AAA', 'BBB'])
    symbols, date = ['AAA', 'BBB'], '20260923'
    if variant == 'universe':
        symbols = ['AAA']
    elif variant == 'order':
        symbols.reverse()
    elif variant == 'date':
        date = '20260924'
    elif variant == 'expired':
        now[0] += 61
    else:
        detector.get_previous_snapshot(date, symbols)
    detector.get_previous_snapshot(date, symbols)
    assert len(calls) == 2


def test_standalone_previous_downloads_once(detector, monkeypatch):
    calls = []
    monkeypatch.setattr(detector.yf, 'download', lambda *a, **kw: calls.append(a) or history('ticker_first'))
    previous, date = detector.get_previous_snapshot('20260923', ['AAA', 'BBB'])
    assert len(calls) == 1 and len(previous) == 2 and date == '20260922'


def test_empty_and_zero_volume_contract(detector, monkeypatch):
    monkeypatch.setattr(detector.yf, 'download', lambda *a, **kw: pd.DataFrame())
    empty = detector.get_snapshot('20260923', ['AAA'])
    assert list(empty.columns) == ['Open', 'High', 'Low', 'Close', 'Volume', 'Amount']
    assert empty.attrs['snapshot_coverage']['status'] == 'UNAVAILABLE'
    raw = history('flat')
    raw['Volume'] = 0
    monkeypatch.setattr(detector.yf, 'download', lambda *a, **kw: raw)
    assert detector.get_snapshot('20260923', ['AAA']).loc['AAA', 'Amount'] == 0


@pytest.mark.parametrize('problem', ['wrong_symbol', 'duplicate_column', 'duplicate_date'])
def test_ambiguous_provider_observations_rejected(detector, monkeypatch, problem):
    raw = history(symbols=('OTHER',)) if problem == 'wrong_symbol' else history('flat')
    if problem == 'duplicate_column':
        raw = pd.concat([raw, raw[['Close']]], axis=1)
    elif problem == 'duplicate_date':
        raw = pd.concat([raw, raw.iloc[[-1]]])
    monkeypatch.setattr(detector.yf, 'download', lambda *a, **kw: raw)
    result = detector.get_snapshot('20260923', ['AAA'])
    assert result.empty and result.attrs['snapshot_coverage']['missing_count'] == 1


def test_mismatch_does_not_consume_matching_cache(detector, monkeypatch):
    calls = []
    monkeypatch.setattr(detector.yf, 'download', lambda *a, **kw: calls.append(a) or history())
    detector.get_snapshot('20260923', ['AAA', 'BBB'])
    detector.get_previous_snapshot('20260923', ['AAA'])
    detector.get_previous_snapshot('20260923', ['AAA', 'BBB'])
    assert len(calls) == 2


def test_failed_refresh_does_not_leave_old_cache(detector, monkeypatch):
    monkeypatch.setattr(detector.yf, 'download', lambda *a, **kw: history())
    detector.get_snapshot('20260923', ['AAA', 'BBB'])

    def fail(*args, **kwargs):
        raise RuntimeError('provider unavailable')

    monkeypatch.setattr(detector.yf, 'download', fail)
    with pytest.raises(ValueError, match='Failed to get snapshot'):
        detector.get_snapshot('20260923', ['AAA', 'BBB'])
    with pytest.raises(ValueError, match='Failed to get previous snapshot'):
        detector.get_previous_snapshot('20260923', ['AAA', 'BBB'])
