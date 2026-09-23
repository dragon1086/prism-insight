"""Source catalog regression tests, using only synthetic source fixtures."""
import copy
import hashlib

import pytest
from lxml import etree

from prism_core.dart_source_tree_catalog import (
    build_catalog,
    decode_table,
    encode_table,
    heading_level,
    pack_units,
    unpack_units,
)
from prism_core.filing_html_tables import parse_html_table


@pytest.mark.parametrize('html', [
    '<table><tr><td></td><th>한국어\t 단위</th></tr></table>',
    '<table><tbody><tr><th colspan="2">단위</th></tr><tr><td rowspan="2">A</td><td>B</td></tr><tr><td></td></tr></tbody></table>',
    '<table><thead><tr><th>A</th><td>B</td><th>C</th></tr></thead><tbody><tr><td>D</td></tr></tbody></table>',
    '<table><tr><td rowspan="2">x</td></tr><tr></tr></table>',
])
def test_table_codec_exact(html):
    root = etree.fromstring(html.encode(), etree.HTMLParser(encoding='utf-8'))
    original = parse_html_table(root.xpath('//table')[0])
    assert decode_table(encode_table(original), original['source_path']) == original


@pytest.mark.parametrize('html', [
    '<table><thead><tr><th>A</th><th>B</th></tr></thead><tbody><tr><td>C</td><td>D</td></tr><tr><td>E</td><td>F</td></tr></tbody></table>',
    '<table><thead><tr><th>A</th></tr><tr><th>B</th></tr></thead><tbody><tr><td>C</td></tr></tbody><tfoot><tr><td>D</td></tr></tfoot></table>',
    '<table><tbody><tr><td>A</td></tr></tbody><tbody><tr><td>B</td></tr><tr><td>C</td></tr></tbody></table>',
])
def test_mixed_row_groups_exact(html):
    root = etree.fromstring(html.encode(), etree.HTMLParser(encoding='utf-8'))
    original = parse_html_table(root.xpath('//table')[0])
    payload = encode_table(original)
    assert payload[4][0] == 'groups'
    assert decode_table(payload, original['source_path']) == original


def test_header_prefix_range_and_mixed_headers():
    first = build_catalog('<table><tr><th>A</th><th>B</th><td>C</td></tr></table>')['units'][0]
    assert first['payload'][5] == 2
    second = build_catalog('<table><tr><th>A</th><td>B</td><th>C</th></tr></table>')['units'][0]
    assert second['payload'][5] == [0, 2]
    assert unpack_units(pack_units([first, second])) == [first, second]


@pytest.mark.parametrize('groups', [[], [['/tbody/tr', 0]], [['/tbody/tr', 301]],
                                    [['/../../tr', 1]], [['/tbody/tr', True]],
                                    [['/thead/tr', 200], ['/tbody/tr', 200]]])
def test_invalid_row_groups_rejected(groups):
    payload = [1, 1, ['x'], [], ['groups', groups], []]
    with pytest.raises(ValueError):
        decode_table(payload, '/html/body/table')


def test_nested_container_content_and_order():
    html = '<p>1. 정책</p><table class="nb"><tr><td>앞<p>2-1. 추정</p><p>A <b>B</b></p><table><tr><td>금액</td></tr></table>뒤</td></tr></table><p>2. 부채</p>'
    catalog = build_catalog(html)
    units = catalog['units']
    assert catalog['sha256'] == hashlib.sha256(html.encode()).hexdigest()
    assert [u['kind'] for u in units] == ['text', 'container', 'text', 'text', 'text', 'table', 'text', 'text']
    assert [u['payload'] for u in units if u['kind'] == 'text'] == ['1. 정책', '앞', '2-1. 추정', 'A B', '뒤', '2. 부채']
    outer = decode_table(units[1]['payload'], units[1]['path'])
    assert outer['cells'][0]['text'] == ''
    assert units[-1]['context'] == []
    assert units[4]['context'] == [units[0]['path'], units[3]['path']]
    assert unpack_units(pack_units(units)) == units


def test_narrative_leaf_container_splits_p_without_duplicate_text():
    units = build_catalog('<table class="nb"><tbody><tr><td><p>2-1. 작성</p><p>설명</p></td></tr></tbody></table>')['units']
    assert [u['kind'] for u in units] == ['container', 'text', 'text']
    assert units[0]['payload'][2] == ['']


def test_three_level_nested_geometry_does_not_confuse_ancestor_rows():
    html = '<table><tr><td><table><tr><td><table><tr><td>A</td></tr></table></td></tr></table></td></tr></table>'
    units = build_catalog(html)['units']
    assert [u['kind'] for u in units] == ['container', 'container', 'table']
    assert unpack_units(pack_units(units)) == units


def test_leaf_table_caption_and_noncell_text_retained_once():
    units = build_catalog('<table>앞<caption>금액 <b>단위</b></caption><tr><td>셀</td>뒤</tr></table>')['units']
    assert units[0]['kind'] == 'table'
    assert [u['payload'] for u in units[1:]] == ['앞', '금액 단위', '뒤']
    assert decode_table(units[0]['payload'], units[0]['path'])['cells'][0]['text'] == '셀'


@pytest.mark.parametrize(('title', 'expected'), [
    ('2. 중요한 정책', 2), ('2-1. 기준', 3), ('2.1 기준', 3),
    ('(1) 작성 기준', 4), ('1) 추정', 5), ('2026 원', None),
    ('III. 재무 정보', 1), ('일반 설명', None),
])
def test_heading_levels(title, expected):
    assert heading_level(title) == expected


