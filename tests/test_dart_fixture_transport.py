"""Offline contracts for quarantined DART transport fixtures."""
import asyncio
import json
import os
import threading
from datetime import date, datetime

import httpx
import pytest

from tools import dart_fixture_transport as fixture
from tools.dart_fixture_transport import FixtureError, FixtureRecorder, FixtureReplay

URL = 'https://dart.fss.or.kr/dsab001/searchCorp.ax'
FORM = {'publicType': ['A001', 'A002'], 'textCrpNm': '회사'}


def run(coro):
    return asyncio.run(coro)


async def capture(path, body=b'raw\xff', status=200, headers=None):
    recorder = await FixtureRecorder.create(path, lambda: httpx.MockTransport(
        lambda request: httpx.Response(status, content=body, headers=headers)))
    async with recorder.client_factory() as client, client.stream('POST', URL, data=FORM) as response:
        if status == 200 and not headers:
            await response.aread()
    return recorder, await recorder.finish({'case': 'test'}, {'count': 1})


def test_exact_bytes_repeated_form_replay(tmp_path):
    async def scenario():
        path = tmp_path / 'fixture'
        recorder, manifest = await capture(path)
        assert not recorder.failed and manifest['complete']
        assert (path / 'response-0001.bin').read_bytes() == b'raw\xff'
        assert manifest['responses'][0]['request']['form'][:2] == [['publicType', 'A001'], ['publicType', 'A002']]
        replay = await FixtureReplay.load(path)
        async with replay.client_factory() as client:
            response = await client.post(URL, data=FORM)
            assert response.content == b'raw\xff'
        replay.assert_consumed()
        assert path.stat().st_mode & 0o777 == 0o700
        assert all(p.stat().st_mode & 0o777 == 0o600 for p in path.iterdir())
    run(scenario())


@pytest.mark.parametrize('status,headers', [(403, None), (200, {'content-encoding': 'gzip'})])
def test_policy_rejection_replay(tmp_path, status, headers):
    async def scenario():
        path = tmp_path / 'fixture'
        _, manifest = await capture(path, b'', status, headers)
        assert manifest['responses'][0]['state'] == 'POLICY_REJECTED'
        replay = await FixtureReplay.load(path)
        async with replay.client_factory() as client, client.stream('POST', URL, data=FORM) as response:
            assert response.status_code == status
        replay.assert_consumed()
    run(scenario())


def test_hash_tamper_and_incomplete_rejected(tmp_path):
    async def scenario():
        path = tmp_path / 'fixture'
        await capture(path)
        (path / 'response-0001.bin').write_bytes(b'tamper')
        with pytest.raises(FixtureError):
            await FixtureReplay.load(path)
        other = tmp_path / 'unfinished'
        await FixtureRecorder.create(other)
        with pytest.raises(FixtureError):
            await FixtureReplay.load(other)
    run(scenario())


def test_unsafe_paths_and_requests(tmp_path):
    async def scenario():
        with pytest.raises(FixtureError):
            await FixtureRecorder.create(tmp_path)
        link = tmp_path / 'link'
        link.symlink_to(tmp_path, target_is_directory=True)
        with pytest.raises(FixtureError):
            await FixtureRecorder.create(link / 'new')
        recorder = await FixtureRecorder.create(tmp_path / 'fixture')
        async with recorder.client_factory() as client:
            with pytest.raises(FixtureError):
                await client.get('https://example.com/')
        assert recorder.failed
    run(scenario())


def test_divergence_unused_and_malicious_manifest(tmp_path):
    async def scenario():
        path = tmp_path / 'fixture'
        await capture(path)
        replay = await FixtureReplay.load(path)
        with pytest.raises(FixtureError):
            replay.assert_consumed()
        async with replay.client_factory() as client:
            with pytest.raises(FixtureError):
                await client.post(URL, data={'textCrpNm': 'different'})
        manifest_path = path / 'manifest.json'
        manifest = json.loads(manifest_path.read_text())
        manifest['responses'][0]['file'] = '../secret'
        manifest_path.write_text(json.dumps(manifest))
        os.chmod(manifest_path, 0o600)
        with pytest.raises(FixtureError):
            await FixtureReplay.load(path)
    run(scenario())


class Stream(httpx.AsyncByteStream):
    def __init__(self, chunks, error=False):
        self.chunks, self.error = chunks, error

    async def __aiter__(self):
        for chunk in self.chunks:
            yield chunk
        if self.error:
            raise httpx.ReadError('private error is never persisted')


@pytest.mark.parametrize('mode,state', [('complete', 'EOF_COMPLETE'), ('closed', 'CONSUMER_CLOSED'),
                                       ('error', 'STREAM_ERROR'), ('limit', 'CAPTURE_LIMIT_EXCEEDED')])
