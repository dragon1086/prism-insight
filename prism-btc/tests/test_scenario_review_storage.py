import gzip
import sqlite3
import sys
from pathlib import Path

import pytest

from analysis.scenario_replay import _runtime_event_hash
from live.scenario_review_storage import MAGIC, MAX_BYTES, review_text

sys.path.append(str(Path(__file__).resolve().parents[2]))
from observability.review_storage import review_text as export_text


def test_legacy_and_compressed_have_same_replay_hash():
    conn = sqlite3.connect(':memory:')
    conn.execute('CREATE TABLE llm_scenario_review_events(slot,sequence,body)')
    conn.execute('CREATE TABLE llm_scenario_review_tapes(slot,body)')
    value = '{"kind":"MODEL_ERROR","reason":"hello"}'
    conn.execute('INSERT INTO llm_scenario_review_events VALUES(?,?,?)', (1, 0, value))
    conn.execute('INSERT INTO llm_scenario_review_tapes VALUES(?,?)', (1, value))
    original = _runtime_event_hash(conn)
    packed = MAGIC + gzip.compress(value.encode(), mtime=0)
    conn.execute('UPDATE llm_scenario_review_events SET body=?', (packed,))
    conn.execute('UPDATE llm_scenario_review_tapes SET body=?', (packed,))
    assert _runtime_event_hash(conn) == original
    assert review_text(packed) == export_text(packed) == value


@pytest.mark.parametrize('data', [MAGIC+b'bad', MAGIC+gzip.compress(b'{}')[:-1],
                                  MAGIC+gzip.compress(b'{}')+b'junk',
                                  MAGIC+gzip.compress(b'x'*(MAX_BYTES+1))])
def test_bad_compressed_evidence_never_becomes_empty(data):
    with pytest.raises(ValueError):
        review_text(data)
    with pytest.raises(ValueError):
        export_text(data)


def test_comparator_reads_compressed_event_without_changing_hash(tmp_path):
    import json
    from tools.compare_btc_scenario_replays import digest, verify_economic_rejections
    event = {'kind': 'CALCULATED_CANDIDATES', 'body': {'candidates': []}}
    text = json.dumps(event)
    with sqlite3.connect(tmp_path / 'replay.sqlite') as conn:
        conn.execute('CREATE TABLE llm_scenario_review_events(slot,sequence,body)')
        conn.execute('CREATE TABLE llm_scenario_decisions(slot,proposal,context)')
        conn.execute('CREATE TABLE llm_scenario_intents(payload)')
        conn.execute('CREATE TABLE llm_scenario_recovery_events(slot,kind)')
        conn.execute('INSERT INTO llm_scenario_review_events VALUES(?,?,?)',
                     (10, 0, MAGIC + gzip.compress(text.encode(), mtime=0)))
    report = {'contract': {'policy_profile': 'economic-reject-goal-v2'},
              'decision_outcomes': {}, 'economic_rejections': {
                  'policy': 'preauthority-economic-reject-v1', 'rejected_ticks': 0,
                  'rejected_candidates': 0, 'codes': {}, 'durable_events_sha256': digest([(10, event)])}}
    verify_economic_rejections(tmp_path, report)


def test_new_tape_encoding_round_trips_and_short_records_stay_plain():
    from live.scenario_review_storage import review_storage
    for original in ('{}', '{"large":"' + 'x' * 10000 + '"}'):
        encoded = review_storage(original)
        assert review_text(encoded) == export_text(encoded) == original
    assert isinstance(review_storage('{}'), str)
    assert isinstance(review_storage('{"large":"' + 'x' * 10000 + '"}'), bytes)
    with pytest.raises(ValueError):
        review_storage('x' * (MAX_BYTES + 1))

