"""Two approved diagnostic child reads; not production note collection."""
import argparse
import asyncio
import hashlib
import json
import sys
from pathlib import Path

import httpx

if __package__ in (None, ''):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.capture_dart_fixture import _settle
from tools.dart_fixture_transport import (
    FixtureError,
    FixtureRecorder,
    _private_read,
    _request_key,
)

MAIN_FILE = Path('/Users/rocky/.cokacdir/workspace/lmg90x0p/'
                 'dart_032830_fixture_20260920/response-0010.bin')
MAIN_SHA256 = 'ec707c5dcbbd8d3e2b45ab3d04c01003f1d8fa7f6095a224323a8f51c38c8daa'
PARENT = '11213317:24'
TARGETS = ('11213317:47', '11213317:78')
MAX_TOTAL_BYTES = 4 * 1024 * 1024
TOTAL_TIMEOUT = 60


def _source_graph():
    try:
        raw = _private_read(MAIN_FILE, 2 * 1024 * 1024)
        if hashlib.sha256(raw).hexdigest() != MAIN_SHA256:
            raise ValueError
        manifest = json.loads(_private_read(MAIN_FILE.parent / 'manifest.json', 1024 * 1024))
        rows = [r for r in manifest['responses'] if r.get('file') == MAIN_FILE.name]
        if (len(rows) != 1 or rows[0].get('sha256') != MAIN_SHA256 or rows[0].get('size') != len(raw)
                or rows[0].get('state') not in {'EOF_COMPLETE', 'BUFFERED_COMPLETE'}):
            raise ValueError
        from prism_core.dart_viewer_tree import parse_viewer_tree

        return parse_viewer_tree(raw.decode('utf-8'), '20260331004244', '00126256')
    except Exception:  # noqa: BLE001 - never expose private source or filesystem details
        raise FixtureError('SOURCE_GRAPH_UNVERIFIED') from None


def _targets(graph):
    try:
        if graph['main_sha256'] != MAIN_SHA256:
            raise ValueError
        nodes = {n['key']: n for n in graph['nodes']}
        if len(nodes) != len(graph['nodes']):
            raise ValueError
        selected = []
        for key in TARGETS:
            node = nodes[key]
            if node['parent_key'] != PARENT or nodes[PARENT]['children_keys'].count(key) != 1:
                raise ValueError
            request = httpx.Request('GET', node['viewer_url'])
            _request_key(request)
            params = request.url.params
            if (params['rcpNo'] != '20260331004244'
                    or params['dcmNo'] + ':' + params['eleId'] != key
                    or dict(params) != {k: node[k] for k in ('rcpNo', 'dcmNo', 'eleId', 'offset', 'length', 'dtd')}):
                raise ValueError
            selected.append(node)
        return selected
    except (KeyError, TypeError, ValueError):
        raise FixtureError('SOURCE_TARGET_UNVERIFIED') from None


async def probe(path, *, live=False, transport_factory=None):
    if live is not True:
        raise FixtureError('LIVE_ACK_REQUIRED')
    nodes = _targets(await asyncio.to_thread(_source_graph))
    # This diagnostic approval remains 2 MiB per child, independently of the
    # ordinary viewer envelope. Reject before buffered or streamed persistence.
    recorder = await FixtureRecorder.create(path, transport_factory, max_body_bytes=2 * 1024 * 1024)
    summary = {'purpose': 'NOTE_CHILD_DIAGNOSTIC', 'main_sha256': MAIN_SHA256,
               'parent_key': PARENT, 'target_keys': list(TARGETS),
               'production_input_allowed': False, 'body_sha256': []}
    complete, cancelled = False, False

    async def acquire():
        received = 0
        async with recorder.client_factory(timeout=10) as client:
            for node in nodes:
                client.cookies.clear()
                async with client.stream('GET', node['viewer_url'], headers={'Accept-Encoding': 'identity'}) as response:
                    if response.status_code != 200:
                        raise FixtureError('CHILD_HTTP_REJECTED')
                    if response.headers.get('content-encoding', 'identity').lower() != 'identity':
                        raise FixtureError('CHILD_ENCODING_REJECTED')
                    body = bytearray()
                    chunks = response.aiter_bytes() if response.is_stream_consumed else response.aiter_raw()
                    async for chunk in chunks:
                        received += len(chunk)
                        if received > MAX_TOTAL_BYTES:
                            raise FixtureError('CHILD_TOTAL_LIMIT')
                        body.extend(chunk)
                    body.decode('utf-8')
                    summary['body_sha256'].append(hashlib.sha256(body).hexdigest())
    try:
        await asyncio.wait_for(acquire(), timeout=TOTAL_TIMEOUT)
        complete = True
    except asyncio.CancelledError:
        cancelled = True
        summary['failure'] = 'PROBE_CANCELLED'
    except (asyncio.TimeoutError, httpx.TimeoutException):
        summary['failure'] = 'PROBE_TIMEOUT'
    except Exception:  # noqa: BLE001 - no transport exception text enters summaries
        summary['failure'] = 'PROBE_FAILED'
    summary['requests'] = len(recorder.responses)
    summary['observed_bytes'] = sum(r['observed_bytes'] for r in recorder.responses)
    finalizer = asyncio.create_task(recorder.finish({'purpose': summary['purpose']}, summary,
                                                    success=complete and not recorder.failed))
    try:
        manifest = await asyncio.shield(finalizer)
    except asyncio.CancelledError:
        finalizer.cancel()
        await _settle(finalizer, recorder)
        invalidator = asyncio.create_task(recorder.invalidate())
        await _settle(invalidator, recorder)
        raise
    if cancelled:
        raise asyncio.CancelledError
    return {**summary, 'complete': manifest['complete']}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--live', action='store_true')
    parser.add_argument('--out', required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        result = asyncio.run(probe(args.out, live=args.live))
    except Exception:  # noqa: BLE001 - CLI never prints source/path/credential errors
        result = {'complete': False, 'failure': 'PROBE_UNAVAILABLE'}
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result['complete'] else 2


if __name__ == '__main__':
    raise SystemExit(main())
