import hashlib
import json
import sqlite3

import pytest
from live.scenario_review_storage import review_storage, review_text
from observability.btc_scenario_export import _anchor_hash
from tools import btc_recovery_compaction as subject
from utils.btc_recovery_references import canonical, read_recovery


def fixture(tmp_path):
    conn = sqlite3.connect(tmp_path/'source.sqlite', timeout=0)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA journal_mode=WAL')
    conn.executescript('CREATE TABLE llm_scenario_review_events(slot INTEGER,sequence INTEGER,body TEXT);'
                       'CREATE TABLE llm_scenario_recovery_events(slot INTEGER,kind TEXT,body BLOB);')
    payload = ''.join(hashlib.sha256(str(i).encode()).hexdigest() for i in range(2000))
    event = {'kind': 'MODEL_REQUEST', 'request': payload}
    raw = canonical(dict(event, calculated_review_version=1, input_id='test'))
    body = review_storage(raw)
    conn.execute('INSERT INTO llm_scenario_review_events VALUES(?,?,?)', (7, 3, json.dumps(event, sort_keys=True)))
    conn.execute('INSERT INTO llm_scenario_recovery_events VALUES(?,?,?)', (7, 'MODEL_REQUEST', body))
    conn.commit()
    args = dict(rowid=1, slot=7, kind='MODEL_REQUEST', expected_type='blob',
                expected_sha256=hashlib.sha256(body).hexdigest(),
                expected_raw_sha256=hashlib.sha256(raw.encode()).hexdigest(), guard=lambda: True)
    return conn, raw, args


def test_lossless_and_idempotent_with_export_anchor(tmp_path):
    conn, raw, args = fixture(tmp_path)
    def row():
        return conn.execute('SELECT rowid AS source_rowid,slot,kind,body FROM llm_scenario_recovery_events').fetchone()
    anchor = _anchor_hash('llm_scenario_recovery_events', row(), conn)
    assert subject.compact_one(conn, **args) == 'UPDATED'
    assert row()['source_rowid'] == 1
    assert read_recovery(row()['body'], conn, 7, decode_legacy=review_text, expected_kind='MODEL_REQUEST') == raw
    assert _anchor_hash('llm_scenario_recovery_events', row(), conn) == anchor
    assert subject.compact_one(conn, **args) == 'UNCHANGED'


def test_ambiguous_missing_and_guard_do_not_write(tmp_path):
    conn, _, args = fixture(tmp_path)
    original = conn.execute('SELECT body FROM llm_scenario_recovery_events').fetchone()[0]
    assert subject.compact_one(conn, **dict(args, expected_raw_sha256='0'*64)) == 'SOURCE_CHANGED'
    assert subject.compact_one(conn, **dict(args, guard=lambda:False)) == 'RESOURCE_LIMIT'
    event = conn.execute('SELECT body FROM llm_scenario_review_events').fetchone()[0]
    conn.execute('INSERT INTO llm_scenario_review_events VALUES(?,?,?)', (7, 4, event))
    conn.commit()
    assert subject.compact_one(conn, **args) == 'AMBIGUOUS'
    assert conn.execute('SELECT body FROM llm_scenario_recovery_events').fetchone()[0] == original


def test_reference_change_during_prepare_does_not_write(tmp_path, monkeypatch):
    conn, _, args = fixture(tmp_path)
    before = conn.execute('SELECT body FROM llm_scenario_recovery_events').fetchone()[0]
    original = subject.pack
    def mutate(value):
        candidate = original(value)
        other = sqlite3.connect(tmp_path/'source.sqlite', timeout=0)
        other.execute("UPDATE llm_scenario_review_events SET body='{}' WHERE sequence=3")
        other.commit(); other.close()
        return candidate
    monkeypatch.setattr(subject, 'pack', mutate)
    assert subject.compact_one(conn, **args) == 'SOURCE_CHANGED'
    assert conn.execute('SELECT body FROM llm_scenario_recovery_events').fetchone()[0] == before


def test_entire_batch_prevalidated(tmp_path):
    conn, _, args = fixture(tmp_path)
    row = {k: v for k, v in args.items() if k != 'guard'}
    manifest = {'source_identity':'a'*64, 'rows':[row, dict(row, rowid=2, expected_sha256='bad')]}
    with pytest.raises(ValueError, match='invalid_manifest'):
        subject.compact_batch(conn, manifest, lambda:True)
    assert conn.total_changes == 2
    manifest['rows'] = [row]
    assert subject.compact_batch(conn, manifest, lambda:True)['counts']['UPDATED'] == 1
    assert subject.compact_batch(conn, manifest, lambda:True)['counts']['UNCHANGED'] == 1
