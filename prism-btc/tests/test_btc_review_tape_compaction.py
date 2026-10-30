import hashlib
import io
import json
import sqlite3
import sys
from types import SimpleNamespace
import pytest

from live.scenario_review_storage import review_storage, review_text
from observability.btc_scenario_export import _anchor_hash
from tools import btc_review_tape_compaction as subject
from utils.btc_review_references import canonical, read_tape


def fixture(tmp_path):
    conn = sqlite3.connect(tmp_path/'tape.sqlite', timeout=0)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA journal_mode=WAL')
    conn.executescript('CREATE TABLE llm_scenario_decisions(slot INTEGER PRIMARY KEY,snapshot TEXT,context TEXT);'
                       'CREATE TABLE llm_scenario_review_events(slot INTEGER,sequence INTEGER,body TEXT);'
                       'CREATE TABLE llm_scenario_review_tapes(slot INTEGER PRIMARY KEY,body BLOB);')
    text = ''.join(hashlib.sha256(str(i).encode()).hexdigest() for i in range(2000))
    snapshot, context, event = {'value': text}, {'value': text}, {'raw': text}
    raw = json.dumps({'inputs': {'snapshot': snapshot, 'context': context},
                      'operations': [{'kind': 'record', 'event': event}], 'outcome': {'status': 'COMPLETE'}})
    original = review_storage(raw)
    conn.execute('INSERT INTO llm_scenario_decisions VALUES(?,?,?)', (1, canonical(snapshot), canonical(context)))
    conn.execute('INSERT INTO llm_scenario_review_events VALUES(?,?,?)', (1, 0, canonical(event)))
    conn.execute('INSERT INTO llm_scenario_review_tapes VALUES(?,?)', (1, original))
    conn.commit()
    args = dict(slot=1, expected_type='blob', expected_sha256=hashlib.sha256(original).hexdigest(),
                expected_raw_sha256=hashlib.sha256(raw.encode()).hexdigest(), guard=lambda: True)
    return conn, raw, args


def test_backup_matched_tape_preserves_text_and_export_anchor(tmp_path):
    conn, raw, args = fixture(tmp_path)
    def row():
        return conn.execute('SELECT rowid AS source_rowid,slot,body FROM llm_scenario_review_tapes').fetchone()
    before = _anchor_hash('llm_scenario_review_tapes', row(), conn)
    assert subject.compact_one(conn, **args) == 'UPDATED'
    assert row()['source_rowid'] == 1
    assert read_tape(row()['body'], conn, 1, decode_legacy=review_text) == raw
    assert _anchor_hash('llm_scenario_review_tapes', row(), conn) == before
    assert subject.compact_one(conn, **args) == 'UNCHANGED'


def test_guard_and_backup_mismatch_do_not_write(tmp_path):
    conn, _, args = fixture(tmp_path)
    before = conn.execute('SELECT body FROM llm_scenario_review_tapes').fetchone()[0]
    assert subject.compact_one(conn, **dict(args, guard=lambda: False)) == 'RESOURCE_LIMIT'
    assert subject.compact_one(conn, **dict(args, expected_raw_sha256='0'*64)) == 'SOURCE_CHANGED'
    assert conn.execute('SELECT body FROM llm_scenario_review_tapes').fetchone()[0] == before


def test_main_stops_before_opening_database_when_backup_or_reader_is_active(tmp_path, monkeypatch, capsys):
    conn, _, args = fixture(tmp_path)
    before = conn.execute('SELECT body FROM llm_scenario_review_tapes').fetchone()[0]
    manifest = {'source_identity': 'a' * 64, 'rows': [{k: v for k, v in args.items() if k != 'guard'}]}
    monkeypatch.setenv('PRISM_BTC_STORAGE_APPROVED', '1')
    monkeypatch.setattr(subject, 'SOURCE', tmp_path/'tape.sqlite')
    monkeypatch.setattr(subject, 'source_identity', lambda path: 'a' * 64)
    monkeypatch.setattr(subject, 'resource_guard', lambda path, identity: True)
    monkeypatch.setattr(subject, '_backup_idle', lambda path: False)
    monkeypatch.setattr(sys, 'stdin', SimpleNamespace(buffer=io.BytesIO(json.dumps(manifest).encode())))
    subject.main()
    assert json.loads(capsys.readouterr().out)['status'] == 'RESOURCE_LIMIT'
    assert conn.execute('SELECT body FROM llm_scenario_review_tapes').fetchone()[0] == before