def test_stream_states(tmp_path, mode, state, monkeypatch):
    async def scenario():
        if mode == 'limit':
            monkeypatch.setattr(fixture, 'MAX_BODY', 3)
        recorder = await FixtureRecorder.create(tmp_path / 'fixture', lambda: httpx.MockTransport(
            lambda request: httpx.Response(200, stream=Stream([b'ab', b'cd'], mode == 'error'))))
        async with recorder.client_factory() as client, client.stream('POST', URL, data=FORM) as response:
            if mode == 'closed':
                async for _ in response.aiter_raw():
                    break
            elif mode in {'error', 'limit'}:
                with pytest.raises((httpx.ReadError, FixtureError)):
                    await response.aread()
            else:
                assert await response.aread() == b'abcd'
        manifest = await recorder.finish({}, {})
        assert manifest['responses'][0]['state'] == state
        if mode == 'error':
            assert 'capture_failure_codes' not in manifest['responses'][0]
        assert manifest['complete'] == (mode == 'complete')
        if mode != 'complete':
            assert not list(recorder.path.glob('*.bin'))
            with pytest.raises(FixtureError):
                await FixtureReplay.load(recorder.path)
    run(scenario())


def test_transport_error_replays_static_kind(tmp_path):
    async def scenario():
        def fail(request):
            raise httpx.ConnectError('private address')
        recorder = await FixtureRecorder.create(tmp_path / 'fixture', lambda: httpx.MockTransport(fail))
        async with recorder.client_factory() as client:
            with pytest.raises(httpx.ConnectError):
                await client.post(URL, data=FORM)
        manifest = await recorder.finish({}, {})
        assert 'private address' not in json.dumps(manifest)
        replay = await FixtureReplay.load(recorder.path)
        async with replay.client_factory() as client:
            with pytest.raises(httpx.ConnectError):
                await client.post(URL, data=FORM)
        replay.assert_consumed()
    run(scenario())


@pytest.mark.parametrize('limit', ['MAX_RESPONSES', 'MAX_TOTAL', 'MAX_MANIFEST'])
def test_limits_fail_closed(tmp_path, monkeypatch, limit):
    async def scenario():
        monkeypatch.setattr(fixture, limit, 1)
        recorder = await FixtureRecorder.create(tmp_path / 'fixture', lambda: httpx.MockTransport(
            lambda request: httpx.Response(200, content=b'ab')))
        with pytest.raises(FixtureError):
            async with recorder.client_factory() as client:
                await client.post(URL, data=FORM)
                if limit == 'MAX_RESPONSES':
                    await client.post(URL, data=FORM)
            await recorder.finish({}, {})
        assert recorder.failed
        assert (recorder.path / fixture.MARKER).exists()
    run(scenario())


@pytest.mark.parametrize('field,value', [('file', '../escape'), ('state', 'CONSUMER_CLOSED'),
                                        ('size', -1), ('sha256', '0' * 64), ('status', 403)])
def test_malicious_entry_rejected(tmp_path, field, value):
    async def scenario():
        path = tmp_path / 'fixture'
        await capture(path)
        manifest_path = path / 'manifest.json'
        manifest = json.loads(manifest_path.read_text())
        manifest['responses'][0][field] = value
        manifest_path.write_text(json.dumps(manifest))
        with pytest.raises(FixtureError):
            await FixtureReplay.load(path)
    run(scenario())


@pytest.mark.parametrize('mutation', ['permissions', 'symlink', 'extra'])
def test_replay_file_safety(tmp_path, mutation):
    async def scenario():
        path = tmp_path / 'fixture'
        await capture(path)
        body = path / 'response-0001.bin'
        if mutation == 'permissions':
            body.chmod(0o644)
        elif mutation == 'symlink':
            body.unlink()
            body.symlink_to(path / 'manifest.json')
        else:
            (path / 'extra').write_bytes(b'')
        with pytest.raises(FixtureError):
            await FixtureReplay.load(path)
    run(scenario())


def test_write_cancelled_is_joined_and_never_complete(tmp_path, monkeypatch):
    async def scenario():
        started, release = threading.Event(), threading.Event()
        original = fixture._write
        def slow(path, data):
            if path.suffix == '.bin':
                started.set()
                release.wait(5)
            original(path, data)
        recorder = await FixtureRecorder.create(tmp_path / 'fixture', lambda: httpx.MockTransport(
            lambda request: httpx.Response(200, content=b'body')))
        monkeypatch.setattr(fixture, '_write', slow)
        async with recorder.client_factory() as client:
            request = asyncio.create_task(client.post(URL, data=FORM))
            assert await asyncio.to_thread(started.wait, 2)
            request.cancel()
            with pytest.raises(asyncio.CancelledError):
                await request
            release.set()
        manifest = await recorder.finish({}, {})
        assert not manifest['complete'] and recorder.failed
        assert all(task.done() for task in recorder._writes)
        assert (recorder.path / 'response-0001.bin').read_bytes() == b'body'
        assert (recorder.path / fixture.MARKER).exists()
    run(scenario())


