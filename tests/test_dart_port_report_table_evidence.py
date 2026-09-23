"""Deterministic structural associations, never inferred financial semantics."""
import copy

import pytest

from prism_core.dart_source_table_evidence import (
    expand_table_evidence,
    pack_addressed_units,
    pack_readable_units,
    unpack_readable_units,
)
from prism_core.dart_source_tree_catalog import build_catalog, pack_units


def table(html):
    return next(u for u in build_catalog(html)['units'] if u['kind'] == 'table')


def test_multilevel_headers_and_rowspan_keep_original_columns():
    unit = table('''<table><thead><tr><th rowspan="2">항목</th>
        <th colspan="2">당기</th><th colspan="2">전기</th></tr>
        <tr><th>3개월</th><th>누적</th><th>3개월</th><th>누적</th></tr></thead>
        <tbody><tr><td>수익</td><td></td><td>10</td><td>-</td><td>20</td></tr>
        </tbody></table>''')
    before = copy.deepcopy(unit)
    result = expand_table_evidence(unit)
    assert unit == before
    assert result['status'] == 'STRUCTURAL_ONLY'
    cells = result['cells']
    cumulative = next(c for c in cells if c['text'] == '20')
    assert [cells[i]['text'] for i in cumulative['column_header_candidates']] == ['전기', '누적']
    assert cumulative['column'] == 4
    assert result['grid'][0][0] == result['grid'][1][0]
    assert any(c['text'] == '' for c in cells)
    assert any(c['text'] == '-' for c in cells)
    assert expand_table_evidence(unit) == result


def test_td_labels_are_ambiguous_not_invented_headers():
    result = expand_table_evidence(table('<table><tr><td>당기</td><td>전기</td></tr>'
                                         '<tr><td>10</td><td>20</td></tr></table>'))
    assert result['status'] == 'AMBIGUOUS'
    assert result['reason'] == 'NO_EXPLICIT_HEADER_BAND'
    assert all(not c['column_header_candidates'] for c in result['cells'])
    assert [c['text'] for c in result['cells']] == ['당기', '전기', '10', '20']


def test_sparse_grid_and_container_do_not_assert_header_semantics():
    result = expand_table_evidence(table('<table><tr><th>A</th><th>B</th></tr>'
                                         '<tr><td>1</td></tr></table>'))
    assert result['status'] == 'AMBIGUOUS'
    assert result['reason'] == 'SPARSE_GRID'
    assert result['grid'][-1][-1] is None
    unit = table('<table><tr><th>A</th></tr><tr><td>1</td></tr></table>')
    unit['kind'] = 'container'
    assert expand_table_evidence(unit)['reason'] == 'CONTAINER_NOT_DATA_TABLE'


def test_invalid_geometry_fails_without_partial_projection():
    unit = table('<table><tr><td>1</td></tr></table>')
    unit['payload'][3] = [[0, 2, 1]]
    with pytest.raises(ValueError, match='span outside grid'):
        expand_table_evidence(unit)


def test_body_header_after_values_does_not_leak_into_later_section():
    result = expand_table_evidence(table('<table><tr><th>A</th><th>B</th></tr>'
        '<tr><td>1</td><td>2</td></tr><tr><th>C</th><th>D</th></tr>'
        '<tr><td>3</td><td>4</td></tr></table>'))
    assert result['status'] == 'AMBIGUOUS'
    assert result['reason'] == 'HEADERS_OUTSIDE_LEADING_BAND'
    assert all(not c['column_header_candidates'] for c in result['cells'])


def test_aligned_codec_roundtrip_markers_blanks_sparse_and_nested_geometry():
    units = build_catalog('''<h1>원문</h1><table><tr><td><p>상위</p>
        <table><tr><th rowspan="2">^</th><th colspan="2">``</th></tr>
        <tr><td>^^value</td><td></td></tr><tr><td>`</td><td>^^</td></tr>
        </table></td></tr></table>''')['units']
    before = copy.deepcopy(units)
    packed = pack_readable_units(units)
    assert packed[0] == 'source-tree-grid-v1'
    assert units == before
    assert unpack_readable_units(packed) == units
    assert unpack_readable_units(pack_units(units)) == units
    inner = next(row[2] for row in packed[3]
                 if row[1] == 't' and isinstance(row[2], list) and row[2][3])
    assert inner[2] == ['^^\t```\t^', '^\t^^^value\t', '``\t^^^\t`']


