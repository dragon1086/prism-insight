"""Source boundaries, not financial truth, are this parser's contract."""
import hashlib

from prism_core.filing_html import parse_filing_html


def test_toc_does_not_steal_business_section_and_scope_resets():
    raw = ('<nav><p>II. 사업의 내용</p><p>III. 재무에 관한 사항</p></nav>'
           '<h2>II. 사업의 내용</h2><p>주요 제품 매출은 원문 주장입니다.</p>'
           '<h2>III. 재무에 관한 사항</h2><h3>3. 연결재무제표 주석</h3>'
           '<p>매출채권의 손실충당금은 확인이 필요합니다.</p>'
           '<h3>5. 재무제표 주석</h3><p>별도 매출채권입니다.</p>'
           '<h2>IV. 이사의 경영진단 및 분석의견</h2><p>사업 위험입니다.</p>')
    result = parse_filing_html(raw)
    rows = result['records']
    assert result['source_sha256'] == hashlib.sha256(raw.encode()).hexdigest()
    assert len(rows) == 4
    assert rows[0]['section_path'] == ['II. 사업의 내용']
    assert [r['scope'] for r in rows] == ['unknown', 'consolidated', 'standalone', 'unknown']
    assert all(r['source_path'] for r in rows)


def test_unmarked_duplicate_major_headings_are_not_guessed():
    raw = ('<p>II. 사업의 내용</p><p>III. 재무에 관한 사항</p>'
           '<p>II. 사업의 내용</p><p>주요 제품 매출입니다.</p>'
           '<p>III. 재무에 관한 사항</p><p>현금흐름 위험입니다.</p>')
    out = parse_filing_html(raw)
    assert out['status'] == 'UNSUPPORTED'
    assert out['records'] == []
    assert 'AMBIGUOUS_MAJOR_HEADINGS' in out['errors']


def test_table_keeps_local_unit_period_and_following_note():
    raw = ('<h2>III. 재무에 관한 사항</h2><h3>2. 연결재무제표</h3>'
           '<p>제 20 기 반기말</p><p>(단위: 백만원)</p>'
           '<table><tr><th>항목</th><th>당반기</th><th>전기</th></tr>'
           '<tr><td>매출액</td><td>-</td><td>12</td></tr></table>'
           '<p>※ 잠정 수치로 확정되지 않았습니다.</p>'
           '<h3>4. 재무제표</h3><p>별도 현금흐름입니다.</p>')
    out = parse_filing_html(raw)
    row = next(r for r in out['records'] if r['kind'] == 'table')
    assert row['scope'] == 'consolidated'
    assert '백만원' in row['context_before'] and '반기말' in row['context_before']
    assert '잠정' in row['footnotes']
    assert '-' in row['text'] and '"12"' in row['text']
    assert row['table']['cells'][4]['text'] == '-'


def test_scripts_hidden_navigation_and_head_title_not_evidence():
    raw = ('<html><head><title>사업보고서</title></head><body>'
           '<h2>II. 사업의 내용</h2><p>주요 제품 매출입니다.</p>'
           '<script>가짜매출</script><p hidden>숨긴매출</p>'
           '<p style="display: none">비공개매출</p>'
           '<p><a href="#toc">III. 재무에 관한 사항</a></p></body></html>')
    out = parse_filing_html(raw)
    assert len(out['records']) == 1
    assert out['records'][0]['text'] == '주요 제품 매출입니다.'


def test_unsupported_table_is_a_gap_not_fallback_prose():
    raw = '<h2>II. 사업의 내용</h2><table><tr><td colspan="oops">매출 10</td></tr></table>'
    out = parse_filing_html(raw)
    assert out['status'] == 'PARTIAL'
    assert not out['records']
    assert 'TABLE_UNSUPPORTED' in out['errors']


def test_input_and_node_limits_fail_without_partial_evidence():
    assert parse_filing_html('x' * (8 * 1024 * 1024 + 1))['status'] == 'LIMIT_EXCEEDED'
    assert parse_filing_html('<p>x</p>' * 30001)['records'] == []


def test_no_scope_or_period_inferred_from_report_title_or_numbers():
    out = parse_filing_html('<h1>반기보고서</h1><p>매출액 2026년 100입니다.</p>')
    assert out['records'][0]['scope'] == 'unknown'
    assert out['records'][0]['context_before'] == ''


