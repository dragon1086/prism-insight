"""Canonical financial statements belong to financial analysis, not event news."""
import pytest

from prism_core.filing_html import parse_filing_html
from prism_core.filing_report_evidence import _record_blocks


def blocks(title, *, parent='2. 연결재무제표', material=True, body=None):
    table = body or '<table><tr><td>차입부채</td><td>100</td></tr><tr><td>충당부채</td><td>50</td></tr></table>'
    parsed = parse_filing_html(f'<h3>{parent}</h3><h4>{title}</h4>' + table)
    result, _ = _record_blocks(parsed['records'], material_notes=material, representation='DART_VIEWER_HTML',
        digest=parsed['source_sha256'], md_hash=None, gaps=[], parsed=parsed)
    return result


@pytest.mark.parametrize('title', [
    '2-1. 연결 재무상태표', '2-2. 연결포괄손익계산서', '2-3. 연결 자본변동표',
    '2-4. 연결 현금흐름표', '재무상태표', '별도 손익계산서', '개별포괄손익계산서',
    '4) 자본변동표 (별도)',
])
def test_explicit_statement_title_owns_its_entire_table(title):
    result = blocks(title)
    assert result and all(b['topic'] == 'financial_quality_valuation' for b in result)
    assert all(not b['provenance'].get('projected') for b in result)


@pytest.mark.parametrize('title', ['28. 충당부채', '28-1. 우발채무 및 약정사항', '재무상태표 관련 주석'])
def test_risk_note_below_statement_ancestor_keeps_risk_owner(title):
    result = blocks(title, parent='2. 연결 재무상태표')
    assert result and result[0]['topic'] == 'catalysts_risks_counterevidence'


def test_canonical_title_is_sufficient_without_risk_keywords():
    result = blocks('2-1. 연결 재무상태표', body='<table><tr><td>항목</td><td>100</td></tr></table>')
    assert result and result[0]['topic'] == 'financial_quality_valuation'


def test_legacy_non_material_route_and_original_source_are_preserved():
    old = blocks('2-1. 연결 재무상태표', material=False)[0]
    new = blocks('2-1. 연결 재무상태표', material=True)[0]
    assert old['topic'] == 'catalysts_risks_counterevidence'
    assert new['provenance']['source_path'] == old['provenance']['source_path']
    assert new['provenance']['representation_sha256'] == old['provenance']['representation_sha256']
    for field in ('scope', 'section_path', 'context_before', 'footnotes'):
        assert new['provenance'][field] == old['provenance'][field]
