import json
import sqlite3
from contextlib import contextmanager

import pytest

from observability.btc_scenario_export import _anchor_hash
from tools import btc_accounting_audit_compaction as compact
from utils.btc_accounting_audit_references import ORIGINAL_HASH, TAG, canonical, read_accounting, sha


IDENTITY = 'a' * 64
PROOF = 'b' * 64
NOW = 1_000_000
CUTOFF = NOW - 7 * 86400


def setup_database(path):
    conn = sqlite3.connect(path, isolation_level=None)
    conn.executescript('CREATE TABLE llm_scenario_broker_evidence(kind TEXT,captured_at REAL,body TEXT);'
                       'CREATE TABLE llm_scenario_audit_events(kind TEXT,observed_at REAL,body TEXT);')
    account, financial = {'wallet': 'a' * 1000}, {'trades': 'f' * 1000}
    conn.executemany('INSERT INTO llm_scenario_broker_evidence VALUES(?,?,?)',
                     [('account', 10, canonical(account)), ('financial', 10, canonical(financial))])
    original = canonical({'observation': account, 'financial_evidence': financial, 'children': [], 'result': 'x' * 2000})
    event = json.loads(original)
    event.update({TAG: 1, ORIGINAL_HASH: sha(original)})
    event['observation'] = {'rowid': 1, 'captured_at': 10, 'sha256': sha(canonical(account))}
    event['financial_evidence'] = {'rowid': 2, 'captured_at': 10, 'sha256': sha(canonical(financial))}
    stored = canonical(event)
    conn.executemany('INSERT INTO llm_scenario_audit_events VALUES(?,?,?)',
                     [('decision_input', 10, '{}'), ('accounting_observation', 10, stored),
                      ('accounting_observation', CUTOFF, stored), ('accounting_observation', 20, stored)])
    return conn, original


def manifest(conn, limit=1000):
    conn.execute('PRAGMA query_only=ON')
    conn.execute('BEGIN')
    try:
        data = compact.plan_old_audits(conn, IDENTITY, CUTOFF, limit=limit)
    finally:
        conn.rollback()
        conn.execute('PRAGMA query_only=OFF')
    return dict(data, backup_proof_sha256=PROOF)


def test_plan_is_bounded_and_compaction_preserves_exact_evidence_and_anchor(tmp_path):
    conn, original = setup_database(tmp_path/'db.sqlite')
    plan = manifest(conn)
    assert [p['rowid'] for p in plan['rows']] == [2, 4]
    assert plan['scanned_through'] == 4
    before = conn.execute('SELECT body FROM llm_scenario_audit_events WHERE rowid=2').fetchone()[0]
    anchor = _anchor_hash('llm_scenario_audit_events', {'kind': 'accounting_observation', 'body': before}, conn)
    result = compact.compact_attested_batch(conn, plan, IDENTITY, NOW, lambda: True)
    assert result['status'] == 'COMMITTED' and result['updated'] == 2 and result['saved_bytes'] > 0
    body = conn.execute('SELECT body FROM llm_scenario_audit_events WHERE rowid=2').fetchone()[0]
    assert isinstance(body, bytes) and read_accounting(body, conn) == original
    assert _anchor_hash('llm_scenario_audit_events', {'kind': 'accounting_observation', 'body': body}, conn) == anchor
    assert isinstance(conn.execute('SELECT body FROM llm_scenario_audit_events WHERE rowid=3').fetchone()[0], str)
    conn.close()


def test_pagination_does_not_skip_scan_limit(tmp_path):
    conn, _ = setup_database(tmp_path/'db.sqlite')
    first = manifest(conn, limit=2)
    assert first['scanned_through'] == 2 and [x['rowid'] for x in first['rows']] == [2]
    conn.execute('PRAGMA query_only=ON'); conn.execute('BEGIN')
    second = compact.plan_old_audits(conn, IDENTITY, CUTOFF, after_rowid=first['scanned_through'], limit=2)
    assert second['scanned_through'] == 4 and [x['rowid'] for x in second['rows']] == [4]
    conn.rollback(); conn.close()


