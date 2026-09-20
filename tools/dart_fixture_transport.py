"""Private, bounded diagnostic fixtures; never imported by production collectors."""
import asyncio
import hashlib
import json
import os
import re
import stat
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import httpx

MAX_BODY = 2 * 1024 * 1024
MAX_TOTAL = 12 * 1024 * 1024
MAX_MANIFEST = 1024 * 1024
MAX_RESPONSES = 32
MARKER = 'INCOMPLETE'
_FORM_KEYS = {'currentPage', 'maxResults', 'maxLinks', 'sort', 'series', 'textCrpNm',
              'textCrpCik', 'autoSearchCorp', 'pageGubun', 'startDate', 'endDate', 'publicType'}
_ERRORS = {name: getattr(httpx, name) for name in (
    'ConnectError', 'ReadError', 'WriteError', 'CloseError', 'ConnectTimeout',
    'ReadTimeout', 'WriteTimeout', 'PoolTimeout', 'RemoteProtocolError', 'LocalProtocolError',
    'ProxyError', 'UnsupportedProtocol')}


class FixtureError(ValueError):
    """Static diagnostic failure; never contains a body or credentials."""


def _request_key(request):
    url = urlsplit(str(request.url))
    if (url.scheme != 'https' or url.netloc != 'dart.fss.or.kr' or url.fragment
            or any(key.lower() in {'authorization', 'proxy-authorization', 'cookie', 'x-api-key', 'api-key'}
                   for key in request.headers)):
        raise FixtureError('REQUEST_POLICY_REJECTED')
    query = parse_qsl(url.query, keep_blank_values=True, strict_parsing=True)
    form = []
    if request.method == 'POST' and url.path in {'/dsab001/searchCorp.ax', '/dsae001/selectPopup.ax'}:
        allowed = _FORM_KEYS if url.path.endswith('searchCorp.ax') else {'selectKey'}
        if query or len(request.content) > 8192:
            raise FixtureError('REQUEST_POLICY_REJECTED')
        form = parse_qsl(request.content.decode('utf-8'), keep_blank_values=True, strict_parsing=True)
        if any(k not in allowed or len(v) > 256 for k, v in form):
            raise FixtureError('REQUEST_POLICY_REJECTED')
    elif request.method == 'GET' and url.path in {'/dsaf001/main.do', '/report/viewer.do'}:
        allowed = {'rcpNo'} if url.path.endswith('main.do') else {'rcpNo', 'dcmNo', 'eleId', 'offset', 'length', 'dtd'}
        if request.content or len(query) != len(allowed) or {k for k, _ in query} != allowed:
            raise FixtureError('REQUEST_POLICY_REJECTED')
        for key, value in query:
            pattern = r'dart\d{1,2}\.xsd' if key == 'dtd' else (r'\d{14}' if key == 'rcpNo' else r'\d{1,20}')
            if not re.fullmatch(pattern, value):
                raise FixtureError('REQUEST_POLICY_REJECTED')
    else:
        raise FixtureError('REQUEST_POLICY_REJECTED')
    return {'method': request.method,
            'url': urlunsplit((url.scheme, url.netloc, url.path, urlencode(sorted(query)), '')),
            'form': [list(pair) for pair in form]}


def _path(path):
    path = Path(os.path.abspath(path))
    for part in [path, *path.parents]:
        if part.is_symlink():
            raise FixtureError('SYMLINK_REJECTED')
        if (part / '.git').exists():
            raise FixtureError('GIT_PATH_REJECTED')
    return path


def _write(path, data):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'wb') as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())


def _private_read(path, limit):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as stream:
        info = os.fstat(stream.fileno())
        if (not stat.S_ISREG(info.st_mode) or stat.S_IMODE(info.st_mode) != 0o600
                or info.st_nlink != 1 or info.st_uid != os.getuid() or info.st_size > limit):
            raise FixtureError('FILE_POLICY_REJECTED')
        data = stream.read(limit + 1)
        if len(data) > limit:
            raise FixtureError('FILE_LIMIT')
        return data


