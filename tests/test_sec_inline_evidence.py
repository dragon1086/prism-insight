"""Offline adversarial inline-XBRL revenue contract."""
import json

import pytest

from prism_core.sec_inline_evidence import parse_inline_revenue


def document(facts='', contexts='', units='', *, cik='0000000123', start='2025-01-01', end='2025-03-31'):
    return f'''<html xmlns="http://www.w3.org/1999/xhtml"
      xmlns:i="http://www.xbrl.org/2013/inlineXBRL"
      xmlns:x="http://www.xbrl.org/2003/instance"
      xmlns:d="http://xbrl.org/2006/xbrldi"
      xmlns:g="http://fasb.org/us-gaap/2025"
      xmlns:co="https://example.org/company"
      xmlns:iso="http://www.xbrl.org/2003/iso4217"
      xmlns:t="http://www.xbrl.org/inlineXBRL/transformation/2020-02-12"
      xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"><body>
      <x:context id="c"><x:entity><x:identifier scheme="http://www.sec.gov/CIK">{cik}</x:identifier>
      <x:segment><d:explicitMember dimension="co:BusinessAxis">co:CloudMember</d:explicitMember></x:segment>
      </x:entity><x:period><x:startDate>{start}</x:startDate><x:endDate>{end}</x:endDate></x:period></x:context>
      <x:unit id="u"><x:measure>iso:EUR</x:measure></x:unit>{contexts}{units}{facts}</body></html>'''


def fact(value='1,234.5', attrs='', context='c'):
    return f'<i:nonFraction name="g:Revenues" unitRef="u" contextRef="{context}" decimals="-3" format="t:num-dot-decimal" {attrs}>{value}</i:nonFraction>'


def test_scale_sign_unit_period_and_provenance_not_guessed():
    result = parse_inline_revenue(document(fact('<b>1,234.5</b>', 'scale="3" sign="-"')), expected_cik='123')
    assert result['status'] == 'ok'
    f = result['facts'][0]
    assert f['value'] == '-1234500'
    assert f['unit'].endswith('}EUR')
    assert f['period'] == {'start': '2025-01-01', 'end': '2025-03-31'}
    assert f['decimals'] == '-3' and f['scale'] == 3
    assert f['dimensions'][0]['member'].endswith('}CloudMember')
    assert f['raw_value'] == '1,234.5' and f['source_line']
    json.dumps(result)


@pytest.mark.parametrize('attrs,value,reason', [
    ('xsi:nil="true"', '', 'nil_fact'),
    ('scale="999999"', '2', 'invalid_numeric'),
    ('sign="+"', '2', 'invalid_numeric'),
    ('', '1,23', 'invalid_numeric'),
    ('precision="3"', '2', 'invalid_accuracy'),
])
def test_invalid_or_missing_is_not_zero(attrs, value, reason):
    result = parse_inline_revenue(document(fact(value, attrs)))
    assert not result['facts']
    assert reason in {g['reason'] for g in result['gaps']}


def test_duplicates_collapse_but_conflicts_are_not_picked_by_maximum():
    assert len(parse_inline_revenue(document(fact('2') + fact('2')))['facts']) == 1
    result = parse_inline_revenue(document(fact('2') + fact('3')))
    assert result['facts'] == []
    assert result['gaps'][0]['reason'] == 'conflicting_facts'


def test_issuer_mismatch_and_missing_context_fail_closed():
    assert not parse_inline_revenue(document(fact()), expected_cik='456')['facts']
    assert not parse_inline_revenue(document(fact(context='missing')))['facts']


def test_accuracy_is_not_scaling():
    assert parse_inline_revenue(document(fact('2')))['facts'][0]['value'] == '2'


def test_unsupported_transformation_and_malformed_document_are_explicit():
    result = parse_inline_revenue(document(fact().replace('num-dot-decimal', 'unknown')))
    assert result['gaps'][0]['reason'] == 'unsupported_transform'
    assert parse_inline_revenue('<html>')['status'] == 'invalid'


def test_unknown_concepts_are_not_claimed_as_total_revenue():
    result = parse_inline_revenue(document(fact().replace('g:Revenues', 'co:RevenuePerCustomer')))
    assert not result['facts']


def test_multiple_issuers_require_expected_identity():
    other = '<x:context id="other"><x:entity><x:identifier scheme="http://www.sec.gov/CIK">456</x:identifier></x:entity><x:period><x:startDate>2025-01-01</x:startDate><x:endDate>2025-03-31</x:endDate></x:period></x:context>'
    html = document(fact('2') + fact('3', context='other'), contexts=other)
    assert not parse_inline_revenue(html)['facts']
    assert len(parse_inline_revenue(html, expected_cik='123')['facts']) == 1


def test_input_budget_is_explicit():
    assert parse_inline_revenue(' ' * (16 * 1024 * 1024 + 1))['status'] == 'too_large'


