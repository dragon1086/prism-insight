import hashlib
import json
from urllib.parse import urlencode

from prism_core.dart_report_evidence import section_blocks


def fixture():
    html = ('<h2>3. 연결재무제표 주석</h2><h3>38. 차입금 및 약정사항</h3>'
            '<p>차입금 약정이 있으나 위반한 사실은 없습니다.</p>'
            '<p>다만 향후 조건 변경 시 조기상환 의무가 발생할 수 있습니다.</p>')
    node = {'rcpNo': '20260814001631', 'dcmNo': '12345678', 'eleId': '3',
            'offset': '100', 'length': '1000', 'dtd': 'dart4.xsd'}
    section = {'html': html, 'tuple': node,
               'url': 'https://dart.fss.or.kr/report/viewer.do?' + urlencode(node),
               'sha256': hashlib.sha256(html.encode()).hexdigest(), 'utf8_bytes': len(html.encode())}
    row = {'receipt_id': node['rcpNo'], 'scope': 'consolidated', 'scope_verified': True,
           'body_status': 'available', 'kind': 'interim', 'period_start': '2026-01-01',
           'period_end': '2026-06-30', 'submitted_date': '2026-08-14'}
    return row, section

def test_section_hash_url_scope_or_receipt_mismatch_fails_closed():
    for mutation in ({'sha256': '0' * 64}, {'url': 'https://example.com/body'},
                     {'tuple': {'rcpNo': '20260814000000'}}, {'utf8_bytes': 1}):
        row, section = fixture()
        assert section_blocks(row, {**section, **mutation})[0] == []
    row, section = fixture()
    assert section_blocks({**row, 'scope': 'standalone'}, section)[0] == []
    assert section_blocks({**row, 'scope_verified': False}, section)[0] == []

def test_unknown_scope_not_silently_assigned_and_oversize_not_sliced():
    row, section = fixture()
    for html in ('<p>차입금 약정은 위반하지 않았습니다.</p>', ' ' * (8 * 1024 * 1024 + 1)):
        section.update(html=html, sha256=hashlib.sha256(html.encode()).hexdigest(), utf8_bytes=len(html.encode()))
        blocks, gaps = section_blocks(row, section)
        assert not blocks and gaps
        assert 'html' not in json.dumps(gaps).lower()
