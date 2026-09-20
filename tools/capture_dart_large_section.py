"""One approved 8 MiB diagnostic capture; never a production parser input.

No source text or credentials are printed. This narrow one-off authorization
does not relax ordinary fixture, collection, parsing, or report budgets.
"""
import argparse
import asyncio
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import httpx

if __package__ in (None, ''):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.dart_fixture_transport import (
    MARKER,
    FixtureError,
    _path,
    _request_key,
    _write,
)

APPROVED_URL = ('https://dart.fss.or.kr/report/viewer.do?rcpNo=20260331004244'
                '&dcmNo=11213317&eleId=24&offset=977339&length=14241233&dtd=dart4.xsd')
MAX_DIAGNOSTIC_BYTES = 8 * 1024 * 1024
TOTAL_TIMEOUT = 60


async def _download(metadata, transport):
    # Validate before creating any network request, including the fixed URL.
    _request_key(httpx.Request('GET', APPROVED_URL))
    async with httpx.AsyncClient(transport=transport, trust_env=False, follow_redirects=False,
                                timeout=10, headers={'Accept-Encoding': 'identity'}) as client:
        metadata['requests'] = 1
        async with client.stream('GET', APPROVED_URL) as response:
            metadata['http_status'] = response.status_code
            if response.status_code != 200:
                raise FixtureError('HTTP_STATUS_REJECTED')
            if response.headers.get('content-encoding', 'identity').lower() != 'identity':
                raise FixtureError('HTTP_ENCODING_REJECTED')
            body = bytearray()
            chunks = response.aiter_bytes() if response.is_stream_consumed else response.aiter_raw()
            async for chunk in chunks:
                metadata['observed_bytes'] += len(chunk)
                if len(body) + len(chunk) > MAX_DIAGNOSTIC_BYTES:
                    raise FixtureError('DIAGNOSTIC_BODY_LIMIT')
                body.extend(chunk)
            try:
                body.decode('utf-8')
            except UnicodeDecodeError:
                raise FixtureError('HTTP_UTF8_INVALID') from None
            return bytes(body)


def capture(path, *, live=False, transport=None):
    """Synchronous CLI boundary: no asynchronous disk writer can outlive it."""
    if live is not True:
        raise FixtureError('LIVE_ACK_REQUIRED')
    path = _path(path)
    path.mkdir(mode=0o700)
    _write(path / MARKER, b'INCOMPLETE\n')
    result = {'version': 1, 'kind': 'APPROVED_LARGE_DART_DIAGNOSTIC', 'complete': False,
              'production_input_allowed': False, 'historical_version_verified': False,
              'url': APPROVED_URL, 'max_bytes': MAX_DIAGNOSTIC_BYTES,
              'started_at': datetime.now(timezone.utc).isoformat(),
              'http_status': None, 'requests': 0, 'observed_bytes': 0}
    body = None
    async def run():
        return await asyncio.wait_for(_download(result, transport), timeout=TOTAL_TIMEOUT)
    try:
        body = asyncio.run(run())
    except FixtureError as exc:
        result['failure'] = str(exc)
    except (asyncio.TimeoutError, httpx.TimeoutException):
        result['failure'] = 'TOTAL_TIMEOUT'
    except httpx.HTTPError:
        result['failure'] = 'HTTP_TRANSPORT_FAILURE'
    except Exception:  # noqa: BLE001 - diagnostic errors must not disclose provider details
        result['failure'] = 'DIAGNOSTIC_FAILED'
    result['finished_at'] = datetime.now(timezone.utc).isoformat()
    try:
        if body is not None:
            _write(path / 'body.bin', body)
            result.update(complete=True, file='body.bin', size=len(body),
                          sha256=hashlib.sha256(body).hexdigest())
        _write(path / 'manifest.json', json.dumps(result, ensure_ascii=False).encode())
        if result['complete']:
            (path / MARKER).unlink()
    except OSError:
        raise FixtureError('DIAGNOSTIC_STORAGE_FAILED') from None
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--live', action='store_true')
    parser.add_argument('--out', required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        result = capture(args.out, live=args.live)
    except FixtureError as exc:
        result = {'complete': False, 'failure': str(exc)}
    except Exception:  # noqa: BLE001 - no paths or raw exception details at the CLI
        result = {'complete': False, 'failure': 'DIAGNOSTIC_FAILED'}
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result['complete'] else 2


if __name__ == '__main__':
    raise SystemExit(main())
