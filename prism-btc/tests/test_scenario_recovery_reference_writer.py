"""Recovery deduplication preserves raw evidence and tolerates missing sources."""
import hashlib
import json
import sqlite3

import pytest

from live import scenario_recovery as recovery
from live.scenario_review_storage import review_text
from utils import btc_recovery_references as refs


def fixture(tmp_path):
    conn = sqlite3.connect(tmp_path / 'source.sqlite', timeout=0)
    conn.execute('PRAGMA journal_mode=WAL')
    recovery.ensure_schema(conn)
    conn.executescript('CREATE TABLE llm_scenario_decisions(slot INTEGER PRIMARY KEY,context TEXT);'
                       'CREATE TABLE llm_scenario_review_events(slot INTEGER,sequence INTEGER,body TEXT);')
    payload = ''.join(hashlib.sha256(str(i).encode()).hexdigest() for i in range(2000))
    event = {'kind': 'MODEL_REQUEST', 'request': payload}
    conn.execute('INSERT INTO llm_scenario_decisions VALUES(?,?)', (7, json.dumps({'input_id': 'test'})))
    conn.execute('INSERT INTO llm_scenario_review_events VALUES(?,?,?)', (7, 3, json.dumps(event, sort_keys=True)))
    conn.commit()
    return conn, event


def test_input_reference_commits_with_decision_and_rolls_back_together(tmp_path):
    conn, _ = fixture(tmp_path)
    payload = {'input_id': 'test', 'decision_slot': 7, 'recovery': {'baseline': 'x' * 4000}}
    original = recovery.encoded(payload)
    event_id = recovery.journal(conn, 'INPUT', payload, 1, slot=7)
    conn.execute('UPDATE llm_scenario_decisions SET context=? WHERE slot=7', (json.dumps(payload),))
    assert recovery.compact_input_event(conn, event_id, 7)
    stored = conn.execute('SELECT body FROM llm_scenario_recovery_events WHERE event_id=?', (event_id,)).fetchone()[0]
    assert refs.read_recovery(stored, conn, 7, decode_legacy=review_text, expected_kind='INPUT') == original
    conn.rollback()
    assert conn.execute('SELECT 1 FROM llm_scenario_recovery_events WHERE event_id=?', (event_id,)).fetchone() is None
    assert json.loads(conn.execute('SELECT context FROM llm_scenario_decisions WHERE slot=7').fetchone()[0]) == {'input_id': 'test'}
    event_id = recovery.journal(conn, 'INPUT', payload, 1, slot=7)
    conn.execute('UPDATE llm_scenario_decisions SET context=? WHERE slot=7', (json.dumps(payload),))
    assert recovery.compact_input_event(conn, event_id, 7)
    conn.commit()
    stored = conn.execute('SELECT body FROM llm_scenario_recovery_events WHERE event_id=?', (event_id,)).fetchone()[0]
    assert refs.read_recovery(stored, conn, 7, decode_legacy=review_text, expected_kind='INPUT') == original
    conn.close()


def test_input_reference_falls_back_when_source_mismatch(tmp_path):
    conn, _ = fixture(tmp_path)
    payload = {'input_id': 'test', 'decision_slot': 7, 'recovery': {'baseline': 'x' * 4000}}
    event_id = recovery.journal(conn, 'INPUT', payload, 1, slot=7)
    assert not recovery.compact_input_event(conn, event_id, 7)
    assert conn.execute('SELECT body FROM llm_scenario_recovery_events WHERE event_id=?', (event_id,)).fetchone()[0] == recovery.encoded(payload)
    conn.commit()
    with pytest.raises(ValueError, match='transaction_required'):
        recovery.compact_input_event(conn, event_id, 7)
    conn.close()