@pytest.mark.parametrize('damage', ['audit', 'account', 'guard', 'compressed', 'trigger'])
def test_compaction_rejects_stale_proof_and_rolls_back(tmp_path, damage):
    conn, _ = setup_database(tmp_path/'db.sqlite')
    plan = manifest(conn)
    if damage == 'audit':
        conn.execute("UPDATE llm_scenario_audit_events SET body='{}' WHERE rowid=4")
    elif damage == 'account':
        conn.execute("UPDATE llm_scenario_broker_evidence SET body='{}' WHERE rowid=1")
    elif damage == 'compressed':
        conn.execute("UPDATE llm_scenario_audit_events SET body=x'01' WHERE rowid=4")
    elif damage == 'trigger':
        conn.execute('CREATE TRIGGER audit_trigger AFTER UPDATE ON llm_scenario_audit_events BEGIN SELECT 1; END')
    initial = conn.execute('SELECT body FROM llm_scenario_audit_events WHERE rowid=2').fetchone()[0]
    calls = []
    def guard():
        calls.append(1)
        return damage != 'guard' or len(calls) < 3
    with pytest.raises(ValueError):
        compact.compact_attested_batch(conn, plan, IDENTITY, NOW, guard)
    assert conn.execute('SELECT body FROM llm_scenario_audit_events WHERE rowid=2').fetchone()[0] == initial
    conn.close()


def test_forged_or_too_recent_attestation_is_rejected(tmp_path):
    conn, _ = setup_database(tmp_path/'db.sqlite')
    plan = manifest(conn)
    plan['cutoff'] += 1
    with pytest.raises(ValueError):
        compact.compact_attested_batch(conn, plan, IDENTITY, NOW, lambda: True)
    assert conn.execute('SELECT typeof(body) FROM llm_scenario_audit_events WHERE rowid=2').fetchone()[0] == 'text'
    conn.close()


def test_codec_failure_on_second_row_rolls_back_the_first(tmp_path, monkeypatch):
    conn, _ = setup_database(tmp_path/'db.sqlite')
    plan = manifest(conn)
    original = compact.review_storage
    calls = []
    def damaged(text):
        calls.append(1)
        return original(text) if len(calls) == 1 else b'PRISM_REVIEW_GZIP_V1\x00broken'
    monkeypatch.setattr(compact, 'review_storage', damaged)
    with pytest.raises(ValueError):
        compact.compact_attested_batch(conn, plan, IDENTITY, NOW, lambda: True)
    assert conn.execute('SELECT typeof(body) FROM llm_scenario_audit_events WHERE rowid=2').fetchone()[0] == 'text'
    conn.close()


def test_operational_guard_requires_seal_lock_and_safe_runtime(tmp_path, monkeypatch):
    from tools import btc_review_event_compaction as guards
    from utils import btc_review_retention as retention
    import live.shared_entry_coordinator as coordinator
    path = tmp_path/'db.sqlite'
    conn, _ = setup_database(path)
    conn.execute('PRAGMA busy_timeout=0')
    identity = guards.source_identity(path)
    conn.execute('PRAGMA query_only=ON'); conn.execute('BEGIN')
    plan = compact.plan_old_audits(conn, identity, CUTOFF)
    conn.rollback(); conn.execute('PRAGMA query_only=OFF')
    plan['backup_proof_sha256'] = PROOF
    held = []
    @contextmanager
    def lock(_):
        held.append(True)
        try:
            yield
        finally:
            held.pop()
    monkeypatch.setattr('time.time', lambda: NOW)
    monkeypatch.setattr(guards, 'SOURCE', path)
    monkeypatch.setattr(guards, 'resource_guard', lambda *args: bool(held))
    monkeypatch.setattr(coordinator, 'mutation_lock', lock)
    monkeypatch.setattr(retention, '_backup_idle', lambda *args, **kwargs: True)
    monkeypatch.setattr(retention, '_runtime_safe', lambda *args: False)
    seal = compact.manifest_sha256(plan)
    assert compact.guarded_operational_batch(conn, plan, seal, PROOF)['status'] == 'DEFERRED'
    assert conn.execute('SELECT typeof(body) FROM llm_scenario_audit_events WHERE rowid=2').fetchone()[0] == 'text'
    with pytest.raises(ValueError):
        compact.guarded_operational_batch(conn, plan, '0'*64, PROOF)
    monkeypatch.setattr(retention, '_runtime_safe', lambda *args: True)
    assert compact.guarded_operational_batch(conn, plan, seal, PROOF)['updated'] == 2
    conn.close()


