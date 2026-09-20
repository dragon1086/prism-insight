"""One approved diagnostic fetch must not relax production acquisition limits."""
import json

import httpx
import pytest

from tools import capture_dart_large_section as tool
from tools.dart_fixture_transport import MAX_BODY, FixtureError, FixtureReplay


class Stream(httpx.AsyncByteStream):
    def __init__(self, chunks, fail=False):
        self.chunks, self.fail = chunks, fail

    async def __aiter__(self):
        for chunk in self.chunks:
            yield chunk
        if self.fail:
            raise httpx.ReadError('private provider message')


def transport(body=b'<html>notes</html>', *, status=200, headers=None, fail=False):
    calls = []
    def respond(request):
        calls.append(request)
        assert str(request.url) == tool.APPROVED_URL
        assert request.headers['accept-encoding'] == 'identity'
        assert not any(k in request.headers for k in ('authorization', 'cookie', 'proxy-authorization'))
        return httpx.Response(status, headers=headers, stream=Stream([body], fail))
    return httpx.MockTransport(respond), calls


@pytest.mark.parametrize('size', [MAX_BODY + 1, 8 * 1024 * 1024])
def test_complete_large_raw_is_quarantined_not_a_production_fixture(tmp_path, size):
    import asyncio

    inner, calls = transport(b'x' * size)
    path = tmp_path / 'new'
    result = tool.capture(path, live=True, transport=inner)
    assert result['complete'] and result['observed_bytes'] == size
    assert result['production_input_allowed'] is False
    assert len(calls) == 1
    assert (path / 'body.bin').read_bytes() == b'x' * size
    assert not (path / 'INCOMPLETE').exists()
    assert path.stat().st_mode & 0o777 == 0o700
    assert all(p.stat().st_mode & 0o777 == 0o600 for p in path.iterdir())
    assert MAX_BODY == 2 * 1024 * 1024
    with pytest.raises(FixtureError):
        asyncio.run(FixtureReplay.load(path))


@pytest.mark.parametrize('body,status,headers,fail,reason', [
    (b'x' * (8 * 1024 * 1024 + 1), 200, None, False, 'DIAGNOSTIC_BODY_LIMIT'),
    (b'no', 302, {'location': 'https://example.com'}, False, 'HTTP_STATUS_REJECTED'),
    (b'no', 403, None, False, 'HTTP_STATUS_REJECTED'),
    (b'no', 429, None, False, 'HTTP_STATUS_REJECTED'),
    (b'no', 200, {'content-encoding': 'gzip'}, False, 'HTTP_ENCODING_REJECTED'),
    (b'no', 200, {'content-encoding': ''}, False, 'HTTP_ENCODING_REJECTED'),
    (b'\xff', 200, None, False, 'HTTP_UTF8_INVALID'),
    (b'prefix', 200, None, True, 'HTTP_TRANSPORT_FAILURE'),
])
def test_failed_download_never_persists_a_partial_body(tmp_path, body, status, headers, fail, reason):
    inner, calls = transport(body, status=status, headers=headers, fail=fail)
    path = tmp_path / 'case'
    result = tool.capture(path, live=True, transport=inner)
    assert not result['complete'] and result['failure'] == reason
    assert len(calls) == 1
    assert not (path / 'body.bin').exists()
    assert (path / 'INCOMPLETE').exists()
    assert 'private provider message' not in json.dumps(result)


def test_timeout_and_live_guard(tmp_path, monkeypatch):
    import asyncio

    async def slow(request):
        await asyncio.sleep(10)
    with pytest.raises(FixtureError, match='LIVE_ACK_REQUIRED'):
        tool.capture(tmp_path / 'no', transport=httpx.MockTransport(slow))
    assert not (tmp_path / 'no').exists()
    monkeypatch.setattr(tool, 'TOTAL_TIMEOUT', .01)
    result = tool.capture(tmp_path / 'timeout', live=True, transport=httpx.MockTransport(slow))
    assert result['failure'] == 'TOTAL_TIMEOUT'


def test_existing_symlink_and_git_paths_never_make_requests(tmp_path):
    inner, calls = transport()
    link = tmp_path / 'link'
    link.symlink_to(tmp_path, target_is_directory=True)
    git = tmp_path / 'repo'
    git.mkdir()
    (git / '.git').mkdir()
    for path in (tmp_path, link / 'new', git / 'new'):
        with pytest.raises((FixtureError, FileExistsError)):
            tool.capture(path, live=True, transport=inner)
    assert not calls


def test_manifest_write_failure_retains_marker(tmp_path, monkeypatch):
    original = tool._write
    def failing(path, body):
        if path.name == 'manifest.json':
            raise OSError('private disk detail')
        original(path, body)
    monkeypatch.setattr(tool, '_write', failing)
    inner, _ = transport()
    path = tmp_path / 'case'
    with pytest.raises(FixtureError, match='DIAGNOSTIC_STORAGE_FAILED'):
        tool.capture(path, live=True, transport=inner)
    assert (path / 'INCOMPLETE').exists()