def test_inline_numbers_and_caption_preserved():
    out = parse_filing_html('<h2>II. 사업의 내용</h2><p>매출 1<span>,000</span>원입니다.</p>'
                            '<table><caption>단위: 백만원</caption><tr><td>매출</td><td>10</td></tr></table>')
    assert out['records'][0]['text'] == '매출 1,000원입니다.'
    assert out['records'][1]['context_before'] == '단위: 백만원'


def test_parser_depth_truncation_never_claims_complete():
    out = parse_filing_html('<div>' * 300 + '<p>매출입니다.</p>' + '</div>' * 300)
    assert out['status'] == 'LIMIT_EXCEEDED'
    assert not out['records']


def test_korean_footnote_is_not_a_subheading():
    out = parse_filing_html('<h2>II. 사업의 내용</h2><table><tr><td>매출</td><td>-</td></tr></table>'
                            '<p>주) 금액은 확인되지 않았습니다.</p>')
    assert out['records'][0]['footnotes'] == '주) 금액은 확인되지 않았습니다.'


def test_mixed_container_text_and_tails_are_not_lost():
    out = parse_filing_html('<h2>II. 사업의 내용</h2><div>매출 100입니다.'
                            '<p>단위: 백만원</p><table><tr><td>매출</td><td>100</td></tr></table>'
                            '※ 감사 미확정입니다.</div>')
    assert out['records'][0]['text'] == '매출 100입니다.'
    table = next(r for r in out['records'] if r['kind'] == 'table')
    assert '미확정' in table['footnotes']
    assert table['context_before'] == '단위: 백만원'


def test_failed_table_is_a_footnote_and_context_barrier():
    out = parse_filing_html('<h2>II. 사업의 내용</h2>'
                            '<table><tr><td>매출</td><td>10</td></tr></table>'
                            '<table><tr><td colspan="bad">부채</td></tr></table>'
                            '<p>주) 부채는 미확정입니다.</p>')
    assert out['records'][0]['footnotes'] == ''
    assert out['records'][1]['text'] == '주) 부채는 미확정입니다.'


def test_numbered_table_never_becomes_heading():
    out = parse_filing_html('<h2>II. 사업의 내용</h2>'
                            '<table><tr><td>1.</td><td>매출</td><td>100</td></tr></table>')
    assert out['records'][0]['kind'] == 'table'
    assert out['records'][0]['section_path'] == ['II. 사업의 내용']


def test_hyphen_subsection_retains_parent_scope_and_numbered_prose_not_dropped():
    out = parse_filing_html('<h2>III. 재무에 관한 사항</h2><p>2. 연결재무제표</p>'
                            '<p>2-1. 연결재무상태표</p><table><tr><td>자산</td><td>10</td></tr></table>'
                            '<p>가. 매출은 아직 확정되지 않았습니다.</p>')
    assert out['records'][0]['scope'] == 'consolidated'
    assert out['records'][0]['section_path'][-2:] == ['2. 연결재무제표', '2-1. 연결재무상태표']
    assert out['records'][1]['text'] == '가. 매출은 아직 확정되지 않았습니다.'


def test_collapsed_prose_hidden():
    out = parse_filing_html('<h2>II. 사업의 내용</h2><p style="visibility:collapse">비밀 매출</p><p>사업 내용입니다.</p>')
    assert len(out['records']) == 1
    assert out['records'][0]['text'] == '사업 내용입니다.'


def test_mixed_container_preserves_inline_number_and_resolvable_slots():
    from lxml import html as lhtml

    raw = '<div>매출 1<span>,000</span>원입니다.<p>단위: 원</p></div>'
    out = parse_filing_html(raw)
    assert [r['text'] for r in out['records']] == ['매출 1,000원입니다.', '단위: 원']
    tree = lhtml.fromstring(raw).getroottree()
    pieces = []
    for path in out['records'][0]['source_paths']:
        node = tree.xpath(path)[0]
        pieces.append(node.text_content() if hasattr(node, 'text_content') else str(node))
    assert ''.join(pieces) == '매출 1,000원입니다.'


def test_invalid_unicode_is_explicit_failure_and_html_repairs_are_visible():
    assert parse_filing_html('\ud800')['errors'] == ['HTML_ENCODING_INVALID']
    repaired = parse_filing_html('<h2>II. 사업의 내용</h2><p>매출액 100억원입니다.</badtag>')
    assert repaired['status'] == 'PARTIAL'
    assert repaired['errors'] == ['HTML_RECOVERED_WITH_ERRORS']