def test_real_us_consumer_retains_scopes_and_does_not_make_year_or_zero():
    import importlib.util
    from pathlib import Path
    path = Path(__file__).parents[1] / 'prism-us/cores/data_prefetch.py'
    spec = importlib.util.spec_from_file_location('inline_test_us_prefetch', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    rendered = module._parse_10k_segment_revenue(document(fact('2', 'scale="3"')))
    assert '2000' in rendered and '}EUR' in rendered
    assert '2025-01-01..2025-03-31' in rendered
    assert 'FY2025' not in rendered and 'millions USD' not in rendered
    assert 'CloudMember' in rendered and 'Source SHA256' in rendered


@pytest.mark.parametrize('change,reason', [
    (lambda s: s.replace('explicitMember', 'typedMember'), 'unsupported_dimension'),
    (lambda s: s.replace('2025-03-31', '2024-03-31'), 'invalid_duration'),
    (lambda s: s.replace('<x:measure>iso:EUR</x:measure>', '<x:divide/>'), 'unsupported_unit'),
])
def test_unsupported_context_and_unit_are_explicit(change, reason):
    result = parse_inline_revenue(change(document(fact())))
    assert not result['facts']
    assert reason in {g['reason'] for g in result['gaps']}


def test_exact_periods_do_not_merge_and_excludes_do_not_change_value():
    html = document(fact('2<i:exclude>not numeric</i:exclude>'))
    assert parse_inline_revenue(html)['facts'][0]['value'] == '2'
    assert parse_inline_revenue(document(fact('0.0')))['facts'][0]['value'] == '0'


def test_annual_and_quarterly_and_concepts_do_not_share_a_total():
    annual = '<x:context id="annual"><x:entity><x:identifier scheme="http://www.sec.gov/CIK">0000000123</x:identifier></x:entity><x:period><x:startDate>2024-04-01</x:startDate><x:endDate>2025-03-31</x:endDate></x:period></x:context>'
    result = parse_inline_revenue(document(fact('2') + fact('8', context='annual') + fact('3').replace('g:Revenues', 'g:SalesRevenueNet'), contexts=annual))
    assert len(result['facts']) == 3
    assert {f['period']['start'] for f in result['facts']} == {'2025-01-01', '2024-04-01'}


def test_qname_aliases_and_attribute_order_do_not_change_semantics():
    html = document(fact()).replace('xmlns:g=', 'xmlns:alias=').replace('g:Revenues', 'alias:Revenues')
    assert parse_inline_revenue(html)['facts'][0]['value'] == '1234.5'


def test_external_entity_or_duplicate_id_cannot_supply_numeric_evidence():
    html = '<!DOCTYPE html [<!ENTITY ex SYSTEM "file:///etc/passwd">]>' + document(fact('&ex;'))
    assert parse_inline_revenue(html)['status'] == 'invalid'
    result = parse_inline_revenue(document(fact(), units='<x:unit id="u"><x:measure>iso:USD</x:measure></x:unit>'))
    assert not result['facts']


def test_precision_is_accuracy_and_unsupported_comma_transform_is_not_guessed():
    html = document(fact('123.5')).replace('decimals="-3"', 'precision="4"')
    f = parse_inline_revenue(html)['facts'][0]
    assert f['value'] == '123.5' and f['precision'] == '4'
    result = parse_inline_revenue(document(fact('1.234,5')).replace('num-dot-decimal', 'num-comma-decimal'))
    assert not result['facts']


@pytest.mark.parametrize('form', ['10-Q', '10-K', '20-F', '40-F'])
def test_provider_consumer_is_bounded_and_labels_freshness_unverified(monkeypatch, form):
    import importlib.util
    import io
    import sys
    import urllib.request
    from pathlib import Path
    from types import SimpleNamespace
    from unittest.mock import Mock
    path = Path(__file__).parents[1] / 'prism-us/cores/data_prefetch.py'
    spec = importlib.util.spec_from_file_location('inline_provider_test', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    safe_url = 'https://cdn.yahoofinance.com/prod/sec-filings/123/000000012325000001/f.htm'
    filing = {'type': form, 'date': '2025-04-20', 'exhibits': {form: safe_url}}
    monkeypatch.setitem(sys.modules, 'yfinance', SimpleNamespace(Ticker=lambda _: SimpleNamespace(sec_filings=[filing])))
    opened = Mock(side_effect=lambda *a, **k: io.BytesIO(document(fact('2')).encode()))
    monkeypatch.setattr(urllib.request, 'build_opener', lambda *a: SimpleNamespace(open=opened))
    text = module.prefetch_segment_revenue('ANY')
    assert 'Official latest-filing status NOT verified' in text
    assert 'PROVIDER_PATH_ONLY_NOT_OFFICIAL' in text
    assert f'form={form}' in text and '2025-04-20' in text
    assert opened.call_args.kwargs['timeout'] == 30
    filing['exhibits'][form] = 'http://127.0.0.1/private'
    opened.reset_mock()
    assert 'unsupported provider URL' in module.prefetch_segment_revenue('ANY')
    opened.assert_not_called()
    filing['exhibits'][form] = safe_url
    for invalid_date in ('9999-12-31', 'invalid', ''):
        filing['date'] = invalid_date
        assert 'provider filing date' in module.prefetch_segment_revenue('ANY')
        opened.assert_not_called()
    filing['date'] = '2025-04-20'
    for event_form in ('6-K', '8-K'):
        filing['type'] = event_form
        filing['exhibits'] = {event_form: safe_url}
        assert module.prefetch_segment_revenue('ANY') == ''
        opened.assert_not_called()


def test_fact_limit_cannot_hide_a_conflicting_tail():
    result = parse_inline_revenue(document(fact('2') * 2000 + fact('3')))
    assert result['facts'] == []
    assert result['status'] != 'ok'
    assert 'fact_limit' in {gap['reason'] for gap in result['gaps']}


@pytest.mark.parametrize('change', [
    lambda text: text.replace(' id="c"', '').replace(' contextRef="c"', ''),
    lambda text: text.replace(' id="u"', '').replace(' unitRef="u"', ''),
    lambda text: text.replace('id="c"', 'id=""').replace('contextRef="c"', 'contextRef=""'),
    lambda text: text.replace('id="c"', 'id="bad id"').replace('contextRef="c"', 'contextRef="bad id"'),
    lambda text: text.replace('id="u"', 'id="c"').replace('unitRef="u"', 'unitRef="c"'),
])
def test_missing_invalid_or_cross_type_duplicate_ids_do_not_bind(change):
    result = parse_inline_revenue(change(document(fact())))
    assert not result['facts']


@pytest.mark.parametrize('wrapped', [
    '<i:tuple name="co:Group" tupleID="t">{}</i:tuple>',
    '<i:tuple name="co:Group" tupleID="t"><div>{}</div></i:tuple>',
    '<div target="other">{}</div>',
])
def test_tuple_or_target_ancestry_cannot_flatten_fact_scope(wrapped):
    assert not parse_inline_revenue(document(wrapped.format(fact('2', 'order="1"'))))['facts']


@pytest.mark.parametrize('attrs', ['', 'sign="-"'])
def test_untransformed_negative_lexical_value_is_invalid(attrs):
    html = document(fact('-2', attrs)).replace(' format="t:num-dot-decimal"', '')
    assert not parse_inline_revenue(html)['facts']


@pytest.mark.parametrize('value', [
    '1<i:nonNumeric name="co:Text" contextRef="c">2</i:nonNumeric>',
    '<span>1<i:nonNumeric name="co:Text" contextRef="c">2</i:nonNumeric></span>',
    '1' + fact('2', 'scale="3"'),
    '1' + fact('2', 'scale="3"').replace('g:Revenues', 'g:SalesRevenueNet'),
])
def test_nested_inline_fact_cannot_be_concatenated_or_flattened(value):
    assert not parse_inline_revenue(document(fact(value)))['facts']


@pytest.mark.parametrize('change', [
    lambda s: s.replace('</x:entity>', '<co:Scope>Other</co:Scope></x:entity>'),
    lambda s: s.replace('</x:context>', '<co:Scope>Other</co:Scope></x:context>'),
    lambda s: s.replace('</x:entity>', '<x:identifier scheme="other">other</x:identifier></x:entity>'),
])
def test_unknown_context_scope_is_not_silently_discarded(change):
    assert not parse_inline_revenue(change(document(fact())))['facts']


def test_consumer_requires_provider_path_body_identity(monkeypatch):
    import importlib.util
    from pathlib import Path
    path = Path(__file__).parents[1] / 'prism-us/cores/data_prefetch.py'
    spec = importlib.util.spec_from_file_location('inline_identity_test', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    output = module._parse_10k_segment_revenue(document(fact()), expected_cik='456')
    assert 'issuer_mismatch' in output and '1234.5' not in output


@pytest.mark.parametrize('sibling', [
    fact('', 'xsi:nil="true"'),
    fact('2').replace('num-dot-decimal', 'unknown'),
    fact('2', 'precision="3"'),
])
def test_invalid_duplicate_taints_only_its_semantic_group(sibling):
    annual = '<x:context id="annual"><x:entity><x:identifier scheme="http://www.sec.gov/CIK">0000000123</x:identifier></x:entity><x:period><x:startDate>2024-04-01</x:startDate><x:endDate>2025-03-31</x:endDate></x:period></x:context>'
    result = parse_inline_revenue(document(fact('2') + sibling + fact('8', context='annual'), contexts=annual))
    assert [f['value'] for f in result['facts']] == ['8']
    assert 'invalid_duplicate_group' in {g['reason'] for g in result['gaps']}


def test_canonical_source_path_survives_namespace_rebinding_and_comments():
    from lxml import etree
    nested = '<div xmlns:i="http://www.xbrl.org/2008/inlineXBRL"><!--before-->' + fact('3').replace('g:Revenues', 'g:SalesRevenueNet') + '</div>'
    html = document('<!--root-->' + fact('2') + nested + fact('2'))
    root = etree.fromstring(html.encode())
    result = parse_inline_revenue(html)
    assert len(result['facts']) == 2
    for item in result['facts']:
        source = root.xpath(item['source_path'])
        assert len(source) == 1 and source[0].text == item['raw_value']
        for duplicate in item['duplicate_sources']:
            duplicate_node = root.xpath(duplicate['source_path'])
            assert len(duplicate_node) == 1 and duplicate_node[0].text == item['raw_value']
