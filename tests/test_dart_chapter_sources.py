import hashlib
import json

import pytest

from prism_core.dart_chapter_sources import (
    build_dart_chapter_inputs,
    enrich_dart_chapter_inputs,
)
from prism_core.dart_source_table_evidence import pack_readable_units
from prism_core.dart_specialist_roles import _role


def source(body, section='financial_notes', context=None):
    return {'source_id': 's1', 'html': body, 'sha256': hashlib.sha256(body.encode()).hexdigest(),
            'url': 'https://dart.fss.or.kr/report/viewer.do', 'scope_context': context,
            'filing': {'role': 'primary', 'section': section, 'scope': 'consolidated',
                       'period_start': '2026-01-01', 'period_end': '2026-06-30'}}


BODY = '''<h2>1. 차입금</h2><p>단위: 백만원. 만기는 2027년입니다.</p>
<table><tr><th rowspan="2">계정</th><th colspan="2">차입금</th></tr>
<tr><th>당기</th><th>전기</th></tr><tr><td>사채</td><td>100</td><td>90</td></tr></table>
<p>위반 시 조기상환하되 동의를 받은 경우 면제됩니다.</p>
<h2>2. 우발약정</h2><p>A사와 10년 약정 123억원, 이행 조건은 사용량입니다.</p>
<h2>3. 영업부문</h2><p>통신 매출 100억원입니다.</p>'''


def test_source_units_roundtrip_and_exactly_one_core_writer():
    packet = build_dart_chapter_inputs([source(BODY)])
    assert packet['ready']
    receipt = packet['receipt']
    assert receipt['core_conserved']
    assert receipt['core_union_sha256'] == receipt['delivered_core_union_sha256']
    paths = []
    for context in packet['contexts'].values():
        decoded = json.loads(context)
        assert 'Three-field rows inherit' in decoded['codec_guide']
        assert '[row_count,column_count,TSV_anchor_rows,spans,rowpaths,headers]' in decoded['codec_guide']
        assert 'source.filing contains' in decoded['codec_guide']
        assert 'source.scope_context' in decoded['codec_guide']
        assert 'Registry fields' not in decoded['codec_guide']
        assert 'COLUMN-ALIGNED TSV' in decoded['grid_guide']
        for group in decoded['sources']:
            assert group['source']['filing']['scope'] == 'consolidated'
            assert 'scope' not in group['source']
            paths.extend(group['core_paths'])
    assert len(paths) == len(set(paths)) == receipt['selected_core_units']
    assert '10년 약정 123억원' in packet['contexts']['risks']
    assert '면제됩니다' in packet['contexts']['finance']
    assert '통신 매출' in packet['contexts']['business']
    assert receipt['full_filing_coverage'] is False


def test_capacity_rejects_whole_transfer_without_silent_drop():
    p = build_dart_chapter_inputs([source(BODY)], writer_max_bytes=100)
    assert not p['ready'] and not p['contexts']
    assert p['receipt']['selected_core_units'] > 0
    assert not p['receipt']['capacity_ok']


def test_fragment_requires_parent_but_retains_actual_section_name():
    with pytest.raises(ValueError, match='parent context'):
        build_dart_chapter_inputs([source(BODY, 'financial_notes_fragment')])
    p = build_dart_chapter_inputs([source(BODY, 'financial_notes_fragment', {'basis': 'verified'})])
    assert p['ready']
    assert 'financial_notes_fragment' in p['contexts']['risks']


def test_unsupported_table_never_claims_readiness():
    p = build_dart_chapter_inputs([source('<h2>1. 우발약정</h2><table><tr><td colspan="9999">100</td></tr></table>')])
    assert not p['ready'] and p['receipt']['unsupported']


def test_digest_mismatch_and_duplicate_source_rejected():
    s = source(BODY)
    with pytest.raises(ValueError, match='duplicate'):
        build_dart_chapter_inputs([s, s])
    s['sha256'] = '0' * 64
    with pytest.raises(ValueError, match='digest'):
        build_dart_chapter_inputs([s])


def test_financial_statements_never_route_core_tables_to_business():
    body = '<h2>1. 일반사항</h2><table><tr><td>영업이익</td><td>123</td></tr></table>'
    p = build_dart_chapter_inputs([source(body, 'financial_statements')])
    assert p['ready'] and '123' in p['contexts']['finance']
    assert 'business' not in p['contexts']
    assert p['receipt']['role_inventory']['roles']['financial_performance']['assigned_units'] == 1


def test_hybrid_capital_and_trs_terms_route_intact_with_shared_inventory():
    body = ('<h2>1. 신종자본증권</h2><p>만기 30년, 이자 지급은 연기할 수 있습니다.</p>'
            '<h2>2. 파생상품 위험관리</h2><p>TRS 기초자산은 주식이며 수익률 보장 의무가 있습니다.</p>')
    p = build_dart_chapter_inputs([source(body)])
    assert '이자 지급은 연기' in p['contexts']['business']
    assert 'TRS 기초자산' in p['contexts']['risks']
    for context in p['contexts'].values():
        inventory = json.loads(context)['role_inventory']['roles']
        assert inventory['ownership_capital']['assigned_units'] == 1
        assert inventory['contingent_risks']['assigned_units'] == 1


@pytest.mark.parametrize('owner,title,label,role', [
    ('company_status', '연결재무제표', '재무상태표', 'debt_liquidity'),
    ('company_status', '연결재무제표', '자본변동표', 'accounting_valuation'),
    ('company_status', '현금흐름', '', 'financial_performance'),
    ('company_overview', '관계기업', '요약 재무정보', 'business_segments'),
    ('company_overview', '신종자본증권', '', 'ownership_capital'),
    ('news_analysis', '사업결합', '', 'corporate_events'),
    ('news_analysis', '파생상품', 'TRS', 'contingent_risks'),
])
def test_final_seven_role_classification_preserved(owner, title, label, role):
    assert _role(owner, title, label) == role