def test_bounded_run_advances_empty_scans_and_stops_on_exact_receipt():
    calls = []
    def plan(_proof, cutoff, *, after_rowid):
        calls.append(after_rowid)
        rows = [{'rowid': 8}] if after_rowid == 5 else []
        end = {0: 5, 5: 10, 10: 10}[after_rowid]
        return {'source_identity': IDENTITY, 'backup_proof_sha256': PROOF, 'cutoff': cutoff,
                'after_rowid': after_rowid, 'scanned_through': end, 'rows': rows}
    def apply(manifest, seal, proof):
        assert proof == PROOF and seal == compact.manifest_sha256(manifest)
        return {'status': 'COMMITTED', 'manifest_sha256': seal, 'updated': 1, 'unchanged': 0, 'saved_bytes': 20}
    result = compact.run_bounded_batches('proof', CUTOFF, plan, apply, clock=lambda: 0)
    assert calls == [0, 5, 10]
    assert result == {'status': 'COMPLETE', 'after_rowid': 10, 'updated': 1, 'unchanged': 0, 'saved_bytes': 20, 'batches': 1}


def test_bounded_run_defers_without_advancing_or_retrying():
    calls = []
    def plan(_proof, cutoff, *, after_rowid):
        return {'source_identity': IDENTITY, 'backup_proof_sha256': PROOF, 'cutoff': cutoff,
                'after_rowid': after_rowid, 'scanned_through': 5, 'rows': [{'rowid': 4}]}
    def apply(manifest, seal, proof):
        calls.append(1)
        return {'status': 'DEFERRED', 'manifest_sha256': seal, 'updated': 0}
    result = compact.run_bounded_batches('proof', CUTOFF, plan, apply, clock=lambda: 0)
    assert result['status'] == 'DEFERRED' and result['after_rowid'] == 0 and len(calls) == 1


def test_transport_sends_only_sealed_manifest_and_rejects_wrong_receipt(monkeypatch):
    from types import SimpleNamespace
    from tools import btc_accounting_audit_transport as transport
    plan = {'backup_proof_sha256': PROOF, 'rows': [{'rowid': 1}]}
    seal = compact.manifest_sha256(plan)
    calls = []
    def runner(args, **kwargs):
        calls.append((args, kwargs))
        return SimpleNamespace(stdout=json.dumps({'status': 'DEFERRED', 'updated': 0, 'manifest_sha256': seal}).encode())
    monkeypatch.setattr(transport.subprocess, 'run', runner)
    assert transport.apply_remote(plan, seal, PROOF, commit='c'*40)['status'] == 'DEFERRED'
    assert len(calls) == 1 and calls[0][0][0] == '/usr/bin/ssh'
    with pytest.raises(ValueError):
        transport.apply_remote(plan, '0'*64, PROOF, commit='c'*40)
    assert len(calls) == 1
    monkeypatch.setattr(transport.subprocess, 'run', lambda *a, **k: SimpleNamespace(stdout=b'{}'))
    with pytest.raises(ValueError):
        transport.apply_remote(plan, seal, PROOF, commit='c'*40)


def test_verified_controller_checkpoints_a_bounded_run(tmp_path, monkeypatch):
    from tools import btc_account_compaction_transport as account_transport
    from tools import btc_accounting_audit_transport as audit_transport
    parent = tmp_path/'private'
    parent.mkdir(mode=0o700)
    monkeypatch.setattr('time.time', lambda: NOW)
    monkeypatch.setattr(account_transport, 'verified_merge', lambda number: 'c'*40)
    def plan(_proof, cutoff, *, after_rowid=0, limit=1000):
        if limit == 1:
            return {'source_identity': IDENTITY, 'backup_proof_sha256': PROOF}
        rows = [{'rowid': 4}] if after_rowid == 0 else []
        return {'source_identity': IDENTITY, 'backup_proof_sha256': PROOF, 'cutoff': cutoff,
                'after_rowid': after_rowid, 'scanned_through': 5, 'rows': rows}
    def apply(manifest, seal, proof, *, commit):
        assert commit == 'c'*40 and proof == PROOF
        return {'status': 'COMMITTED', 'manifest_sha256': seal, 'updated': 1, 'unchanged': 0, 'saved_bytes': 20}
    monkeypatch.setattr(compact, 'plan_attested_audits', plan)
    monkeypatch.setattr(audit_transport, 'apply_remote', apply)
    result = audit_transport.run_verified_controller(parent/'state.json', 'proof', CUTOFF, 1)
    assert result['status'] == 'COMPLETE' and result['after_rowid'] == 5 and result['updated_total'] == 1
    assert json.loads((parent/'state.json').read_text())['status'] == 'COMPLETE'