class FixtureRecorder:
    def __init__(self, path, transport_factory):
        self.path = path
        self.transport_factory = transport_factory or (lambda: httpx.AsyncHTTPTransport(trust_env=False))
        self.responses = []
        self.failed = False
        self.incomplete = True
        self._writes = []
        self._total = 0
        self._finished = False

    @classmethod
    async def create(cls, path, transport_factory=None):
        path = _path(path)
        def create():
            path.mkdir(mode=0o700)
            _write(path / MARKER, b'INCOMPLETE\n')
        try:
            # Creation is shielded and joined even when its caller is cancelled.
            task = asyncio.create_task(asyncio.to_thread(create))
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                await task
                raise
        except OSError as exc:
            raise FixtureError('CREATE_FAILED') from exc
        return cls(path, transport_factory)

    async def _io(self, function, *args):
        task = asyncio.create_task(asyncio.to_thread(function, *args))
        self._writes.append(task)
        try:
            return await asyncio.shield(task)
        except BaseException:
            self.failed = True
            raise

    def client_factory(self, **opts):
        if self._finished or self.failed:
            raise FixtureError('RECORDER_NOT_ACTIVE')
        if any(key in opts for key in ('transport', 'mounts', 'proxy', 'auth', 'cookies', 'headers')):
            raise FixtureError('CLIENT_POLICY_REJECTED')
        return httpx.AsyncClient(**{**opts, 'trust_env': False, 'follow_redirects': False,
                                   'transport': _RecordingTransport(self, self.transport_factory())})

    async def _body(self, entry, body, state):
        number = next(i for i, item in enumerate(self.responses, 1) if item is entry)
        filename = f'response-{number:04d}.bin'
        await self._io(_write, self.path / filename, body)
        entry.update(state=state, file=filename, size=len(body), sha256=hashlib.sha256(body).hexdigest())

    def _capture_limit(self, entry):
        codes = []
        if entry['observed_bytes'] > MAX_BODY:
            codes.append('CAPTURE_BODY_LIMIT')
        if self._total > MAX_TOTAL:
            codes.append('CAPTURE_TOTAL_LIMIT')
        if codes:
            self.failed = True
            entry.update(state='CAPTURE_LIMIT_EXCEEDED', capture_failure_codes=codes)
        return bool(codes)

    async def invalidate(self):
        """Join prior I/O and quarantine even an already finalized capture.

        The owner must join its finalizer before invoking this method, and shield
        and join this operation when handling cancellation.
        """
        self.failed = True
        self.incomplete = True
        self._finished = True
        if self._writes:
            await asyncio.shield(asyncio.gather(*self._writes, return_exceptions=True))
        def restore():
            if not (self.path / MARKER).exists():
                _write(self.path / MARKER, b'INCOMPLETE\n')
        await self._io(restore)

    async def finish(self, inputs, summary, success=True):
        if self._finished:
            raise FixtureError('ALREADY_FINISHED')
        self._finished = True
        if self._writes:
            pending = asyncio.gather(*self._writes, return_exceptions=True)
            try:
                results = await asyncio.shield(pending)
            except asyncio.CancelledError:
                self.failed = True
                await pending
                raise
            self.failed |= any(isinstance(result, BaseException) for result in results)
        valid_states = {'EOF_COMPLETE', 'BUFFERED_COMPLETE', 'POLICY_REJECTED', 'TRANSPORT_ERROR'}
        complete = bool(success and not self.failed and all(e['state'] in valid_states for e in self.responses))
        manifest = {'version': 1, 'classification': 'QUARANTINED_CAPTURE', 'complete': complete,
                    'inputs': inputs, 'summary': summary, 'responses': self.responses}
        try:
            data = json.dumps(manifest, ensure_ascii=False, allow_nan=False).encode('utf-8')
            if len(data) > MAX_MANIFEST:
                raise FixtureError('MANIFEST_LIMIT')
            await self._io(_write, self.path / 'manifest.json', data)
            if complete:
                await self._io(os.unlink, self.path / MARKER)
                self.incomplete = False
        except BaseException:
            self.failed = True
            # A shielded unlink may finish after caller cancellation. Join it and
            # restore the marker rather than allowing a cancelled finalization
            # to leave a seemingly complete fixture.
            await asyncio.gather(*self._writes, return_exceptions=True)
            if not (self.path / MARKER).exists():
                await asyncio.to_thread(_write, self.path / MARKER, b'INCOMPLETE\n')
            raise
        return manifest


