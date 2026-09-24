"""Private diagnostics never expose credentials or change the original failure."""
import json
import os
from pathlib import Path
import re
import stat
import time
from types import SimpleNamespace

import pytest

from cores import report_review_diagnostics as diagnostics


def record(**kwargs):
    values = dict(stage='assessor', company_code='123456', reference_date='20260924',
                  sections={'company_status': 'Public report facts'}, response='Model output',
                  error=ValueError('never capture error secrets'), model='fixture-model')
    values.update(kwargs)
    return diagnostics.record_report_review_failure(**values)


@pytest.fixture
def directory(tmp_path, monkeypatch):
    # /tmp itself can be a symlink on macOS; the API intentionally rejects that.
    root = tmp_path.resolve() / 'diagnostics'
    monkeypatch.setenv('PRISM_REPORT_DIAGNOSTICS_DIR', str(root))
    return root


def load(directory, identifier):
    return json.loads((directory / (identifier + '.json')).read_text())


def test_private_permissions_opaque_identity_and_source_metadata(directory):
    identifier = record(response_id='provider-response-123')
    assert re.fullmatch(r'rr_[0-9a-f]{32}', identifier)
    assert str(directory) not in identifier
    path = directory / (identifier + '.json')
    assert stat.S_IMODE(directory.stat().st_mode) == 0o700
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    data = load(directory, identifier)
    assert data['code'] == 'REVIEW_ERROR'
    assert data['sections']['company_status']['text'] == 'Public report facts'
    assert len(data['sections']['company_status']['sha256']) == 64
    assert data['sections']['company_status']['characters'] == len('Public report facts')
    assert data['response_id'] == 'provider-response-123'
    assert 'never capture error secrets' not in path.read_text()


@pytest.mark.parametrize('secret', [
    'Bearer abcdefPrivate', 'sk-privateSecret123',
    'https://example.com?api_key=hiddenPrivate&public=yes',
    'https://example.com?access=hiddenPrivate&token=otherPrivate',
    'https://example.com?key=hiddenPrivate&signature=otherPrivate',
    '"api_key": "hiddenPrivate"', "access_token='hiddenPrivate'", 'password=hiddenPrivate',
    '"api_key": "prefix\\"hiddenPrivate"',
    '-----BEGIN PRIVATE KEY-----\nhiddenPrivate\n-----END PRIVATE KEY-----',
])
def test_redacts_report_and_response_secrets(directory, secret):
    identifier = record(sections={'company_status': secret}, response=secret)
    text = (directory / (identifier + '.json')).read_text()
    assert 'hiddenPrivate' not in text and 'otherPrivate' not in text
    assert 'abcdefPrivate' not in text and 'privateSecret123' not in text
    assert '[REDACTED]' in text


def test_sensitive_values_unknown_sections_and_error_details_are_not_captured(directory, monkeypatch):
    monkeypatch.setenv('PRIVATE_ACCOUNT_ENV', 'do-not-capture-environment')
    error = ValueError('do-not-capture-error')
    error.code = 'FACT_CONFLICT'
    error.details = {'section': 'company_status', 'field': 'replacements', 'index': 2, 'stage': 'guard',
                     'reason': {'api_key': 'hiddenPrivate'},
                     'environment': 'do-not-capture-environment', 'account': 'do-not-capture-account'}
    identifier = record(error=error, sections={'company_status': 'public', 'environment': 'do-not-capture-environment',
                                              'account_snapshot': 'do-not-capture-account'},
                        response={'access_token': 'hiddenPrivate', 'status': 'blocked'})
    text = (directory / (identifier + '.json')).read_text()
    assert 'hiddenPrivate' not in text and 'do-not-capture' not in text
    data = json.loads(text)
    assert data['code'] == 'FACT_CONFLICT' and set(data['details']) == {'section', 'reason', 'field', 'index', 'stage'}


def test_record_size_bound_for_large_unicode_and_details(directory):
    error = ValueError()
    error.details = {'issues': ['긴설명' * 100000] * 50}
    identifier = record(error=error, sections={key: '공개문장' * 150000 for key in diagnostics._SECTIONS},
                        response='응답문장' * 500000)
    path = directory / (identifier + '.json')
    assert path.stat().st_size <= diagnostics.MAX_BYTES
    assert json.loads(path.read_text())['truncated'] is True


