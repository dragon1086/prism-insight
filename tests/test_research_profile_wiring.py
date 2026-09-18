"""Discovery context reuses existing provider calls without new lookups."""
import ast
import importlib.util
import socket
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def us_prefetch(monkeypatch):
    def no_network(*args, **kwargs):
        raise AssertionError('network access is forbidden')

    monkeypatch.setattr(socket.socket, 'connect', no_network)
    spec = importlib.util.spec_from_file_location(
        'research_profile_us_prefetch', ROOT / 'prism-us/cores/data_prefetch.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_stock_info_captures_only_descriptive_fields_from_single_lookup(us_prefetch, monkeypatch):
    info = {'name': 'Example', 'sector': 'Technology', 'industry': 'Optical components',
            'website': 'https://example.com', 'description': 'Makes optical components.',
            'revenue': 123456, 'market_cap': 987654, 'peers': ['MADEUP']}
    lookup = Mock(return_value=info)
    monkeypatch.setattr(us_prefetch, '_get_us_data_client',
                        lambda: SimpleNamespace(get_company_info=lookup))
    captured = {}
    rendered = us_prefetch.prefetch_stock_info('TEST', company_context=captured)
    lookup.assert_called_once_with('TEST')
    assert 'Company Info: Example (TEST)' in rendered
    assert captured == {key: info[key] for key in
                        ('name', 'sector', 'industry', 'website', 'description')}


def test_stock_info_legacy_string_return_is_unchanged(us_prefetch, monkeypatch):
    monkeypatch.setattr(us_prefetch, '_get_us_data_client',
                        lambda: SimpleNamespace(get_company_info=lambda ticker: {'name': 'Example'}))
    assert us_prefetch.prefetch_stock_info('TEST') == us_prefetch.prefetch_stock_info('TEST', {})


@pytest.mark.parametrize('info', [{}, {'name': 'Example', 'sector': None, 'industry': 123}])
def test_missing_profile_does_not_create_descriptive_values(us_prefetch, monkeypatch, info):
    monkeypatch.setattr(us_prefetch, '_get_us_data_client',
                        lambda: SimpleNamespace(get_company_info=lambda ticker: info))
    captured = {}
    us_prefetch.prefetch_stock_info('TEST', captured)
    assert captured == ({'name': 'Example'} if info else {})


def test_analysis_prefetch_preserves_markdown_and_reuses_existing_company_lookup(us_prefetch, monkeypatch):
    lookup = Mock(return_value={'name': 'Example', 'sector': 'Technology'})
    monkeypatch.setattr(us_prefetch, '_get_us_data_client',
                        lambda: SimpleNamespace(get_company_info=lookup))
    for name in ('prefetch_us_stock_ohlcv', 'prefetch_us_holder_info', 'prefetch_us_market_indices',
                 'prefetch_recommendations', 'prefetch_analysis_estimates',
                 'prefetch_financial_statements', 'prefetch_segment_revenue'):
        monkeypatch.setattr(us_prefetch, name, Mock(return_value=''))
    monkeypatch.setattr(us_prefetch, 'prefetch_company_profile', Mock(return_value='Original markdown profile'))
    result = us_prefetch.prefetch_us_analysis_data('TEST')
    lookup.assert_called_once_with('TEST')
    assert result['company_profile'] == 'Original markdown profile'
    assert result['company_research_profile'] == {'name': 'Example', 'sector': 'Technology'}
    assert isinstance(result['stock_info'], str)


@pytest.mark.parametrize('path,symbol', [('cores/analysis.py', 'company_code'),
                                       ('prism-us/cores/us_analysis.py', 'ticker')])
def test_both_analysis_callers_forward_prefetch_and_macro_context(path, symbol):
    tree = ast.parse((ROOT / path).read_text())
    calls = [node for node in ast.walk(tree) if isinstance(node, ast.Call)
             and isinstance(node.func, ast.Name) and node.func.id == 'prefetch_report_research']
    assert len(calls) == 1
    context = next(keyword.value for keyword in calls[0].keywords if keyword.arg == 'company_context')
    assert isinstance(context, ast.Call)
    assert context.func.id == 'company_research_context'
    assert [argument.id for argument in context.args] == ['prefetched', 'macro_context', symbol]