def test_writer_stores_reference_that_restores_original(tmp_path):
    conn, event = fixture(tmp_path)
    recovery.journal(conn, 'MODEL_REQUEST', dict(event, calculated_review_version=1), 1, slot=7, review_sequence=3)
    conn.commit()
    stored = conn.execute('SELECT body FROM llm_scenario_recovery_events').fetchone()[0]
    expected = recovery.encoded(dict(event, calculated_review_version=1, input_id='test'))
    assert isinstance(stored, bytes) and stored.startswith(refs.MAGIC)
    assert refs.read_recovery(stored, conn, 7, decode_legacy=review_text, expected_kind='MODEL_REQUEST') == expected
    assert len(stored) < len(review_text(expected).encode())
    conn.close()


@pytest.mark.parametrize('fault', ['missing', 'mismatch', 'duplicate', 'invalid_sequence', 'codec_error', 'kind_mismatch'])
def test_writer_falls_back_to_original_for_optional_reference_failures(tmp_path, monkeypatch, fault):
    conn, event = fixture(tmp_path)
    if fault == 'missing':
        conn.execute('DELETE FROM llm_scenario_review_events')
    elif fault == 'mismatch':
        conn.execute('UPDATE llm_scenario_review_events SET body=?', ('{}',))
    elif fault == 'duplicate':
        conn.execute('INSERT INTO llm_scenario_review_events VALUES(?,?,?)', (7, 3, json.dumps(event, sort_keys=True)))
    elif fault == 'codec_error':
        def broken(*args):
            raise RuntimeError('fixture_codec_error')
        monkeypatch.setattr(refs, 'compact', broken)
    conn.commit()
    sequence = True if fault == 'invalid_sequence' else 3
    kind = 'MODEL_RAW' if fault == 'kind_mismatch' else 'MODEL_REQUEST'
    recovery.journal(conn, kind, dict(event, calculated_review_version=1), 1, slot=7, review_sequence=sequence)
    conn.commit()
    stored = conn.execute('SELECT body FROM llm_scenario_recovery_events').fetchone()[0]
    assert not (isinstance(stored, bytes) and stored.startswith(refs.MAGIC))
    assert review_text(stored) == recovery.encoded(dict(event, calculated_review_version=1, input_id='test'))
    conn.close()


def test_source_change_between_prepare_and_lock_preserves_original(tmp_path, monkeypatch):
    conn, event = fixture(tmp_path)
    original_compact = refs.compact

    def change_source(review_raw, raw, slot, sequence):
        candidate = original_compact(review_raw, raw, slot, sequence)
        other = sqlite3.connect(tmp_path / 'source.sqlite', timeout=0)
        other.execute('UPDATE llm_scenario_review_events SET body=? WHERE slot=7 AND sequence=3', ('{}',))
        other.commit()
        other.close()
        return candidate

    monkeypatch.setattr(refs, 'compact', change_source)
    recovery.journal(conn, 'MODEL_REQUEST', dict(event, calculated_review_version=1), 1, slot=7, review_sequence=3)
    conn.commit()
    stored = conn.execute('SELECT body FROM llm_scenario_recovery_events').fetchone()[0]
    assert not (isinstance(stored, bytes) and stored.startswith(refs.MAGIC))
    assert review_text(stored) == recovery.encoded(dict(event, calculated_review_version=1, input_id='test'))
    conn.close()


def test_reference_source_cannot_change_before_journal_commit(tmp_path):
    conn, event = fixture(tmp_path)
    recovery.journal(conn, 'MODEL_REQUEST', dict(event, calculated_review_version=1), 1, slot=7, review_sequence=3)
    assert conn.in_transaction
    other = sqlite3.connect(tmp_path / 'source.sqlite', timeout=0)
    with pytest.raises(sqlite3.OperationalError, match='locked'):
        other.execute('UPDATE llm_scenario_review_events SET body=? WHERE slot=7 AND sequence=3', ('{}',))
    conn.commit()
    other.execute('UPDATE llm_scenario_review_events SET body=? WHERE slot=7 AND sequence=3', ('{}',))
    other.rollback()
    other.close()
    conn.close()