@pytest.mark.parametrize('response', [
    {'request': 'x' * 80000},
    {'items': list(range(51))},
    {str(index): index for index in range(51)},
    {'a': {'b': {'c': {'d': {'e': 'deep'}}}}},
    {'k' * 129: 'value'},
    'x' * 600000,
])
def test_response_only_clipping_is_reported_with_short_sections(directory, response):
    identifier = record(response=response)
    data = load(directory, identifier)
    assert data['sections']['company_status']['text'] == 'Public report facts'
    assert data['response_truncated'] is True and data['truncated'] is True


def test_complete_small_response_is_not_marked_truncated(directory):
    data = load(directory, record(response={'status': 'blocked', 'issues': ['source mismatch']}))
    assert data['response_truncated'] is False and data['truncated'] is False


def test_retention_and_ttl_touch_only_owned_regular_matching_records(directory):
    first = record()
    first_path = directory / (first + '.json')
    stale = time.time() - diagnostics.TTL_SECONDS - 10
    os.utime(first_path, (stale, stale))
    unrelated = directory / 'report.json'
    unrelated.write_text('keep')
    link = directory / ('rr_' + 'a' * 32 + '.json')
    link.symlink_to(unrelated)
    for _ in range(25):
        assert record()
    regular = [p for p in directory.glob('rr_*.json') if not p.is_symlink()]
    assert len(regular) == diagnostics.MAX_RECORDS
    assert not first_path.exists() and unrelated.read_text() == 'keep' and link.is_symlink()


def test_rejects_root_or_parent_symlink(directory, monkeypatch):
    outside = directory.parent / 'outside'
    outside.mkdir()
    directory.symlink_to(outside, target_is_directory=True)
    assert record() is None and not list(outside.iterdir())
    monkeypatch.setenv('PRISM_REPORT_DIAGNOSTICS_DIR', str(directory / 'nested'))
    assert record() is None and not list(outside.iterdir())


def test_symlink_lock_rejected_without_modifying_target(directory):
    directory.mkdir()
    target = directory.parent / 'keep'
    target.write_text('private-other-file')
    (directory / '.retention.lock').symlink_to(target)
    assert record() is None and target.read_text() == 'private-other-file'


def test_disk_failure_returns_none_and_removes_partial_record(directory, monkeypatch):
    def failed(*args, **kwargs): raise OSError('fixture disk full')
    monkeypatch.setattr(diagnostics.os, 'fsync', failed)
    assert record() is None
    assert not list(directory.glob('rr_*.json'))


def test_exclusive_create_collision_never_deletes_existing_record(directory, monkeypatch):
    first = record()
    path = directory / (first + '.json')
    before = path.read_bytes()
    monkeypatch.setattr(diagnostics.uuid, 'uuid4', lambda: SimpleNamespace(hex=first[3:]))
    assert record() is None
    assert path.read_bytes() == before


def test_hardlinked_lock_does_not_change_other_file_permissions(directory):
    directory.mkdir()
    outside = directory.parent / 'other'
    outside.write_text('other')
    outside.chmod(0o644)
    os.link(outside, directory / '.retention.lock')
    assert record() is None
    assert stat.S_IMODE(outside.stat().st_mode) == 0o644


def test_invalid_relative_override_is_not_resolved_into_working_directory(monkeypatch):
    monkeypatch.setenv('PRISM_REPORT_DIAGNOSTICS_DIR', '../unsafe')
    assert record() is None


def test_validation_capture_uses_structured_json_and_preserves_result():
    from tools.verify_kr_report_depth import _validation_reply_text
    from pydantic import BaseModel

    class Reply(BaseModel):
        status: str

    result = SimpleNamespace(text='', structured=Reply(status='blocked'))
    assert json.loads(_validation_reply_text(result)) == {'status': 'blocked'}
    assert result.text == '' and result.structured.status == 'blocked'
    assert _validation_reply_text(SimpleNamespace(text='plain', structured=None)) == 'plain'
