"""Viewer envelope boundaries; production collectors additionally verify scope."""
import asyncio
import json

import httpx
import pytest

from tests.test_dart_fixture_transport import Stream
from tools import dart_fixture_transport as fixture

MIB = 1024 * 1024
VIEWER = ('https://dart.fss.or.kr/report/viewer.do?rcpNo=20260814000001'
          '&dcmNo=12345678&eleId=0&offset=0&length=1000&dtd=dart4.xsd')
MAIN = 'https://dart.fss.or.kr/dsaf001/main.do?rcpNo=20260814000001'
CATALOG = 'https://dart.fss.or.kr/dsab001/searchCorp.ax'


async def request(client, url):
    return await client.post(url, data={'textCrpNm': '회사'}) if url == CATALOG else await client.get(url)


@pytest.mark.parametrize('buffered', [False, True])
@pytest.mark.parametrize('size', [2 * MIB + 1, 8 * MIB])
def test_large_viewer_exact_byte_roundtrip(tmp_path, buffered, size):
    # Multibyte source makes a character-count admission bug observable.
    body = ('한' * (size // 3)).encode() + b'x' * (size % 3)
    async def scenario():
        recorder = await fixture.FixtureRecorder.create(tmp_path / 'case', lambda: httpx.MockTransport(
            lambda req: httpx.Response(200, **({'content': body} if buffered else
                {'stream': Stream([body[:2 * MIB], body[2 * MIB:]])}))))
        async with recorder.client_factory() as client:
            assert (await request(client, VIEWER)).content == body
        assert (await recorder.finish({}, {}))['complete']
        replay = await fixture.FixtureReplay.load(recorder.path)
        async with replay.client_factory() as client:
            assert (await request(client, VIEWER)).content == body
        replay.assert_consumed()
    asyncio.run(scenario())


@pytest.mark.parametrize('buffered', [False, True])
@pytest.mark.parametrize('url,size', [(VIEWER, 8 * MIB + 1), (MAIN, 2 * MIB + 1),
                                    (CATALOG, 2 * MIB + 1)])
def test_overflow_retains_incomplete_marker(tmp_path, buffered, url, size):
    body = b'x' * size
    async def scenario():
        recorder = await fixture.FixtureRecorder.create(tmp_path / 'case', lambda: httpx.MockTransport(
            lambda req: httpx.Response(200, **({'content': body} if buffered else {'stream': Stream([body])}))))
        async with recorder.client_factory() as client:
            with pytest.raises(fixture.FixtureError, match='BODY_LIMIT'):
                await request(client, url)
        manifest = await recorder.finish({}, {})
        assert not manifest['complete']
        assert manifest['responses'][0]['capture_failure_codes'] == ['CAPTURE_BODY_LIMIT']
        assert (recorder.path / fixture.MARKER).exists()
        assert not list(recorder.path.glob('response-*.bin'))
        with pytest.raises(fixture.FixtureError):
            await fixture.FixtureReplay.load(recorder.path)
    asyncio.run(scenario())


@pytest.mark.parametrize('overflow', [False, True])
def test_total_budget_remains_twelve_mib(tmp_path, overflow):
    assert fixture.MAX_TOTAL == 12 * MIB
    bodies = iter([b'x' * (8 * MIB), b'y' * (4 * MIB + int(overflow))])
    async def scenario():
        recorder = await fixture.FixtureRecorder.create(tmp_path / 'case', lambda: httpx.MockTransport(
            lambda req: httpx.Response(200, content=next(bodies))))
        async with recorder.client_factory() as client:
            await request(client, VIEWER)
            if overflow:
                with pytest.raises(fixture.FixtureError, match='BODY_LIMIT'):
                    await request(client, VIEWER)
            else:
                await request(client, VIEWER)
        manifest = await recorder.finish({}, {})
        assert manifest['complete'] is not overflow
        if overflow:
            assert manifest['responses'][1]['capture_failure_codes'] == ['CAPTURE_TOTAL_LIMIT']
            with pytest.raises(fixture.FixtureError):
                await fixture.FixtureReplay.load(recorder.path)
        else:
            await fixture.FixtureReplay.load(recorder.path)
    asyncio.run(scenario())


def test_replay_revalidates_control_limit_not_just_capture(tmp_path):
    async def scenario():
        body = b'x' * (2 * MIB + 1)
        recorder = await fixture.FixtureRecorder.create(tmp_path / 'case', lambda: httpx.MockTransport(
            lambda req: httpx.Response(200, content=body)))
        async with recorder.client_factory() as client:
            await request(client, VIEWER)
        manifest = await recorder.finish({}, {})
        # Keep all content hashes/byte counts intact but reclassify as a control.
        manifest['responses'][0]['request'] = fixture._request_key(httpx.Request('GET', MAIN))
        (recorder.path / 'manifest.json').write_text(json.dumps(manifest))
        with pytest.raises(fixture.FixtureError):
            await fixture.FixtureReplay.load(recorder.path)
    asyncio.run(scenario())


def test_cancellation_after_large_prefix_cannot_complete_fixture(tmp_path):
    async def scenario():
        entered = asyncio.Event()
        class PausedStream(httpx.AsyncByteStream):
            async def __aiter__(self):
                yield b'x' * (2 * MIB + 1)
                entered.set()
                await asyncio.Event().wait()
        recorder = await fixture.FixtureRecorder.create(tmp_path / 'case', lambda: httpx.MockTransport(
            lambda req: httpx.Response(200, stream=PausedStream())))
        async with recorder.client_factory() as client:
            task = asyncio.create_task(request(client, VIEWER))
            try:
                await asyncio.wait_for(entered.wait(), timeout=2)
            finally:
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task
        manifest = await recorder.finish({}, {})
        assert not manifest['complete']
        assert manifest['responses'][0]['state'] == 'CANCELLED'
        assert not list(recorder.path.glob('response-*.bin'))
        with pytest.raises(fixture.FixtureError):
            await fixture.FixtureReplay.load(recorder.path)
    asyncio.run(scenario())


@pytest.mark.parametrize('limit', [True, False, 0, -1, 8 * MIB + 1, 1.5, '2097152', None])
def test_optional_capture_limit_is_lowering_only_and_validated_before_io(tmp_path, limit):
    path = tmp_path / 'invalid'
    with pytest.raises(fixture.FixtureError, match='INVALID_BODY_LIMIT'):
        fixture.FixtureRecorder(path, None, max_body_bytes=limit)
    with pytest.raises(fixture.FixtureError, match='INVALID_BODY_LIMIT'):
        asyncio.run(fixture.FixtureRecorder.create(path, max_body_bytes=limit))
    assert not path.exists()


@pytest.mark.parametrize('buffered', [False, True])
@pytest.mark.parametrize('overflow', [False, True])
def test_lowered_capture_limit_before_persistence(tmp_path, buffered, overflow):
    body = b'x' * (2 * MIB + int(overflow))
    async def scenario():
        recorder = await fixture.FixtureRecorder.create(tmp_path / 'case', lambda: httpx.MockTransport(
            lambda req: httpx.Response(200, **({'content': body} if buffered else
                {'stream': Stream([body])}))), max_body_bytes=2 * MIB)
        async with recorder.client_factory() as client:
            if overflow:
                with pytest.raises(fixture.FixtureError, match='BODY_LIMIT'):
                    await request(client, VIEWER)
            else:
                assert (await request(client, VIEWER)).content == body
        manifest = await recorder.finish({}, {})
        assert manifest['complete'] is not overflow
        if overflow:
            assert not list(recorder.path.glob('response-*.bin'))
            with pytest.raises(fixture.FixtureError):
                await fixture.FixtureReplay.load(recorder.path)
        else:
            await fixture.FixtureReplay.load(recorder.path)
    asyncio.run(scenario())
