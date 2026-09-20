import hashlib
import json

import pytest

from prism_core.filing_html import parse_filing_html
from tools.evaluate_large_filing_html import audit_source_paths, evaluate_case

HTML = ('<html><body><h2>III. 재무에 관한 사항</h2><h3>3. 연결재무제표 주석</h3>'
        '<p>매출채권은 1<span>,000</span>원입니다.</p><p>단위: 원</p>'
        '<table><tr><th>항목</th><th>당기</th></tr><tr><td>매출채권</td><td>1000</td></tr></table>'
        '<p>주) 추정 금액입니다.</p></body></html>')


def test_full_document_prose_cell_and_note_locators_are_audited():
    result = audit_source_paths(HTML, parse_filing_html(HTML))
    assert not result['errors']
    assert result['checked_unique_paths'] >= 7


def test_fabricated_cell_path_is_not_accepted():
    parsed = parse_filing_html(HTML)
    table = next(r for r in parsed['records'] if r['kind'] == 'table')
    table['table']['cells'][0]['source_path'] = '/html/body/table[99]/tr[1]/td[1]'
    assert audit_source_paths(HTML, parsed)['errors']


def test_mutated_source_text_is_not_accepted():
    parsed = parse_filing_html(HTML)
    parsed['records'][0]['text'] = '매출채권은 9,999원입니다.'
    assert audit_source_paths(HTML, parsed)['errors'][0]['reason'] == 'PROSE_LOCATOR_TEXT_MISMATCH'


def _context_fixture():
    html = ('<html><body><table><tr><td>충당부채</td><td>당기</td><td>단위: 백만원</td></tr></table>'
            '<table><tr><th>항목</th><th>금액</th></tr><tr><td>기말</td><td>0</td></tr></table></body></html>')
    parsed = parse_filing_html(html)
    record = parsed['records'][1]
    record['context_before'] = '기존 prose 문맥\n충당부채\n당기\n단위: 백만원'
    record['context_paths'] = [f'/html/body/table[1]/tr/td[{i}]' for i in (1, 2, 3)]
    return html, parsed


def test_layout_context_paths_resolve_without_requiring_all_prose_locators():
    html, parsed = _context_fixture()
    assert not audit_source_paths(html, parsed)['errors']


@pytest.mark.parametrize('last', [
    '<table class="nb"><tr><td>(주2) 소송은 미확정입니다.</td></tr></table>',
    '<p>(주2) 소송은 <span>미확정</span>입니다.</p>',
])
def test_separate_footnote_cells_keep_boundaries_without_splitting_inline_text(last):
    html = ('<html><body><table><tr><td>충당부채</td><td>100</td></tr></table>'
            '<table class="nb"><tr><td>(주1) 최선의 추정치입니다.</td></tr></table>' + last + '</body></html>')
    parsed = parse_filing_html(html)
    target = parsed['records'][0]
    assert target['footnotes'] == '(주1) 최선의 추정치입니다.\n(주2) 소송은 미확정입니다.'
    assert not audit_source_paths(html, parsed)['errors']


@pytest.mark.parametrize('mutation', ['missing', 'value', 'order', 'duplicate', 'later'])
def test_layout_context_locator_corruption_is_rejected(mutation):
    html, parsed = _context_fixture()
    record = parsed['records'][1]
    if mutation == 'missing':
        record['context_paths'][0] = '/html/body/table[99]/tr/td'
    elif mutation == 'value':
        record['context_before'] = record['context_before'].replace('백만원', '천원')
    elif mutation == 'order':
        record['context_paths'].reverse()
    elif mutation == 'duplicate':
        record['context_paths'].append(record['context_paths'][0])
    else:
        record['context_paths'] = ['/html/body/table[2]/tr[2]/td[2]']
        record['context_before'] = '0'
    assert audit_source_paths(html, parsed)['errors']


def test_input_hash_and_actual_final_packet_are_measured(tmp_path):
    path = tmp_path / 'fixture.json'
    data = {'html': HTML, 'markdown': '## III. 재무에 관한 사항\n### 3. 연결재무제표 주석\n\n매출채권은 1,000원입니다.\n\n',
            'metadata': {'sourceURL': 'https://kind.krx.co.kr/example.html', 'statusCode': 200}}
    path.write_text(json.dumps(data))
    case = {'file': str(path), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest(), 'symbol': 'TEST', 'name': 'fixture'}
    out = evaluate_case(case)
    assert out['record_count'] and not out['provenance_audit']['errors']
    assert out['network_calls'] == out['model_calls'] == 0
    assert all(size <= 6000 for size in out['section_utf8_bytes'].values())
    with pytest.raises(ValueError, match='INPUT_FILE_HASH_MISMATCH'):
        evaluate_case({**case, 'sha256': 'wrong'})
