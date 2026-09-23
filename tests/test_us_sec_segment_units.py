"""SEC facts must carry proven monetary units and duration periods."""
import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest


@pytest.fixture
def module():
    path = Path(__file__).resolve().parents[1] / 'prism-us/cores/data_prefetch.py'
    spec = importlib.util.spec_from_file_location('sec_segment_test', path)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


def filing(value='33', unit='pure', scale='-2', concept='Revenues', extra=''):
    return f'''<html><body><x:context id="c"><x:entity><x:segment>
      <d:explicitMember dimension="srt:ProductOrServiceAxis">dgx:DiagnosticsMember</d:explicitMember>
      </x:segment></x:entity><x:period><x:startDate>2026-01-01</x:startDate>
      <x:endDate>2026-03-31</x:endDate></x:period></x:context>
      <x:unit id="u"><x:measure>{unit}</x:measure></x:unit>
      <ix:nonFraction name="us-gaap:{concept}" scale="{scale}" unitRef="u"
      contextRef="c" {extra}>{value}</ix:nonFraction></body></html>'''


def test_percentages_never_become_millions(module):
    assert module._parse_10k_segment_revenue(filing()) == ''


def test_original_dgx_percentage_shape_is_not_money(module):
    html = filing().replace('<x:', '<xbrli:').replace('</x:', '</xbrli:')
    html = html.replace('<d:', '<xbrldi:').replace('</d:', '</xbrldi:')
    html = html.replace('name="us-gaap:Revenues" scale="-2" unitRef="u"\n      contextRef="c"',
                        'contextRef="c" name="us-gaap:Revenues" unitRef="u" scale="-2"')
    assert module._parse_10k_segment_revenue(html) == ''


@pytest.mark.parametrize('value,scale,expected', [('33', '6', '$33,000,000'),
    ('33,000', '3', '$33,000,000'), ('33.5', '0', '$33.5'), ('0', '6', '$0')])
def test_money_scale_and_quarter_not_fy(module, value, scale, expected):
    text = module._parse_10k_segment_revenue(filing(value, 'iso4217:USD', scale))
    assert expected in text
    assert '2026-01-01' in text and '2026-03-31' in text and '90 days' in text
    assert 'FY2026' not in text


def test_sign_nested_markup_and_unknown_transform(module):
    text = module._parse_10k_segment_revenue(filing('<b>12.5</b>', 'iso4217:USD', '3', extra='sign="-"'))
    assert '-$12,500' in text
    assert module._parse_10k_segment_revenue(filing('12,5', 'iso4217:USD', '3', extra='format="ixt:num-comma-decimal"')) == ''


@pytest.mark.parametrize('value,extra', [
    ('1<ix:exclude>2</ix:exclude>', ''),
    ('1', 'continuedAt="continued"'),
    ('1<ix:continuation id="continued">2</ix:continuation>', ''),
    ('1<ix:nonFraction contextRef="c" name="us-gaap:Revenues" unitRef="u">2</ix:nonFraction>', ''),
])
def test_unsupported_inline_numeric_composition_is_omitted(module, value, extra):
    assert module._parse_10k_segment_revenue(filing(value, 'iso4217:USD', '0', extra=extra)) == ''


@pytest.mark.parametrize('extra', [
    '<x:unit id="u"><x:measure>xbrli:pure</x:measure></x:unit>',
    ('<x:context id="c"><x:period><x:startDate>2025-01-01</x:startDate>'
     '<x:endDate>2025-12-31</x:endDate></x:period></x:context>'),
    '<x:context id="c"><x:period><x:instant>2026-03-31</x:instant></x:period></x:context>',
])
@pytest.mark.parametrize('before', [True, False])
def test_duplicate_context_or_unit_ids_are_ambiguous(module, extra, before):
    html = filing('12', 'iso4217:USD', '6')
    html = html.replace('<body>', '<body>' + extra) if before else html.replace('</body>', extra + '</body>')
    assert module._parse_10k_segment_revenue(html) == ''


