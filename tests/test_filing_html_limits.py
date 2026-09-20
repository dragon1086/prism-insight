"""HTML admission is independent of structural, text and packet budgets."""
import hashlib

import pytest

from prism_core import filing_html

LIMIT = 8 * 1024 * 1024


def padded(size, *, multibyte=False):
    prefix = '<html><body><!--'
    suffix = '--><p>계약금은 승인 실패 시 반환합니다.</p></body></html>'
    room = size - len((prefix + suffix).encode())
    fill = '가' * (room // 3) + 'x' * (room % 3) if multibyte else 'x' * room
    return prefix + fill + suffix


@pytest.mark.parametrize('size', [2 * 1024 * 1024 + 1, LIMIT])
@pytest.mark.parametrize('multibyte', [False, True])
def test_large_html_retains_late_condition_and_exact_source_hash(size, multibyte):
    raw = padded(size, multibyte=multibyte)
    assert len(raw.encode()) == size
    result = filing_html.parse_filing_html(raw)
    assert result['status'] == 'COMPLETE', result['errors']
    assert [r['text'] for r in result['records']] == ['계약금은 승인 실패 시 반환합니다.']
    assert result['source_sha256'] == hashlib.sha256(raw.encode()).hexdigest()
    assert result['records'][0]['source_path'] == '/html[1]/body[1]/p[1]'


@pytest.mark.parametrize('multibyte', [False, True])
def test_one_byte_above_html_limit_rejects_before_parsing(multibyte):
    result = filing_html.parse_filing_html(padded(LIMIT + 1, multibyte=multibyte))
    assert result['errors'] == ['HTML_BYTE_LIMIT']
    assert not result['records'] and result['source_sha256'] is None


def test_time_limit_discards_staged_records_and_headings(monkeypatch):
    now = [0.0]
    original = filing_html._Stream.event

    def event(self, kind, node):
        original(self, kind, node)
        if self.reducer.out['records']:
            now[0] = 11.0

    monkeypatch.setattr(filing_html, 'monotonic', lambda: now[0], raising=False)
    monkeypatch.setattr(filing_html._Stream, 'event', event)
    result = filing_html.parse_filing_html(
        '<h2>II. 사업의 내용</h2><p>계약입니다.</p><p>승인 조건부입니다.</p>',
        _capture_headings=True)
    assert result['status'] == 'LIMIT_EXCEEDED'
    assert result['errors'] == ['HTML_TIME_LIMIT']
    assert not result['records'] and not result['heading_events']
    assert result['document_complete'] is False


def test_time_limit_checked_after_final_parser_event(monkeypatch):
    now = [0.0]
    original = filing_html._Stream.event

    def event(self, kind, node):
        original(self, kind, node)
        if kind == 'end' and node.tag == 'html':
            now[0] = 11.0

    monkeypatch.setattr(filing_html, 'monotonic', lambda: now[0], raising=False)
    monkeypatch.setattr(filing_html._Stream, 'event', event)
    result = filing_html.parse_filing_html('<p>확정으로 반환하면 안 됩니다.</p>')
    assert result['errors'] == ['HTML_TIME_LIMIT']
    assert result['records'] == []


def test_markdown_limit_is_not_raised():
    from prism_core.filing_report_evidence import filing_blocks

    blocks, gaps = filing_blocks({'markdown': 'x' * (2 * 1024 * 1024 + 1)}, 'https://example.com')
    assert not blocks and gaps == ['FILING_MARKDOWN_BYTE_LIMIT']


@pytest.mark.parametrize('body,code', [
    ('<p>검증 전입니다.</p>' * 2001, 'HTML_RECORD_LIMIT'),
    ('<p>' + '<!--x-->' * 30001 + '</p>', 'HTML_ACTIVE_NODE_LIMIT'),
    ('<div>' * 102 + '<p>조건입니다.</p>' + '</div>' * 102, 'HTML_DEPTH_LIMIT'),
    (('<table>' + '<tr><td colspan="300">조건부</td></tr>' * 40 + '</table>') * 11,
     'HTML_DOCUMENT_GRID_LIMIT'),
    (('<table>' + ('<tr>' + '<td>1</td>' * 300 + '</tr>') * 40 + '</table>') * 6,
     'HTML_DOCUMENT_CELL_LIMIT'),
])
def test_large_html_preserves_independent_structure_limits(body, code):
    raw = '<!--' + 'x' * (2 * 1024 * 1024) + '-->' + body
    result = filing_html.parse_filing_html(raw, _capture_headings=True)
    assert result['status'] == 'LIMIT_EXCEEDED'
    assert result['errors'] == [code]
    assert not result['records'] and not result['heading_events']


def test_large_html_preserves_locator_budget(monkeypatch):
    monkeypatch.setattr(filing_html, '_MAX_LOCATOR_BYTES', 100)
    raw = '<!--' + 'x' * (2 * 1024 * 1024) + '-->' + '<div><p>조건부입니다.</p></div>' * 5
    result = filing_html.parse_filing_html(raw)
    assert result['errors'] == ['HTML_DOCUMENT_LOCATOR_LIMIT']
    assert not result['records']


def test_parent_admission_deadline_is_not_reset(monkeypatch):
    monkeypatch.setattr(filing_html, 'monotonic', lambda: 20.0)
    result = filing_html.parse_filing_html('<p>이전 단계에서 기한이 소진됐습니다.</p>', _deadline=19.0)
    assert result['errors'] == ['HTML_TIME_LIMIT'] and not result['records']


@pytest.mark.parametrize('value', [True, '10', float('inf'), float('nan')])
def test_parent_deadline_rejects_non_finite_or_non_numeric_values(value):
    with pytest.raises(ValueError):
        filing_html.parse_filing_html('<p>본문</p>', _deadline=value)
