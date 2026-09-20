"""Bounded diagnostic child requests, no live source or issuer body text."""
import asyncio
import hashlib
import json

import httpx
import pytest

from tools import probe_dart_note_children as tool
from tools.dart_fixture_transport import FixtureError, FixtureReplay


def graph():
    nodes = [{'key': tool.PARENT, 'children_keys': list(tool.TARGETS)}]
    for key in tool.TARGETS:
        ele = key.split(':')[1]
        nodes.append({'key': key, 'parent_key': tool.PARENT, 'text': '주석 (연결)',
                      'rcpNo': '20260331004244', 'dcmNo': '11213317', 'eleId': ele,
                      'offset': '1', 'length': '100', 'dtd': 'dart4.xsd',
                      'viewer_url': 'https://dart.fss.or.kr/report/viewer.do?'
                      f'rcpNo=20260331004244&dcmNo=11213317&eleId={ele}&offset=1&length=100&dtd=dart4.xsd'})
    return {'main_sha256': tool.MAIN_SHA256, 'nodes': nodes}


def provider(*, fail_at=None, body=b'<p>source clause</p>'):
    calls = []
    def handle(request):
        calls.append(request)
        assert request.method == 'GET'
        assert request.headers['accept-encoding'] == 'identity'
        assert not any(k in request.headers for k in ('authorization', 'cookie'))
        if len(calls) == fail_at:
            return httpx.Response(403)
        return httpx.Response(200, content=body)
    return lambda: httpx.MockTransport(handle), calls


def test_two_exact_children_complete_and_replay_without_network(tmp_path, monkeypatch):
    monkeypatch.setattr(tool, '_source_graph', graph)
    factory, calls = provider()
    result = asyncio.run(tool.probe(tmp_path / 'new', live=True, transport_factory=factory))
    assert result['complete'] and result['requests'] == len(calls) == 2
    assert [q.url.params['eleId'] for q in calls] == ['47', '78']
    async def replay():
        fixture = await FixtureReplay.load(tmp_path / 'new')
        async with fixture.client_factory() as client:
            for q in calls:
                response = await client.get(str(q.url), headers={'Accept-Encoding': 'identity'})
                assert hashlib.sha256(response.content).hexdigest() in result['body_sha256']
        fixture.assert_consumed()
    asyncio.run(replay())


@pytest.mark.parametrize('fail_at', [1, 2])
def test_first_failure_stops_and_never_finalizes_complete(tmp_path, monkeypatch, fail_at):
    monkeypatch.setattr(tool, '_source_graph', graph)
    factory, calls = provider(fail_at=fail_at)
    result = asyncio.run(tool.probe(tmp_path / 'new', live=True, transport_factory=factory))
    assert not result['complete'] and len(calls) == fail_at
    assert (tmp_path / 'new' / 'INCOMPLETE').exists()
    with pytest.raises(FixtureError):
        asyncio.run(FixtureReplay.load(tmp_path / 'new'))


@pytest.mark.parametrize('kind', ['hash', 'parent', 'url', 'offset'])
def test_wrong_graph_never_calls_network(tmp_path, monkeypatch, kind):
    value = graph()
    if kind == 'hash':
        value['main_sha256'] = '0' * 64
    elif kind == 'parent':
        value['nodes'][1]['parent_key'] = 'other'
    elif kind == 'offset':
        value['nodes'][1]['viewer_url'] = value['nodes'][1]['viewer_url'].replace('offset=1', 'offset=999')
    else:
        value['nodes'][1]['viewer_url'] = value['nodes'][1]['viewer_url'].replace('eleId=47', 'eleId=99')
    monkeypatch.setattr(tool, '_source_graph', lambda: value)
    factory, calls = provider()
    with pytest.raises(FixtureError):
        asyncio.run(tool.probe(tmp_path / 'new', live=True, transport_factory=factory))
    assert not calls and not (tmp_path / 'new').exists()


def test_no_live_and_source_hash_mismatch(tmp_path, monkeypatch):
    factory, calls = provider()
    with pytest.raises(FixtureError, match='LIVE_ACK_REQUIRED'):
        asyncio.run(tool.probe(tmp_path / 'new', transport_factory=factory))
    p = tmp_path / 'main.bin'
    p.write_bytes(b'<html>not the approved source</html>')
    p.chmod(0o600)
    monkeypatch.setattr(tool, 'MAIN_FILE', p)
    with pytest.raises(FixtureError, match='SOURCE_GRAPH_UNVERIFIED'):
        tool._source_graph()
    assert not calls


