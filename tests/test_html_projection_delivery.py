"""Partial cells are source-bound and never passed off as the whole table."""
import copy
import json

import pytest

from prism_core.filing_html import parse_filing_html
from prism_core.filing_report_evidence import _record_blocks
from prism_core.report_insight_prefetch import packet
from tools.evaluate_large_filing_html import audit_delivered_provenance


def setup():
    raw = ('<h3>3. 연결재무제표 주석</h3><p>28. 우발채무</p><p>단위: 백만원</p>'
           '<table><tr><th></th><th>분쟁 대상</th><th>독립적인 서비스</th></tr>'
           '<tr><td>금액</td><td>100</td><td>200</td></tr>'
           '<tr><td>설명</td><td>소송은 최종 판결 전이며 결과를 예측할 수 없습니다.</td><td>'
           + '독립적인 정기 서비스 운영 설명입니다. ' * 100 + '</td></tr></table>')
    parsed = parse_filing_html(raw)
    blocks, gaps = _record_blocks(parsed['records'], material_notes=True, representation='DART_VIEWER_HTML',
        digest=parsed['source_sha256'], md_hash=None, gaps=[], parsed=parsed)
    evidence = packet('KR', 'TEST', '2026-09-21', {'sources': [{'source_id': 'S', 'blocks': blocks}],
                    'gaps': gaps, 'calls': 0})
    note = json.loads(evidence['section_notes']['news_analysis'])
    return parsed, blocks, note


def test_oversized_independent_columns_are_explicitly_partial_and_source_exact():
    parsed, blocks, note = setup()
    projected = [b for b in blocks if b['provenance'].get('projected')]
    assert len(projected) == 1
    assert projected[0]['provenance']['excerpt_encoding'] == 'html_column_view_v1'
    assert projected[0]['provenance']['selected_columns'] == [0, 1]
    assert any(r['provenance'].get('projected') for r in note['sources'])
    assert not audit_delivered_provenance(blocks, note, parsed, source_id='S')


def test_partial_marker_removal_cannot_hide_omissions():
    parsed, blocks, note = setup()
    forged_blocks, forged_note = copy.deepcopy(blocks), copy.deepcopy(note)
    target = next(r for r in forged_note['sources'] if r['provenance'].get('projected'))
    for source in [target, next(b for b in forged_blocks if b['provenance'].get('projected'))]:
        source['provenance'].pop('projected')
        source['provenance'].pop('projection_kind')
    assert audit_delivered_provenance(forged_blocks, forged_note, parsed, source_id='S')


def test_mutated_view_mask_and_false_shape_fail_source_audit():
    parsed, blocks, note = setup()
    forged = copy.deepcopy(note)
    target = next(r for r in forged['sources'] if r['provenance'].get('projected'))
    target['provenance']['selected_columns'] = [0, 2]
    assert audit_delivered_provenance(blocks, forged, parsed, source_id='S')


def test_expired_common_deadline_discards_all_staged_evidence(monkeypatch):
    from prism_core import filing_report_evidence as adapter

    parsed, _, _ = setup()
    monkeypatch.setattr(adapter, 'monotonic', lambda: 11.0)
    blocks, gaps = adapter._record_blocks(parsed['records'], material_notes=True,
        representation='DART_VIEWER_HTML', digest=parsed['source_sha256'], md_hash=None,
        gaps=[], parsed=parsed, deadline=10.0)
    assert blocks == [] and 'FILING_EVIDENCE_TIME_LIMIT' in gaps


@pytest.mark.parametrize('field,value', [
    ('context_before', '개별재무제표 2024년 (단위: USD)'),
    ('footnotes', '모든 소송이 종결되었습니다.'),
    ('scope_context', {'scope': 'standalone'}),
])
def test_delivered_context_and_qualifier_text_cannot_be_forged(field, value):
    parsed, blocks, note = setup()
    target = next(r for r in note['sources'] if r['provenance'].get('projected'))
    target['provenance'][field] = value
    assert audit_delivered_provenance(blocks, note, parsed, source_id='S')


def test_matching_forged_blocks_cannot_change_original_footnote():
    parsed, blocks, note = setup()
    target = next(r for r in note['sources'] if r['provenance'].get('projected'))
    target['provenance']['footnotes'] = '모든 소송이 종결되었습니다.'
    next(b for b in blocks if b['provenance'].get('projected'))['provenance']['footnotes'] = target['provenance']['footnotes']
    assert audit_delivered_provenance(blocks, note, parsed, source_id='S')


def test_boolean_projection_metadata_is_not_equal_to_integer_coordinates():
    parsed, blocks, note = setup()
    for target in [next(r for r in note['sources'] if r['provenance'].get('projected')),
                   next(b for b in blocks if b['provenance'].get('projected'))]:
        target['provenance']['selected_columns'] = [False, True]
        target['provenance']['row_label_columns'] = True
    assert audit_delivered_provenance(blocks, note, parsed, source_id='S')


def test_preselection_ordering_shares_the_admission_deadline(monkeypatch):
    from prism_core import filing_report_evidence as adapter
    from prism_core import material_filing_selection as selection

    parsed, _, _ = setup()
    now = [0.0]
    monkeypatch.setattr(adapter, 'monotonic', lambda: now[0])
    original = selection.order_material_html_blocks

    def late_order(rows):
        ordered = original(rows)
        now[0] = 11.0
        return ordered

    monkeypatch.setattr(selection, 'order_material_html_blocks', late_order)
    blocks, gaps = adapter._record_blocks(parsed['records'], material_notes=True,
        representation='DART_VIEWER_HTML', digest=parsed['source_sha256'], md_hash=None,
        gaps=[], parsed=parsed, deadline=10.0)
    assert not blocks and 'FILING_EVIDENCE_TIME_LIMIT' in gaps
