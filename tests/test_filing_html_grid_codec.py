"""Whole-table grid compression preserves anchors, text and nonunit spans."""
import copy
import json

import pytest

from prism_core import filing_html_codec as codec


def dump(value):
    return json.dumps(value, ensure_ascii=False, separators=(',', ':'))


def grid(rows=None, shape=None, spans=None):
    return {'schema': 'html_cell_grid_v1', 'shape': shape or [1, 1],
            'rows': [['0']] if rows is None else rows, 'spans': [] if spans is None else spans}


def record(cells, shape):
    objects = [dict(zip(codec._FIELDS, cell)) for cell in cells]
    return {'kind': 'table', 'text': dump({'cells': objects}),
            'table': {'status': 'COMPLETE', 'row_count': shape[0],
                      'column_count': shape[1], 'cells': objects}}


def test_grid_roundtrip_exact_text_spans_and_holes_without_mutation():
    cells = [[0, 0, 2, 1, '제목'], [0, 1, 1, 2, '기간'],
             [1, 1, 1, 1, ''], [1, 2, 1, 1, '0'],
             [2, 0, 1, 1, '-'], [2, 2, 1, 1, '(100)'],
             [3, 0, 1, 1, '\x00\b\f\n\r\t"\\한글é😀'],
             [3, 1, 1, 1, '조건은 확정되지 않았습니다.']]
    item = record(cells, [4, 3])
    before = copy.deepcopy(item)
    encoded = codec.compact_html_grid_excerpt(item)
    value = json.loads(encoded)
    assert value['schema'] == 'html_cell_grid_v1'
    assert value['rows'][:3] == [['제목', '기간', None], [None, '', '0'], ['-', None, '(100)']]
    assert value['spans'] == [[0, 0, 2, 1], [0, 1, 1, 2]]
    assert codec.expand_html_table_excerpt(encoded) == item['text']
    assert item == before
    assert len(dump(encoded).encode()) < len(dump(item['text']).encode())


@pytest.mark.parametrize('value', [
    {**grid(), 'extra': 1}, {**grid(), 'cell_fields': codec._FIELDS},
    {**grid(), 'shape': [True, 1]}, {**grid(), 'shape': [1.0, 1]},
    {**grid(), 'shape': [0, 1]}, {**grid(), 'shape': [301, 1]},
    {**grid(), 'shape': [81, 81]}, {**grid(), 'shape': [80, 151]},
    {**grid(), 'shape': [10**100, 1]}, {**grid(), 'rows': '0'},
    grid([]), grid(['0']), grid([[]]), grid([['0', '1']]),
    grid([[None]]), grid([[True]]), grid([[0]]), grid([[{}]]),
    {**grid(), 'spans': None}, grid(spans=[[0, 0, 1, 1]]),
    grid(spans=[[0, 0, 1]]), grid(spans=[[True, 0, 1, 2]]),
    grid(spans=[[0, 0, 1.0, 2]]), grid(spans=[[0, 0, 0, 2]]),
    grid(spans=[[-1, 0, 1, 2]]), grid(spans=[[0, 0, 1, 2]]),
    grid([['a', 'b']], [1, 2], [[0, 0, 1, 2]]),
    grid([['a', None]], [1, 2], [[0, 1, 1, 2]]),
    grid([['a', None]], [1, 2], [[0, 0, 1, 2], [0, 0, 1, 2]]),
    grid([['a', None, 'b', None]], [1, 4], [[0, 2, 1, 2], [0, 0, 1, 2]]),
    grid([['a', None], [None, 'b']], [2, 2], [[0, 0, 2, 2]]),
    grid([['\ud800']]),
])
def test_grid_rejects_noncanonical_or_invalid_geometry(value):
    with pytest.raises(ValueError, match='^INVALID_HTML_TABLE_CODEC$'):
        codec.expand_html_table_excerpt(dump(value))


def test_grid_rejects_duplicate_keys():
    with pytest.raises(ValueError, match='^INVALID_HTML_TABLE_CODEC$'):
        codec.expand_html_table_excerpt(dump(grid()).replace('"spans":[]', '"spans":[],"spans":[]'))