def test_manifest_write_failure_keeps_marker(tmp_path, monkeypatch):
    async def scenario():
        recorder = await FixtureRecorder.create(tmp_path / 'fixture')
        def fail(*args):
            raise OSError('disk unavailable')
        monkeypatch.setattr(fixture, '_write', fail)
        with pytest.raises(OSError):
            await recorder.finish({}, {})
        assert recorder.failed and (recorder.path / fixture.MARKER).exists()
    run(scenario())


@pytest.mark.parametrize('url,fields,headers', [
    ('http://dart.fss.or.kr/dsab001/searchCorp.ax', FORM, {}),
    ('https://dart.fss.or.kr:8443/dsab001/searchCorp.ax', FORM, {}),
    (URL + '?unknown=1', FORM, {}),
    (URL, {'secret': 'not allowed'}, {}),
    (URL, FORM, {'Authorization': 'private'}),
    (URL, FORM, {'Cookie': 'private'}),
    (URL, FORM, {'X-API-Key': 'private'}),
])
def test_request_policy_before_transport(tmp_path, url, fields, headers):
    async def scenario():
        called = []
        recorder = await FixtureRecorder.create(tmp_path / 'fixture', lambda: httpx.MockTransport(
            lambda request: called.append(request)))
        async with recorder.client_factory() as client:
            with pytest.raises(FixtureError):
                await client.post(url, data=fields, headers=headers)
        assert recorder.failed and called == []
    run(scenario())


def test_git_directory_rejected(tmp_path):
    (tmp_path / '.git').write_text('gitdir: elsewhere')
    with pytest.raises(FixtureError):
        run(FixtureRecorder.create(tmp_path / 'fixture'))


def test_literal_identity_encoding_preserved(tmp_path):
    async def scenario():
        recorder = await FixtureRecorder.create(tmp_path / 'fixture', lambda: httpx.MockTransport(
            lambda request: httpx.Response(200, content=b'body', headers={'content-encoding': ' Identity '})))
        async with recorder.client_factory() as client:
            response = await client.post(URL, data=FORM)
            assert response.content == b'body'
        manifest = await recorder.finish({}, {})
        assert manifest['responses'][0]['encoding'] == ' Identity '
        replay = await FixtureReplay.load(recorder.path)
        async with replay.client_factory() as client:
            response = await client.post(URL, data=FORM)
            assert response.headers['content-encoding'] == ' Identity '
            assert response.content == b'body'
    run(scenario())


def test_replay_divergence_sticky_even_after_all_consumed(tmp_path):
    async def scenario():
        await capture(tmp_path / 'fixture')
        replay = await FixtureReplay.load(tmp_path / 'fixture')
        async with replay.client_factory() as client:
            await client.post(URL, data=FORM)
            with pytest.raises(FixtureError):
                await client.post(URL, data=FORM)
        with pytest.raises(FixtureError):
            replay.assert_consumed()
    run(scenario())


def test_blank_encoding_is_not_omitted(tmp_path):
    async def scenario():
        recorder = await FixtureRecorder.create(tmp_path / 'fixture', lambda: httpx.MockTransport(
            lambda request: httpx.Response(200, content=b'', headers={'content-encoding': ''})))
        async with recorder.client_factory() as client, client.stream('POST', URL, data=FORM) as response:
            assert response.headers.get('content-encoding') == ''
        await recorder.finish({}, {})
        replay = await FixtureReplay.load(recorder.path)
        async with replay.client_factory() as client, client.stream('POST', URL, data=FORM) as response:
            assert response.headers.get('content-encoding') == ''
    run(scenario())


def test_private_read_uses_nonblocking_before_file_type_check(tmp_path, monkeypatch):
    fifo = tmp_path / 'fifo'
    os.mkfifo(fifo, 0o600)
    original = os.open
    def checked(path, flags, *args, **kwargs):
        assert flags & os.O_NONBLOCK
        return original(path, flags, *args, **kwargs)
    monkeypatch.setattr(os, 'open', checked)
    with pytest.raises(FixtureError):
        fixture._private_read(fifo, 1024)


def test_observation_timestamp_required_and_aware(tmp_path):
    async def scenario():
        _, manifest = await capture(tmp_path / 'fixture')
        stamp = manifest['responses'][0]['observed_at']
        assert datetime.fromisoformat(stamp).utcoffset() is not None
        manifest['responses'][0]['observed_at'] = '2026-09-20T00:00:00'
        (tmp_path / 'fixture' / 'manifest.json').write_text(json.dumps(manifest))
        with pytest.raises(FixtureError):
            await FixtureReplay.load(tmp_path / 'fixture')
    run(scenario())


