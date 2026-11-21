import hashlib
import sqlite3

import pytest
from observability.btc_scenario_export import _anchor_hash
from tools import btc_audit_compaction as subject
from utils.btc_audit_references import canonical, read_audit, sha


def fixture(tmp_path):
    conn = sqlite3.connect(tmp_path/'source.sqlite', timeout=0)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA journal_mode=WAL')
    conn.executescript('CREATE TABLE llm_scenario_decisions(slot INTEGER PRIMARY KEY,snapshot TEXT,context TEXT);'
                       'CREATE TABLE llm_scenario_audit_events(kind TEXT,decision_slot INTEGER,body TEXT);')
    snapshot, context = {'history': 'a'*5000}, {'observations': 'b'*5000}
    raw = canonical({'snapshot': snapshot, 'context': context,
                     'snapshot_hash': sha(canonical(snapshot)), 'context_hash': sha(canonical(context))})
    conn.execute('INSERT INTO llm_scenario_decisions VALUES(?,?,?)', (7, canonical(snapshot), canonical(context)))
    conn.execute('INSERT INTO llm_scenario_audit_events VALUES(?,?,?)', ('decision_input', 7, raw))
    conn.commit()
    digest = hashlib.sha256(raw.encode()).hexdigest()
    return conn, raw, dict(rowid=1, slot=7, expected_sha256=digest, expected_raw_sha256=digest, guard=lambda:True)


def test_lossless_idempotent_and_anchor_preserved(tmp_path):
    conn, raw, args = fixture(tmp_path)
    def row():
        return conn.execute('SELECT rowid AS source_rowid,kind,decision_slot,body FROM llm_scenario_audit_events').fetchone()
    anchor = _anchor_hash('llm_scenario_audit_events', row(), conn)
    assert subject.compact_one(conn, **args) == 'UPDATED'
    assert row()['source_rowid'] == 1 and read_audit(row()['body'], conn, 7) == raw
    assert _anchor_hash('llm_scenario_audit_events', row(), conn) == anchor
    assert subject.compact_one(conn, **args) == 'UNCHANGED'


def test_backup_mismatch_and_guard_do_not_write(tmp_path):
    conn, raw, args = fixture(tmp_path)
    assert subject.compact_one(conn, **dict(args, expected_raw_sha256='0'*64)) == 'SOURCE_CHANGED'
    assert subject.compact_one(conn, **dict(args, guard=lambda:False)) == 'RESOURCE_LIMIT'
    assert conn.execute('SELECT body FROM llm_scenario_audit_events').fetchone()[0] == raw


def test_reference_change_during_prepare_does_not_write(tmp_path, monkeypatch):
    conn, raw, args = fixture(tmp_path)
    original = subject.compact
    def mutate(*values):
        candidate = original(*values)
        other = sqlite3.connect(tmp_path/'source.sqlite', timeout=0)
        other.execute("UPDATE llm_scenario_decisions SET context='{}' WHERE slot=7")
        other.commit();other.close()
        return candidate
    monkeypatch.setattr(subject, 'compact', mutate)
    assert subject.compact_one(conn, **args) == 'SOURCE_CHANGED'
    assert conn.execute('SELECT body FROM llm_scenario_audit_events').fetchone()[0] == raw


def test_batch_rejects_all_invalid_rows_before_writing(tmp_path):
    conn, _, args = fixture(tmp_path)
    row = {k:v for k,v in args.items() if k!='guard'}
    manifest = {'source_identity':'a'*64, 'rows':[row,dict(row,rowid=2,expected_sha256='bad')]}
    with pytest.raises(ValueError, match='invalid_manifest'):
        subject.compact_batch(conn, manifest, lambda:True)
    assert conn.total_changes == 2
    manifest['rows'] = [row]
    assert subject.compact_batch(conn, manifest, lambda:True)['counts']['UPDATED'] == 1
    assert subject.compact_batch(conn, manifest, lambda:True)['counts']['UNCHANGED'] == 1
