"""Large viewer bodies keep independent resource and transport bounds."""
import hashlib

import httpx
import pytest
from test_dart_report_evidence import fixture
from test_dart_section_collection import BODY, NOTES, run_sections

from prism_core import dart_public_filings as dart
from prism_core import dart_section_html as bounded
from prism_core.dart_report_evidence import _admit_section, section_blocks


@pytest.fixture(autouse=True)
def no_wall_pacing(monkeypatch):
    async def no_wait(seconds):
        pass
    monkeypatch.setattr(dart.asyncio, 'sleep', no_wait)


def test_large_financial_and_notes_are_admitted():
    financial = BODY + '<!--' + 'x' * (2 * 1024 * 1024) + '-->'
    notes = NOTES + '<!--' + 'y' * (2 * 1024 * 1024) + '-->'
    result, requests = run_sections(financial=financial, notes=notes)
    row = result['filings'][0]
    assert row['section_delivery']['status'] == 'AVAILABLE'
    assert row['sections']['financial_statements']['html'] == financial
    assert row['sections']['financial_notes']['html'] == notes
    assert len(requests) == 5


def test_large_helper_rejects_total_nodes_including_comments():
    body = BODY + '<!--x-->' * 100001 + '<!--' + 'x' * (2 * 1024 * 1024) + '-->'
    result, _ = run_sections(financial=body)
    row = result['filings'][0]
    assert row['body_status'] == 'unavailable'
    assert 'SECTION_HTML_NODE_LIMIT' in row['errors']
    assert 'financial_statements' not in row['sections']


def test_large_helper_rejects_depth():
    body = '<div>' * 101 + BODY + '</div>' * 101 + '<!--' + 'x' * (2 * 1024 * 1024) + '-->'
    result, _ = run_sections(financial=body)
    assert 'SECTION_HTML_DEPTH_LIMIT' in result['filings'][0]['errors']


def test_exact_eight_mib_stops_on_aggregate_without_fallback():
    notes = NOTES + ' ' * (8 * 1024 * 1024 - len(NOTES.encode()))
    result, requests = run_sections(notes=notes)
    row = result['filings'][0]
    assert 'financial_notes' not in row['sections']
    assert row['note_fragment_selection']['requested_keys'] == []
    assert row['note_fragment_selection']['stop_reason'] == 'RESPONSE_BYTES_EXCEEDED'
    assert len(requests) == 5


