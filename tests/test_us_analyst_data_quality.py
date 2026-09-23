"""Real-module report input tests; provider/network/model calls are mocked."""
import importlib.util
import runpy
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest
import yfinance as yf

ROOT = Path(__file__).resolve().parents[1]
PROPERTIES = ('earnings_estimate', 'revenue_estimate', 'eps_trend', 'eps_revisions',
              'growth_estimates', 'analyst_price_targets', 'recommendations_summary')


def load(relative):
    spec = importlib.util.spec_from_file_location('quality_' + Path(relative).stem, ROOT / relative)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def modules():
    return (load('prism-us/cores/data_prefetch.py'), load('prism-us/cores/us_data_client.py'),
            load('prism-us/cores/agents/company_info_agents.py'))


def provider(monkeypatch, values):
    calls = []

    class Stock:
        def __getattr__(self, name):
            calls.append(name)
            value = values.get(name)
            if isinstance(value, Exception):
                raise value
            return value

    monkeypatch.setattr(yf, 'Ticker', lambda ticker: Stock())
    return calls


def test_partial_estimates_keep_zero_negative_period_and_status(modules, monkeypatch):
    prefetch, _, _ = modules
    calls = provider(monkeypatch, {
        'earnings_estimate': pd.DataFrame({'avg': [0.0, -2.0, float('inf')]}, index=['0q', '+1y', '+5y']),
        'revenue_estimate': RuntimeError('private provider error'),
        'analyst_price_targets': {'current': 0, 'mean': None, 'high': float('nan'), 'low': -1, 'median': 123},
    })
    status = {}
    text = prefetch.prefetch_analysis_estimates('TEST', status=status)
    assert calls == list(PROPERTIES)
    assert status['status'] == 'partial'
    assert status['components']['revenue_estimate'] == 'error'
    assert status['components']['eps_trend'] == 'missing'
    assert '| 0q | 0.0 |' in text and '| +1y | -2.0 |' in text
    assert '| +5y | N/A |' in text
    assert '$123.00' in text and '$0.00' not in text and '$-1.00' not in text and '$None' not in text
    assert 'private provider error' not in text and 'Yahoo Finance / yfinance' in text
    assert 'Capture time (UTC)' in text and 'Estimate publication time: unavailable' in text


@pytest.mark.parametrize('error', [False, True])
def test_empty_and_failed_estimates_are_not_blank(modules, monkeypatch, error):
    prefetch, _, _ = modules
    provider(monkeypatch, {p: RuntimeError('no') if error else None for p in PROPERTIES})
    status = {}
    text = prefetch.prefetch_analysis_estimates('TEST', status=status)
    assert status['status'] == ('error' if error else 'missing')
    assert text and 'No usable analyst estimates' in text


def test_financial_defaults_and_ratio_units_preserve_real_zero(modules, monkeypatch):
    prefetch, client_module, _ = modules
    monkeypatch.setattr(yf, 'Ticker', lambda ticker: SimpleNamespace(info={
        'longName': 'Example', 'trailingEps': 0, 'profitMargins': 1.25,
        'returnOnEquity': -1.5, 'targetHighPrice': float('inf')}))
    client = client_module.USDataClient()
    info = client.get_company_info('TEST')
    assert info['forward_pe'] is None and info['earnings_per_share'] == 0
    assert info['price'] == 0 and info['market_cap'] == 0
    monkeypatch.setattr(prefetch, '_get_us_data_client', lambda: client)
    text = prefetch.prefetch_stock_info('TEST')
    assert '| Diluted EPS | 0.00 |' in text
    assert '| Profit Margin | 125.00% |' in text and '| ROE | -150.00% |' in text
    assert '| Target High | N/A |' in text


@pytest.mark.parametrize('stock', [False, True])
@pytest.mark.parametrize('language', ['ko', 'en'])
def test_company_status_reuses_partial_data_without_new_optional_gap_lookups(modules, stock, language):
    _, _, agents = modules
    data = {'analysis_estimates': 'PARTIAL_ESTIMATE_SENTINEL',
            'analysis_estimates_status': {'status': 'partial'}}
    if stock:
        data['stock_info'] = 'STOCK'
    urls = {k: 'https://example.test/' + k for k in ('key_statistics', 'financials', 'analysis')}
    agent = agents.create_us_company_status_agent('Example', 'TEST', '20260923', urls, language, data)
    assert 'PARTIAL_ESTIMATE_SENTINEL' in agent.instruction
    assert agent.server_names == ([] if stock else ['firecrawl', 'yahoo_finance'])
    assert urls['analysis'] not in agent.instruction
    if not stock:
        assert urls['key_statistics'] in agent.instruction
        assert 'yahoo_finance-get_stock_info' in agent.instruction


@pytest.mark.parametrize('status_value', ['missing', 'error'])
@pytest.mark.parametrize('with_metadata', [False, True])
def test_status_only_does_not_disable_original_fallback(modules, status_value, with_metadata):
    _, _, agents = modules
    data = {'stock_info': 'STOCK', 'analysis_estimates': f'### Analyst collection status: {status_value}\nNO_DATA'}
    if with_metadata:
        data['analysis_estimates_status'] = {'status': status_value}
    urls = {k: 'https://example.test/' + k for k in ('key_statistics', 'financials', 'analysis')}
    agent = agents.create_us_company_status_agent('Example', 'TEST', '20260923', urls, 'ko', data)
    assert agent.server_names == ['firecrawl'] and 'NO_DATA' in agent.instruction


