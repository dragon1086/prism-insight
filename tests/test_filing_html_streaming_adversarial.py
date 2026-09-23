"""Independent streaming invariants against the unchanged source document."""

import hashlib

import pytest
from lxml import html as lhtml

from prism_core.filing_html import parse_filing_html


def _resolved_text(tree, paths):
    pieces = []
    for path in paths:
        matches = tree.xpath(path)
        assert len(matches) == 1, path
        value = matches[0]
        pieces.append(value.text_content() if hasattr(value, 'text_content') else str(value))
    return ' '.join(''.join(pieces).split())


@pytest.mark.parametrize('feed_size', [1, 7, 8192])
def test_utf8_feed_boundaries_do_not_change_evidence(feed_size):
    raw = '<h2>II. 사업의 내용</h2><p>매출 1<span>,000</span>원이며 조건부 계약입니다.</p>'
    reference = parse_filing_html(raw, _feed_size=8192)
    actual = parse_filing_html(raw, _feed_size=feed_size)
    assert actual['records'] == reference['records']


def test_comment_and_hidden_element_tails_preserve_visible_numbers():
    raw = ('<h2>II. 사업의 내용</h2><p>매출 1<span>,000</span>'
           '<!-- 포함하면 안 됨 -->원<span hidden>가짜 매출</span>이며 '
           '<script>가짜 이익</script>미확정입니다.</p>')
    actual = parse_filing_html(raw, _feed_size=1)
    assert [row['text'] for row in actual['records']] == ['매출 1,000원이며 미확정입니다.']


def test_nested_container_text_locators_resolve_in_original_document():
    raw = ('<article><div>매출 1<span>,000</span>원입니다.'
           '<section><p>반환 조건이 있습니다.</p></section>승인 전입니다.</div></article>')
    actual = parse_filing_html(raw, _feed_size=7)
    tree = lhtml.document_fromstring(raw).getroottree()
    assert [_resolved_text(tree, row['source_paths']) for row in actual['records']] == [
        '매출 1,000원입니다.', '반환 조건이 있습니다.', '승인 전입니다.']


def test_identical_prose_at_different_positions_has_distinct_source_paths():
    raw = '<div><p>매출은 미확정입니다.</p></div><div><p>매출은 미확정입니다.</p></div>'
    actual = parse_filing_html(raw, _feed_size=1)
    paths = [row['source_path'] for row in actual['records']]
    tree = lhtml.document_fromstring(raw).getroottree()
    assert len(paths) == len(set(paths)) == 2
    assert [_resolved_text(tree, [path]) for path in paths] == ['매출은 미확정입니다.'] * 2


def test_unit_period_and_following_footnote_cross_streaming_units():
    raw = ('<h2>III. 재무에 관한 사항</h2><h3>2. 연결재무제표</h3>'
           '<div><p>제 20 기 반기말</p></div><section><p>(단위: 백만원)</p></section>'
           '<div><table><caption>연결 현금흐름표</caption>'
           '<tr><td>영업현금흐름</td><td>-</td></tr></table></div>'
           '<section><p>주) 미확정 금액으로 변경될 수 있습니다.</p></section>')
    actual = parse_filing_html(raw, _feed_size=7)
    table = next(row for row in actual['records'] if row['kind'] == 'table')
    assert (table['scope'], table['context_before'], table['footnotes']) == (
        'consolidated', '제 20 기 반기말\n(단위: 백만원)\n연결 현금흐름표',
        '주) 미확정 금액으로 변경될 수 있습니다.')


def test_table_cell_locators_survive_preceding_sibling_release():
    raw = ('<div><p>앞부분입니다.</p></div><div><table><tr><td rowspan="2">제품</td>'
           '<td>10</td></tr><tr><td>-</td></tr></table></div>')
    actual = parse_filing_html(raw, _feed_size=7)
    tree = lhtml.document_fromstring(raw).getroottree()
    table = next(row for row in actual['records'] if row['kind'] == 'table')['table']
    assert [_resolved_text(tree, [cell['source_path']]) for cell in table['cells']] == ['제품', '10', '-']


def test_late_duplicate_major_heading_invalidates_previously_emitted_evidence():
    raw = ('<h2>II. 사업의 내용</h2><p>확정 계약이라고 기재되었습니다.</p>'
           + '<div><i></i></div>' * 1100 + '<h2>II. 사업의 내용</h2><p>다른 문서입니다.</p>')
    actual = parse_filing_html(raw, _feed_size=7)
    assert actual['records'] == []
    assert 'AMBIGUOUS_MAJOR_HEADINGS' in actual['errors']