@pytest.mark.parametrize('character', ['x', '한'])
@pytest.mark.parametrize('extra', [0, 1])
def test_admission_exact_utf8_eight_mib_boundary(character, extra):
    row, section = fixture()
    size = 8 * 1024 * 1024 + extra
    body = character * (size // len(character.encode()))
    body += ' ' * (size - len(body.encode()))
    section.update(html=body, utf8_bytes=size, sha256=hashlib.sha256(body.encode()).hexdigest())
    assert (_admit_section(row, section) == body) is (extra == 0)


def test_large_section_late_condition_reaches_bounded_packet():
    from prism_core.kr_official_report_inputs import _render
    row, section = fixture()
    body = '<!--' + 'x' * (2 * 1024 * 1024) + '-->' + section['html']
    section.update(html=body, utf8_bytes=len(body.encode()), sha256=hashlib.sha256(body.encode()).hexdigest())
    blocks, gaps = section_blocks(row, section)
    assert blocks and not gaps
    result = _render({
        'sources': [{'source_id': 'D1', 'url': section['url'], 'blocks': blocks,
                     'published': row['submitted_date'], 'filing': {**row, 'role': 'primary'}}],
        'gaps': gaps, 'calls': 0}, '검증회사')
    assert any('다만' in note for note in result['section_contexts'].values())
    assert all(len(note.encode()) <= 24000 for note in result['section_contexts'].values())


@pytest.mark.parametrize('path', ['catalog', 'main', 'cover'])
def test_non_section_control_responses_keep_two_mib(path):
    def hook(request):
        matched = (request.method == 'POST' if path == 'catalog' else
                   request.url.path.endswith('main.do') if path == 'main' else
                   request.url.params.get('eleId') == '0')
        if matched:
            return httpx.Response(200, content=b'x' * (2 * 1024 * 1024 + 1))
    result, requests = run_sections(response_hook=hook)
    errors = result['errors'] + [e for row in result['filings'] for e in row['errors']]
    assert 'RESPONSE_BYTES_EXCEEDED' in errors
    assert len(requests) == {'catalog': 1, 'main': 2, 'cover': 3}[path]


def test_provenance_only_financial_path_keeps_two_mib():
    result, requests = run_sections(include=False, financial=BODY + ' ' * (2 * 1024 * 1024))
    row = result['filings'][0]
    assert 'RESPONSE_BYTES_EXCEEDED' in row['errors']
    assert row['body_status'] == 'unavailable'
    assert 'financial_statements' not in row['sections']
    assert len(requests) == 4


def test_aggregate_exact_and_one_byte_over():
    baseline, _ = run_sections()
    remaining = dart._TOTAL_LIMIT - baseline['metrics']['response_bytes']
    for extra in (0, 1):
        result, requests = run_sections(notes=NOTES + ' ' * (remaining + extra))
        row = result['filings'][0]
        assert result['metrics']['response_bytes'] == dart._TOTAL_LIMIT + extra
        assert ('financial_notes' in row['sections']) is (extra == 0)
        assert len(requests) == 5


def test_bounded_preview_matches_old_text_without_joining_full_document():
    body = '<html><body> first<span>mid</span>\n\t <p>한 글</p>tail<!--ignore-->' + 'z' * 2000 + '</body></html>'
    scan = bounded.SectionHTML(body)
    assert scan.text(scan.root, limit=1200) == dart._text(dart._tree(body))[:1200]
    assert scan.text(scan.root, compact=True) == dart._compact(dart._text(dart._tree(body)))


def test_bounded_total_node_boundary_counts_comments():
    body = '<html><body>' + '<!--x-->' * (bounded.MAX_NODES - 2) + '</body></html>'
    assert bounded.SectionHTML(body).root is not None
    with pytest.raises(bounded.SectionHTMLLimit, match='SECTION_HTML_NODE_LIMIT'):
        bounded.SectionHTML(body.replace('</body>', '<!--one--></body>'))


def test_bounded_depth_boundary():
    body = '<html><body>' + '<div>' * 98 + '</div>' * 98 + '</body></html>'
    bounded.SectionHTML(body)
    with pytest.raises(bounded.SectionHTMLLimit, match='SECTION_HTML_DEPTH_LIMIT'):
        bounded.SectionHTML(body.replace('<body>', '<body><div>').replace('</body>', '</div></body>'))


def test_parse_timeout_discards_financial_section(monkeypatch):
    ticks = iter([0.0, 11.0])
    monkeypatch.setattr(bounded, 'monotonic', lambda: next(ticks, 11.0))
    result, _ = run_sections(financial=BODY + '<!--' + 'x' * (2 * 1024 * 1024) + '-->')
    row = result['filings'][0]
    assert 'SECTION_HTML_TIMEOUT' in row['errors']
    assert row['body_status'] == 'unavailable'
    assert 'financial_statements' not in row['sections']


def test_scope_and_preview_share_parser_deadline(monkeypatch):
    now = [0.0]
    monkeypatch.setattr(bounded, 'monotonic', lambda: now[0])
    scan = bounded.SectionHTML(BODY)
    dart._scope_body(BODY, 'consolidated', scan)
    now[0] = 11.0
    with pytest.raises(bounded.SectionHTMLLimit, match='SECTION_HTML_TIMEOUT'):
        dart._section_scope(BODY, 'consolidated', scan=scan)
    with pytest.raises(bounded.SectionHTMLLimit, match='SECTION_HTML_TIMEOUT'):
        scan.text(scan.root, limit=1200)


def test_deadline_checked_after_candidate_processing(monkeypatch):
    now = [0.0]
    monkeypatch.setattr(bounded, 'monotonic', lambda: now[0])
    scan = bounded.SectionHTML(BODY)
    original = scan.text
    def slow_text(*args, **kwargs):
        result = original(*args, **kwargs)
        now[0] = 11.0
        return result
    monkeypatch.setattr(scan, 'text', slow_text)
    with pytest.raises(bounded.SectionHTMLLimit, match='SECTION_HTML_TIMEOUT'):
        dart._section_scope(BODY, 'consolidated', scan=scan)


@pytest.mark.parametrize('character', ['x', '한'])
def test_helper_has_its_own_utf8_byte_cap(character):
    with pytest.raises(bounded.SectionHTMLLimit, match='SECTION_HTML_BYTES_LIMIT'):
        bounded.SectionHTML(character * (bounded.MAX_HTML_BYTES // len(character.encode()) + 1))


def test_fragment_provenance_uses_bounded_helper(monkeypatch):
    body = '<div>' * 101 + 'x' + '</div>' * 101 + ' ' * (2 * 1024 * 1024)
    with pytest.raises(bounded.SectionHTMLLimit, match='SECTION_HTML_DEPTH_LIMIT'):
        dart._provenance(body, {})


def test_large_body_reuses_one_dom_for_all_scans(monkeypatch):
    calls = []
    original = dart.SectionHTML
    def tracked(body):
        calls.append(len(body))
        return original(body)
    monkeypatch.setattr(dart, 'SectionHTML', tracked)
    result, _ = run_sections(financial=BODY + ' ' * (2 * 1024 * 1024),
                             notes=NOTES + ' ' * (2 * 1024 * 1024))
    assert result['filings'][0]['section_delivery']['status'] == 'AVAILABLE'
    assert len(calls) == 2


def test_large_stream_is_complete_and_hash_exact():
    body = (BODY + '<!--' + 'x' * (2 * 1024 * 1024) + '-->').encode()
    closed = []
    class Stream(httpx.AsyncByteStream):
        async def __aiter__(self):
            for offset in range(0, len(body), 8192):
                yield body[offset:offset + 8192]
        async def aclose(self):
            closed.append(True)
    result, _ = run_sections(response_hook=lambda request: httpx.Response(200, stream=Stream())
        if request.url.params.get('eleId') == '1' else None)
    section = result['filings'][0]['sections']['financial_statements']
    assert section['utf8_bytes'] == len(body)
    assert section['sha256'] == hashlib.sha256(body).hexdigest()
    assert closed == [True]


def test_aggregate_stream_aborts_before_eof_and_closes():
    closed, after_overflow = [], []
    class Stream(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield NOTES.encode()
            for _ in range(128):
                yield b'x' * 65536
            after_overflow.append(True)
            yield b'never'
        async def aclose(self):
            closed.append(True)
    result, requests = run_sections(response_hook=lambda request: httpx.Response(200, stream=Stream())
        if request.url.params.get('eleId') == '3' else None)
    assert 'financial_notes' not in result['filings'][0]['sections']
    assert result['filings'][0]['note_fragment_selection']['requested_keys'] == []
    assert not after_overflow and closed == [True] and len(requests) == 5


def test_large_stream_external_cancellation_closes_response():
    closed = []
    class Stream(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield BODY.encode() + b' ' * (2 * 1024 * 1024)
            raise dart.asyncio.CancelledError
        async def aclose(self):
            closed.append(True)
    with pytest.raises(dart.asyncio.CancelledError):
        run_sections(response_hook=lambda request: httpx.Response(200, stream=Stream())
            if request.url.params.get('eleId') == '1' else None)
    assert closed == [True]


def test_fragment_graph_large_financial_does_not_expand_cover_limit():
    from test_dart_note_fragment_context import call
    from test_dart_note_fragment_context import fixture as fragment_fixture
    row, child, main = fragment_fixture()
    financial = row['sections']['financial_statements']
    financial.pop('html')
    financial['utf8_bytes'] = 8 * 1024 * 1024
    assert call(row, child, main)[0]
    row['sections']['cover']['utf8_bytes'] = 2 * 1024 * 1024 + 1
    assert not call(row, child, main)[0]
