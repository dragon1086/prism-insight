"""Private, bounded failure evidence. Never a public log or a report cache."""
from collections.abc import Mapping
from datetime import datetime, timezone
import fcntl
import hashlib
from itertools import islice
import json
import os
from pathlib import Path
import re
import stat
import time
import uuid
from urllib.parse import unquote_plus, urlsplit, urlunsplit


MAX_BYTES = 1024 * 1024
MAX_RECORDS = 20
TTL_SECONDS = 7 * 24 * 3600
_ROOT = Path(__file__).resolve().parents[1] / 'runtime' / 'report_diagnostics'
_FILES = re.compile(r'rr_[0-9a-f]{32}\.json\Z')
_SECTIONS = frozenset({
    'price_volume_analysis', 'investor_trading_analysis', 'institutional_holdings_analysis',
    'company_status', 'company_overview', 'news_analysis', 'market_index_analysis',
    'investment_strategy', 'summary', 'shared_reference', 'macro_context',
    'dart_deep_analysis', 'dart_depth_limit', 'peer_comparison',
})
_DETAILS = frozenset({'section', 'sections', 'targets', 'reason', 'issue', 'issues',
                      'count', 'limit', 'status', 'attempt', 'field', 'index', 'stage'})
_SENSITIVE_NAMES = r'(?:(?:x[-_])?api[_-]?key|api[_-]?token|api|access[_-]?token|access|refresh[_-]?token|id[_-]?token|token|key|signature|x-amz-(?:signature|credential|security-token)|authorization|password|passwd|secret|client[_-]?secret|(?:private|secret)[_-]?key)'
_SECRET_KEY = re.compile(r'^' + _SENSITIVE_NAMES + r'$', re.I)


def _clip(text, limit):
    return text.encode('utf-8', errors='replace')[:limit].decode('utf-8', errors='ignore')


def _redact_url(match):
    url = match.group(0)
    # Strip userinfo before parsing: secret values may themselves contain URL
    # punctuation or brackets that make urlsplit reject an otherwise usable URL.
    url = re.sub(r'^(https?://)[^/?#]*@', r'\1REDACTED@', url, flags=re.I)
    try:
        parsed = urlsplit(url)
        fields = re.split(r'([&;])', parsed.query)
        for index in range(0, len(fields), 2):
            key, separator, value = fields[index].partition('=')
            decoded = key
            for _ in range(3):
                next_key = unquote_plus(decoded)
                if next_key == decoded:
                    break
                decoded = next_key
            if separator and _SECRET_KEY.fullmatch(decoded):
                fields[index] = key + '=[REDACTED]'
        return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, ''.join(fields), parsed.fragment))
    except ValueError:
        # Never retain a potentially credential-bearing malformed URL.
        return '[REDACTED_URL]'


def _redact(text):
    text = _clip(text, MAX_BYTES * 2)
    text = re.sub(r'-----BEGIN(?: RSA| EC| OPENSSH)? PRIVATE KEY-----.*?(?:-----END[^\n]*PRIVATE KEY-----|\Z)',
                  '[REDACTED]', text, flags=re.S)
    text = re.sub(r'\bBearer\s+[^\s"\'<>]+', 'Bearer [REDACTED]', text, flags=re.I)
    text = re.sub(r'\bsk-[A-Za-z0-9_-]+', '[REDACTED]', text)
    text = re.sub(r'([?&]' + _SENSITIVE_NAMES + r'=)[^&#\s"\'<>]*', r'\1[REDACTED]', text, flags=re.I)
    text = re.sub(r'((?:["\']?' + _SENSITIVE_NAMES + r'["\']?)\s*[:=]\s*)'
                  r'(?:"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'|[^\s,;}\]<>&#]+)',
                  r'\1[REDACTED]', text, flags=re.I)
    text = re.sub(r'https?://[^\s<>"\']+', _redact_url, text, flags=re.I)
    return text


def _safe(value, depth=0, budget=None, truncated=None):
    budget = [512] if budget is None else budget
    truncated = [False] if truncated is None else truncated
    budget[0] -= 1
    if depth > 4 or budget[0] < 0:
        truncated[0] = True
        return '[OMITTED]'
    if isinstance(value, str):
        cleaned = _redact(value)
        truncated[0] |= len(value.encode('utf-8', errors='replace')) > MAX_BYTES * 2 or len(cleaned.encode()) > 65536
        return _clip(cleaned, 65536)
    if value is None or isinstance(value, (bool, int)):
        return value
    if isinstance(value, float):
        return value if value == value and abs(value) != float('inf') else None
    if isinstance(value, Mapping):
        truncated[0] |= len(value) > 50
        result = {}
        for key, item in islice(value.items(), 50):
            cleaned_key = _redact(str(key))
            truncated[0] |= len(cleaned_key.encode()) > 128
            result[_clip(cleaned_key, 128)] = ('[REDACTED]' if _SECRET_KEY.fullmatch(str(key))
                                              else _safe(item, depth + 1, budget, truncated))
        return result
    if isinstance(value, (list, tuple)):
        truncated[0] |= len(value) > 50
        return [_safe(item, depth + 1, budget, truncated) for item in value[:50]]
    truncated[0] = True
    return '[UNSUPPORTED]'


