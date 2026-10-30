import hashlib
import sqlite3

import pytest

from live.scenario_review_storage import review_text
from tools import btc_snapshot_compaction as subject


def setup_db(tmp_path):
    path = tmp_path / 'source.sqlite'
    conn = sqlite3.connect(path)
    conn.execute('CREATE TABLE llm_scenario_decisions(slot INTEGER PRIMARY KEY, snapshot TEXT, context TEXT)')
    original = '{"market":"한글"}' * 1000
    conn.execute('INSERT INTO llm_scenario_decisions VALUES(?,?,?)', (7, original, 'context'))
    conn.commit()
    proof = {'rowid':7, 'slot':7, 'expected_sha256':hashlib.sha256(original.encode()).hexdigest()}
    return conn, original, proof


def test_backed_snapshot_roundtrip_and_idempotence(tmp_path):
    conn, original, proof = setup_db(tmp_path)
    manifest = {'source_identity':'a'*64, 'rows':[proof]}
    assert subject.compact_batch(conn, manifest, lambda:True)['counts']['UPDATED'] == 1
    stored, context = conn.execute('SELECT snapshot,context FROM llm_scenario_decisions').fetchone()
    assert isinstance(stored, bytes) and len(stored) < len(original.encode())
    assert review_text(stored) == original and context == 'context'
    assert subject.compact_batch(conn, manifest, lambda:True)['counts']['UNCHANGED'] == 1


def test_mismatch_and_resource_limit_keep_original(tmp_path):
    conn, original, proof = setup_db(tmp_path)
    for bad in (dict(proof, slot=8), dict(proof, expected_sha256='0'*64)):
        assert subject.compact_one(conn, bad, lambda:True) == 'SOURCE_CHANGED'
    guard = iter([True, False])
    assert subject.compact_one(conn, proof, lambda:next(guard)) == 'RESOURCE_LIMIT'
    assert conn.execute('SELECT snapshot FROM llm_scenario_decisions').fetchone()[0] == original


def test_concurrent_change_never_overwritten(tmp_path, monkeypatch):
    conn, original, proof = setup_db(tmp_path)
    pack = subject.snapshot_storage
    def change(value):
        with sqlite3.connect(tmp_path / 'source.sqlite') as other:
            other.execute("UPDATE llm_scenario_decisions SET snapshot='changed'")
        return pack(value)
    monkeypatch.setattr(subject, 'snapshot_storage', change)
    assert subject.compact_one(conn, proof, lambda:True) == 'SOURCE_CHANGED'
    assert conn.execute('SELECT snapshot FROM llm_scenario_decisions').fetchone()[0] == 'changed'


def test_plan_requires_read_only_and_bounds(tmp_path):
    conn, original, proof = setup_db(tmp_path)
    with pytest.raises(ValueError, match='invalid_manifest'):
        subject.plan_backed_snapshots(conn, 'a'*64)
    conn.execute('PRAGMA query_only=ON')
    result = subject.plan_backed_snapshots(conn, 'a'*64)
    assert result['manifest']['rows'] == [proof]
    assert subject.plan_backed_snapshots(conn, 'a'*64, after_rowid=7)['manifest']['rows'] == []
    with pytest.raises(ValueError, match='invalid_manifest'):
        subject.plan_backed_snapshots(conn, 'a'*64, after_rowid=True)


def test_invalid_manifest_fails_before_changes(tmp_path):
    conn, original, proof = setup_db(tmp_path)
    with pytest.raises(ValueError, match='invalid_manifest'):
        subject.compact_batch(conn, {'source_identity':'a'*64, 'rows':[proof, proof]}, lambda:True)
    assert conn.execute('SELECT snapshot FROM llm_scenario_decisions').fetchone()[0] == original
