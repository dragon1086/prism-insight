"""Narrow layout grammar and adjacency; no numeric interpretation."""
import json

import pytest

from prism_core.filing_html import parse_filing_html
from prism_core.filing_report_evidence import (
    _record_blocks,
    compact_html_provenance,
    expand_html_provenance,
)
from prism_core.material_filing_selection import material_html_records

HEAD = '<h3>3. 연결재무제표 주석</h3>'
DATA = '<table border="1"><tr><td>소송 충당부채</td><td>0</td></tr></table>'
CAP = '<table class="nb"><tbody><tr><td colspan="2">소송 충당부채</td></tr><tr><td>당반기말</td><td>(단위 : 백만원)</td></tr></tbody></table>'
FOOT = '<table class="nb"><tr><td>(주1) 최종 승소하여 지급 의무가 소멸했습니다.</td></tr></table>'
SPACE = '<table class="nb"><tr><td></td></tr></table>'


def parse(body, head=HEAD):
    return parse_filing_html(head + body)


def test_caption_data_spacer_footnote_preserves_raw_records():
    out = parse(CAP + DATA + SPACE + FOOT)
    cap, data, foot = out['records']
    assert cap['layout_role'] == 'caption'
    assert foot['layout_role'] == 'footnote'
    assert data['context_before'] == '소송 충당부채\n당반기말\n(단위 : 백만원)'
    assert len(data['context_paths']) == 3
    assert data['footnotes'] == '(주1) 최종 승소하여 지급 의무가 소멸했습니다.'
    assert data['footnote_paths'][0].endswith('/td[1]')
    original = parse(DATA)['records'][0]
    assert data['text'] == original['text']
    assert data['table']['grid'] == original['table']['grid']
    assert material_html_records(out['records'])[0] == [data]
    provenance = {'source_path': data['source_path'], 'context_paths': data['context_paths']}
    assert expand_html_provenance(json.loads(json.dumps(compact_html_provenance(provenance)))) == json.loads(json.dumps(provenance))


@pytest.mark.parametrize('boundary', ['<p>기타 설명입니다.</p>', '<h4>새 범위</h4>', '<table><tr><td></td></tr></table>', '<table><tr><td colspan="bad">x</td></tr></table>', DATA])
def test_non_spacer_breaks_caption_and_footnote(boundary):
    out = parse(CAP + boundary + DATA)
    assert not out['records'][-1]['context_before']
    out = parse(DATA + boundary + FOOT)
    assert not out['records'][0]['footnotes']


def test_period_unit_replaces_caption_and_new_caption_ends_note_target():
    unit = '<table class="nb"><tr><td>전기말</td><td>(단위:천원)</td></tr></table>'
    out = parse(DATA + CAP + FOOT + CAP + unit + DATA)
    assert not out['records'][0]['footnotes']
    assert out['records'][-1]['context_before'] == '전기말\n(단위:천원)'


@pytest.mark.parametrize('bad', [
    CAP.replace('소송 충당부채', '123'), CAP.replace('백만원)', '백만원) 100'),
    CAP.replace('백만원', '유로'), CAP.replace('<tbody>', '<caption>조건</caption><tbody>'),
    CAP.replace('<tbody>', '별도 조건<tbody>'), CAP.replace('</tr><tr>', '</tr>누락 조건<tr>'),
    CAP.replace('colspan="2"', 'colspan="2" rowspan="1"'),
    CAP.replace('소송 충당부채', '<script>bad</script>소송 충당부채'),
    CAP.replace('소송 충당부채', '<span hidden>bad</span>소송 충당부채'),
    CAP.replace('소송 충당부채', '<table><tr><td>x</td></tr></table>'),
    CAP.replace('class="nb"', 'class="nb" border="1"'),
    CAP.replace('<td>당반기말', '<td colspan="1">당반기말'),
    CAP.replace('</tbody>', '<tr><td>추가 조건</td><td>10</td></tr></tbody>'),
])
def test_unsupported_shapes_not_reclassified(bad):
    out = parse(bad + DATA)
    assert not any(r.get('layout_role') for r in out['records'])
    assert not out['records'][-1]['context_before']