@pytest.mark.parametrize('replacement', ['x\tB', '^\t^', 'A', '^unescaped\tB'])
def test_aligned_codec_rejects_corrupt_marker_or_column_position(replacement):
    units = [table('<table><tr><td rowspan="2">A</td><td>1</td></tr>'
                   '<tr><td>B</td></tr></table>')]
    packed = pack_readable_units(units)
    packed[3][0][2][2][1] = replacement
    with pytest.raises(ValueError):
        unpack_readable_units(packed)


def test_aligned_caption_and_no_anchor_row_roundtrip_exactly():
    units = build_catalog('<table><tbody><tr><td colspan="2">caption</td></tr>'
                          '<tr><td>1</td><td>2</td></tr></tbody></table>'
                          '<table><tbody><tr><td rowspan="2">A</td></tr>'
                          '<tr></tr></tbody></table>')['units']
    assert unpack_readable_units(pack_readable_units(units)) == units


def test_aligned_catalog_requires_rows_list():
    with pytest.raises(TypeError, match='catalog rows'):
        unpack_readable_units(['source-tree-grid-v1', '/', [], {}])


def addressed_fixture():
    return build_catalog('<h1>제목</h1><p>단위</p><table><tr><td>^</td>'
                         '<td>`</td></tr></table><p>주석</p><table><tr>'
                         '<td colspan="2">병합</td></tr><tr><td></td>'
                         '<td>5</td></tr></table>')['units']


def test_addressed_tables_use_original_unit_ordinal_and_exact_roundtrip():
    units = addressed_fixture()
    before = copy.deepcopy(units)
    packed = pack_addressed_units(units)
    assert packed[0] == 'source-tree-addressed-grid-v1'
    assert unpack_readable_units(packed) == units == before
    baseline = pack_readable_units(units)
    for i, row in enumerate(packed[3]):
        assert row[0] == ([i, baseline[3][i][0]] if row[1] == 't' else baseline[3][i][0])
        assert row[1:] == baseline[3][i][1:]


@pytest.mark.parametrize('identifier', [True, False, -1, 999, 1.0, '2', None])
def test_addressed_catalog_rejects_noncanonical_ids(identifier):
    packed = pack_addressed_units(addressed_fixture())
    row = next(row for row in packed[3] if row[1] == 't')
    row[0][0] = identifier
    with pytest.raises(ValueError, match='table address'):
        unpack_readable_units(packed)


@pytest.mark.parametrize('path', [None, 2, True, ['t'], '^', '`', '^^'])
def test_addressed_catalog_rejects_invalid_typed_or_marker_paths(path):
    packed = pack_addressed_units(addressed_fixture())
    row = next(row for row in packed[3] if row[1] == 't')
    row[0][1] = path
    with pytest.raises(ValueError):
        unpack_readable_units(packed)


def test_addressed_catalog_rejects_duplicate_and_missing_addresses():
    packed = pack_addressed_units(addressed_fixture())
    tables = [row for row in packed[3] if row[1] == 't']
    tables[1][0][0] = tables[0][0][0]
    with pytest.raises(ValueError, match='table address'):
        unpack_readable_units(packed)
    packed = pack_addressed_units(addressed_fixture())
    row = next(row for row in packed[3] if row[1] == 't')
    row[0] = row[0][1]
    with pytest.raises(ValueError, match='table address'):
        unpack_readable_units(packed)


def test_addressed_catalog_does_not_address_nested_containers():
    units = build_catalog('<table><tr><td><p>외부</p><table><tr><td>1</td>'
                          '</tr></table></td></tr></table>')['units']
    packed = pack_addressed_units(units)
    assert unpack_readable_units(packed) == units
    assert any(row[1] == 'c' for row in packed[3])
    assert all(isinstance(row[0], str) for row in packed[3] if row[1] != 't')
    container = next(row for row in packed[3] if row[1] == 'c')
    container[0] = [0, container[0]]
    with pytest.raises(ValueError, match='table address'):
        unpack_readable_units(packed)
