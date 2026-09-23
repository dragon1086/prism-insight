import copy
import json

from prism_core.dart_source_table_evidence import pack_readable_units
from prism_core.dart_source_tree_catalog import build_catalog, decode_table
from prism_core.dart_writer_context import render_dart_writer_context


def context(html, role='primary', end='2026-06-30'):
    units = build_catalog(html)['units']
    return {'sources': [{'source': {'source_id': 's', 'url': 'https://dart.fss.or.kr/x',
                                   'filing': {'role': role, 'period_start': end[:4] + '-01-01',
                                              'period_end': end, 'scope': 'consolidated',
                                              'section': 'financial_notes'}},
                         'core_paths': [u['path'] for u in units],
                         'catalog': pack_readable_units(units)}]}


def render(value):
    return render_dart_writer_context(json.dumps(value, ensure_ascii=False))


def test_small_kdb_table_individual_and_aggregate_preserved():
    data = context('<h2>차입금</h2><table><tr><th>차입처</th><th>잔액</th><th>조건</th></tr>'
                   '<tr><td>KDB</td><td>3,125</td><td>2026-02-10 상환</td></tr>'
                   '<tr><td>은행B</td><td>200,000</td><td>유동</td></tr>'
                   '<tr><td>은행C</td><td>300,000</td><td>비유동</td></tr>'
                   '<tr><td>합계</td><td>500,000</td><td></td></tr>'
                   '<tr><td>조정</td><td>0</td><td>(100)</td></tr>'
                   '<tr><td>주석</td><td>-10</td><td>별도 조건</td></tr></table>')
    before = copy.deepcopy(data)
    text, receipt = render(data)
    assert data == before
    assert '표 7행×3열' in text
    assert 'r1\t"KDB"\t"3,125"\t"2026-02-10 상환"' in text
    assert 'r4\t"합계"\t"500,000"\t""' in text
    assert '"0"\t"(100)"' in text and '"-10"' in text
    assert receipt['cell_count'] == 21 and receipt['cell_text_conserved']
    assert receipt['source_cells_sha256'] == receipt['rendered_cells_sha256']
    assert not receipt['semantic_verification'] and not receipt['truncated']


def test_kdb_column_specific_repayment_balance_and_note_actual_shape():
    html = ('<table><tr><th>항목</th><th>산업은행 장기차입금</th><th>차입금 합계</th></tr>'
            '<tr><td>기초</td><td>3,125</td><td>503,125</td></tr>'
            '<tr><td>상환</td><td>3,125</td><td></td></tr>'
            '<tr><td>기말</td><td></td><td>500,000</td></tr>'
            '<tr><td>유동</td><td></td><td>200,000</td></tr>'
            '<tr><td>비유동</td><td></td><td>300,000</td></tr>'
            '<tr><td>주석</td><td>2026-02-10 만기 상환</td><td></td></tr></table>')
    text, receipt = render(context(html))
    assert '표 7행×3열' in text
    assert 'c1: "산업은행 장기차입금" | c2: "차입금 합계"' in text
    assert 'r2\t"상환"\t"3,125"\t""' in text
    assert 'r3\t"기말"\t""\t"500,000"' in text
    assert 'r6\t"주석"\t"2026-02-10 만기 상환"\t""' in text
    original = next(u for u in build_catalog(html)['units'] if u['kind'] == 'table')
    expected = {(c['row'], c['col']): c['text']
                for c in decode_table(original['payload'], original['path'])['cells']}
    actual = {}
    for line in text.splitlines():
        fields = line.split('\t')
        if fields[0].startswith('r') and fields[0][1:].isdigit():
            for col, value in enumerate(fields[1:]):
                if value.startswith('"'):
                    actual[int(fields[0][1:]), col] = json.loads(value)
    assert actual == expected
    assert receipt['cell_count'] == len(actual) == 21


def test_multilevel_company_period_paths_and_merged_geometry():
    text, receipt = render(context('<table><tr><th rowspan="3">계정</th>'
        '<th colspan="2">개별회사</th><th colspan="2">합계</th></tr>'
        '<tr><th colspan="2">당기</th><th colspan="2">당기</th></tr>'
        '<tr><th>분기</th><th>누적</th><th>분기</th><th>누적</th></tr>'
        '<tr><td>매출</td><td>1</td><td>2</td><td>3</td><td>4</td></tr></table>'))
    assert 'c2: "개별회사" → "당기" → "누적"' in text
    assert 'c4: "합계" → "당기" → "누적"' in text
    assert '@0,0' in text and '@0,1' in text
    assert receipt['ambiguous_table_count'] == 0


def test_spanning_body_cell_does_not_assign_neighbor_column_header():
    text, _ = render(context('<table><tr><th>부문A</th><th>부문B</th></tr>'
                            '<tr><td colspan="2">합산 100</td></tr></table>'))
    assert '열 경로: c0: "부문A" | c1: "부문B"' in text
    assert 'r1\t"합산 100"\t@1,0' in text


def test_every_table_repeats_filing_period_not_inferred_cell_period():
    html = ('<p>회계연도 비교</p><table><tr><td>2024년</td><td>2025년</td></tr></table>'
            '<p>각주: 조건부 보장</p><table><tr><td>216,746</td><td>(91,558)</td></tr></table>')
    annual, receipt = render(context(html, 'annual_supplement', '2025-12-31'))
    assert annual.count('공시 대상기간 2025-01-01 ~ 2025-12-31') == 2
    assert '"2024년"\t"2025년"' in annual
    assert annual.index('회계연도 비교') < annual.index('각주: 조건부 보장') < annual.index('"216,746"')
    assert receipt['ambiguous_table_count'] == 2
    half, _ = render(context(html))
    assert half.count('공시 대상기간 2026-01-01 ~ 2026-06-30') == 2


def test_repeated_body_header_sparse_and_literal_markers_not_guessed():
    text, receipt = render(context('<table><tr><th>값</th><th>값</th></tr>'
        '<tr><td>~</td><td>@0,0</td></tr><tr><th>재표시</th><th>기간</th></tr>'
        '<tr><td>단일</td></tr></table>'))
    assert '머리글 미확정' in text
    assert '열 경로:' not in text
    assert '"~"\t"@0,0"' in text
    assert '"단일"\t~' in text
    assert receipt['cell_count'] == 7


def test_deterministic_receipt_counts_all_units_and_no_source_mutation():
    data = context('<h2>부채</h2><p>조건 원문</p><table><tr><td></td></tr></table>')
    a = render(data)
    assert a == render(data)
    assert a[1]['unit_count'] == len(build_catalog('<h2>부채</h2><p>조건 원문</p>'
                                                '<table><tr><td></td></tr></table>')['units'])
    assert a[1]['rendered_bytes'] == len(a[0].encode())
