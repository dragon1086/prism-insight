"""General pre-cap note diversity without issuer, amount or polarity ranking."""
import copy
import json

import pytest

from prism_core import material_filing_selection as selection
from prism_core.report_insight_prefetch import packet


def block(family, index, *, qualified=False, specific=True):
    return {'topic': 'catalysts_risks_counterevidence',
            'excerpt': f'원문 {family} {index}', 'status': 'SOURCE_TEXT_NOT_FACT_VALIDATED',
            '_retrieval': {'specific_note': specific, 'qualified_claim': qualified,
                           'context_complete': True},
            'provenance': {'parser_version': 'material_v2', 'representation': 'DART_VIEWER_HTML',
                'representation_sha256': 'a' * 64, 'scope': 'consolidated',
                'section_path': ['3. 연결재무제표 주석', family],
                'kind': 'prose', 'source_path': f'/p[{index}]', 'source_paths': [f'/p[{index}]']}}


def test_late_specific_complete_note_is_ranked_before_repeated_family():
    blocks = [block('1. 담보자산', i) for i in range(30)]
    late = block('2. 약정사항', 31, qualified=True)
    blocks.append(late)
    original = copy.deepcopy(blocks)
    ordered = selection.order_material_html_blocks(blocks)
    assert ordered[0] is late
    assert blocks == original


def test_first_pass_covers_note_families_before_repeats():
    blocks = [block('1. 첫 주석', i, qualified=True) for i in range(30)]
    last = block('2. 다른 주석', 31, qualified=True)
    blocks.append(last)
    assert selection.order_material_html_blocks(blocks)[1] is last


def test_positive_negative_and_amount_changes_do_not_change_class():
    common = {'section_path': ['3. 연결재무제표 주석', '28. 약정사항'], 'scope': 'consolidated',
              'context_before': '당반기 단위: 백만원', 'kind': 'prose'}
    positive = {**common, 'text': '당반기 재무약정 위반이 없으며 최종 결과는 확정되지 않았습니다. 1백만원입니다.'}
    negative = {**common, 'text': '당반기 재무약정 위반이 있으며 최종 결과는 확정되지 않았습니다. 999백만원입니다.'}
    assert selection.html_retrieval_class(positive) == selection.html_retrieval_class(negative)


def test_packet_uses_general_order_before_fixed_frontier_and_keeps_it_bounded():
    rows = [block(f'{i}. 독립 주석', i, qualified=True) for i in range(25)]
    for b in rows[:24]:
        b['excerpt'] = 'x' * 6500
    rows[24]['excerpt'] = 'fit but outside the unchanged frontier'
    result = packet('KR', 'TEST', '2026-09-21', {'sources': [{'source_id': 'S', 'blocks': rows}],
                    'gaps': [], 'calls': 0})
    assert json.loads(result['section_notes']['news_analysis'])['sources'] == []


def test_packet_delivers_late_qualified_note_without_internal_rank_metadata():
    rows = [block('1. 같은 주석', i) for i in range(30)] + [block('2. 사건 주석', 31, qualified=True)]
    result = packet('KR', 'TEST', '2026-09-21', {'sources': [{'source_id': 'S', 'blocks': rows}],
                    'gaps': [], 'calls': 0})
    data = json.loads(result['section_notes']['news_analysis'])
    assert data['sources'][0]['excerpt'] == rows[-1]['excerpt']
    assert '_retrieval' not in result['section_notes']['news_analysis']
    assert all(len(n.encode()) <= 6000 for n in result['section_notes'].values())


def test_non_material_html_order_is_unchanged():
    rows = [block('1. 첫 주석', 1), block('2. 다른 주석', 2, qualified=True)]
    for b in rows:
        b['provenance']['parser_version'] = 'structured_v1'
    assert selection.order_material_html_blocks(rows) == rows


def test_distinct_explicit_subjects_within_one_note_get_first_pass():
    rows = [block('1. 우발사항', i, qualified=True) for i in range(30)]
    for b in rows:
        b['provenance']['context_before'] = '첫 독립 대상\n당반기\n단위: 원'
    last = block('1. 우발사항', 31, qualified=True)
    last['provenance']['context_before'] = '별개 독립 대상\n당반기\n단위: 원'
    assert selection.order_material_html_blocks([*rows, last])[1] is last


@pytest.mark.parametrize('state', ['uncertain', 'resolved', 'reversed', 'settled', 'not in breach'])
def test_explicit_outcome_states_have_equal_source_detail_class(state):
    common = {'kind': 'prose', 'scope': 'consolidated', 'section_path': ['28. Contingencies'],
              'context_before': 'Current period, USD'}
    baseline = selection.html_retrieval_class({**common,
        'text': 'The outcome remains uncertain at the end of the reporting period.'})
    assert selection.html_retrieval_class({**common,
        'text': f'The outcome remains {state} at the end of the reporting period.'}) == baseline


def test_amount_length_cannot_change_source_detail_priority():
    common = {'kind': 'prose', 'scope': 'consolidated', 'section_path': ['28. Contingencies'],
              'context_before': 'Current period, USD'}
    small = selection.html_retrieval_class({**common, 'text': 'The outcome is uncertain: liability $1.'})
    large = selection.html_retrieval_class({**common, 'text': 'The outcome is uncertain: liability $1000000.'})
    assert small == large


@pytest.mark.parametrize('footnote', [
    '조건의 이행 여부에 따라 금액이 변동될 수 있습니다.',
    '조건의 이행 여부에 따라 금액이 변동될 수 있습니다. (타 지분 포함: 100)',
    '반환 의무는 소멸되었습니다.(단위: 원)',
    '반환 의무는 남아 있습니다. (단위: 원) (범위: 연결)',
    'The covenant has been waived. (USD 1)',
    'The covenant remains uncertain.（USD 1000000）',
])
def test_intact_footnote_narrative_participates_in_retrieval(footnote):
    record = {'kind': 'table', 'scope': 'consolidated', 'section_path': ['28. 약정사항'],
              'context_before': '당반기 (단위: 원)', 'footnotes': footnote,
              'table': {'cells': [{'tag': 'td', 'text': '100'}]}}
    before = copy.deepcopy(record)
    assert selection.html_retrieval_class(record)['qualified_claim'] is True
    assert record == before


@pytest.mark.parametrize('footnote', [
    '한도 1.5 (원)', '1. 약정 조건', '약정조건은 충족했습니다. 다만 승인 조건은',
    '약정조건은 충족했습니다. (다만 승인 조건은',
    '조건입니다. (닫힘 오류）', '조건입니다. (외부 (내부) 부연)',
])
def test_unfinished_or_numeric_footnotes_do_not_get_narrative_class(footnote):
    record = {'kind': 'table', 'scope': 'consolidated', 'section_path': [],
              'footnotes': footnote, 'table': {'cells': [{'tag': 'td', 'text': '100'}]}}
    assert selection.html_retrieval_class(record)['qualified_claim'] is False