@pytest.mark.parametrize('head,title', [(HEAD, '별도 소송'), (HEAD, '개별 소송'), ('', '연결 소송'), ('', '별도 소송')])
def test_explicit_scope_conflict_is_not_silently_stripped(head, title):
    out = parse(CAP.replace('소송 충당부채', title) + DATA, head)
    data = out['records'][-1]
    assert data['context_incomplete'] is True
    assert not data['context_before']
    assert material_html_records(out['records'])[0] == []


def test_unknown_context_does_not_infer_scope():
    data = parse(CAP + DATA, '')['records'][-1]
    assert data['scope'] == 'unknown'
    assert data['context_before']


@pytest.mark.parametrize('notes', [FOOT.replace('최종 승소하여 지급 의무가 소멸했습니다.', '가' * 1400), FOOT.replace('최종 승소하여 지급 의무가 소멸했습니다.', '가' * 750) * 2])
@pytest.mark.parametrize('material', [True, False])
def test_incomplete_note_cannot_bypass_common_routing(notes, material):
    out = parse(DATA + notes + DATA + FOOT)
    first = out['records'][0]
    assert first['context_incomplete'] is True
    assert 'LAYOUT_CONTEXT_LIMIT' in out['errors']
    assert first not in material_html_records(out['records'])[0]
    blocks, _ = _record_blocks(out['records'][:-2], material_notes=material,
        representation='DART_VIEWER_HTML', digest='a' * 64, md_hash=None, gaps=[], parsed=out)
    assert blocks == []
    assert out['records'][-2]['footnotes']


@pytest.mark.parametrize('material', [True, False])
def test_layout_only_cannot_bypass_common_routing(material):
    out = parse(CAP + FOOT)
    blocks, _ = _record_blocks(out['records'], material_notes=material,
        representation='DART_VIEWER_HTML', digest='a' * 64, md_hash=None, gaps=[], parsed=out)
    assert blocks == []


def test_common_routing_preserves_context_paths_after_real_compaction():
    out = parse(CAP + DATA + SPACE + FOOT)
    blocks, _ = _record_blocks(out['records'], material_notes=True,
        representation='DART_VIEWER_HTML', digest='a' * 64, md_hash=None, gaps=[], parsed=out)
    assert len(blocks) == 1
    provenance = blocks[0]['provenance']
    assert provenance['context_paths'] == out['records'][1]['context_paths']
    assert expand_html_provenance(json.loads(json.dumps(compact_html_provenance(provenance)))) == json.loads(json.dumps(provenance))


def test_only_strict_spacer_is_transparent_to_prose_adjacency():
    before, after = '<p>소송 청구금액은 100입니다.</p>', '<p>다만 지급 의무는 확정되지 않았습니다.</p>'
    for spacer, count in [(SPACE, 1), ('<table><tr><td></td></tr></table>', 2)]:
        records = parse(before + spacer + after)['records']
        assert len(material_html_records(records)[0]) == count


@pytest.mark.parametrize('group', ['<colgroup><col width="436"/><col width="230"/></colgroup>', '<colgroup>\n<col/> <col width="230"/>\n</colgroup>'])
def test_observed_empty_column_geometry_is_allowed(group):
    out = parse(CAP.replace('<tbody>', group + '<tbody>') + DATA)
    assert out['records'][0]['layout_role'] == 'caption'
    assert out['records'][1]['context_before'] == '소송 충당부채\n당반기말\n(단위 : 백만원)'


@pytest.mark.parametrize('group', [
    '<colgroup><col/></colgroup>', '<colgroup><col/><col/><col/></colgroup>',
    '<colgroup><col/><col/></colgroup>' * 2,
    '<colgroup span="2"><col/><col/></colgroup>',
    '<colgroup hidden><col/><col/></colgroup>',
    '<colgroup>조건<col/><col/></colgroup>',
    '<colgroup><col/>조건<col/></colgroup>',
    '<colgroup><col/><col/></colgroup>조건',
    '<colgroup><col span="1"/><col/></colgroup>',
    '<colgroup><col width="20%"/><col/></colgroup>',
    '<colgroup><col width="２３０"/><col/></colgroup>',
    '<colgroup><col style="display:none"/><col/></colgroup>',
    '<colgroup><span>조건</span><col/><col/></colgroup>',
    '<colgroup><colgroup><col/><col/></colgroup></colgroup>',
    '<col/><col/>',
])
def test_column_geometry_never_discards_conditions_or_unsupported_attributes(group):
    out = parse(CAP.replace('<tbody>', group + '<tbody>') + DATA)
    assert not any(r.get('layout_role') for r in out['records'])
    assert not out['records'][-1]['context_before']