def test_many_small_units_recover_late_material_clause_with_bounded_active_nodes():
    raw = ('<h2>II. 사업의 내용</h2>' + '<div>' + '<section><i></i></section>' * 16000
           + '<p>계약금은 승인 실패 시 전액 반환해야 합니다.</p></div>')
    actual = parse_filing_html(raw, _feed_size=8192)
    assert [row['text'] for row in actual['records']] == ['계약금은 승인 실패 시 전액 반환해야 합니다.']
    assert actual['streaming']['total_nodes'] > 30000
    assert actual['streaming']['peak_active_nodes'] < 2000


def test_whole_source_hash_includes_trailing_unselected_content():
    raw = '<p>매출입니다.</p><script>비공개 데이터</script><!-- 뒤쪽 주석 -->'
    actual = parse_filing_html(raw, _feed_size=1)
    assert actual['source_sha256'] == hashlib.sha256(raw.encode('utf-8')).hexdigest()


def test_streaming_revision_and_feed_provenance_are_explicit():
    actual = parse_filing_html('<p>매출입니다.</p>', _feed_size=7)
    assert (actual['parser_version'], actual['streaming']['feed_size']) == ('filing_html_v2', 7)


def test_navigation_and_link_only_runs_cannot_create_duplicate_major_headings():
    raw = ('<div role="navigation"><section><p>II. 사업의 내용</p></section></div>'
           '<p><a href="#business">II. 사업의 내용</a></p>'
           '<h2>II. 사업의 내용</h2><p>계약은 승인 조건부입니다.</p>')
    actual = parse_filing_html(raw, _feed_size=1)
    assert [(row['section_path'], row['text']) for row in actual['records']] == [
        (['II. 사업의 내용'], '계약은 승인 조건부입니다.')]


def test_many_comments_do_not_absorb_visible_tail_or_inflate_active_tree():
    raw = ('<div>' + '<!-- 제외할 문구 -->' * 32000
           + '승인 전에는 매출로 확정하지 않습니다.<p>주석 확인이 필요합니다.</p></div>')
    actual = parse_filing_html(raw, _feed_size=8192)
    assert [row['text'] for row in actual['records']] == [
        '승인 전에는 매출로 확정하지 않습니다.', '주석 확인이 필요합니다.']
    assert actual['streaming']['peak_active_nodes'] < 2000


def test_oversized_table_is_not_converted_into_unconditional_prose():
    raw = ('<h2>II. 사업의 내용</h2><table><tr><td>확정 매출 100</td></tr>'
           + '<tr><td>조건 검토 중</td></tr>' * 300 + '</table><p>주) 승인 시에만 발생합니다.</p>')
    actual = parse_filing_html(raw, _feed_size=7)
    assert not any('확정 매출 100' in row['text'] for row in actual['records'])
    assert actual['status'] != 'COMPLETE'


@pytest.mark.parametrize('feed_size', [0, -1, True, 65537, 1.5])
def test_feed_size_rejects_invalid_values(feed_size):
    with pytest.raises(ValueError):
        parse_filing_html('<p>매출입니다.</p>', _feed_size=feed_size)


@pytest.mark.parametrize('count,exceeded', [(10, False), (11, True)])
def test_wide_tables_preserve_document_grid_aggregate_limit(count, exceeded):
    # Each 40 x 300 table occupies 12,000 slots but only 40 source cells.
    table = '<table>' + '<tr><td colspan="300">조건부</td></tr>' * 40 + '</table>'
    actual = parse_filing_html('<h2>II. 사업의 내용</h2>' + table * count)
    assert actual['streaming']['accepted_grid_slots'] == 120000
    if exceeded:
        assert 'HTML_DOCUMENT_GRID_LIMIT' in actual['errors']
        assert actual['records'] == []
    else:
        assert actual['status'] == 'COMPLETE'
        assert len(actual['records']) == count


@pytest.mark.parametrize('count,exceeded', [(5, False), (6, True)])
def test_wide_tables_preserve_document_origin_cell_aggregate_limit(count, exceeded):
    table = '<table>' + ('<tr>' + '<td>1</td>' * 300 + '</tr>') * 40 + '</table>'
    actual = parse_filing_html('<h2>II. 사업의 내용</h2>' + table * count)
    assert actual['streaming']['accepted_origin_cells'] == 60000
    if exceeded:
        assert 'HTML_DOCUMENT_CELL_LIMIT' in actual['errors']
        assert actual['records'] == []
    else:
        assert actual['status'] == 'COMPLETE'
        assert len(actual['records']) == count