def test_compressed_recovery_wire_preserves_replay_and_fails_closed():
    from analysis.scenario_replay import _single_pass_audit_rows
    conn = sqlite3.connect(':memory:')
    conn.execute('CREATE TABLE llm_scenario_recovery_events(slot,kind,body)')
    original = '{"input_id":"fixture"}'
    conn.execute('INSERT INTO llm_scenario_recovery_events VALUES(?,?,?)', (1, 'MODEL_REQUEST', original))
    expected = _runtime_event_hash(conn)
    packed = MAGIC + gzip.compress(original.encode(), mtime=0)
    conn.execute('UPDATE llm_scenario_recovery_events SET body=?', (packed,))
    assert _runtime_event_hash(conn) == expected
    assert _single_pass_audit_rows(conn, 1) == ((original,), None)
    conn.execute('UPDATE llm_scenario_recovery_events SET body=?', (MAGIC+b'bad',))
    with pytest.raises(ValueError):
        _runtime_event_hash(conn)
    with pytest.raises(ValueError):
        _single_pass_audit_rows(conn, 1)


def test_referenced_tape_keeps_runtime_hash_and_fails_if_source_disappears():
    import json
    from utils.btc_review_references import canonical, compact, pack

    conn = sqlite3.connect(':memory:')
    conn.execute('CREATE TABLE llm_scenario_decisions(slot INTEGER,snapshot TEXT,context TEXT,proposal TEXT,outcome TEXT)')
    conn.execute('CREATE TABLE llm_scenario_review_events(slot INTEGER,sequence INTEGER,body TEXT)')
    conn.execute('CREATE TABLE llm_scenario_review_tapes(slot INTEGER,body TEXT)')
    snapshot, context = {'z': 1, 'a': 2}, {'input_id': 'fixture'}
    event = {'kind': 'MODEL_RAW', 'raw': 'fixture'}
    body = {'inputs': {'snapshot': snapshot, 'context': context}, 'operations': [{'kind': 'record', 'event': event}]}
    original = json.dumps(body)
    s, c, e = map(canonical, (snapshot, context, event))
    conn.execute('INSERT INTO llm_scenario_decisions VALUES(?,?,?,?,?)', (1, s, c, None, None))
    conn.execute('INSERT INTO llm_scenario_review_events VALUES(?,?,?)', (1, 0, e))
    conn.execute('INSERT INTO llm_scenario_review_tapes VALUES(?,?)', (1, original))
    expected = _runtime_event_hash(conn)
    conn.execute('UPDATE llm_scenario_review_tapes SET body=?', (pack(compact(original, s, c, [e])),))
    assert _runtime_event_hash(conn) == expected
    conn.execute('DELETE FROM llm_scenario_review_events')
    with pytest.raises(ValueError):
        _runtime_event_hash(conn)


def test_tape_writer_deduplicates_only_complete_recoverable_evidence():
    import hashlib
    import json
    from live.scenario_review_storage import review_storage, tape_storage
    from utils.btc_review_references import MAGIC as REF_MAGIC, canonical, read_tape

    conn = sqlite3.connect(':memory:')
    conn.execute('CREATE TABLE llm_scenario_decisions(slot INTEGER,snapshot TEXT,context TEXT)')
    conn.execute('CREATE TABLE llm_scenario_review_events(slot INTEGER,sequence INTEGER,body TEXT)')
    text = ''.join(hashlib.sha256(str(i).encode()).hexdigest() for i in range(2000))
    snapshot, context, event = {'z': text, 'a': 1}, {'z': 2, 'a': text}, {'raw': text}
    tape = {'inputs': {'snapshot': snapshot, 'context': context},
            'operations': [{'kind': 'record', 'event': event}], 'outcome': {'status': 'COMPLETE'}}
    raw = json.dumps(tape)
    conn.execute('INSERT INTO llm_scenario_decisions VALUES(?,?,?)',
                 (1, canonical(snapshot), canonical(context)))
    conn.execute('INSERT INTO llm_scenario_review_events VALUES(?,?,?)', (1, 0, canonical(event)))
    encoded = tape_storage(conn, 1, raw)
    assert encoded.startswith(REF_MAGIC) and len(encoded) < len(review_storage(raw))
    assert read_tape(encoded, conn, 1, decode_legacy=review_text) == raw
    conn.execute('DELETE FROM llm_scenario_review_events')
    assert review_text(tape_storage(conn, 1, raw)) == raw
    conn.execute('INSERT INTO llm_scenario_review_events VALUES(?,?,?)', (1, 1, canonical(event)))
    assert review_text(tape_storage(conn, 1, raw)) == raw
    tape['outcome']['status'] = 'FAILED'
    failed = json.dumps(tape)
    assert review_text(tape_storage(conn, 1, failed)) == failed