def test_failed_tape_compaction_preserves_original_and_export_anchor(tmp_path):
    from utils.btc_review_references import MAGIC
    conn, raw, args = fixture(tmp_path)
    value = json.loads(raw)
    value['outcome']['status'] = 'FAILED'
    failed = json.dumps(value)
    original = review_storage(failed)
    conn.execute('UPDATE llm_scenario_review_tapes SET body=? WHERE slot=1', (original,))
    conn.commit()
    row = lambda: conn.execute('SELECT rowid AS source_rowid,slot,body FROM llm_scenario_review_tapes').fetchone()
    anchor = _anchor_hash('llm_scenario_review_tapes', row(), conn)
    args.update(expected_sha256=hashlib.sha256(original).hexdigest(),
                expected_raw_sha256=hashlib.sha256(failed.encode()).hexdigest())
    assert subject.compact_one(conn, **args) == 'UPDATED'
    encoded = row()['body']
    assert encoded.startswith(MAGIC) and len(encoded) < len(original)
    assert read_tape(encoded, conn, 1, decode_legacy=review_text) == failed
    assert _anchor_hash('llm_scenario_review_tapes', row(), conn) == anchor


def test_failed_tape_without_source_remains_unchanged(tmp_path):
    conn, raw, args = fixture(tmp_path)
    value = json.loads(raw)
    value['outcome']['status'] = 'FAILED'
    failed = json.dumps(value)
    original = review_storage(failed)
    conn.execute('UPDATE llm_scenario_review_tapes SET body=? WHERE slot=1', (original,))
    conn.execute('DELETE FROM llm_scenario_review_events')
    conn.commit()
    args.update(expected_sha256=hashlib.sha256(original).hexdigest(),
                expected_raw_sha256=hashlib.sha256(failed.encode()).hexdigest())
    assert subject.compact_one(conn, **args) == 'UNCHANGED'
    assert conn.execute('SELECT body FROM llm_scenario_review_tapes').fetchone()[0] == original


def test_reference_change_before_write_stops(tmp_path, monkeypatch):
    conn, _, args = fixture(tmp_path)
    before = conn.execute('SELECT body FROM llm_scenario_review_tapes').fetchone()[0]
    original = subject.tape_storage
    def change_reference(connection, slot, raw, **kwargs):
        candidate = original(connection, slot, raw, **kwargs)
        other = sqlite3.connect(tmp_path/'tape.sqlite', timeout=0)
        other.execute('UPDATE llm_scenario_decisions SET context=? WHERE slot=1', ('{}',))
        other.commit()
        other.close()
        return candidate
    monkeypatch.setattr(subject, 'tape_storage', change_reference)
    assert subject.compact_one(conn, **args) == 'SOURCE_CHANGED'
    assert conn.execute('SELECT body FROM llm_scenario_review_tapes').fetchone()[0] == before


def test_batch_validates_entire_manifest_before_writes(tmp_path):
    conn, _, args = fixture(tmp_path)
    row = {k: v for k, v in args.items() if k != 'guard'}
    manifest = {'source_identity': 'a'*64, 'rows': [row, dict(row, slot=2, expected_sha256='bad')]}
    with pytest.raises(ValueError, match='invalid_manifest'):
        subject.compact_batch(conn, manifest, lambda: True)
    assert conn.total_changes == 3
    manifest['rows'] = [row]
    result = subject.compact_batch(conn, manifest, lambda: True)
    assert result['counts']['UPDATED'] == 1 and result['last_slot'] == 1
    assert subject.compact_batch(conn, manifest, lambda: True)['counts']['UNCHANGED'] == 1