@pytest.mark.parametrize('mode', ['total', 'body', 'timeout'])
def test_limits_leave_incomplete_fixture(tmp_path, monkeypatch, mode):
    monkeypatch.setattr(tool, '_source_graph', graph)
    if mode == 'total':
        monkeypatch.setattr(tool, 'MAX_TOTAL_BYTES', 3)
    if mode == 'timeout':
        monkeypatch.setattr(tool, 'TOTAL_TIMEOUT', .01)
        async def slow(request):
            await asyncio.sleep(10)
        factory = lambda: httpx.MockTransport(slow)
    else:
        factory, _ = provider(body=b'x' * (2 * 1024 * 1024 + 1) if mode == 'body' else b'abcd')
    result = asyncio.run(tool.probe(tmp_path / 'new', live=True, transport_factory=factory))
    assert result['complete'] is False
    assert (tmp_path / 'new' / 'INCOMPLETE').exists()
    assert 'body' not in result
    if mode == 'body':
        assert not list((tmp_path / 'new').glob('response-*.bin'))


@pytest.mark.parametrize('buffered', [False, True])
def test_diagnostic_body_limit_stops_before_persistence_and_second_call(tmp_path, monkeypatch, buffered):
    from tests.test_dart_fixture_transport import Stream

    monkeypatch.setattr(tool, '_source_graph', graph)
    calls = []
    body = b'x' * (2 * 1024 * 1024 + 1)
    def handler(request):
        calls.append(request)
        return httpx.Response(200, **({'content': body} if buffered else {'stream': Stream([body])}))
    path = tmp_path / 'new'
    result = asyncio.run(tool.probe(path, live=True, transport_factory=lambda: httpx.MockTransport(handler)))
    assert result['complete'] is False and len(calls) == 1
    assert (path / 'INCOMPLETE').exists()
    assert not list(path.glob('response-*.bin'))


def test_cancellation_during_fetch_retains_marker(tmp_path, monkeypatch):
    monkeypatch.setattr(tool, '_source_graph', graph)
    async def run():
        ready = asyncio.Event()
        async def slow(request):
            ready.set()
            await asyncio.sleep(10)
        task = asyncio.create_task(tool.probe(tmp_path / 'new', live=True,
                                             transport_factory=lambda: httpx.MockTransport(slow)))
        await ready.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert (tmp_path / 'new' / 'INCOMPLETE').exists()
        manifest = json.loads((tmp_path / 'new' / 'manifest.json').read_text())
        assert manifest['complete'] is False
    asyncio.run(run())


@pytest.mark.parametrize('repeat', [False, True])
def test_finalizer_cancellation_is_joined_and_quarantined(tmp_path, monkeypatch, repeat):
    import threading

    from tools import dart_fixture_transport as transport

    monkeypatch.setattr(tool, '_source_graph', graph)
    entered, release = threading.Event(), threading.Event()
    path = tmp_path / 'new'
    original = transport.os.unlink
    def delayed(target, *args, **kwargs):
        if str(target) == str(path / 'INCOMPLETE'):
            entered.set()
            release.wait(2)
        return original(target, *args, **kwargs)
    monkeypatch.setattr(transport.os, 'unlink', delayed)
    factory, _ = provider()
    async def run():
        task = asyncio.create_task(tool.probe(path, live=True, transport_factory=factory))
        try:
            assert await asyncio.to_thread(entered.wait, 2)
            task.cancel()
            if repeat:
                await asyncio.sleep(.01)
                task.cancel()
        finally:
            release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert (path / 'INCOMPLETE').exists()
        with pytest.raises(FixtureError):
            await FixtureReplay.load(path)
    asyncio.run(run())


def test_manifest_write_failure_never_leaves_success(tmp_path, monkeypatch):
    from tools import dart_fixture_transport as transport

    monkeypatch.setattr(tool, '_source_graph', graph)
    original = transport._write
    def failing(path, value):
        if path.name == 'manifest.json':
            raise OSError('simulated storage failure')
        return original(path, value)
    monkeypatch.setattr(transport, '_write', failing)
    factory, _ = provider()
    path = tmp_path / 'new'
    with pytest.raises(OSError):
        asyncio.run(tool.probe(path, live=True, transport_factory=factory))
    assert (path / 'INCOMPLETE').exists()
