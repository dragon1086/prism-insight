"""Final synthesis receives exact facts without contaminating specialist caches."""
import copy
from unittest.mock import Mock

import pytest

from prism_core.kr_report_context import (
    market_cache_key,
    reference_context,
    render_flow_reference,
    synthesis_reference_context,
)


def packet():
    flow = {'asof_utc': '2026-09-24T00:12:34Z', 'windows': {
        '20': {'status': 'OK', 'start': '2026-08-25', 'end': '2026-09-23',
               'observed_sessions': 20, 'required_sessions': 20,
               'net_shares': {'foreign': -123456, 'institution': 234567, 'combined': 111111},
               'positive_sessions': {'foreign': 7, 'institution': 11, 'combined': 12},
               'trailing_positive_sessions_within_window': {'foreign': 1, 'institution': 3, 'combined': 2},
               'combined_pct_of_traded_shares': 0.12345678}}}
    return {
        'report_calculation_reference': 'STOCK: 12345.6789원, 2026-09-23, 20일',
        'market_calculation_reference': 'INDEX: 3456.7891포인트, 2026-09-23, 60일',
        'report_calculations': {'facts': [{'id': 'index.1001.sma.60', 'value': 3456.7891}]},
        'official_dart': {'public_receipt': 'DART: 2026년 반기 연결 987654천원'},
        'flow_evidence': 'RAW_FLOW_PRIVATE_DIAGNOSTICS',
        'flow_evidence_public': render_flow_reference(flow),
    }


@pytest.mark.parametrize('language', ['ko', 'en'])
def test_synthesis_preserves_all_exact_references(language):
    data = packet()
    before = copy.deepcopy(data)
    result = synthesis_reference_context(data, language)
    for key in ('report_calculation_reference', 'market_calculation_reference', 'flow_evidence_public'):
        assert data[key] in result
    assert data['official_dart']['public_receipt'] in result
    assert '-123456주' in result and '0.12345678%' in result
    assert '2026-08-25~2026-09-23' in result
    assert 'RAW_FLOW_PRIVATE_DIAGNOSTICS' not in result
    assert data == before


def test_market_only_context_and_cache_remain_ticker_free():
    data = packet()
    context = reference_context(data, market_only=True)
    assert data['market_calculation_reference'] in context
    assert 'STOCK:' not in context and 'DART:' not in context and '-123456주' not in context
    key = market_cache_key(data, '20260924', 'ko')
    synthesis_reference_context(data)
    data.update(report_calculation_reference='other stock', flow_evidence_public='other flow', official_dart={})
    assert market_cache_key(data, '20260924', 'ko') == key


def test_missing_flow_and_legacy_fallback_are_not_invented():
    data = packet()
    data.pop('flow_evidence_public')
    assert data['flow_evidence'] in synthesis_reference_context(data)
    data.pop('flow_evidence')
    assert '순매수' not in synthesis_reference_context(data)


@pytest.mark.parametrize('modern,cache_only,reused', [(False, False, False), (True, False, True), (False, True, True)])
def test_kr_service_real_same_day_cache_depth_contract(monkeypatch, tmp_path, modern, cache_only, reused):
    import report_generator as generator
    from prism_core import report_service as service

    reports = tmp_path / 'reports'
    pdfs = tmp_path / 'pdfs'
    reports.mkdir()
    pdfs.mkdir()
    monkeypatch.setattr(generator, 'REPORTS_DIR', reports)
    monkeypatch.setattr(generator, 'PDF_REPORTS_DIR', pdfs)
    content = '# Same-day company report\nValid basic analysis'
    if modern:
        content += '\n<!-- DART_DEEP_ANALYSIS_START -->\nDeep analysis\n<!-- DART_DEEP_ANALYSIS_END -->'
    md = reports / '000660_SK_20260924_analysis.md'
    md.write_text(content, encoding='utf-8')
    pdf = pdfs / md.with_suffix('.pdf').name
    pdf.write_bytes(b'existing PDF')
    # Explicitly make this a same-day cache test independent of test wall clock.
    monkeypatch.setattr(generator, '_is_current_kst_day', lambda _: True)
    generate = Mock(return_value='# Newly generated report')
    monkeypatch.setattr(service, 'generate_report_response_sync', generate)
    save_md = Mock(return_value=tmp_path / 'new.md')
    save_pdf = Mock(return_value=tmp_path / 'new.pdf')
    monkeypatch.setattr(service, 'save_report', save_md)
    monkeypatch.setattr(service, 'save_pdf_report', save_pdf)
    result = service.generate_report('000660', 'SK', cache_only=cache_only)
    assert result.succeeded and result.cached is reused
    assert md.read_text(encoding='utf-8') == content
    if reused:
        generate.assert_not_called()
        save_md.assert_not_called()
        save_pdf.assert_not_called()
    else:
        generate.assert_called_once_with('000660', 'SK')
        save_md.assert_called_once()
        save_pdf.assert_called_once()


def test_us_cache_signature_and_kr_evaluation_cache_miss(monkeypatch):
    from prism_core import report_service as service

    cache = Mock(return_value=(True, '# US report', None, None))
    monkeypatch.setattr(service, 'get_cached_us_report', cache)
    assert service.generate_report('AAPL', 'Apple', market='us').cached
    cache.assert_called_once_with('AAPL')
    miss = Mock(return_value=(False, '', None, None))
    generate = Mock(side_effect=AssertionError('cache-only must never generate'))
    monkeypatch.setattr(service, 'get_cached_report', miss)
    monkeypatch.setattr(service, 'generate_report_response_sync', generate)
    assert service.generate_report('000660', 'SK', cache_only=True).status == service.SKIPPED
    miss.assert_called_once_with('000660')