def test_all_nan_metadata_only_and_boolean_values_are_not_usable(modules, monkeypatch):
    prefetch, _, _ = modules
    provider(monkeypatch, {
        'earnings_estimate': pd.DataFrame({'numberOfAnalysts': [10], 'avg': [float('nan')]}),
        'revenue_estimate': pd.DataFrame({'avg': [float('inf')]}),
        'eps_trend': pd.DataFrame({'current': [True]}),
        'analyst_price_targets': {'current': 100, 'currency': 'USD', 'mean': None},
    })
    status = {}
    text = prefetch.prefetch_analysis_estimates('TEST', status=status)
    assert status['status'] == 'missing' and 'No usable analyst estimates' in text


def test_seven_usable_properties_are_collected_once_and_marked_complete(modules, monkeypatch):
    prefetch, _, _ = modules
    values = {p: pd.DataFrame({'avg': [0.0]}, index=['+1y']) for p in PROPERTIES}
    values['analyst_price_targets'] = {'mean': 100}
    calls = provider(monkeypatch, values)
    status = {}
    text = prefetch.prefetch_analysis_estimates('TEST', status=status)
    assert calls == list(PROPERTIES)
    assert status['status'] == 'complete' and text.count('+1y') == 6
    assert status['estimate_published_at'] is None


@pytest.mark.parametrize('targets', [{'current': 100}, {'mean': 0}, {'mean': -1}, {'mean': True}])
def test_nonpositive_or_current_only_target_is_not_consensus(modules, monkeypatch, targets):
    prefetch, _, _ = modules
    provider(monkeypatch, {'analyst_price_targets': targets})
    status = {}
    prefetch.prefetch_analysis_estimates('TEST', status=status)
    assert status['status'] == 'missing'
    assert status['components']['analyst_price_targets'] == 'missing'


def test_real_prefetch_to_agent_preserves_status_without_duplicate_estimate_calls(modules, monkeypatch):
    prefetch, _, agents = modules
    calls = provider(monkeypatch, {'earnings_estimate': pd.DataFrame({'avg': [-3.0]}, index=['+1y'])})
    for name in ('prefetch_us_stock_ohlcv', 'prefetch_us_holder_info', 'prefetch_us_market_indices',
                 'prefetch_recommendations', 'prefetch_company_profile', 'prefetch_financial_statements',
                 'prefetch_segment_revenue'):
        monkeypatch.setattr(prefetch, name, lambda *args, **kwargs: '')
    monkeypatch.setattr(prefetch, '_get_us_data_client',
                        lambda: SimpleNamespace(get_company_info=lambda ticker: {'name': 'Example'}))
    data = prefetch.prefetch_us_analysis_data('TEST')
    assert isinstance(data['analysis_estimates'], str)
    assert data['analysis_estimates_status']['status'] == 'partial'
    urls = {k: 'https://example.test/' + k for k in ('key_statistics', 'financials', 'analysis')}
    agent = agents.create_us_company_status_agent('Example', 'TEST', '20260923', urls, 'en', data)
    assert '| +1y | -3.0 |' in agent.instruction and agent.server_names == []
    assert calls == list(PROPERTIES) + ['sec_filings']


def test_client_cli_missing_pe_prints_na_without_crashing(monkeypatch, capsys):
    monkeypatch.setattr(yf, 'Ticker', lambda ticker: SimpleNamespace(
        info={'longName': 'Example'}, history=lambda **kwargs: pd.DataFrame(),
        institutional_holders=None, mutualfund_holders=None, major_holders=None))
    runpy.run_path(str(ROOT / 'prism-us/cores/us_data_client.py'), run_name='__main__')
    output = capsys.readouterr().out
    assert 'P/E Ratio: N/A' in output and 'Test Complete' in output


def test_report_price_validity_and_zero_ratio_do_not_change_raw_quotes(modules, monkeypatch):
    prefetch, client_module, _ = modules
    monkeypatch.setattr(yf, 'Ticker', lambda ticker: SimpleNamespace(info={
        'longName': 'Example', 'currentPrice': -3, 'previousClose': -4,
        'fiftyTwoWeekHigh': -5, 'fiftyTwoWeekLow': 0,
        'fiftyDayAverage': -6, 'twoHundredDayAverage': -7,
        'marketCap': -8, 'enterpriseValue': -9, 'beta': 0, 'shortRatio': 0}))
    client = client_module.USDataClient()
    info = client.get_company_info('TEST')
    assert info['price'] == -3 and info['previous_close'] == -4 and info['market_cap'] == -8
    assert info['beta'] == info['short_ratio'] == 0
    monkeypatch.setattr(prefetch, '_get_us_data_client', lambda: client)
    text = prefetch.prefetch_stock_info('TEST')
    for label in ('Current Price', 'Previous Close', '52-Week High', '52-Week Low',
                  '50-Day Average', '200-Day Average', 'Market Cap'):
        assert f'| {label} | N/A |' in text
    assert '| Enterprise Value | $-9.00 |' in text
    assert '| Beta | 0.00 |' in text and '| Short Ratio | 0.00 |' in text
    monkeypatch.setattr(yf, 'Ticker', lambda ticker: SimpleNamespace(info={'longName': 'Example'}))
    missing = client.get_company_info('TEST')
    assert missing['beta'] is None and missing['short_ratio'] is None
    assert missing['price'] == missing['market_cap'] == 0
