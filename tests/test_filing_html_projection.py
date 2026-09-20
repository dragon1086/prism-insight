import copy
import json
import time

import pytest
from lxml import etree

from prism_core.filing_html_codec import _FIELDS, _legacy
from prism_core.filing_html_projection import (
    encode_html_column_view,
    expand_html_column_excerpt,
    project_html_columns,
)
from prism_core.filing_html_tables import parse_html_table


def record(headers=('Alpha', 'Equipment', 'Gamma'), bodies=None):
    bodies = bodies or ('소송은 종결되었으나 항소 시 변경 가능', '설비 감가상각', '법적 분쟁 진행 중')
    html = '<table><tr><th></th>' + ''.join(f'<th>{v}</th>' for v in headers) + '</tr>'
    for label, values in [('내용', bodies), ('단위', ['백만원'] * len(headers)),
                          ('조건', ['조건 전체를 보존'] * len(headers))]:
        html += '<tr><td>' + label + '</td>' + ''.join(f'<td>{v}</td>' for v in values) + '</tr>'
    table = parse_html_table(etree.fromstring(html + '</table>'))
    return {'kind': 'table', 'table': table, 'text': _legacy([[c[f] for f in _FIELDS] for c in table['cells']]),
            'footnotes': ['모든 금액에는 세금이 포함됨'], 'source': {'locator': 'table/x'}}


def test_retains_exact_cells_rows_context_and_source_immutability():
    source = record()
    before = copy.deepcopy(source)
    projected, diagnostics = project_html_columns(source)
    assert projected['selected_columns'] == [0, 1, 3]
    assert projected['table']['cells'] == [c for c in source['table']['cells'] if c['col'] != 2]
    assert projected['footnotes'] == source['footnotes']
    assert source == before
    assert diagnostics['source_cell_inspections'] <= 2 * len(source['table']['cells'])
    assert expand_html_column_excerpt(encode_html_column_view(projected)) == projected['text']


def test_symmetry_permutation_and_polarity():
    source = record(('A', 'B', 'C', 'D'), ('소송 종결', '설비', '소송 패소', 'covenant waiver'))
    projected, _ = project_html_columns(source)
    assert projected['selected_columns'] == [0, 1, 3, 4]
    source = record(('D', 'C', 'B', 'A'), ('covenant breach', '소송 승소', '설비', '소송 진행'))
    projected, _ = project_html_columns(source)
    assert projected['selected_columns'] == [0, 1, 2, 4]


@pytest.mark.parametrize('body', ['다른 열 참조', 'see other column', '상기 금액과 합산'])
def test_ambiguous_cross_references_refused(body):
    assert project_html_columns(record(bodies=('소송 ' + body, '설비', '기타')))[0] is None


def test_td_header_cross_span_and_deadline_refused():
    source = record()
    source['table']['cells'][0]['tag'] = 'td'
    assert project_html_columns(source)[0] is None
    source = record()
    source['table']['cells'][1]['colspan'] = 2
    assert project_html_columns(source)[0] is None
    assert project_html_columns(record(), deadline=time.monotonic() - 1)[1]['code'] == 'PROJECTION_DEADLINE'


@pytest.mark.parametrize('mutation', [
    lambda v: v.update(extra=1),
    lambda v: v.update(selected_columns=[0, 1, 1, 3]),
    lambda v: v.update(selected_columns=[True, 1, 3]),
    lambda v: v.update(selected_columns=[1, 3]),
    lambda v: v.update(row_label_columns=True),
    lambda v: v['cells'].pop(),
    lambda v: v['cells'].append(v['cells'][0]),
    lambda v: v['cells'][1].__setitem__(3, 2),
])
def test_codec_fail_closed(mutation):
    projected, _ = project_html_columns(record())
    value = json.loads(encode_html_column_view(projected))
    mutation(value)
    with pytest.raises(ValueError, match='INVALID_HTML_COLUMN_VIEW'):
        expand_html_column_excerpt(json.dumps(value))


@pytest.mark.parametrize('change', [{'context_incomplete': False}, {'layout_role': 'data'}])
def test_context_and_layout_refused(change):
    assert project_html_columns({**record(), **change})[0] is None


def test_no_theme_or_all_theme_does_not_crop():
    assert project_html_columns(record(bodies=('추정', '충당부채', '설비')))[1]['code'] == 'NO_THEME_COLUMNS'
    assert project_html_columns(record(bodies=('소송', '소송', '소송')))[1]['code'] == 'ALL_COLUMNS_REQUIRED'


def test_midpass_expiry_has_no_partial_result(monkeypatch):
    import prism_core.filing_html_projection as projection
    calls = iter([0] * 8 + [11] * 100)
    monkeypatch.setattr(projection.time, 'monotonic', lambda: next(calls))
    result, diagnostics = project_html_columns(record(), deadline=10)
    assert result is None
    assert diagnostics['code'] == 'PROJECTION_DEADLINE'
    assert diagnostics['selected_columns'] == []