def _saved_packet(units, *, annual=False):
    group = {'source': {'source_id': 'fixture', 'filing': {
        'role': 'annual_supplement' if annual else 'primary',
        'period_end': '2025-12-31' if annual else '2026-06-30'}},
        'catalog': pack_readable_units(units)}
    return {'ready': True, 'receipt': {}, 'contexts': {'risks': json.dumps({'sources': [group]})}}


def test_actual_skt_573_wide_grid_values_remain_attached_to_original_labels():
    # Frozen source table573 geometry, not a financial interpretation fixture.
    payload = [5, 20, ['\t자산과 부채',
        '\t매각예정으로 분류된 비유동 자산이나 처분자산집단\t항공기 등 유형자산',
        '\t현금및현금성자산\t매출채권 및 기타채권\t선급금\t선급비용\t재고자산\t유형자산\t영업권 이외의 무형자산\t사용권자산\t금융상품\t이연법인세자산\t확정급여자산\t미지급금및기타채무\t예수금\t리스 부채\t계약부채\t충당부채\t기타부채\t당기법인세부채',
        '매각예정으로 분류된 비유동 자산이나 처분자산집단\t37,346\t25,810\t105\t1,694\t4,579\t10,584\t19,811\t3,132\t35,003\t4,921\t935\t\t\t\t\t\t\t\t11,970',
        '매각예정으로 분류된 처분자산집단에 포함된 부채\t\t\t\t\t\t\t\t\t\t\t\t34,609\t11,406\t2,281\t227\t305\t3,656\t1,533\t'],
        [[1, 1, 19], [3, 1, 18], [4, 2, 1]], ['groups', [['/thead/tr', 3], ['/tbody/tr', 2]]], 24]
    original = _saved_packet([{'path': '/html/body/table[573]', 'kind': 'table',
                               'payload': payload, 'context': []}])
    saved = json.dumps(original)
    p = enrich_dart_chapter_inputs(original)
    group = json.loads(p['contexts']['risks'])['sources'][0]
    rows = group['reading_aids']['wide_cells'][0][1]
    assert [3, 8, '사용권자산', '3,132'] in rows
    assert [3, 9, '금융상품', '35,003'] in rows
    assert [3, 10, '이연법인세자산', '4,921'] in rows
    assert [4, 12, '미지급금및기타채무', '34,609'] in rows
    assert not any(r[:2] == [3, 12] for r in rows)  # Genuine blank, not shifted liability.
    assert json.dumps(original) == saved
    assert enrich_dart_chapter_inputs(p) == p
    assert group['catalog'] == json.loads(original['contexts']['risks'])['sources'][0]['catalog']


def test_actual_annual_35_stamp_is_source_period_not_guessed_cell_period():
    payload = [3, 4, ['\t단기금융상품\t장기금융상품\t금융상품 합계',
        '사용이 제한된 금융자산\t90,163\t370\t90,533',
        '사용이 제한된 금융자산에 대한 설명\t\t\t공익신탁기금 등'], [],
        ['groups', [['/thead/tr', 1], ['/tbody/tr', 2]]], 4]
    p = enrich_dart_chapter_inputs(_saved_packet([{'path': '/html/body/table[35]',
        'kind': 'table', 'payload': payload, 'context': []}], annual=True))
    ctx = json.loads(p['contexts']['risks'])
    aids = ctx['sources'][0]['reading_aids']
    assert aids['annual_table_period'] == [[0, '2025-12-31']]
    assert aids['wide_cells'] == [] and aids['excluded']['narrow_tables'] == 1
    assert '각 셀의 회계기간을 확정하지 않습니다' in ctx['reading_aid_guide']


def test_reading_aids_repeat_headers_and_spanning_value_are_not_guessed():
    from prism_core.dart_source_tree_catalog import build_catalog
    html = ('<table><tr>' + ''.join(f'<th>{"같은명칭" if i < 2 else i}</th>' for i in range(16))
            + '</tr><tr><td colspan="2">50</td>' + '<td>7</td>' * 14 + '</tr></table>')
    p = enrich_dart_chapter_inputs(_saved_packet(build_catalog(html)['units']))
    rows = json.loads(p['contexts']['risks'])['sources'][0]['reading_aids']['wide_cells'][0][1]
    assert [1, 0, ['같은명칭', '같은명칭'], '50'] in rows
    assert [1, 2, '2', '7'] in rows


def test_reading_aid_capacity_failure_is_atomic():
    original = _saved_packet([])
    p = enrich_dart_chapter_inputs(original, writer_max_bytes=100)
    assert not p['ready'] and p['contexts'] == {}
    assert not p['receipt']['capacity_ok'] and original['ready']


def test_wide_td_only_labels_are_not_promoted_to_verified_headers():
    from prism_core.dart_source_tree_catalog import build_catalog
    html = '<table><tr>' + '<td>당기</td>' * 16 + '</tr><tr>' + '<td>1</td>' * 16 + '</tr></table>'
    p = enrich_dart_chapter_inputs(_saved_packet(build_catalog(html)['units']))
    aids = json.loads(p['contexts']['risks'])['sources'][0]['reading_aids']
    assert aids['wide_cells'] == []
    assert aids['excluded']['ambiguous_tables'] == [[0, 'NO_EXPLICIT_HEADER_BAND']]
