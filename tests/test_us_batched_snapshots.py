"""Both sessions from one chunk, exact dates and bounded partial collection."""
import importlib.util
import sys
from pathlib import Path

import pandas as pd
import pytest


@pytest.fixture
def provider(monkeypatch):
    original = sys.path[:]
    spec = importlib.util.spec_from_file_location(
        'batched_provider', Path(__file__).resolve().parents[1] /
        'prism-us/cores/us_surge_detector.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, 'get_last_trading_day',
                        lambda _: pd.Timestamp('2026-09-22').date())
    yield module
    sys.path[:] = original


def raw(symbols, shape):
    frame = pd.DataFrame({'Open': [10., 11.], 'High': [13., 14.],
                          'Low': [9., 10.], 'Close': [12., 13.],
                          'Volume': [1e6, 2e6]},
                         index=pd.to_datetime(['2026-09-22', '2026-09-23']))
    if shape == 'flat':
        return frame
    result = pd.concat({symbol: frame for symbol in symbols}, axis=1)
    return result.swaplevel(axis=1) if shape == 'price_first' else result


@pytest.mark.parametrize('shape', ['price_first', 'ticker_first'])
def test_chunks_decode_both_dates_once(provider, monkeypatch, shape):
    calls = []
    def download(symbols, **kwargs):
        calls.append((symbols, kwargs))
        return raw(symbols, shape)
    monkeypatch.setattr(provider.yf, 'download', download)
    current, previous, date, stats = provider.get_batched_snapshot_pair(
        '20260923', ['AAA', 'BBB', 'CCC', 'AAA'], batch_size=2)
    assert list(current.index) == list(previous.index) == ['AAA', 'BBB', 'CCC']
    assert date == '20260922'
    assert list(previous.Close) == [12., 12., 12.]
    assert len(calls) == stats['download_invocations'] == 2
    assert all(kwargs['threads'] == 2 for _, kwargs in calls)
    assert current.attrs['snapshot_coverage']['status'] == 'COMPLETE'


def test_ambiguous_flat_not_assigned_to_multiple_symbols(provider, monkeypatch):
    monkeypatch.setattr(provider.yf, 'download', lambda symbols, **kw: raw(symbols, 'flat'))
    current, previous, _, _ = provider.get_batched_snapshot_pair(
        '20260923', ['AAA', 'BBB', 'CCC'], batch_size=2)
    assert list(current.index) == list(previous.index) == ['CCC']
    assert current.attrs['snapshot_coverage']['reason_counts'] == {'ambiguous_flat_universe': 2}


def test_budget_exhaustion_reports_unattempted_symbols(provider, monkeypatch):
    monkeypatch.setattr(provider.time, 'monotonic', iter([0, 0, 601, 602]).__next__)
    monkeypatch.setattr(provider.yf, 'download', lambda symbols, **kw: raw(symbols, 'ticker_first'))
    current, previous, _, stats = provider.get_batched_snapshot_pair(
        '20260923', ['AAA', 'BBB'], batch_size=1)
    assert list(current.index) == ['AAA']
    assert previous.attrs['snapshot_coverage']['reason_counts'] == {'collection_budget_exhausted': 1}
    assert stats['download_invocations'] == 1


def test_error_and_missing_day_preserve_missing(provider, monkeypatch):
    def download(symbols, **kwargs):
        if symbols == ['AAA']:
            raise RuntimeError('private provider detail')
        return raw(symbols, 'ticker_first').loc['2026-09-23':]
    monkeypatch.setattr(provider.yf, 'download', download)
    current, previous, _, _ = provider.get_batched_snapshot_pair(
        '20260923', ['AAA', 'BBB'], batch_size=1)
    assert list(current.index) == ['BBB'] and previous.empty
    assert previous.attrs['snapshot_coverage']['reason_counts'] == {
        'provider_error': 1, 'missing_exact_date': 1}
