"""Streaming admission limits and semantic continuity, without provider I/O."""

import pytest
from lxml import html as lhtml

from prism_core.filing_html import parse_filing_html
from prism_core.filing_html_tables import parse_html_table


@pytest.mark.parametrize('feed_size', [1, 13, 8192, 65536])
def test_nested_containers_keep_inline_runs_scope_and_condition(feed_size):
    raw = ('<html><body><section><h2>III. 재무에 관한 사항</h2>'
           '<div><h3>3. 연결재무제표 주석</h3>'
           '계약 1<span>,000</span>원입니다.<!--원문 주석-->'
           '<article><p>단, 해지 시 반환해야 합니다.</p></article>'
           '후속 조건입니다.</div></section></body></html>')
    out = parse_filing_html(raw, _feed_size=feed_size)
    assert out['status'] == 'COMPLETE'
    assert [r['text'] for r in out['records']] == [
        '계약 1,000원입니다.', '단, 해지 시 반환해야 합니다.', '후속 조건입니다.']
    assert [r['event_index'] for r in out['records']] == [2, 3, 4]
    assert all(r['scope'] == 'consolidated' for r in out['records'])
    tree = lhtml.document_fromstring(raw).getroottree()
    for record in out['records']:
        values = [tree.xpath(p)[0] for p in record['source_paths']]
        literal = ''.join(v.text_content() if hasattr(v, 'text_content') else str(v) for v in values)
        assert literal == record['text']


def test_record_limit_discards_unqualified_prefix_instead_of_returning_it():
    raw = ('<h2>II. 사업의 내용</h2>' + '<p>계약 금액입니다.</p>' * 2000
           + '<p>단, 조건을 충족하지 못하면 반환합니다.</p>')
    out = parse_filing_html(raw)
    assert out['status'] == 'LIMIT_EXCEEDED'
    assert out['errors'] == ['HTML_RECORD_LIMIT']
    assert out['records'] == []


def test_comments_inside_one_atomic_unit_count_toward_active_node_limit():
    out = parse_filing_html('<p>매출입니다.' + '<!--x-->' * 30001 + '</p>')
    assert out['status'] == 'LIMIT_EXCEEDED'
    assert out['errors'] == ['HTML_ACTIVE_NODE_LIMIT']
    assert not out['records']


def test_large_nontransparent_wrapper_is_explicit_limit_not_silent_loss():
    out = parse_filing_html('<custom-wrapper>' + '<p><span>본문입니다.</span></p>' * 15001 + '</custom-wrapper>')
    assert out['status'] == 'LIMIT_EXCEEDED'
    assert out['errors'] == ['HTML_ACTIVE_NODE_LIMIT']
    assert not out['records']


def test_table_locator_injection_survives_removed_previous_sibling():
    root = lhtml.document_fromstring(
        '<body><table><tr><td>첫째</td></tr></table>'
        '<table><tr><td rowspan="2">둘째</td></tr><tr/></table></body>')
    tables = root.xpath('//table')
    paths = {node: node.getroottree().getpath(node) for node in tables[1].iter()}
    tables[0].getparent().remove(tables[0])
    out = parse_html_table(tables[1], path_resolver=paths.__getitem__)
    assert out['status'] == 'COMPLETE'
    assert out['source_path'] == '/html/body/table[2]'
    assert out['cells'][0]['source_path'] == '/html/body/table[2]/tr[1]/td'


def test_failed_atomic_table_breaks_context_and_footnote_adjacency():
    raw = ('<div><p>단위: 원</p><table><tr><td>확인 수치</td></tr></table>'
           '<section><table><tr><td colspan="bad">미확인 수치</td></tr></table></section>'
           '<p>주) 미확인 수치에 대한 조건입니다.</p></div>')
    out = parse_filing_html(raw, _feed_size=7)
    table = next(r for r in out['records'] if r['kind'] == 'table')
    assert table['context_before'] == '단위: 원'
    assert not table['footnotes']
    assert out['records'][-1]['text'] == '주) 미확인 수치에 대한 조건입니다.'


def test_truncated_deep_document_never_returns_staged_prose():
    out = parse_filing_html('<p>먼저 읽힌 수치입니다.</p>' + '<div>' * 102 + '<p>조건입니다.')
    assert out['status'] == 'LIMIT_EXCEEDED'
    assert not out['records']


@pytest.mark.parametrize('feed_size', [1, 8192])
def test_post_document_reopened_root_has_no_fabricated_duplicate_paths(feed_size):
    # libxml emits a second root that document_fromstring does not retain.
    out = parse_filing_html('<div>본문입니다.</div></html><div>조건입니다.</div>',
                            _feed_size=feed_size)
    assert out['status'] == 'UNSUPPORTED'
    assert out['errors'] == ['HTML_MULTIPLE_DOCUMENT_ROOTS']
    assert not out['records']


@pytest.mark.parametrize('wrapper', ['main', 'form', 'aside', 'header', 'footer'])
def test_standard_wrapper_releases_large_sibling_units(wrapper):
    raw = (f'<{wrapper}><h2>II. 사업의 내용</h2>'
           + ('<p>매출 ' + '<span>확인</span>' * 33 + '입니다.</p>') * 1000
           + f'<p>단, 조건부 계약입니다.</p></{wrapper}>')
    out = parse_filing_html(raw)
    assert out['status'] == 'COMPLETE'
    assert len(out['records']) == 1001
    assert out['records'][-1]['text'] == '단, 조건부 계약입니다.'
    assert out['streaming']['total_nodes'] > 30000
    assert out['streaming']['peak_active_nodes'] < 100


