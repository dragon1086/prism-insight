import gzip
import json
import random

import pytest

from utils import btc_review_event_references as refs


def canonical(value):
    return json.dumps(value, allow_nan=False, sort_keys=True)


def sample():
    large = random.Random(17).randbytes(16000).hex()
    request = {'kind': 'MODEL_REQUEST', 'phase': 'single', 'request': {
        'system_prompt': large, 'user_prompt': large[::-1], 'timeout': 50.0, 'model': 'fixture'}}
    actual = dict(request['request'], timeout=49.75)
    raw = {'kind': 'MODEL_RAW', 'phase': 'single', 'actual_request': actual,
           'raw': '{"action":"WAIT"}', 'actual_request_sha256': 'a' * 64}
    return canonical(request), canonical(raw)


def test_unique_matching_request_restores_exact_original_bytes():
    request, raw = sample()
    encoded = refs.compact(raw, [(2, request)], 100, 3)
    assert isinstance(encoded, bytes) and encoded.startswith(refs.MAGIC)
    assert len(encoded) < len(gzip.compress(raw.encode(), compresslevel=1, mtime=0))
    assert refs.restore(encoded, request, 100, 3) == raw


def test_ambiguous_missing_or_noncanonical_evidence_keeps_original():
    request, raw = sample()
    assert refs.compact(raw, [], 100, 3) == raw
    assert refs.compact(raw, [(1, request), (2, request)], 100, 3) == raw
    assert refs.compact(raw, [(3, request)], 100, 3) == raw
    assert refs.compact(raw + ' ', [(2, request)], 100, 3) == raw + ' '
    changed = json.loads(request)
    changed['request']['model'] = 'different'
    assert refs.compact(raw, [(2, canonical(changed))], 100, 3) == raw
    assert refs.compact('[]', [(2, request)], 100, 3) == '[]'
    assert refs.compact(raw, [(2, '[]')], 100, 3) == raw


def test_bool_and_number_are_not_treated_as_identical_json():
    request, raw = sample()
    left, right = json.loads(request), json.loads(raw)
    left['request']['flag'] = 1
    right['actual_request']['flag'] = True
    assert refs.compact(canonical(right), [(2, canonical(left))], 100, 3) == canonical(right)


def test_reference_rejects_corrupt_or_changed_sources():
    request, raw = sample()
    encoded = refs.compact(raw, [(2, request)], 100, 3)
    with pytest.raises(ValueError):
        refs.restore(encoded, request + ' ', 100, 3)
    with pytest.raises(ValueError):
        refs.restore(encoded, request, 101, 3)
    with pytest.raises(ValueError):
        refs.restore(encoded, request, 100, 4)
    with pytest.raises(ValueError):
        refs.restore(encoded[:-4], request, 100, 3)
    with pytest.raises(ValueError):
        refs.restore(encoded + b'extra', request, 100, 3)
