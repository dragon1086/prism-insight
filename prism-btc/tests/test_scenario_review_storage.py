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

def test_recovery_wire_writer_preserves_text_and_other_events():
    import json
    from live import scenario_recovery
    conn = sqlite3.connect(':memory:')
    scenario_recovery.ensure_schema(conn)
    original = {'user_prompt': 'market-data-' * 200, 'input_id': 'input-1'}
    scenario_recovery.journal(conn, 'MODEL_REQUEST', original, 1)
    scenario_recovery.journal(conn, 'MODEL_RAW', {'raw': 'response-' * 200}, 2)
    scenario_recovery.journal(conn, 'OTHER', original, 3)
    rows = conn.execute('SELECT kind,body FROM llm_scenario_recovery_events ORDER BY rowid').fetchall()
    assert isinstance(rows[0][1], bytes) and rows[0][1].startswith(MAGIC)
    assert json.loads(review_text(rows[0][1])) == original
    assert isinstance(rows[1][1], bytes) and json.loads(review_text(rows[1][1]))['raw'] == 'response-' * 200
    assert isinstance(rows[2][1], str) and json.loads(rows[2][1]) == original