@pytest.mark.parametrize('span', ['0', '-1', 'x', '301', '9999999'])
def test_bad_span_explicitly_unsupported(span):
    units = build_catalog(f'<table><tr><td colspan="{span}">x</td></tr></table>')['units']
    assert units[0]['kind'] == 'unsupported'


def test_hidden_content_not_falsely_flattened():
    units = build_catalog('<p>A<span hidden>B</span>C</p><script>bad</script><table><tr><td hidden>x</td></tr></table>')['units']
    assert units[0]['payload'] == 'AC'
    assert units[1]['kind'] == 'unsupported'


def test_direct_text_and_tails_have_correct_xpath():
    html = '<div>one<b>two</b>three<p>four</p>five<!-- note -->six</div>'
    root = etree.fromstring(html.encode(), etree.HTMLParser(encoding='utf-8'))
    for unit in build_catalog(html)['units']:
        original = root.xpath(unit['path'])[0]
        text = original if isinstance(original, str) else ''.join(original.itertext())
        assert unit['payload'] == ' '.join(text.split())


@pytest.mark.parametrize('mutate', [
    lambda p: p[3].append([999, 2, 1]),
    lambda p: p[3].append([0, 0, 1]),
    lambda p: p[5].append(4),
    lambda p: p[2].__setitem__(0, 'x\ny'),
    lambda p: p.__setitem__(4, ['/../../x']),
    lambda p: p.__setitem__(0, True),
])
def test_decode_rejects_invalid_payload(mutate):
    payload = copy.deepcopy(build_catalog('<table><tr><td>x</td></tr></table>')['units'][0]['payload'])
    mutate(payload)
    with pytest.raises(ValueError):
        decode_table(payload, '/html/body/table')


def test_wire_contexts_and_canonical_guard():
    units = build_catalog('<p>1. 제목</p><p>내용</p><p>다음</p>')['units']
    wire = pack_units(units)
    assert len(wire[2]) == 2
    assert unpack_units(wire) == units
    wire[2].append([])
    with pytest.raises(ValueError, match='noncanonical'):
        unpack_units(wire)


@pytest.mark.parametrize('text', ['', '한국어 금액 123', 'groups', 'p1'])
def test_default_cell_wire_elision_is_readable_and_exact(text):
    units = build_catalog(f'<table><tbody><tr><td>{text}</td></tr></tbody></table>')['units']
    wire = pack_units(units)
    assert wire[3][0][2] == text
    assert unpack_units(wire) == units


@pytest.mark.parametrize('html', [
    '<table><tr><td>A</td></tr></table>',
    '<table><tbody><tr><th>A</th></tr></tbody></table>',
    '<table><tbody><tr><td colspan="2">A</td></tr></tbody></table>',
    '<table><tbody><tr><td>A</td><td>B</td></tr></tbody></table>',
])
def test_nondefault_table_shape_is_not_elided(html):
    units = build_catalog(html)['units']
    wire = pack_units(units)
    assert not isinstance(wire[3][0][2], str)
    assert unpack_units(wire) == units


def test_bounds_and_empty_html():
    with pytest.raises(ValueError):
        build_catalog('')
    with pytest.raises(ValueError):
        build_catalog('x' * (8 * 1024 * 1024 + 1))
    with pytest.raises(ValueError):
        build_catalog('<div>' * 102 + 'x' + '</div>' * 102)


def test_standard_header_body_wire_defaults_do_not_touch_visible_text():
    units = build_catalog('<table><thead><tr><th>이름</th><th>금액</th></tr></thead>'
                          '<tbody><tr><td>A</td><td>100</td></tr>'
                          '<tr><td>B</td><td>200</td></tr></tbody></table>')['units']
    wire = pack_units(units)
    assert wire[3][0][2][4] == {'head': 1}
    assert wire[3][0][2][2] == ['이름\t금액', 'A\t100', 'B\t200']
    assert unpack_units(wire) == units


@pytest.mark.parametrize('head', [True, 0, -1, 2, 301, '1'])
def test_invalid_standard_row_group_descriptor_is_rejected(head):
    units = build_catalog('<table><thead><tr><th>제목</th></tr></thead>'
                          '<tbody><tr><td>내용</td></tr></tbody></table>')['units']
    wire = pack_units(units)
    wire[3][0][2][4] = {'head': head}
    with pytest.raises(ValueError):
        unpack_units(wire)


@pytest.mark.parametrize('html', [
    '<table><tbody><tr><td>A</td><td>B</td></tr><tr><td>C</td></tr></tbody></table>',
    ('<table><tbody><tr><td colspan="2">표 제목</td></tr>'
     '<tr><td>당기</td><td>단위: 원</td></tr></tbody></table>'),
])
def test_plain_body_and_caption_templates_are_exact(html):
    units = build_catalog(html)['units']
    wire = pack_units(units)
    assert isinstance(wire[3][0][2], dict)
    assert unpack_units(wire) == units


def test_previous_context_elision_does_not_cross_chunk_start():
    units = build_catalog('<p>1. 제목</p><p>A</p><p>B</p><p>C</p>')['units']
    wire = pack_units(units)
    assert len(wire[3][-1]) == 3
    assert unpack_units(wire) == units
    wire[3] = wire[3][-1:]
    with pytest.raises(ValueError, match='context'):
        unpack_units(wire)
