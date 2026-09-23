"""Explicit full-cell serialization, not extraction or financial projection."""
import copy
import json

import pytest

from prism_core import filing_html_codec as codec
from prism_core.filing_html import parse_filing_html
from prism_core.filing_html_codec import (
    compact_html_table_excerpt,
    expand_html_table_excerpt,
)
from prism_core.filing_report_evidence import _record_blocks

FIELDS = ['row', 'col', 'rowspan', 'colspan', 'text']


def dump(value):
    return json.dumps(value, ensure_ascii=False, separators=(',', ':'))


def encoded(cells=None, shape=None):
    return {'schema': 'html_cell_tuples_v1', 'cell_fields': FIELDS,
            'shape': shape or [1, 1], 'cells': cells or [[0, 0, 1, 1, '0']]}


def record(body):
    return parse_filing_html('<h3>3. 연결재무제표 주석</h3><p>28. 소송 충당부채</p>' + body)['records'][-1]


TABLE = '<table><tr>' + ''.join('<td>' + value + '</td>' for value in ['소송', '0', '', '-', '(100)', 'USD', '마지막 조건은 확정되지 않았습니다.']) + '</tr></table>'


@pytest.mark.parametrize('body', [TABLE, '<table><tr><th rowspan="2">소송</th><th colspan="2">기간</th></tr><tr><td>0</td><td>-</td></tr><tr><td></td><td>조건 "&lt;x&gt;\\"</td></tr></table>'])
def test_full_cells_roundtrip_without_mutation(body):
    row = record(body)
    original = copy.deepcopy(row)
    compact = compact_html_table_excerpt(row)
    assert compact != row['text']
    assert expand_html_table_excerpt(compact) == row['text']
    assert row == original
    assert len(dump(compact).encode()) < len(dump(row['text']).encode())


@pytest.mark.parametrize('change', [{'layout_role': 'caption'}, {'context_incomplete': True}, {'context_incomplete': False}, {'kind': 'prose'}, {'text': 'not canonical'}])
def test_encoder_falls_back_for_unsupported_record(change):
    row = {**record(TABLE), **change}
    assert compact_html_table_excerpt(row) == row['text']


def test_small_table_stays_legacy():
    row = record('<table><tr><td>소송</td></tr></table>')
    assert compact_html_table_excerpt(row) == row['text']


@pytest.mark.parametrize('value', [
    {}, {'schema': 'unknown'}, {**encoded(), 'extra': 1},
    {**encoded(), 'cell_fields': FIELDS[::-1]}, {**encoded(), 'shape': [True, 1]},
    {**encoded(), 'shape': [0, 1]}, {**encoded(), 'shape': [301, 1]},
    {**encoded(), 'shape': [81, 81]}, {**encoded(), 'shape': [80, 151]},
    {**encoded(), 'cells': []}, {**encoded(), 'cells': [[0, 0, 1, 1]]},
    {**encoded(), 'cells': [[0, 0, 1, 1, None]]},
    {**encoded(), 'cells': [[True, 0, 1, 1, 'x']]},
    {**encoded(), 'cells': [[0.0, 0, 1, 1, 'x']]},
    {**encoded(), 'cells': [[-1, 0, 1, 1, 'x']]},
    {**encoded(), 'cells': [[0, 0, 0, 1, 'x']]},
    {**encoded(), 'cells': [[0, 0, 2, 1, 'x']]},
    encoded([[0, 0, 1, 1, 'x'], [0, 0, 1, 1, 'y']]),
    encoded([[0, 1, 1, 1, 'x'], [0, 0, 1, 1, 'y']], [1, 2]),
    encoded([[0, 0, 1, 2, 'x'], [0, 1, 1, 1, 'y']], [1, 2]),
])
def test_decoder_rejects_invalid_schema_and_geometry(value):
    with pytest.raises(ValueError, match='^INVALID_HTML_TABLE_CODEC$'):
        expand_html_table_excerpt(dump(value))


@pytest.mark.parametrize('text', ['{"schema":"x","schema":"x"}', 'NaN', 'Infinity', '"\\uD800"', '"\\x"', '"unterminated', '[' * 100 + '0' + ']' * 100, '{"cells":[' + '0,' * 75001 + '0]}'])
def test_malformed_inputs_have_code_only_errors(text):
    with pytest.raises(ValueError, match='^INVALID_HTML_TABLE_CODEC$'):
        expand_html_table_excerpt(text)


def test_literal_string_punctuation_does_not_count_as_structure():
    value = encoded([[0, 0, 1, 1, '[{,:}]-"\\' * 1000]])
    restored = json.loads(expand_html_table_excerpt(dump(value)))
    assert restored['cells'][0]['text'] == value['cells'][0][4]


def blocks(row, material=True, representation='DART_VIEWER_HTML'):
    return _record_blocks([row], material_notes=material, representation=representation,
        digest='a' * 64, md_hash=None, gaps=[], parsed={'parser_version': 'filing_html_v2'})[0]


def test_adapter_compares_full_block_including_marker_and_preserves_routing():
    row = record(TABLE)
    actual = blocks(row)[0]
    assert actual['provenance']['excerpt_encoding'] == 'html_cell_tuples_v1'
    assert expand_html_table_excerpt(actual['excerpt']) == row['text']
    assert blocks(row, False)[0]['excerpt'] == row['text']
    three = record('<table><tr><td>소송</td><td>0</td><td>-</td></tr></table>')
    assert compact_html_table_excerpt(three) != three['text']
    assert blocks(three)[0]['excerpt'] == three['text']
    assert 'excerpt_encoding' not in blocks(three)[0]['provenance']