class _RecordingTransport(httpx.AsyncBaseTransport):
    def __init__(self, recorder, inner):
        self.recorder, self.inner = recorder, inner

    async def handle_async_request(self, request):
        recorder = self.recorder
        try:
            key = _request_key(request)
            if recorder._finished or len(recorder.responses) >= MAX_RESPONSES:
                raise FixtureError('RESPONSE_COUNT_LIMIT')
        except (ValueError, UnicodeError):
            recorder.failed = True
            raise FixtureError('REQUEST_POLICY_REJECTED') from None
        entry = {'request': key, 'status': None, 'encoding': None, 'state': 'CANCELLED', 'observed_bytes': 0,
                 'observed_at': datetime.now(timezone.utc).isoformat()}
        recorder.responses.append(entry)
        try:
            response = await self.inner.handle_async_request(request)
        except httpx.HTTPError as exc:
            name = type(exc).__name__
            if name in _ERRORS:
                entry.update(state='TRANSPORT_ERROR', error=name)
            else:
                recorder.failed = True
                entry['state'] = 'STREAM_ERROR'
            raise
        except BaseException:
            recorder.failed = True
            raise
        encoding = response.headers.get('content-encoding')
        if encoding is not None and len(encoding) > 128:
            recorder.failed = True
            await response.aclose()
            raise FixtureError('ENCODING_LIMIT')
        entry.update(status=response.status_code, encoding=encoding, state='CONSUMER_CLOSED')
        # Policy rejections require status/encoding only, not an invented body.
        if response.status_code != 200 or (encoding is not None and encoding.strip().lower() != 'identity'):
            entry['state'] = 'POLICY_REJECTED'
            await response.aclose()
            return httpx.Response(response.status_code, headers={'content-encoding': encoding} if encoding is not None else {},
                                  stream=httpx.ByteStream(b''))
        if response.is_stream_consumed:
            body = response.content
            entry['observed_bytes'] = len(body)
            recorder._total += len(body)
            if recorder._capture_limit(entry):
                await response.aclose()
                raise FixtureError('BODY_LIMIT')
            await recorder._body(entry, body, 'BUFFERED_COMPLETE')
            return response
        return httpx.Response(response.status_code, headers=response.headers,
                              stream=_RecordingStream(recorder, response, entry))

    async def aclose(self):
        await self.inner.aclose()


class _RecordingStream(httpx.AsyncByteStream):
    def __init__(self, recorder, response, entry):
        self.recorder, self.response, self.entry = recorder, response, entry

    async def __aiter__(self):
        body = bytearray()
        try:
            async for chunk in self.response.stream:
                self.entry['observed_bytes'] += len(chunk)
                self.recorder._total += len(chunk)
                if self.recorder._capture_limit(self.entry):
                    raise FixtureError('BODY_LIMIT')
                body.extend(chunk)
                yield chunk
            await self.recorder._body(self.entry, bytes(body), 'EOF_COMPLETE')
        except asyncio.CancelledError:
            self.entry['state'] = 'CANCELLED'
            self.recorder.failed = True
            raise
        except Exception:
            if self.entry['state'] != 'CAPTURE_LIMIT_EXCEEDED':
                self.entry['state'] = 'STREAM_ERROR'
            self.recorder.failed = True
            raise

    async def aclose(self):
        await self.response.aclose()


