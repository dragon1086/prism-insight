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