@pytest.mark.parametrize('layout_first', [True, False])
def test_mixed_layout_and_prose_footnotes_share_limit(layout_first):
    layout = FOOT.replace('최종 승소하여 지급 의무가 소멸했습니다.', '가' * 750)
    prose = '<p>(주2) ' + '나' * 750 + '</p>'
    out = parse(DATA + (layout + prose if layout_first else prose + layout))
    assert out['records'][0]['context_incomplete'] is True
    assert 'LAYOUT_CONTEXT_LIMIT' in out['errors']
    assert material_html_records(out['records'])[0] == []


def test_overflow_suppresses_remaining_footnote_chain_until_boundary():
    huge = FOOT.replace('최종 승소하여 지급 의무가 소멸했습니다.', '가' * 1400)
    tail = '<p>(주2) 차입금 소송 청구금액은 100입니다.</p>'
    out = parse(DATA + huge + SPACE + tail + FOOT)
    assert material_html_records(out['records'])[0] == []
    for material in (True, False):
        assert _record_blocks(out['records'], material_notes=material, representation='DART_VIEWER_HTML',
            digest='a' * 64, md_hash=None, gaps=[], parsed=out)[0] == []
    resumed = parse(DATA + huge + '<p>새로운 설명입니다.</p>' + tail)
    assert resumed['records'][-1]['text'].startswith('(주2)')


@pytest.mark.parametrize('layout', [CAP, SPACE, FOOT])
def test_extra_empty_rows_are_not_layout(layout):
    out = parse(layout.replace('</table>', '<tr></tr></table>') + DATA)
    assert not any(r.get('layout_role') for r in out['records'])


@pytest.mark.parametrize('first_layout', [True, False])
@pytest.mark.parametrize('extra', [0, 1])
def test_footnote_byte_boundary_includes_separator(first_layout, extra):
    first = '(주1) ' + 'x' * 2000
    second = '(주2) ' + 'y' * (4096 - len(first.encode()) - 1 - len('(주2) '.encode()) + extra)
    layout = lambda text: '<table class="nb"><tr><td>' + text + '</td></tr></table>'
    prose = lambda text: '<p>' + text + '</p>'
    body = layout(first) + prose(second) if first_layout else prose(first) + layout(second)
    out = parse(DATA + body)
    assert bool(out['records'][0].get('context_incomplete')) == bool(extra)
    if not extra:
        assert len(out['records'][0]['footnotes'].encode()) == 4096


@pytest.mark.parametrize('boundary', ['<p>새 설명입니다.</p>', CAP, DATA, '<h4>새 주석</h4>'])
def test_overflow_chain_resets_at_non_footnote_boundary(boundary):
    huge = '<p>(주1) ' + 'x' * 4100 + '</p>'
    tail = '<p>(주2) 소송 청구금액은 100입니다.</p>'
    out = parse(DATA + huge + boundary + tail)
    assert out['records'][0]['context_incomplete'] is True
    assert any('소송 청구금액은 100' in r['text'] or '소송 청구금액은 100' in r['footnotes'] for r in out['records'][1:])


@pytest.mark.parametrize('boundary', [SPACE, '<table><tr><td colspan="bad">불명확한 경계</td></tr></table>', '<table><tr><td></td></tr></table>'])
def test_blocked_following_prose_retains_original_record_without_candidate(boundary):
    huge = FOOT.replace('최종 승소하여 지급 의무가 소멸했습니다.', '가' * 1400)
    text = '(주2) 소송 청구금액은 100입니다.'
    out = parse(DATA + huge + boundary + '<p>' + text + '</p>')
    note = out['records'][-1]
    assert note['text'] == text
    assert note['kind'] == 'prose'
    assert note['source_paths'] == [note['source_path']]
    assert isinstance(note['event_index'], int)
    assert note['context_incomplete'] is True
    assert material_html_records(out['records'])[0] == []
    for material in (True, False):
        assert _record_blocks(out['records'], material_notes=material, representation='DART_VIEWER_HTML',
            digest='a' * 64, md_hash=None, gaps=[], parsed=out)[0] == []
