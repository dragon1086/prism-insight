"""Real report assembly uses the prefetched raw-price chart snapshot."""
import asyncio
import importlib.util
from types import SimpleNamespace

import pandas as pd
import pytest
import yfinance as yf
from test_us_evidence_pipeline_contract import (  # noqa: F401
    analysis,
    isolated_imports_and_effects,
)
from test_us_report_public_quote_inputs import load


def test_prefetch_exports_detached_display_frame(monkeypatch):
    prefetch = load('data_prefetch')
    source = pd.DataFrame({'close': [233.0]}, index=pd.DatetimeIndex(['2026-09-22']))
    source.attrs['price_basis'] = 'provider_unadjusted_close'
    monkeypatch.setattr(prefetch, '_get_us_data_client', lambda: SimpleNamespace(get_ohlcv=lambda *a, **kw: source))
    metadata = {}
    text = prefetch.prefetch_us_stock_ohlcv('TEST', metadata=metadata)
    exported = metadata['_report_ohlcv_frame']
    assert 'Close' in exported and exported.attrs['price_basis'] == 'provider_unadjusted_close'
    exported.iloc[0, 0] = 999
    assert source.iloc[0, 0] == 233 and '| SMA' in text


@pytest.mark.parametrize('snapshot_present', [True, False])
def test_real_report_chart_consumer_reuses_snapshot_or_explicit_raw_fallback(analysis, monkeypatch, snapshot_present):  # noqa: F811 - shared pytest fixture
    frame = pd.DataFrame({'Open': [232.], 'High': [234.], 'Low': [231.], 'Close': [233.], 'Volume': [100.]},
                         index=pd.DatetimeIndex(['2026-09-22'], name='Date'))
    prefetched = {'_report_ohlcv_frame': frame} if snapshot_present else {}
    original = analysis.importlib.util.spec_from_file_location

    class Loader:
        def create_module(self, spec):
            return None

        def exec_module(self, module):
            module.prefetch_us_analysis_data = lambda ticker: prefetched

    def spec_for(name, path, *args, **kwargs):
        if name == 'us_data_prefetch':
            return importlib.util.spec_from_loader(name, Loader())
        return original(name, path, *args, **kwargs)

    monkeypatch.setattr(analysis.importlib.util, 'spec_from_file_location', spec_for)
    history_calls = []

    def history(**kwargs):
        history_calls.append(kwargs)
        assert not snapshot_present, 'No second history fetch permitted'
        return frame.copy(deep=True)

    monkeypatch.setattr(yf, 'Ticker', lambda _: SimpleNamespace(history=history, major_holders=None, institutional_holders=None))
    seen = []

    def chart(ticker, company_name, hist, **kwargs):
        pd.testing.assert_frame_equal(hist, frame)
        seen.append(hist.copy(deep=True))
        hist.iloc[0, 0] = 999  # Consumers cannot mutate the canonical snapshot.
        return '<img />'

    monkeypatch.setattr(analysis, 'get_us_price_chart_html', chart)
    monkeypatch.setattr(analysis, 'get_us_technical_chart_html', chart)
    monkeypatch.setattr(analysis, 'get_us_institutional_chart_html', lambda *a, **kw: '')

    async def synthesis(*args, **kwargs):
        return 'SYNTHESIS'

    monkeypatch.setattr(analysis, 'generate_investment_strategy', synthesis)
    monkeypatch.setattr(analysis, 'generate_summary', synthesis)
    report = asyncio.run(analysis.analyze_us_stock('TEST', 'Example', '20260923', 'en', include_news=False))
    assert len(seen) == 2 and '<img />' in report
    assert frame.iloc[0, 0] == 232
    assert history_calls == ([] if snapshot_present else [{'period': '1y', 'auto_adjust': False}])