class FixtureReplay:
    def __init__(self, manifest, bodies):
        self.manifest, self.bodies = manifest, bodies
        self.index = 0
        self.failed = False

    @classmethod
    async def load(cls, path):
        def load():
            root = _path(path)
            if not root.is_dir() or stat.S_IMODE(root.stat().st_mode) != 0o700 or (root / MARKER).exists():
                raise FixtureError('FIXTURE_INCOMPLETE_OR_UNSAFE')
            manifest = json.loads(_private_read(root / 'manifest.json', MAX_MANIFEST))
            if (not isinstance(manifest, dict) or type(manifest.get('version')) is not int or manifest.get('version') != 1
                    or set(manifest) != {'version', 'classification', 'complete', 'inputs', 'summary', 'responses'}
                    or manifest.get('complete') is not True or manifest.get('classification') != 'QUARANTINED_CAPTURE'
                    or not isinstance(manifest.get('inputs'), dict) or not isinstance(manifest.get('summary'), dict)):
                raise FixtureError('MANIFEST_SCHEMA')
            entries = manifest.get('responses')
            if not isinstance(entries, list) or len(entries) > MAX_RESPONSES:
                raise FixtureError('MANIFEST_SCHEMA')
            bodies, total, files = [], 0, {'manifest.json'}
            for number, entry in enumerate(entries, 1):
                if not isinstance(entry, dict):
                    raise FixtureError('MANIFEST_SCHEMA')
                key = entry['request']
                if not isinstance(key, dict) or set(key) != {'method', 'url', 'form'}:
                    raise FixtureError('MANIFEST_SCHEMA')
                form = key['form']
                if not isinstance(form, list) or any(not isinstance(p, list) or len(p) != 2 or any(type(v) is not str for v in p) for p in form):
                    raise FixtureError('MANIFEST_SCHEMA')
                request = httpx.Request(key['method'], key['url'], content=urlencode([tuple(p) for p in form]).encode())
                if _request_key(request) != key:
                    raise FixtureError('REQUEST_POLICY_REJECTED')
                state, status, encoding = entry['state'], entry['status'], entry['encoding']
                expected = {'request', 'state', 'status', 'encoding', 'observed_bytes', 'observed_at'}
                if state in {'EOF_COMPLETE', 'BUFFERED_COMPLETE'}:
                    expected |= {'file', 'size', 'sha256'}
                elif state == 'TRANSPORT_ERROR':
                    expected.add('error')
                if set(entry) != expected:
                    raise FixtureError('MANIFEST_SCHEMA')
                observed = entry['observed_at']
                if (not isinstance(observed, str) or len(observed) > 64
                        or datetime.fromisoformat(observed).utcoffset() is None):
                    raise FixtureError('MANIFEST_SCHEMA')
                if encoding is not None and (not isinstance(encoding, str) or len(encoding) > 128):
                    raise FixtureError('MANIFEST_SCHEMA')
                if type(entry['observed_bytes']) is not int or entry['observed_bytes'] < 0:
                    raise FixtureError('MANIFEST_SCHEMA')
                body = b''
                if state == 'TRANSPORT_ERROR':
                    if entry['error'] not in _ERRORS or status is not None or encoding is not None:
                        raise FixtureError('MANIFEST_SCHEMA')
                elif type(status) is not int or not 100 <= status <= 599:
                    raise FixtureError('MANIFEST_SCHEMA')
                elif state in {'EOF_COMPLETE', 'BUFFERED_COMPLETE'}:
                    filename = f'response-{number:04d}.bin'
                    if entry['file'] != filename or status != 200 or (encoding is not None and encoding.strip().lower() != 'identity'):
                        raise FixtureError('MANIFEST_SCHEMA')
                    body = _private_read(root / filename, MAX_BODY)
                    if (type(entry['size']) is not int or len(body) != entry['size']
                            or len(body) != entry['observed_bytes'] or hashlib.sha256(body).hexdigest() != entry['sha256']):
                        raise FixtureError('BODY_HASH_MISMATCH')
                    files.add(filename)
                elif state != 'POLICY_REJECTED' or (status == 200 and (encoding is None or encoding.lower() == 'identity')):
                    raise FixtureError('FIXTURE_INCOMPLETE')
                if state not in {'EOF_COMPLETE', 'BUFFERED_COMPLETE'} and any(k in entry for k in ('file', 'size', 'sha256')):
                    raise FixtureError('MANIFEST_SCHEMA')
                total += len(body)
                if total > MAX_TOTAL:
                    raise FixtureError('BODY_LIMIT')
                bodies.append(body)
            if {p.name for p in root.iterdir()} != files:
                raise FixtureError('UNEXPECTED_FILES')
            return cls(manifest, bodies)
        try:
            return await asyncio.to_thread(load)
        except (OSError, KeyError, TypeError, ValueError, UnicodeError) as exc:
            raise FixtureError('FIXTURE_VALIDATION_FAILED') from exc

    def client_factory(self, **opts):
        if any(key in opts for key in ('transport', 'mounts', 'proxy', 'auth', 'cookies', 'headers')):
            raise FixtureError('CLIENT_POLICY_REJECTED')
        return httpx.AsyncClient(**{**opts, 'trust_env': False, 'follow_redirects': False,
                                   'transport': httpx.MockTransport(self._handle)})

    async def _handle(self, request):
        entries = self.manifest['responses']
        try:
            if self.failed or self.index >= len(entries) or _request_key(request) != entries[self.index]['request']:
                raise FixtureError('REQUEST_DIVERGENCE')
        except (ValueError, UnicodeError):
            self.failed = True
            raise FixtureError('REQUEST_DIVERGENCE') from None
        entry, body = entries[self.index], self.bodies[self.index]
        self.index += 1
        if entry['state'] == 'TRANSPORT_ERROR':
            raise _ERRORS[entry['error']]('RECORDED_TRANSPORT_ERROR', request=request)
        headers = {'content-encoding': entry['encoding']} if entry['encoding'] is not None else {}
        return httpx.Response(entry['status'], headers=headers, stream=httpx.ByteStream(body))

    def assert_consumed(self):
        if self.failed or self.index != len(self.manifest['responses']):
            raise FixtureError('REQUEST_DIVERGENCE')