def test_codec_rejects_duplicate_keys_missing_rows_and_large_allocation():
    projected, _ = project_html_columns(record())
    text = encode_html_column_view(projected)
    with pytest.raises(ValueError, match='INVALID_HTML_COLUMN_VIEW'):
        expand_html_column_excerpt(text.replace('"schema":', '"schema":"duplicate","schema":', 1))
    value = json.loads(text)
    value['cells'] = [c for c in value['cells'] if c[0] != 2]
    with pytest.raises(ValueError, match='INVALID_HTML_COLUMN_VIEW'):
        expand_html_column_excerpt(json.dumps(value))
    projected['table']['cells'] *= 12001
    with pytest.raises(ValueError, match='INVALID_HTML_COLUMN_VIEW'):
        encode_html_column_view(projected)


def test_blank_corner_boundary_and_shared_header_not_seed():
    source = record()
    source['table']['cells'][0]['text'] = '소송'
    source['text'] = _legacy([[c[f] for f in _FIELDS] for c in source['table']['cells']])
    assert project_html_columns(source)[1]['code'] == 'AMBIGUOUS_LABEL_BOUNDARY'
    source = record(bodies=('설비', '설비', '설비'))
    for cell in source['table']['cells']:
        if cell['col'] == 0 and cell['row'] > 0:
            cell['text'] = '법적 소송'
    source['text'] = _legacy([[c[f] for f in _FIELDS] for c in source['table']['cells']])
    assert project_html_columns(source)[1]['code'] == 'NO_THEME_COLUMNS'


@pytest.mark.parametrize('marker', ['주1', '주석1', '(주1)', '(*1)', 'note1', 'Note 2', 'footnote 3', '※1', '①'])
def test_unresolved_note_reference_never_drops_condition(marker):
    source = record(bodies=(f'소송은 {marker}의 조건에 따라 결정됩니다.',
                            f'{marker}: 지급기일은 보증회사 부담 여부에 따라 달라집니다.', '설비'))
    projected, diagnostics = project_html_columns(source)
    assert projected is None
    assert diagnostics['code'] == 'UNRESOLVED_NOTE_REFERENCE'


@pytest.mark.parametrize('field', ['context_before', 'footnotes', 'caption', 'section_path'])
def test_context_note_reference_to_omitted_cell_refuses(field):
    source = record(bodies=('소송 진행 중', '지급기일은 보증회사 부담 여부에 따라 달라집니다.', '설비'))
    source[field] = ['소송 조건은 주1을 따릅니다.'] if field == 'section_path' else '소송 조건은 주1을 따릅니다.'
    assert project_html_columns(source)[1]['code'] == 'UNRESOLVED_NOTE_REFERENCE'


def test_exact_self_contained_qualifier_remains_projectable():
    source = record(bodies=('소송은 종료되었으나 보증회사가 부담하지 않으면 지급 의무가 남습니다.',
                            '설비 계약은 독립적으로 체결됨', '재무약정 면제 승인'))
    source['footnotes'] = '모든 금액은 백만원이며 세금을 포함합니다.'
    projected, diagnostics = project_html_columns(source)
    assert diagnostics['code'] == 'PROJECTED'
    assert projected['table']['cells'] == [c for c in source['table']['cells'] if c['col'] != 2]


@pytest.mark.parametrize('deadline', [True, '10', float('nan'), float('inf'), float('-inf')])
def test_invalid_deadline_is_fail_closed(deadline):
    projected, diagnostics = project_html_columns(record(), deadline=deadline)
    assert projected is None
    assert diagnostics['code'] == 'INVALID_PROJECTION_DEADLINE'


@pytest.mark.parametrize('field', ['context_before', 'footnotes', 'caption', 'heading_path', 'section_path'])
def test_cross_column_reference_in_context_cannot_drop_its_target(field):
    source = record(bodies=('소송 진행 중', '지급 조건은 보증회사가 부담합니다.', '설비 내용'))
    source[field] = ['소송의 지급조건은 다른 열 참조'] if field.endswith('_path') else '소송의 지급조건은 다른 열 참조'
    assert project_html_columns(source)[1]['code'] == 'AMBIGUOUS_CROSS_COLUMN_REFERENCE'


@pytest.mark.parametrize('marker', ['*', '＊', '†', '‡', '※', '¹', '²', '³', '⁴'])
@pytest.mark.parametrize('context', [False, True])
def test_annotation_symbols_never_drop_unresolved_conditions(marker, context):
    source = record(bodies=('소송 진행 중' if context else f'소송 금액{marker}',
                            f'{marker} 지급 조건은 보증회사의 동의를 받은 날입니다.', '설비 내용'))
    if context:
        source['context_before'] = f'소송 금액{marker}'
    assert project_html_columns(source)[1]['code'] == 'UNRESOLVED_NOTE_REFERENCE'