@pytest.mark.parametrize('wrapper', ['main', 'form', 'aside', 'header', 'footer'])
@pytest.mark.parametrize('feed_size', [1, 8192])
def test_leaf_standard_wrapper_does_not_split_inline_number(wrapper, feed_size):
    raw = f'<div>매출 1<{wrapper}><span>,000</span></{wrapper}>원입니다.<p>조건입니다.</p></div>'
    out = parse_filing_html(raw, _feed_size=feed_size)
    assert [r['text'] for r in out['records']] == ['매출 1,000원입니다.', '조건입니다.']
    tree = lhtml.document_fromstring(raw).getroottree()
    row = out['records'][0]
    pieces = [tree.xpath(p)[0] for p in row['source_paths']]
    assert ''.join(v.text_content() if hasattr(v, 'text_content') else str(v) for v in pieces) == row['text']


def test_nested_lazy_wrappers_promote_ancestors_before_first_block():
    raw = ('<div>앞문장입니다.<main>주문액입니다.<aside>내부 조건입니다.'
           '<p>단, 취소 가능합니다.</p>후속 조건입니다.</aside>뒤문장입니다.</main></div>')
    out = parse_filing_html(raw, _feed_size=1)
    assert [r['text'] for r in out['records']] == [
        '앞문장입니다.', '주문액입니다.', '내부 조건입니다.', '단, 취소 가능합니다.',
        '후속 조건입니다.', '뒤문장입니다.']


@pytest.mark.parametrize('feed_size', [1, 8192])
def test_word_export_colon_tag_keeps_literal_name_and_numeric_text(feed_size):
    raw = '<main>1<o:p>,000</o:p>원<p>단, 조건부입니다.</p></main>'
    out = parse_filing_html(raw, _feed_size=feed_size)
    assert out['status'] == 'COMPLETE'
    assert [r['text'] for r in out['records']] == ['1,000원', '단, 조건부입니다.']
    first = out['records'][0]
    assert any("*[name()='o:p'][1]" in path for path in first['source_paths'])
    tree = lhtml.document_fromstring(raw).getroottree()
    values = [tree.xpath(path)[0] for path in first['source_paths']]
    assert ''.join(v.text_content() if hasattr(v, 'text_content') else str(v) for v in values) == '1,000원'


def test_invalid_tag_name_never_enters_an_xpath_literal():
    out = parse_filing_html('<main><x\'y>수치입니다.</x\'y><p>조건입니다.</p></main>')
    assert out['status'] == 'UNSUPPORTED'
    assert out['errors'] == ['HTML_TAG_NAME_UNSUPPORTED']
    assert not out['records']


def test_document_grid_amplification_cannot_accumulate_across_small_tables():
    table = ('<table><tr><td rowspan="150" colspan="80">매출</td></tr>'
             + '<tr></tr>' * 149 + '</table>')
    out = parse_filing_html(table * 64)
    assert out['status'] == 'LIMIT_EXCEEDED'
    assert out['errors'] == ['HTML_DOCUMENT_GRID_LIMIT']
    assert out['records'] == []
    assert out['streaming']['accepted_grid_slots'] == 120000
    assert out['streaming']['accepted_origin_cells'] == 10


def test_document_origin_cells_have_an_independent_cumulative_limit():
    table = '<table>' + ('<tr>' + '<td>1</td>' * 80 + '</tr>') * 100 + '</table>'
    out = parse_filing_html(table * 8)
    assert out['status'] == 'LIMIT_EXCEEDED'
    assert out['errors'] == ['HTML_DOCUMENT_CELL_LIMIT']
    assert out['records'] == []
    assert out['streaming']['accepted_origin_cells'] == 56000
    assert out['streaming']['accepted_grid_slots'] == 56000


def test_deep_inline_locator_amplification_has_a_document_budget():
    raw = ('<div>' * 60 + '<span></span>' * 40000
           + '매출채권의 손실충당금입니다.' + '</div>' * 60)
    out = parse_filing_html(raw)
    assert out['status'] == 'LIMIT_EXCEEDED'
    assert out['errors'] == ['HTML_DOCUMENT_LOCATOR_LIMIT']
    assert not out['records']
    assert out['streaming']['constructed_locator_bytes'] <= 16 * 1024 * 1024


@pytest.mark.parametrize('raw', [
    '<div>앞문장<p>내부</p>뒤문장</div>',
    '<p>매출<b>100<div>조건</div>후속</b></p>',
])
def test_locator_budget_also_charges_container_and_atomic_text_slots(raw, monkeypatch):
    import prism_core.filing_html as parser_module

    root = lhtml.document_fromstring(raw)

    def original_path(node):
        parts = []
        for item in [*reversed(list(node.iterancestors())), node]:
            ordinal = 1 + sum(previous.tag == item.tag for previous in item.itersiblings(preceding=True))
            parts.append(f'{item.tag}[{ordinal}]')
        return '/' + '/'.join(parts)

    element_bytes = sum(len(original_path(node)) for node in root.iter())
    baseline = parse_filing_html(raw)
    assert baseline['streaming']['constructed_locator_bytes'] > element_bytes
    monkeypatch.setattr(parser_module, '_MAX_LOCATOR_BYTES', element_bytes)
    out = parse_filing_html(raw)
    assert out['errors'] == ['HTML_DOCUMENT_LOCATOR_LIMIT']
    assert not out['records']
