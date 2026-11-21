"""Reader-first parity for replay, exporter and offsite archive."""
import json
import sqlite3

import pytest

from analysis.scenario_replay import TapeMismatch, _runtime_event_hash, _single_pass_audit_rows
from observability import btc_scenario_export as export
from utils.btc_offsite_archive import referenced_commits
from utils.btc_recovery_references import canonical, compact, pack


def fixture(path):
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.executescript('CREATE TABLE llm_scenario_review_events(slot INTEGER,sequence INTEGER,body TEXT,PRIMARY KEY(slot,sequence));'
                       'CREATE TABLE llm_scenario_recovery_events(event_id TEXT PRIMARY KEY,slot INTEGER,scenario_id TEXT,kind TEXT,observed_at REAL,body TEXT);')
    event = {'kind': 'MODEL_RAW', 'raw': 'fixture' * 1000, 'git_revision': 'a' * 40}
    review = json.dumps(event, sort_keys=True)
    recovery = canonical(dict(event, calculated_review_version=1, input_id='test'))
    candidate = compact(review, recovery, 7, 0)
    assert candidate != recovery
    conn.execute('INSERT INTO llm_scenario_review_events VALUES(?,?,?)', (7, 0, review))
    conn.execute('INSERT INTO llm_scenario_recovery_events VALUES(?,?,?,?,?,?)', ('id', 7, None, 'MODEL_RAW', 1., recovery))
    conn.commit()
    return conn, recovery, candidate


def test_replay_export_and_archive_preserve_original_evidence(tmp_path):
    path = tmp_path / 'source.sqlite'
    conn, raw, candidate = fixture(path)
    def row():
        return conn.execute('SELECT slot,kind,body FROM llm_scenario_recovery_events').fetchone()
    before_anchor = export._anchor_hash('llm_scenario_recovery_events', row(), conn)
    before_runtime = _runtime_event_hash(conn)
    before_archive = referenced_commits(path)
    with pytest.raises(TapeMismatch, match='multi_phase_review_requires_exact_review_tape'):
        _single_pass_audit_rows(conn, 7)
    conn.execute('UPDATE llm_scenario_recovery_events SET body=?', (pack(candidate),))
    conn.commit()
    assert export._anchor_hash('llm_scenario_recovery_events', row(), conn) == before_anchor
    assert _runtime_event_hash(conn) == before_runtime
    assert referenced_commits(path) == before_archive
    assert export._source_text('llm_scenario_recovery_events', row(), conn) == raw
    with pytest.raises(TapeMismatch, match='multi_phase_review_requires_exact_review_tape'):
        _single_pass_audit_rows(conn, 7)
    conn.execute('DELETE FROM llm_scenario_review_events')
    conn.commit()
    with pytest.raises(ValueError, match='source_missing'):
        _runtime_event_hash(conn)
    with pytest.raises(ValueError, match='reference_recovery_unresolved'):
        referenced_commits(path)
    conn.close()


def test_exporter_continues_across_reference_and_fails_closed_on_missing_source(tmp_path):
    path, state, spool = (tmp_path / name for name in ('source.sqlite', 'state.sqlite', 'spool.jsonl'))
    conn = sqlite3.connect(path)
    conn.executescript('CREATE TABLE llm_scenario_review_events(slot INTEGER,sequence INTEGER,body TEXT,PRIMARY KEY(slot,sequence));'
                       'CREATE TABLE llm_scenario_recovery_events(event_id TEXT PRIMARY KEY,slot INTEGER,scenario_id TEXT,kind TEXT,observed_at REAL,body TEXT);')
    conn.commit()
    assert export.export_once(path, state, spool)['status'] == 'OK'
    event = {'kind': 'MODEL_RAW', 'raw': 'fixture' * 1000}
    review = json.dumps(event, sort_keys=True)
    raw = canonical(dict(event, calculated_review_version=1, input_id='test'))
    candidate = compact(review, raw, 7, 0)
    assert candidate != raw
    conn.execute('INSERT INTO llm_scenario_review_events VALUES(?,?,?)', (7, 0, review))
    conn.execute('INSERT INTO llm_scenario_recovery_events VALUES(?,?,?,?,?,?)', ('id', 7, None, 'MODEL_RAW', 1700000000., raw))
    conn.commit()
    assert export.export_once(path, state, spool)['status'] == 'OK'
    source_events = [json.loads(line) for line in spool.read_text().splitlines()
                     if line.strip() and json.loads(line).get('attributes', {}).get('source_stream') == 'llm_scenario_recovery_events']
    assert len(source_events) == 1 and source_events[0]['attributes']['body_sha256'] == export._hash(raw)
    conn.execute('UPDATE llm_scenario_recovery_events SET body=?', (pack(candidate),))
    conn.commit()
    result = export.export_once(path, state, spool)
    assert result['status'] == 'OK' and result['captured'] == 0
    conn.execute('DELETE FROM llm_scenario_review_events')
    conn.commit()
    assert export.export_once(path, state, spool)['status'] == 'UNAVAILABLE'
    conn.close()
