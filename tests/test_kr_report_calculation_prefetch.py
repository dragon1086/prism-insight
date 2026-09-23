"""Actual prefetch consumer: reuse exactly four responses, never issue extra calls."""

import copy
from types import SimpleNamespace

import pandas as pd
import pytest

from cores import data_prefetch
from cores.report_calculations import (
    compute_prefetched_report_metrics,
    render_report_metrics,
)


def responses():
    days = pd.bdate_range(end='2026-09-21', periods=220)
    prices = {day.strftime('%Y%m%d'): {'Open': i + 100, 'High': i + 105,
              'Low': i + 95, 'Close': i + 100, 'Volume': 1000}
              for i, day in enumerate(days)}
    flows = {day: {'기관합계': 2, '외국인합계': 3, '개인': -4} for day in prices}
    flows['__meta__'] = {'unit': 'shares'}
    return {'stock_ohlcv': prices, 'index_1001': copy.deepcopy(prices),
            'index_2001': copy.deepcopy(prices), 'trading_volume': flows}


def calculate(data):
    return compute_prefetched_report_metrics(data, ticker='017670',
        reference_date='20260921', asof_utc='2026-09-21T10:00:00Z')


def test_real_prefetch_reuses_four_calls_and_separates_market(monkeypatch):
    data = responses()
    original = copy.deepcopy(data)
    calls = []

    def response(key):
        calls.append(key)
        return data[key]

    server = SimpleNamespace(
        get_stock_ohlcv=lambda *args: response('stock_ohlcv'),
        get_stock_trading_volume=lambda *args: response('trading_volume'),
        get_index_ohlcv=lambda start, end, code: response('index_' + code))
    monkeypatch.setattr(data_prefetch, '_get_mcp_server_module', lambda: server)
    result = data_prefetch.prefetch_kr_analysis_data('017670', '20260921', '20250101',
                                                  asof_utc='2026-09-21T10:00:00Z')
    assert calls == ['stock_ohlcv', 'trading_volume', 'index_1001', 'index_2001']
    assert data == original
    facts = {item['id']: item for item in result['report_calculations']['facts']}
    assert facts['stock.sma.5']['value'] == 317
    assert facts['flow.5']['value']['institution_foreign'] == 25
    assert '코스피' not in result['report_calculation_reference']
    assert '코스피' in result['market_calculation_reference']
    assert '기관' not in result['market_calculation_reference']
    assert '017670' not in result['market_calculation_reference']
    assert result['flow_evidence'] in result['trading_volume']
    assert 'KR_FLOW_EVIDENCE_V1' in result['flow_evidence']
    assert 'KR_FLOW_EVIDENCE_V1' not in result['flow_evidence_public']
    assert 'MISSING' not in result['flow_evidence_public']
    assert '합계 25주' in result['flow_evidence_public']
    assert '계산 입력 해시' not in result['flow_evidence_public']
    for key in ('report_calculation_reference', 'market_calculation_reference'):
        for forbidden in ('UNKNOWN', 'MISSING', 'sha256', 'stock.sma', 'OHLCV'):
            assert forbidden not in result[key]


def test_market_text_independent_of_stock_values():
    data = responses()
    before = render_report_metrics(calculate(data), scope='market')
    data['stock_ohlcv'] = {}
    data['trading_volume'] = {}
    assert render_report_metrics(calculate(data), scope='market') == before


@pytest.mark.parametrize('bad', [{}, {'error': 'unavailable'}, {'not-a-date': {'Close': 4}},
                                {'20260921': {'Close': None}}, {'20260922': {'Close': 4}}])
def test_invalid_capture_never_fabricates_price(bad):
    data = responses()
    data['stock_ohlcv'] = bad
    facts = {item['id']: item for item in calculate(data)['facts']}
    assert facts['stock.latest']['value'] is None
    assert facts['index.1001.latest']['value']['Close'] == 319


def test_all_missing_is_not_a_diagnostic_dump():
    result = calculate({})
    assert render_report_metrics(result, scope='stock') == ''
    assert render_report_metrics(result, scope='market') == ''