@pytest.mark.parametrize('text', ['[' * 100, '[' + '[],' * 12011 + '[]]', '[' + '0,' * 75001 + '0]', ' ' * (8 * 1024 * 1024 + 1)])
def test_preflight_rejects_before_json_object_allocation(text, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError('json.loads must not run')
    monkeypatch.setattr(codec.json, 'loads', forbidden)
    with pytest.raises(ValueError, match='^INVALID_HTML_TABLE_CODEC$'):
        expand_html_table_excerpt(text)


def test_maximum_cell_count_and_grid_slots_are_accepted():
    cells = [[r, c, 1, 1, ''] for r in range(80) for c in range(150)]
    restored = json.loads(expand_html_table_excerpt(dump(encoded(cells, [80, 150]))))
    assert len(restored['cells']) == 12000
    with pytest.raises(ValueError):
        expand_html_table_excerpt(dump(encoded(cells + [[0, 0, 1, 1, '']], [80, 150])))


def test_utf8_text_byte_limit_exact_and_overflow():
    text = '가' * (2 * 1024 * 1024 // 3) + 'x' * (2 * 1024 * 1024 % 3)
    assert json.loads(expand_html_table_excerpt(dump(encoded([[0, 0, 1, 1, text]]))))['cells'][0]['text'] == text
    with pytest.raises(ValueError, match='^INVALID_HTML_TABLE_CODEC$'):
        expand_html_table_excerpt(dump(encoded([[0, 0, 1, 1, text + 'x']])))


def test_expanded_size_guard_runs_before_full_serialization(monkeypatch):
    value = encoded([[0, c, 1, 1, ''] for c in range(10)], [1, 10])
    text = dump(value)
    monkeypatch.setattr(codec, '_BYTES', len(text.encode()))
    def forbidden(*args, **kwargs):
        raise AssertionError('full expanded output must not be allocated')
    monkeypatch.setattr(codec, '_legacy', forbidden)
    with pytest.raises(ValueError, match='^INVALID_HTML_TABLE_CODEC$'):
        expand_html_table_excerpt(text)


def test_encoded_byte_limit_includes_whitespace(monkeypatch):
    text = dump(encoded())
    monkeypatch.setattr(codec, '_BYTES', len(text.encode()))
    assert json.loads(expand_html_table_excerpt(text))['cells'][0]['text'] == '0'
    with pytest.raises(ValueError, match='^INVALID_HTML_TABLE_CODEC$'):
        expand_html_table_excerpt(text + ' ')


def test_expanded_byte_boundary_is_exact_before_output(monkeypatch):
    value = encoded([[0, c, 1, 1, '\x00"\\'] for c in range(10)], [1, 10])
    text = dump(value)
    restored = expand_html_table_excerpt(text)
    monkeypatch.setattr(codec, '_BYTES', len(restored.encode()))
    assert expand_html_table_excerpt(text) == restored
    monkeypatch.setattr(codec, '_BYTES', len(restored.encode()) - 1)
    with pytest.raises(ValueError, match='^INVALID_HTML_TABLE_CODEC$'):
        expand_html_table_excerpt(text)


@pytest.mark.parametrize('value', ['\x00\b\f\n\r\t"\\', '한글é😀', '', '[]{}' * 100])
def test_string_size_matches_canonical_json(value):
    assert codec._string_bytes(value) == len(dump(value).encode())


def test_existing_material_routing_and_provenance_are_identical(monkeypatch):
    row = record(TABLE)
    actual = blocks(row)[0]
    monkeypatch.setattr(codec, 'compact_html_table_excerpt', lambda item: item['text'])
    legacy = blocks(row)[0]
    restored = copy.deepcopy(actual)
    restored['excerpt'] = expand_html_table_excerpt(restored['excerpt'])
    del restored['provenance']['excerpt_encoding']
    assert restored == legacy
    assert len(dump(actual).encode()) < len(dump(legacy).encode())


def test_firecrawl_corroboration_cannot_be_bypassed(monkeypatch):
    from prism_core import filing_report_evidence as evidence
    monkeypatch.setattr(evidence, '_same_markdown_unit', lambda *args: False)
    assert blocks(record(TABLE), representation='FIRECRAWL_CLEANED_HTML') == []
    monkeypatch.setattr(evidence, '_same_markdown_unit', lambda *args: True)
    assert blocks(record(TABLE), representation='FIRECRAWL_CLEANED_HTML')[0]['provenance']['excerpt_encoding'] == 'html_cell_tuples_v1'


@pytest.mark.parametrize('representation', ['MARKDOWN', 'SEC_INLINE'])
def test_non_html_material_inputs_keep_original_excerpt(representation):
    row = record(TABLE)
    assert blocks(row, representation=representation)[0]['excerpt'] == row['text']


@pytest.mark.parametrize('mutation', ['overlap', 'incomplete', 'surrogate', 'wrong_shape'])
def test_encoder_validation_failure_keeps_original(mutation):
    row = record(TABLE)
    if mutation == 'overlap':
        row['table']['cells'][1]['col'] = 0
    elif mutation == 'incomplete':
        row['table']['status'] = 'PARTIAL'
    elif mutation == 'surrogate':
        row['table']['cells'][0]['text'] = '\ud800'
    else:
        row['table']['row_count'] = True
    assert compact_html_table_excerpt(row) == row['text']