def test_maximum_grid_slots_roundtrip():
    cells = [[r, c, 1, 1, ''] for r in range(80) for c in range(150)]
    item = record(cells, [80, 150])
    assert codec.expand_html_table_excerpt(codec.compact_html_grid_excerpt(item)) == item['text']


@pytest.mark.parametrize('change', [{'kind': 'prose'}, {'layout_role': 'caption'},
                                 {'context_incomplete': False}, {'text': 'not canonical'}])
def test_grid_encoder_unsupported_record_is_unchanged(change):
    item = {**record([[0, 0, 1, 1, 'x']], [1, 1]), **change}
    assert codec.compact_html_grid_excerpt(item) == item['text']


@pytest.mark.parametrize('text', [' ' * (8 * 1024 * 1024 + 1), '[' * 4 + '0' + ']' * 4,
                                '[' + '0,' * 75001 + '0]'])
def test_preflight_still_rejects_before_json_allocation(text, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError('JSON allocation forbidden')
    monkeypatch.setattr(codec.json, 'loads', forbidden)
    with pytest.raises(ValueError, match='^INVALID_HTML_TABLE_CODEC$'):
        codec.expand_html_table_excerpt(text)


def test_grid_expanded_size_bound_before_serialization(monkeypatch):
    text = dump(grid([[''] * 20], [1, 20]))
    monkeypatch.setattr(codec, '_BYTES', len(text.encode()))
    def forbidden(*args, **kwargs):
        raise AssertionError('Expanded allocation forbidden')
    monkeypatch.setattr(codec, '_legacy', forbidden)
    with pytest.raises(ValueError, match='^INVALID_HTML_TABLE_CODEC$'):
        codec.expand_html_table_excerpt(text)


def test_grid_text_size_bound():
    text = '가' * (codec._TEXT_BYTES // 3) + 'x' * (codec._TEXT_BYTES % 3)
    assert json.loads(codec.expand_html_table_excerpt(dump(grid([[text]]))))['cells'][0]['text'] == text
    with pytest.raises(ValueError, match='^INVALID_HTML_TABLE_CODEC$'):
        codec.expand_html_table_excerpt(dump(grid([[text + 'x']])))


def test_maximum_nonunit_spans_fit_unchanged_preflight_limits():
    cells = [[r, c, 1, 2, 'x'] for r in range(80) for c in range(0, 150, 2)]
    item = record(cells, [80, 150])
    assert codec.expand_html_table_excerpt(codec.compact_html_grid_excerpt(item)) == item['text']


@pytest.mark.parametrize('shape', [[10**100, 1], [80, 151], [False, 1]])
def test_oversized_geometry_stops_before_expansion(shape, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError('Cell validation/expansion forbidden')
    monkeypatch.setattr(codec, '_validate', forbidden)
    with pytest.raises(ValueError, match='^INVALID_HTML_TABLE_CODEC$'):
        codec.expand_html_table_excerpt(dump(grid(shape=shape)))


@pytest.mark.parametrize('mutation', ['overlap', 'partial', 'surrogate', 'shape'])
def test_grid_encoder_invalid_records_return_original(mutation):
    item = record([[0, 0, 1, 1, 'a'], [0, 1, 1, 1, 'b']], [1, 2])
    if mutation == 'overlap':
        item['table']['cells'][1]['col'] = 0
    elif mutation == 'partial':
        item['table']['status'] = 'PARTIAL'
    elif mutation == 'surrogate':
        item['table']['cells'][0]['text'] = '\ud800'
    else:
        item['table']['row_count'] = 10**100
    assert codec.compact_html_grid_excerpt(item) == item['text']


def test_tuple_encoder_contract_stays_separate():
    item = record([[0, c, 1, 1, str(c)] for c in range(8)], [1, 8])
    assert json.loads(codec.compact_html_table_excerpt(item))['schema'] == 'html_cell_tuples_v1'
    assert json.loads(codec.compact_html_grid_excerpt(item))['schema'] == 'html_cell_grid_v1'