def test_invalidate_finished_recorder_restores_marker(tmp_path):
    async def scenario():
        recorder, _ = await capture(tmp_path / 'fixture')
        await recorder.invalidate()
        await recorder.invalidate()
        assert recorder.failed and recorder.incomplete
        assert all(task.done() for task in recorder._writes)
        with pytest.raises(FixtureError):
            await FixtureReplay.load(recorder.path)
    run(scenario())


@pytest.mark.parametrize('encoding', ['', ' ', ' Identity ', 'IDENTITY', 'identity'])
@pytest.mark.parametrize('consumer', ['identity', 'collector'])
def test_real_consumers_encoding_parity(tmp_path, encoding, consumer):
    from prism_core.dart_identity import resolve_dart_identity
    from prism_core.dart_public_filings import collect_dart_periodic_filings

    async def scenario():
        def handler(request):
            return httpx.Response(200, content=b'<html>no rows</html>', headers={'content-encoding': encoding})
        def direct(**opts):
            return httpx.AsyncClient(transport=httpx.MockTransport(handler), **opts)
        async def invoke(factory):
            options = {'start_date': date(2025, 1, 1),
                       'decision_at': datetime.fromisoformat('2026-09-18T15:30:00+09:00'),
                       'client_factory': factory}
            if consumer == 'identity':
                result = await resolve_dart_identity('회사', '000001', **options)
            else:
                result = await collect_dart_periodic_filings(corp_code='12345678', scope='consolidated', **options)
            result.pop('observed_at', None)
            return result
        expected = await invoke(direct)
        recorder = await FixtureRecorder.create(tmp_path / 'fixture', lambda: httpx.MockTransport(handler))
        recorded = await invoke(recorder.client_factory)
        assert recorded == expected
        manifest = await recorder.finish({}, {})
        assert manifest['complete']
        replay = await FixtureReplay.load(recorder.path)
        assert await invoke(replay.client_factory) == expected
        replay.assert_consumed()
    run(scenario())


@pytest.mark.parametrize('name', ['manifest.json', 'response-0001.bin'])
def test_fifo_fixture_rejected_without_waiting_for_writer(tmp_path, name):
    async def scenario():
        path = tmp_path / 'fixture'
        await capture(path)
        target = path / name
        target.unlink()
        os.mkfifo(target, 0o600)
        with pytest.raises(FixtureError):
            await asyncio.wait_for(FixtureReplay.load(path), timeout=1)
    run(scenario())


@pytest.mark.parametrize('buffered', [False, True])
@pytest.mark.parametrize('body_cap,total_cap,codes', [
    (3, 20, ['CAPTURE_BODY_LIMIT']),
    (10, 5, ['CAPTURE_TOTAL_LIMIT']),
    (3, 5, ['CAPTURE_BODY_LIMIT', 'CAPTURE_TOTAL_LIMIT']),
    (4, 6, []),
])
def test_capture_limit_diagnostics_and_exact_boundaries(tmp_path, monkeypatch, buffered, body_cap, total_cap, codes):
    async def scenario():
        monkeypatch.setattr(fixture, 'MAX_BODY', body_cap)
        monkeypatch.setattr(fixture, 'MAX_TOTAL', total_cap)
        bodies = iter([b'ab', b'cdef'])
        def handler(request):
            body = next(bodies)
            return httpx.Response(200, **({'content': body} if buffered else {'stream': Stream([body[:2], body[2:]])}))
        recorder = await FixtureRecorder.create(tmp_path / 'fixture', lambda: httpx.MockTransport(handler))
        async with recorder.client_factory() as client:
            assert (await client.post(URL, data=FORM)).content == b'ab'
            if codes:
                with pytest.raises(FixtureError, match='BODY_LIMIT'):
                    await client.post(URL, data=FORM)
            else:
                assert (await client.post(URL, data=FORM)).content == b'cdef'
        manifest = await recorder.finish({}, {})
        entry = manifest['responses'][1]
        assert manifest['complete'] == (not codes)
        assert entry.get('capture_failure_codes', []) == codes
        assert entry['observed_bytes'] == 4
        assert (recorder.path / 'response-0001.bin').read_bytes() == b'ab'
        if codes:
            assert entry['state'] == 'CAPTURE_LIMIT_EXCEEDED'
            assert recorder.failed and recorder.incomplete
            assert not (recorder.path / 'response-0002.bin').exists()
            assert (recorder.path / fixture.MARKER).exists()
            with pytest.raises(FixtureError):
                await FixtureReplay.load(recorder.path)
        else:
            assert not recorder.failed
            assert (recorder.path / 'response-0002.bin').read_bytes() == b'cdef'
            await FixtureReplay.load(recorder.path)
    run(scenario())
