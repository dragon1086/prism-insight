"""Exact recovery-reference and legacy reader coverage."""
import gzip
import json
import sqlite3

import pytest

from observability.review_storage import MAGIC as LEGACY_MAGIC, review_text
from utils.btc_recovery_references import MAGIC, TAG, canonical, compact, pack, read_recovery, restore


def fixture():
    conn = sqlite3.connect(':memory:')
    conn.execute('CREATE TABLE llm_scenario_review_events(slot INTEGER,sequence INTEGER,body TEXT)')
    review = json.dumps({'kind': 'MODEL_REQUEST', 'request': '한글' * 2000}, sort_keys=True)
    recovery = canonical(dict(json.loads(review), calculated_review_version=1, input_id='test'))
    conn.execute('INSERT INTO llm_scenario_review_events VALUES(?,?,?)', (7, 2, review))
    candidate = compact(review, recovery, 7, 2)
    assert candidate != recovery
    return conn, review, recovery, candidate


def test_reference_roundtrip_and_compressed_legacy():
    conn, review, recovery, candidate = fixture()
    assert read_recovery(pack(candidate), conn, 7, decode_legacy=review_text, expected_kind='MODEL_REQUEST') == recovery
    assert read_recovery(LEGACY_MAGIC + gzip.compress(recovery.encode()), conn, 7, decode_legacy=review_text) == recovery
    assert read_recovery(recovery, None, None, decode_legacy=review_text) == recovery
    assert restore(candidate, review, 7, 2) == recovery
    with pytest.raises(ValueError, match='kind_mismatch'):
        read_recovery(pack(candidate), conn, 7, decode_legacy=review_text, expected_kind='MODEL_RAW')
    with pytest.raises(ValueError, match='kind_mismatch'):
        read_recovery(pack(candidate), conn, 7, decode_legacy=review_text)
    conn.close()


def test_missing_changed_and_duplicate_review_fail_closed():
    conn, review, _, candidate = fixture()
    with pytest.raises(ValueError, match='context_missing'):
        read_recovery(pack(candidate), conn, None, decode_legacy=review_text)
    with pytest.raises(ValueError, match='context_missing'):
        read_recovery(pack(candidate), None, 7, decode_legacy=review_text)
    conn.execute('UPDATE llm_scenario_review_events SET body=?', (review + ' ',))
    with pytest.raises(ValueError, match='reference_invalid'):
        read_recovery(pack(candidate), conn, 7, decode_legacy=review_text)
    conn.execute('DELETE FROM llm_scenario_review_events')
    with pytest.raises(ValueError, match='source_missing'):
        read_recovery(pack(candidate), conn, 7, decode_legacy=review_text)
    conn.executemany('INSERT INTO llm_scenario_review_events VALUES(?,?,?)', [(7, 2, review), (7, 2, review)])
    with pytest.raises(ValueError, match='source_missing'):
        read_recovery(pack(candidate), conn, 7, decode_legacy=review_text)
    with pytest.raises(ValueError, match='compression_invalid'):
        read_recovery(MAGIC + b'bad', conn, 7, decode_legacy=review_text)
    conn.close()


def test_noncanonical_or_nonmatching_inputs_stay_legacy_and_bad_marker_fails():
    review = json.dumps({'kind': 'MODEL_RAW', 'raw': 'x' * 1000})
    recovery = canonical(dict(json.loads(review), calculated_review_version=1))
    assert compact(review, ' ' + recovery, 7, 2) == ' ' + recovery
    different = canonical({'kind': 'MODEL_RAW', 'calculated_review_version': 1})
    assert compact(review, different, 7, 2) == different
    candidate = json.loads(compact(review, recovery, 7, 2))
    candidate['slot'] = True
    with pytest.raises(ValueError, match='reference_invalid'):
        restore(canonical(candidate), review, 1, 2)
    ordinary = canonical({'kind': 'MODEL_RAW', 'raw': 'contains ' + TAG})
    assert read_recovery(ordinary, None, None, decode_legacy=review_text) == ordinary