def _directory_fd(path):
    """Open every component without following symlinks, including parent dirs."""
    if not path.is_absolute() or '..' in path.parts:
        raise ValueError('Diagnostics root must be an absolute safe path')
    fd = os.open(path.anchor, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for component in path.parts[1:]:
            try:
                os.mkdir(component, mode=0o700, dir_fd=fd)
            except FileExistsError:
                pass
            child = os.open(component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = child
        if os.fstat(fd).st_uid != os.getuid():
            raise PermissionError('Diagnostics directory is not owned by this user')
        # This is an O_DIRECTORY descriptor: only its owner may read, write,
        # or traverse it. No group/other permission bits are granted.
        os.fchmod(fd, stat.S_IRWXU)
        return fd
    except BaseException:
        os.close(fd)
        raise


def _payload(stage, company_code, reference_date, sections, response, error, model, response_id):
    metadata = {}
    if isinstance(sections, Mapping):
        for key, value in islice(sections.items(), 64):
            # Unknown keys may denote account/environment snapshots: omit them.
            if key not in _SECTIONS or not isinstance(value, str):
                continue
            cleaned = _redact(value)
            metadata[key] = {'sha256': hashlib.sha256(cleaned.encode()).hexdigest(),
                             'characters': len(value), 'text': _clip(cleaned, 262144)}
    details = getattr(error, 'details', None)
    response_truncated = [False]
    response_text = response if isinstance(response, str) else json.dumps(
        _safe(response, truncated=response_truncated), ensure_ascii=False)
    response_truncated[0] |= len(response_text.encode('utf-8', errors='replace')) > MAX_BYTES * 2
    response_text = _redact(response_text)
    response_truncated[0] |= len(response_text.encode()) > 524288
    data = {
        'schema': 1, 'captured_at': datetime.now(timezone.utc).isoformat(),
        'stage': _clip(_redact(str(stage)), 128),
        'company_code': _clip(_redact(str(company_code)), 64),
        'reference_date': _clip(_redact(str(reference_date)), 32),
        'model': _clip(_redact(str(model)), 128),
        'response_id': _clip(_redact(str(response_id)), 128) if response_id is not None else None,
        'error_type': type(error).__name__, 'code': _clip(_redact(str(getattr(error, 'code', 'REVIEW_ERROR'))), 128),
        'details': {key: _safe(value) for key, value in details.items() if key in _DETAILS} if isinstance(details, Mapping) else {},
        'sections': metadata,
        'response': _clip(response_text, 524288),
        'response_truncated': response_truncated[0],
        'truncated': response_truncated[0],
    }
    if len(json.dumps(data['details'], ensure_ascii=False).encode()) > 65536:
        data['details'] = {'status': 'details_omitted_size_limit'}
        data['truncated'] = True
        if data['response']:
            data['response_truncated'] = True
    data['truncated'] |= any(len(item['text']) < item['characters'] for item in metadata.values())
    while True:
        encoded = json.dumps(data, ensure_ascii=False, allow_nan=False).encode('utf-8')
        if len(encoded) <= MAX_BYTES:
            return encoded
        data['truncated'] = True
        data['response'] = _clip(data['response'], len(data['response'].encode()) // 2)
        for item in metadata.values():
            item['text'] = _clip(item['text'], len(item['text'].encode()) // 2)


def record_report_review_failure(*, stage, company_code, reference_date, sections,
                                 response, error, model, response_id=None) -> str | None:
    """Best effort: return opaque ID only; failures must not mask review errors."""
    directory = lock = target = None
    name = None
    created = False
    try:
        content = _payload(stage, company_code, reference_date, sections, response, error, model, response_id)
        root = Path(os.environ.get('PRISM_REPORT_DIAGNOSTICS_DIR') or _ROOT)
        directory = _directory_fd(root)
        lock = os.open('.retention.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600, dir_fd=directory)
        if (not stat.S_ISREG(os.fstat(lock).st_mode) or os.fstat(lock).st_uid != os.getuid()
                or os.fstat(lock).st_nlink != 1):
            raise ValueError('Unsafe diagnostics lock')
        os.fchmod(lock, 0o600)
        fcntl.flock(lock, fcntl.LOCK_EX)
        records = []
        with os.scandir(directory) as entries:
            for entry in entries:
                if not _FILES.fullmatch(entry.name):
                    continue
                info = entry.stat(follow_symlinks=False)
                if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid():
                    continue
                if info.st_mtime < time.time() - TTL_SECONDS:
                    os.unlink(entry.name, dir_fd=directory)
                else:
                    records.append((info.st_mtime, entry.name))
        for _, old in sorted(records)[:max(0, len(records) - MAX_RECORDS + 1)]:
            os.unlink(old, dir_fd=directory)
        identifier = 'rr_' + uuid.uuid4().hex
        name = identifier + '.json'
        target = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=directory)
        created = True
        os.fchmod(target, 0o600)
        with os.fdopen(target, 'wb') as stream:
            target = None
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        return identifier
    except Exception:
        if created and name is not None and directory is not None:
            try:
                info = os.stat(name, dir_fd=directory, follow_symlinks=False)
                if stat.S_ISREG(info.st_mode) and info.st_uid == os.getuid():
                    os.unlink(name, dir_fd=directory)
            except OSError:
                pass
        return None
    finally:
        for descriptor in (target, lock, directory):
            if descriptor is not None:
                try:
                    os.close(descriptor)
                except OSError:
                    pass