@pytest.mark.parametrize('unit,concept', [('iso4217:EUR', 'Revenues'), ('iso4217:USD', 'RevenueGrowthRate'),
    ('iso4217:USD', 'RevenueRemainingPerformanceObligation')])
def test_unsupported_unit_or_concept_omitted(module, unit, concept):
    assert module._parse_10k_segment_revenue(filing(unit=unit, concept=concept)) == ''


def test_missing_period_and_conflicting_duplicates_omitted(module):
    html = filing('12', 'iso4217:USD', '6')
    assert module._parse_10k_segment_revenue(html.replace('2026-01-01', 'invalid')) == ''
    fact = '<ix:nonFraction contextRef="c" name="us-gaap:Revenues" unitRef="u" scale="6">13</ix:nonFraction>'
    assert module._parse_10k_segment_revenue(html.replace('</body>', fact + '</body>')) == ''


def test_same_value_different_scales_deduplicated_and_multiple_axes_retained(module):
    html = filing('12', 'iso4217:USD', '6')
    fact = '<ix:nonFraction contextRef="c" name="us-gaap:Revenues" unitRef="u" scale="3">12,000</ix:nonFraction>'
    html = html.replace('</body>', fact + '</body>')
    html = html.replace('</x:segment>', '<d:explicitMember dimension="srt:ConsolidationItemsAxis">us-gaap:OperatingSegmentsMember</d:explicitMember></x:segment>')
    text = module._parse_10k_segment_revenue(html)
    assert text.count('$12,000,000') == 1
    assert 'ConsolidationItemsAxis' in text and 'ProductOrServiceAxis' in text


def test_currency_per_share_and_instant_facts_are_not_sales(module):
    html = filing('12', 'iso4217:USD', '6')
    divided = html.replace('<x:measure>iso4217:USD</x:measure>',
        '<x:divide><x:unitNumerator><x:measure>iso4217:USD</x:measure></x:unitNumerator>'
        '<x:unitDenominator><x:measure>xbrli:shares</x:measure></x:unitDenominator></x:divide>')
    assert module._parse_10k_segment_revenue(divided) == ''
    assert module._parse_10k_segment_revenue(html.replace('<x:startDate>2026-01-01</x:startDate>', '')) == ''


def test_latest_valid_filing_selected_and_download_bounded(module, monkeypatch):
    import urllib.request

    import yfinance
    records = [
        {'type': '10-K', 'date': '2026-02-15', 'exhibits': {'10-K': 'https://example.com/old'}},
        {'type': '10-Q', 'date': '2026-05-01', 'exhibits': {'10-Q': 'https://example.com/new'}},
        {'type': '10-Q', 'date': 'invalid', 'exhibits': {'10-Q': 'https://example.com/bad'}},
    ]
    monkeypatch.setattr(yfinance, 'Ticker', lambda _: SimpleNamespace(sec_filings=records))
    calls = []

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def read(self, size=-1):
            assert 0 < size <= 25_000_001
            return filing('12', 'iso4217:USD', '6').encode()

    def fetch(request, timeout):
        calls.append((request.full_url, timeout))
        return Response()

    monkeypatch.setattr(urllib.request, 'urlopen', fetch)
    text = module.prefetch_segment_revenue('DGX')
    assert calls == [('https://example.com/new', 30)]
    assert '10-Q' in text and '2026-05-01' in text


@pytest.mark.parametrize('url', ['file:///etc/passwd', 'http://example.com/file',
                                 'https:///missing-host', 'https://user:pass@example.com/file'])
def test_invalid_filing_url_is_never_opened(module, monkeypatch, url):
    import urllib.request

    import yfinance
    monkeypatch.setattr(yfinance, 'Ticker', lambda _: SimpleNamespace(sec_filings=[
        {'type': '10-Q', 'date': '2026-05-01', 'exhibits': {'10-Q': url}}]))

    def forbidden(*args, **kwargs):
        pytest.fail('Invalid source URL must not be opened')

    monkeypatch.setattr(urllib.request, 'urlopen', forbidden)
    assert module.prefetch_segment_revenue('DGX') == ''
