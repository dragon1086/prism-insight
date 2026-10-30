import hashlib
import json
import sqlite3
import pytest

from tools.btc_account_compaction import compact_batch
from observability.review_storage import MAGIC, review_text


def fixture():
    conn = sqlite3.connect(':memory:')
    conn.execute('CREATE TABLE llm_scenario_broker_evidence(kind TEXT,captured_at REAL,body)')
    values = [json.dumps({'detail': chr(65+i)*4000}) for i in range(3)]
    conn.executemany('INSERT INTO llm_scenario_broker_evidence VALUES(?,?,?)', [('account', i+1, v) for i,v in enumerate(values)])
    conn.commit()
    rows = [{'rowid': i+1, 'captured_at': i+1, 'sha256': hashlib.sha256(v.encode()).hexdigest()} for i,v in enumerate(values)]
    return conn, values, rows


def test_backup_matches_roundtrip_and_repeated_batch_is_idempotent():
    conn, values, rows = fixture()
    result = compact_batch(conn, rows, lambda: True)
    assert result['updated'] == 3 and result['saved_bytes'] > 0
    stored = [row[0] for row in conn.execute('SELECT body FROM llm_scenario_broker_evidence ORDER BY rowid')]
    assert all(isinstance(x,bytes) and x.startswith(MAGIC) for x in stored)
    assert [review_text(x) for x in stored] == values
    assert compact_batch(conn, rows, lambda: True)['unchanged'] == 3


def test_changed_backup_row_rolls_back_entire_batch():
    conn, values, rows = fixture()
    rows[2]['sha256'] = '0' * 64
    with pytest.raises(ValueError, match='source_changed'):
        compact_batch(conn, rows, lambda: True)
    assert [row[0] for row in conn.execute('SELECT body FROM llm_scenario_broker_evidence ORDER BY rowid')] == values


def test_guard_loss_rolls_back_and_trigger_is_rejected():
    conn, values, rows = fixture()
    calls = iter([True, True, False])
    with pytest.raises(ValueError, match='resource_guard'):
        compact_batch(conn, rows, lambda: next(calls))
    assert [row[0] for row in conn.execute('SELECT body FROM llm_scenario_broker_evidence ORDER BY rowid')] == values
    conn.execute('CREATE TRIGGER t AFTER UPDATE ON llm_scenario_broker_evidence BEGIN SELECT 1; END')
    with pytest.raises(ValueError, match='unsupported_trigger'):
        compact_batch(conn, rows, lambda: True)
    assert [row[0] for row in conn.execute('SELECT body FROM llm_scenario_broker_evidence ORDER BY rowid')] == values


def test_duplicate_and_changed_kind_are_rejected():
    conn, values, rows = fixture()
    with pytest.raises(ValueError, match='invalid_manifest'):
        compact_batch(conn, [rows[0],rows[0]], lambda: True)
    conn.execute("UPDATE llm_scenario_broker_evidence SET kind='financial' WHERE rowid=2")
    conn.commit()
    with pytest.raises(ValueError, match='source_changed'):
        compact_batch(conn, rows, lambda: True)
    assert [row[0] for row in conn.execute('SELECT body FROM llm_scenario_broker_evidence ORDER BY rowid')] == values


def test_backup_planner_requires_readonly_snapshot_and_strict_seven_day_cutoff():
    from tools.btc_account_compaction import plan_old_accounts
    conn, _, _ = fixture()
    conn.execute('CREATE INDEX llm_scenario_broker_evidence_kind ON llm_scenario_broker_evidence(kind)')
    conn.commit()
    with pytest.raises(ValueError, match='invalid_plan_context'):
        plan_old_accounts(conn, 'a' * 64, 3)
    conn.execute('PRAGMA query_only=ON')
    conn.execute('BEGIN')
    plan = plan_old_accounts(conn, 'a' * 64, 3)
    assert [r['rowid'] for r in plan['rows']] == [1, 2]
    assert [r['rowid'] for r in plan_old_accounts(conn, 'a' * 64, 3, after_rowid=1)['rows']] == [2]
    assert plan['source_identity'] == 'a' * 64
    assert all(set(r) == {'rowid', 'captured_at', 'sha256'} for r in plan['rows'])


def test_backup_planner_fails_closed_on_non_object_or_missing_index():
    from tools.btc_account_compaction import plan_old_accounts
    conn, _, _ = fixture()
    conn.execute('PRAGMA query_only=ON')
    conn.execute('BEGIN')
    with pytest.raises(ValueError, match='account_index_missing'):
        plan_old_accounts(conn, 'a' * 64, 3)
    conn.rollback()
    conn.execute('PRAGMA query_only=OFF')
    conn.execute('CREATE INDEX llm_scenario_broker_evidence_kind ON llm_scenario_broker_evidence(kind)')
    conn.execute("UPDATE llm_scenario_broker_evidence SET body='[]' WHERE rowid=1")
    conn.commit()
    conn.execute('PRAGMA query_only=ON')
    conn.execute('BEGIN')
    with pytest.raises(ValueError, match='invalid_backup_body'):
        plan_old_accounts(conn, 'a' * 64, 3)


def test_attested_batch_rejects_other_source_or_recent_rows_without_changes():
    from tools.btc_account_compaction import compact_attested_batch
    conn, values, rows = fixture()
    manifest = {'source_identity': 'a' * 64, 'cutoff': 3, 'after_rowid': 0,
                'backup_proof_sha256': 'b' * 64, 'rows': rows[:2]}
    for wrong_identity, now in [('c' * 64, 604803), ('a' * 64, 604802)]:
        with pytest.raises(ValueError, match='invalid_attested_manifest'):
            compact_attested_batch(conn, manifest, wrong_identity, now, lambda: True)
    recent = dict(manifest, rows=rows)
    with pytest.raises(ValueError, match='invalid_attested_row'):
        compact_attested_batch(conn, recent, 'a' * 64, 604803, lambda: True)
    assert [row[0] for row in conn.execute('SELECT body FROM llm_scenario_broker_evidence ORDER BY rowid')] == values
    result = compact_attested_batch(conn, manifest, 'a' * 64, 604803, lambda: True)
    assert result['updated'] == 2


def test_batch_preserves_clickstack_anchor_and_accounting_reference():
    from observability.btc_scenario_export import _anchor_hash
    from utils.btc_accounting_audit_references import canonical, compact, read_accounting
    conn, values, rows = fixture()
    account = json.loads(values[0])
    conn.execute("INSERT INTO llm_scenario_broker_evidence VALUES('financial', 1, '{}')")
    conn.commit()
    raw = canonical({'observation': account, 'financial_evidence': {}})
    reference = compact(raw, conn, 2)
    assert reference != raw and read_accounting(reference, conn) == raw
    before = _anchor_hash('llm_scenario_broker_evidence', {'kind': 'account', 'body': values[0]})
    compact_batch(conn, rows[:1], lambda: True)
    compressed = conn.execute('SELECT body FROM llm_scenario_broker_evidence WHERE rowid=1').fetchone()[0]
    assert _anchor_hash('llm_scenario_broker_evidence', {'kind': 'account', 'body': compressed}) == before
    assert read_accounting(reference, conn) == raw


def test_batch_continuation_requires_same_backup_cutoff_and_committed_progress():
    from tools.btc_account_compaction import manifest_sha256, verify_next_batch
    prior = {'source_identity': 'a'*64, 'backup_proof_sha256': 'b'*64, 'cutoff': 3,
             'after_rowid': 0, 'rows': [{'rowid': 1}, {'rowid': 2}]}
    next_batch = dict(prior, after_rowid=2, rows=[{'rowid': 3}])
    receipt = {'status': 'COMMITTED', 'updated': 1, 'unchanged': 1, 'manifest_sha256': manifest_sha256(prior)}
    assert verify_next_batch(prior, receipt, next_batch)
    for altered in (dict(next_batch, cutoff=4), dict(next_batch, source_identity='c'*64),
                    dict(next_batch, backup_proof_sha256='d'*64), dict(next_batch, after_rowid=1)):
        with pytest.raises(ValueError, match='batch_continuity_invalid'):
            verify_next_batch(prior, receipt, altered)
    with pytest.raises(ValueError, match='batch_continuity_invalid'):
        verify_next_batch(prior, dict(receipt, updated=0), next_batch)
    with pytest.raises(ValueError, match='batch_continuity_invalid'):
        verify_next_batch(prior, dict(receipt, manifest_sha256='0'*64), next_batch)


def test_file_batch_binds_manifest_to_actual_sqlite_inode(tmp_path):
    from tools.btc_account_compaction import compact_file_batch
    path = tmp_path / 'fixture.sqlite'
    conn = sqlite3.connect(path, timeout=0)
    conn.execute('CREATE TABLE llm_scenario_broker_evidence(kind TEXT,captured_at REAL,body)')
    raw = json.dumps({'detail': 'x' * 4000})
    conn.execute("INSERT INTO llm_scenario_broker_evidence VALUES('account',1,?)", (raw,))
    conn.commit()
    stat = path.stat()
    identity = hashlib.sha256(f'{path.resolve()}:{stat.st_dev}:{stat.st_ino}'.encode()).hexdigest()
    manifest = {'source_identity': identity, 'cutoff': 3, 'after_rowid': 0, 'backup_proof_sha256': 'b'*64,
                'rows': [{'rowid': 1, 'captured_at': 1, 'sha256': hashlib.sha256(raw.encode()).hexdigest()}]}
    wrong = dict(manifest, source_identity='f'*64)
    with pytest.raises(ValueError, match='invalid_attested_manifest'):
        compact_file_batch(conn, wrong, 604803, lambda: True)
    assert conn.execute('SELECT body FROM llm_scenario_broker_evidence').fetchone()[0] == raw
    assert compact_file_batch(conn, manifest, 604803, lambda: True)['updated'] == 1
    memory = sqlite3.connect(':memory:', timeout=0)
    with pytest.raises(ValueError, match='durable_database_required'):
        compact_file_batch(memory, manifest, 604803, lambda: True)


def test_file_batch_never_waits_or_partially_changes_a_busy_db(tmp_path):
    from tools.btc_account_compaction import compact_file_batch
    path = tmp_path / 'fixture.sqlite'
    conn = sqlite3.connect(path, timeout=0)
    conn.execute('CREATE TABLE llm_scenario_broker_evidence(kind TEXT,captured_at REAL,body)')
    raw = json.dumps({'detail': 'x' * 4000})
    conn.execute("INSERT INTO llm_scenario_broker_evidence VALUES('account',1,?)", (raw,))
    conn.commit()
    stat = path.stat()
    identity = hashlib.sha256(f'{path.resolve()}:{stat.st_dev}:{stat.st_ino}'.encode()).hexdigest()
    manifest = {'source_identity': identity, 'cutoff': 3, 'after_rowid': 0, 'backup_proof_sha256': 'b'*64,
                'rows': [{'rowid': 1, 'captured_at': 1, 'sha256': hashlib.sha256(raw.encode()).hexdigest()}]}
    other = sqlite3.connect(path, timeout=0)
    other.execute('BEGIN IMMEDIATE')
    with pytest.raises(sqlite3.OperationalError, match='locked'):
        compact_file_batch(conn, manifest, 604803, lambda: True)
    other.rollback()
    assert conn.execute('SELECT body FROM llm_scenario_broker_evidence').fetchone()[0] == raw
    conn.execute('PRAGMA busy_timeout=1')
    with pytest.raises(ValueError, match='nonblocking_connection_required'):
        compact_file_batch(conn, manifest, 604803, lambda: True)


def test_operational_wrapper_requires_lock_and_all_guards(tmp_path, monkeypatch):
    import time
    monkeypatch.setattr(time, "time", lambda: 1800000100.)
    import contextlib
    from pathlib import Path
    from tools.btc_account_compaction import guarded_operational_batch, manifest_sha256
    from tools import btc_review_event_compaction as guards
    from utils import btc_review_retention as retention
    import live.shared_entry_coordinator as coordinator
    path = tmp_path / 'fixture.sqlite'
    conn = sqlite3.connect(path, timeout=0)
    conn.execute('CREATE TABLE llm_scenario_broker_evidence(kind TEXT,captured_at REAL,body)')
    raw = json.dumps({'detail': 'x'*4000})
    conn.execute("INSERT INTO llm_scenario_broker_evidence VALUES('account',1,?)", (raw,))
    conn.commit()
    stat = path.stat()
    identity = hashlib.sha256(f'{path.resolve()}:{stat.st_dev}:{stat.st_ino}'.encode()).hexdigest()
    manifest = {'source_identity': identity, 'cutoff': 3, 'after_rowid': 0, 'backup_proof_sha256': 'b'*64,
                'rows': [{'rowid':1, 'captured_at':1, 'sha256':hashlib.sha256(raw.encode()).hexdigest()}]}
    held = []
    @contextlib.contextmanager
    def lock(_):
        held.append(True)
        try:
            yield
        finally:
            held.pop()
    monkeypatch.setattr(guards, 'SOURCE', Path(path))
    monkeypatch.setattr(coordinator, 'mutation_lock', lock)
    monkeypatch.setattr(guards, 'resource_guard', lambda *a: bool(held))
    monkeypatch.setattr(retention, '_backup_idle', lambda *a, **kw: True)
    monkeypatch.setattr(retention, '_runtime_safe', lambda *a: False)
    assert guarded_operational_batch(conn, manifest, manifest_sha256(manifest), 'b'*64)['status'] == 'DEFERRED'
    assert conn.execute('SELECT body FROM llm_scenario_broker_evidence').fetchone()[0] == raw
    monkeypatch.setattr(retention, '_runtime_safe', lambda *a: True)
    assert guarded_operational_batch(conn, manifest, manifest_sha256(manifest), 'b'*64)['updated'] == 1
    assert not held


def test_only_exact_b3_readonly_descriptor_can_be_allowed(tmp_path, monkeypatch):
    from tools import btc_account_compaction as module
    from utils.btc_review_retention import _backup_idle

    source = tmp_path / 'source.sqlite'
    source.write_bytes(b'fixture')
    proc = tmp_path / 'proc'
    process = proc / '99999999'
    (process / 'fd').mkdir(parents=True)
    (process / 'fdinfo').mkdir()
    fd = process / 'fd' / '4'
    fd.symlink_to(source)
    interpreter = tmp_path / 'python'
    interpreter.write_bytes(b'fixture')
    (process / 'exe').symlink_to(interpreter)
    monkeypatch.setattr(module, '_B3_LAUNCHER', '/fixture/python3')
    monkeypatch.setattr(module, '_B3_INTERPRETER', str(interpreter))
    monkeypatch.setattr(module, '_B3_SCRIPT', '/fixture/run_b3_ae_shadow.py')
    (process / 'cmdline').write_bytes(b'/fixture/python3\0/fixture/run_b3_ae_shadow.py\0--market\0us\0')
    (process / 'fdinfo' / '4').write_text('flags:\t0100000\n')
    assert not _backup_idle(source, proc)
    assert _backup_idle(source, proc, allow_reader=module._trusted_b3_readonly_fd)
    (process / 'fdinfo' / '4').write_text('flags:\t0100002\n')
    assert not _backup_idle(source, proc, allow_reader=module._trusted_b3_readonly_fd)
    (process / 'fdinfo' / '4').write_text('flags:\t0100000\n')
    (process / 'exe').unlink()
    assert not _backup_idle(source, proc, allow_reader=module._trusted_b3_readonly_fd)
    (process / 'exe').symlink_to(interpreter)
    (process / 'cmdline').write_bytes(b'/fixture/python3\0/fixture/other.py\0--market\0us\0')
    assert not _backup_idle(source, proc, allow_reader=module._trusted_b3_readonly_fd)
    (process / 'cmdline').write_bytes(b'/fixture/python3\0/fixture/run_b3_ae_shadow.py\0--market\0us\0')
    (process / 'fdinfo' / '4').unlink()
    assert not _backup_idle(source, proc, allow_reader=module._trusted_b3_readonly_fd)


def test_b3_reader_exception_requires_wal(tmp_path, monkeypatch):
    import contextlib
    from tools import btc_account_compaction as module, btc_review_event_compaction as guards
    from utils import btc_review_retention as retention
    import live.shared_entry_coordinator as coordinator

    path = tmp_path / 'fixture.sqlite'
    conn = sqlite3.connect(path, timeout=0)
    conn.execute('CREATE TABLE llm_scenario_broker_evidence(kind TEXT,captured_at REAL,body)')
    raw = json.dumps({'detail': 'x' * 4000})
    conn.execute("INSERT INTO llm_scenario_broker_evidence VALUES('account',1,?)", (raw,))
    conn.commit()
    stat = path.stat()
    identity = hashlib.sha256(f'{path.resolve()}:{stat.st_dev}:{stat.st_ino}'.encode()).hexdigest()
    manifest = {'source_identity': identity, 'cutoff': 3, 'after_rowid': 0, 'backup_proof_sha256': 'b' * 64,
                'rows': [{'rowid': 1, 'captured_at': 1, 'sha256': hashlib.sha256(raw.encode()).hexdigest()}]}
    monkeypatch.setattr(guards, 'SOURCE', path)
    monkeypatch.setattr(guards, 'resource_guard', lambda *a: True)
    monkeypatch.setattr(retention, '_backup_idle', lambda source, *, allow_reader: allow_reader(None, None))
    monkeypatch.setattr(retention, '_runtime_safe', lambda *a: True)
    monkeypatch.setattr(module, '_trusted_b3_readonly_fd', lambda *a: True)
    monkeypatch.setattr(coordinator, 'mutation_lock', lambda _: contextlib.nullcontext())
    monkeypatch.setattr('time.time', lambda: 1800000100.)
    assert module.guarded_operational_batch(conn, manifest, module.manifest_sha256(manifest), 'b' * 64)['status'] == 'DEFERRED'
    assert conn.execute('PRAGMA journal_mode=WAL').fetchone()[0].lower() == 'wal'
    assert module.guarded_operational_batch(conn, manifest, module.manifest_sha256(manifest), 'b' * 64)['updated'] == 1


def test_wal_reader_keeps_snapshot_during_compaction(tmp_path):
    from tools.btc_account_compaction import compact_file_batch
    path = tmp_path / 'fixture.sqlite'
    writer = sqlite3.connect(path, timeout=0)
    assert writer.execute('PRAGMA journal_mode=WAL').fetchone()[0].lower() == 'wal'
    writer.execute('CREATE TABLE llm_scenario_broker_evidence(kind TEXT,captured_at REAL,body)')
    raw = json.dumps({'sample': 'x' * 4000})
    writer.execute("INSERT INTO llm_scenario_broker_evidence VALUES('account',1,?)", (raw,))
    writer.commit()
    reader = sqlite3.connect(path.as_uri() + '?mode=ro', uri=True, timeout=0)
    reader.execute('BEGIN')
    assert reader.execute('SELECT body FROM llm_scenario_broker_evidence').fetchone()[0] == raw
    stat = path.stat()
    identity = hashlib.sha256(f'{path.resolve()}:{stat.st_dev}:{stat.st_ino}'.encode()).hexdigest()
    manifest = {'source_identity': identity, 'cutoff': 3, 'after_rowid': 0, 'backup_proof_sha256': 'b' * 64,
                'rows': [{'rowid': 1, 'captured_at': 1, 'sha256': hashlib.sha256(raw.encode()).hexdigest()}]}
    assert compact_file_batch(writer, manifest, 604803, lambda: True)['updated'] == 1
    assert reader.execute('SELECT body FROM llm_scenario_broker_evidence').fetchone()[0] == raw
    reader.rollback()
    current = reader.execute('SELECT body FROM llm_scenario_broker_evidence').fetchone()[0]
    assert isinstance(current, bytes) and review_text(current) == raw
    reader.close()
    writer.close()


def test_wal_batch_rolls_back_all_rows_when_guard_fails(tmp_path):
    path = tmp_path / 'wal-fixture.sqlite'
    conn = sqlite3.connect(path, timeout=0)
    assert conn.execute('PRAGMA journal_mode=WAL').fetchone()[0].lower() == 'wal'
    conn.execute('CREATE TABLE llm_scenario_broker_evidence(kind TEXT,captured_at REAL,body)')
    values = [json.dumps({'detail': str(i)*4000}) for i in range(30)]
    conn.executemany('INSERT INTO llm_scenario_broker_evidence VALUES(?,?,?)', [('account', i+1, body) for i,body in enumerate(values)])
    conn.commit()
    rows = [{'rowid':i+1, 'captured_at':i+1, 'sha256':hashlib.sha256(body.encode()).hexdigest()} for i,body in enumerate(values)]
    calls = iter([True, True, False])
    with pytest.raises(ValueError, match='resource_guard'):
        compact_batch(conn, rows, lambda: next(calls))
    assert [row[0] for row in conn.execute('SELECT body FROM llm_scenario_broker_evidence ORDER BY rowid')] == values
    conn.close()
    with sqlite3.connect(path) as verify:
        assert [row[0] for row in verify.execute('SELECT body FROM llm_scenario_broker_evidence ORDER BY rowid')] == values


def test_attested_account_planning_rejects_changed_backup(tmp_path):
    from tools.btc_account_compaction import plan_attested_accounts
    from tools.btc_review_backup_manifest import create_backup_attestation
    directory = tmp_path / 'archive'
    directory.mkdir(mode=0o700)
    archive, restored = directory/'backup.typed.gz', directory/'restored.sqlite'
    archive.write_bytes(b'fixture'); archive.chmod(0o600)
    with sqlite3.connect(restored) as conn:
        conn.execute('CREATE TABLE llm_scenario_broker_evidence(kind TEXT,captured_at REAL,body)')
        conn.execute('CREATE INDEX llm_scenario_broker_evidence_kind ON llm_scenario_broker_evidence(kind)')
        conn.execute("INSERT INTO llm_scenario_broker_evidence VALUES('account',1,'{}')")
    restored.chmod(0o600)
    digest = lambda path: hashlib.sha256(path.read_bytes()).hexdigest()
    detail = {'source_identity':'a'*64, 'archive_bytes':archive.stat().st_size,
              'archive_sha256':digest(archive), 'restored_bytes':restored.stat().st_size,
              'restored_sha256':digest(restored)}
    manifest = directory/'verified-manifest.json'
    manifest.write_text(json.dumps(detail)); manifest.chmod(0o600)
    receipt = tmp_path/'archive.receipt.json'
    receipt.write_text(json.dumps({'status':'VERIFIED','directory':str(directory),
                                   'source_identity':'a'*64,'manifest_sha256':digest(manifest)})); receipt.chmod(0o600)
    proof = tmp_path/'proof.json'
    create_backup_attestation(directory, proof)
    planned = plan_attested_accounts(proof, 2)
    assert len(planned['rows']) == 1 and planned['source_identity'] == 'a'*64
    assert planned['backup_proof_sha256'] == digest(proof)
    archive.write_bytes(b'changed')
    with pytest.raises(ValueError, match='backup_artifact_changed'):
        plan_attested_accounts(proof, 2)


def test_guarded_batch_rejects_unsealed_manifest(tmp_path, monkeypatch):
    from tools.btc_account_compaction import guarded_operational_batch
    path = tmp_path/'fixture.sqlite'
    conn = sqlite3.connect(path, timeout=0)
    with pytest.raises(ValueError, match='backup_binding_invalid'):
        guarded_operational_batch(conn, {'backup_proof_sha256':'a'*64}, 'b'*64, 'a'*64)


def test_interruption_after_commit_retries_without_changing_source(tmp_path):
    import os
    import subprocess
    import sys
    from pathlib import Path
    from tools.btc_account_compaction import compact_file_batch
    path = tmp_path / 'crash-fixture.sqlite'
    conn = sqlite3.connect(path, timeout=0)
    conn.execute('CREATE TABLE llm_scenario_broker_evidence(kind TEXT,captured_at REAL,body)')
    raw = json.dumps({'detail': 'x'*4000})
    conn.execute("INSERT INTO llm_scenario_broker_evidence VALUES('account',1,?)", (raw,))
    conn.commit()
    stat = path.stat()
    identity = hashlib.sha256(f'{path.resolve()}:{stat.st_dev}:{stat.st_ino}'.encode()).hexdigest()
    manifest = {'source_identity':identity, 'cutoff':3, 'after_rowid':0, 'backup_proof_sha256':'b'*64,
                'rows':[{'rowid':1, 'captured_at':1, 'sha256':hashlib.sha256(raw.encode()).hexdigest()}]}
    conn.close()
    root = Path(__file__).resolve().parents[2]
    script = ("import json,os,sqlite3,sys; "
              "sys.path[:0]=[sys.argv[1],sys.argv[1]+'/prism-btc']; "
              "from tools.btc_account_compaction import compact_file_batch; "
              "c=sqlite3.connect(sys.argv[2],timeout=0); "
              "r=compact_file_batch(c,json.loads(sys.argv[3]),604803,lambda:True); "
              "assert r['updated']==1; os._exit(23)")
    env = dict(os.environ, PRISM_DISABLE_SIGNAL_PUBLISH='1')
    child = subprocess.run([sys.executable, '-c', script, str(root), str(path), json.dumps(manifest)],
                           env=env, capture_output=True, timeout=10)
    assert child.returncode == 23, child.stderr.decode(errors='replace')[-400:]
    with sqlite3.connect(path, timeout=0) as recovered:
        result = compact_file_batch(recovered, manifest, 604803, lambda:True)
        assert result['updated'] == 0 and result['unchanged'] == 1
        assert review_text(recovered.execute('SELECT body FROM llm_scenario_broker_evidence').fetchone()[0]) == raw


def test_bounded_batch_runner_stops_on_defer_and_preserves_completed_cursor():
    from tools.btc_account_compaction import run_bounded_batches
    calls = []
    def planner(proof, cutoff, *, after_rowid):
        return {'source_identity':'a'*64, 'backup_proof_sha256':'b'*64, 'cutoff':cutoff,
                'after_rowid':after_rowid, 'rows':[{'rowid':after_rowid+1}]}
    def apply(manifest, seal, proof_sha):
        calls.append(manifest['after_rowid'])
        if len(calls) == 2:
            return {'status':'DEFERRED','manifest_sha256':seal}
        return {'status':'COMMITTED','manifest_sha256':seal,'updated':1,'unchanged':0,'saved_bytes':10}
    result = run_bounded_batches('proof', 3, planner, apply)
    assert result == {'status':'DEFERRED','after_rowid':1,'updated':1,'unchanged':0,'saved_bytes':10,'batches':1}
    assert calls == [0,1]


def test_bounded_batch_runner_rejects_changed_proof_and_unbound_receipt():
    from tools.btc_account_compaction import run_bounded_batches
    def planner(proof, cutoff, *, after_rowid):
        return {'source_identity':'a'*64, 'backup_proof_sha256':('b' if not after_rowid else 'c')*64,
                'cutoff':cutoff,'after_rowid':after_rowid,'rows':[{'rowid':after_rowid+1}]}
    def apply(manifest, seal, proof_sha):
        return {'status':'COMMITTED','manifest_sha256':seal,'updated':1,'unchanged':0,'saved_bytes':10}
    result = run_bounded_batches('proof', 3, planner, apply)
    assert result['status'] == 'ERROR' and result['after_rowid'] == 1 and result['updated'] == 1
    def bad_apply(manifest, seal, proof_sha):
        return {'status':'COMMITTED','manifest_sha256':'0'*64,'updated':1,'unchanged':0,'saved_bytes':10}
    result = run_bounded_batches('proof', 3, planner, bad_apply)
    assert result['status'] == 'ERROR' and result['after_rowid'] == 0


def test_bounded_batch_runner_does_not_treat_unverified_empty_plan_as_complete():
    from tools.btc_account_compaction import run_bounded_batches
    def bad_planner(proof, cutoff, *, after_rowid):
        return {'cutoff':cutoff, 'after_rowid':after_rowid, 'rows':[]}
    result = run_bounded_batches('proof', 3, bad_planner, lambda *args: None)
    assert result['status'] == 'ERROR' and result['after_rowid'] == 0


def test_bounded_batch_runner_respects_batch_cap_without_waiting():
    from tools.btc_account_compaction import run_bounded_batches
    calls = []
    def planner(proof, cutoff, *, after_rowid):
        return {'source_identity':'a'*64, 'backup_proof_sha256':'b'*64, 'cutoff':cutoff,
                'after_rowid':after_rowid, 'rows':[{'rowid':after_rowid+1}]}
    def apply(manifest, seal, proof_sha):
        calls.append(1)
        return {'status':'COMMITTED','manifest_sha256':seal,'updated':1,'unchanged':0,'saved_bytes':10}
    result = run_bounded_batches('proof', 3, planner, apply, max_batches=1)
    assert result['status'] == 'LIMIT_REACHED' and result['after_rowid'] == 1 and calls == [1]


def test_apply_cli_uses_sealed_manifest_and_refuses_unexplicit_invocation(tmp_path, monkeypatch, capsys):
    import time
    monkeypatch.setattr(time, "time", lambda: 1800000100.)
    import contextlib
    import io
    import sys
    from tools import btc_account_compaction as module
    from tools import btc_review_event_compaction as guards
    from utils import btc_review_retention as retention
    import live.shared_entry_coordinator as coordinator
    path = tmp_path/'fixture.sqlite'
    raw = json.dumps({'detail':'x'*4000})
    with sqlite3.connect(path) as conn:
        conn.execute('CREATE TABLE llm_scenario_broker_evidence(kind TEXT,captured_at REAL,body)')
        conn.execute("INSERT INTO llm_scenario_broker_evidence VALUES('account',1,?)", (raw,))
    stat = path.stat()
    identity = hashlib.sha256(f'{path.resolve()}:{stat.st_dev}:{stat.st_ino}'.encode()).hexdigest()
    manifest = {'source_identity':identity, 'cutoff':3, 'after_rowid':0, 'backup_proof_sha256':'b'*64,
                'rows':[{'rowid':1, 'captured_at':1, 'sha256':hashlib.sha256(raw.encode()).hexdigest()}]}
    monkeypatch.setattr(guards, 'SOURCE', path)
    monkeypatch.setattr(guards, 'resource_guard', lambda *a: True)
    monkeypatch.setattr(retention, '_backup_idle', lambda *a, **kw: True)
    monkeypatch.setattr(retention, '_runtime_safe', lambda *a: True)
    monkeypatch.setattr(coordinator, 'mutation_lock', lambda _: contextlib.nullcontext())
    monkeypatch.setattr(sys, 'stdin', type('Input', (), {'buffer': io.BytesIO(json.dumps(manifest).encode())})())
    monkeypatch.setattr(sys, 'argv', ['tool'])
    with pytest.raises(ValueError, match='explicit_apply_required'):
        module.main()
    monkeypatch.setattr(sys, 'argv', ['tool', '--apply', module.manifest_sha256(manifest), 'b'*64])
    module.main()
    result = json.loads(capsys.readouterr().out)
    assert result['status'] == 'COMMITTED' and result['updated'] == 1
    with sqlite3.connect(path) as conn:
        assert review_text(conn.execute('SELECT body FROM llm_scenario_broker_evidence').fetchone()[0]) == raw


def test_manifest_transport_bounds_and_validates_ack(monkeypatch):
    from types import SimpleNamespace
    from tools import btc_account_compaction_transport as transport
    from tools.btc_account_compaction import manifest_sha256
    seen = []
    proof, commit = 'b'*64, 'c'*40
    manifest = {'source_identity':'d'*64,'cutoff':3,'after_rowid':0,'backup_proof_sha256':proof,
                'rows':[{'rowid':1,'captured_at':1,'sha256':'e'*64}]}
    seal = manifest_sha256(manifest)
    def run(args, **kwargs):
        seen.append((args, kwargs))
        return SimpleNamespace(stdout=json.dumps({'status':'DEFERRED','manifest_sha256':seal}).encode())
    monkeypatch.setattr(transport.subprocess, 'run', run)
    assert transport.apply_remote(manifest, seal, proof, commit=commit)['status'] == 'DEFERRED'
    assert json.loads(seen[0][1]['input']) == manifest and seen[0][1]['timeout'] == 25
    assert 'prism-db' in seen[0][0]
    with pytest.raises(ValueError, match='transport_identity_invalid'):
        transport.apply_remote(manifest, seal, proof, commit='$(whoami)')
    with pytest.raises(ValueError, match='transport_manifest_invalid'):
        transport.apply_remote(dict(manifest, raw_account='secret'), seal, proof, commit=commit)
    with pytest.raises(ValueError, match='transport_seal_mismatch'):
        transport.apply_remote(manifest, '0'*64, proof, commit=commit)
    monkeypatch.setattr(transport.subprocess, 'run', lambda *a, **k: SimpleNamespace(stdout=b'{"manifest_sha256":"wrong"}'))
    with pytest.raises(ValueError, match='transport_response_invalid'):
        transport.apply_remote(manifest, seal, proof, commit=commit)


def test_real_runtime_guard_blocks_active_and_unresolved_intent(tmp_path, monkeypatch):
    import time
    monkeypatch.setattr(time, "time", lambda: 1800000100.)
    from tools import btc_review_event_compaction as guards
    from tools.btc_account_compaction import guarded_operational_batch, manifest_sha256
    from utils import btc_review_retention as retention
    path = tmp_path/'fixture.sqlite'
    conn = sqlite3.connect(path, timeout=0)
    conn.executescript('CREATE TABLE llm_scenario_broker_evidence(kind TEXT,captured_at REAL,body);'
                       'CREATE TABLE llm_scenario_state(id INTEGER,body TEXT);'
                       'CREATE TABLE llm_scenario_intents(id TEXT,status TEXT);')
    raw = json.dumps({'detail':'x'*4000})
    conn.execute("INSERT INTO llm_scenario_broker_evidence VALUES('account',1,?)", (raw,))
    conn.execute('INSERT INTO llm_scenario_state VALUES(1,?)', (json.dumps({'active': {'id':'open'}}),))
    conn.commit()
    st = path.stat()
    identity = hashlib.sha256(f'{path.resolve()}:{st.st_dev}:{st.st_ino}'.encode()).hexdigest()
    manifest = {'source_identity':identity, 'cutoff':3, 'after_rowid':0, 'backup_proof_sha256':'b'*64,
                'rows':[{'rowid':1, 'captured_at':1, 'sha256':hashlib.sha256(raw.encode()).hexdigest()}]}
    monkeypatch.setattr(guards, 'SOURCE', path)
    monkeypatch.setattr(guards, 'resource_guard', lambda *a: True)
    monkeypatch.setattr(retention, '_backup_idle', lambda *a, **kw: True)
    def apply(): return guarded_operational_batch(conn, manifest, manifest_sha256(manifest), 'b'*64)
    assert apply()['status'] == 'DEFERRED'
    conn.execute('UPDATE llm_scenario_state SET body=? WHERE id=1', (json.dumps({'active':None}),))
    conn.execute("INSERT INTO llm_scenario_intents VALUES('pending','UNKNOWN')")
    conn.commit()
    assert apply()['status'] == 'DEFERRED'
    assert conn.execute('SELECT body FROM llm_scenario_broker_evidence').fetchone()[0] == raw
    conn.execute("UPDATE llm_scenario_intents SET status='TERMINAL'")
    conn.commit()
    assert apply()['updated'] == 1


def test_bounded_runner_recognizes_valid_end_and_reports_transport_uncertainty():
    import subprocess
    from tools.btc_account_compaction import run_bounded_batches
    def planner(proof, cutoff, *, after_rowid):
        return {'source_identity':'a'*64, 'backup_proof_sha256':'b'*64, 'cutoff':cutoff,
                'after_rowid':after_rowid, 'rows':[] if after_rowid else [{'rowid':1}]}
    def apply(manifest, seal, proof_sha):
        return {'status':'COMMITTED','manifest_sha256':seal,'updated':1,'unchanged':0,'saved_bytes':10}
    result = run_bounded_batches('proof', 3, planner, apply)
    assert result['status'] == 'COMPLETE' and result['after_rowid'] == 1 and result['batches'] == 1
    def timed_out(*args):
        raise subprocess.TimeoutExpired('ssh', 25)
    result = run_bounded_batches('proof', 3, planner, timed_out)
    assert result['status'] == 'ERROR' and result['error_type'] == 'TimeoutExpired' and result['after_rowid'] == 0


def test_bounded_runner_stops_before_next_batch_when_deadline_expires(monkeypatch):
    import time
    from tools.btc_account_compaction import run_bounded_batches
    readings = iter([100.,101.,121.])
    monkeypatch.setattr(time, 'monotonic', lambda: next(readings))
    def planner(proof, cutoff, *, after_rowid):
        return {'source_identity':'a'*64, 'backup_proof_sha256':'b'*64, 'cutoff':cutoff,
                'after_rowid':after_rowid, 'rows':[{'rowid':after_rowid+1}]}
    def apply(manifest, seal, proof_sha):
        return {'status':'COMMITTED','manifest_sha256':seal,'updated':1,'unchanged':0,'saved_bytes':10}
    result = run_bounded_batches('proof', 3, planner, apply)
    assert result['status'] == 'DEADLINE_REACHED' and result['after_rowid'] == 1


def test_merged_pr_guard_requires_exact_head_and_all_checks(monkeypatch):
    from types import SimpleNamespace
    from tools import btc_account_compaction_transport as transport
    names = ['Third-party license compliance','CLA','report-data','Dashboard production build',
             'Test (Python 3.10)','Test (Python 3.11)','Test (Python 3.12)','Codacy Static Code Analysis']
    pr = {'state':'MERGED','headRefOid':'a'*40,'mergeCommit':{'oid':'b'*40},
          'statusCheckRollup':[{'name':name,'status':'COMPLETED','conclusion':'SUCCESS'} for name in names]}
    monkeypatch.setattr(transport.shutil, 'which', lambda _: '/usr/bin/gh')
    monkeypatch.setattr(transport.subprocess, 'run', lambda *a, **k: SimpleNamespace(stdout=json.dumps(pr)))
    monkeypatch.setattr(transport.subprocess, 'check_output', lambda args, **k: 'a'*40+'\n' if args[1]=='rev-parse' else '')
    assert transport.verified_merge(1) == 'b'*40
    pr['statusCheckRollup'][0]['conclusion'] = 'FAILURE'
    with pytest.raises(ValueError, match='pr_verification_failed'):
        transport.verified_merge(1)
    pr['statusCheckRollup'][0]['conclusion'] = 'SUCCESS'
    pr['headRefOid'] = 'c'*40
    with pytest.raises(ValueError, match='pr_verification_failed'):
        transport.verified_merge(1)


def test_resuming_a_range_cannot_claim_full_history_completed(monkeypatch):
    from tools import btc_account_compaction as compaction
    from tools import btc_account_compaction_transport as transport
    monkeypatch.setattr(transport, 'verified_merge', lambda _: 'a'*40)
    def empty_plan(proof, cutoff, *, after_rowid):
        return {'source_identity':'a'*64,'backup_proof_sha256':'b'*64,'cutoff':cutoff,
                'after_rowid':after_rowid,'rows':[]}
    monkeypatch.setattr(compaction, 'plan_attested_accounts', empty_plan)
    assert transport.run_verified_remote_batches('proof', 3, 1, after_rowid=100)['status'] == 'RANGE_COMPLETE'


def test_attested_backup_to_guarded_batches_end_to_end(tmp_path, monkeypatch):
    import time
    monkeypatch.setattr(time, "time", lambda: 1800000100.)
    from tools import btc_review_event_compaction as guards
    from utils import btc_review_retention as retention
    from tools.btc_account_compaction import plan_attested_accounts, guarded_operational_batch, run_bounded_batches
    from tools.btc_review_backup_manifest import create_backup_attestation
    path = tmp_path/'source.sqlite'
    source = sqlite3.connect(path, timeout=0)
    source.executescript('CREATE TABLE llm_scenario_broker_evidence(kind TEXT,captured_at REAL,body);'
                         'CREATE INDEX llm_scenario_broker_evidence_kind ON llm_scenario_broker_evidence(kind);'
                         'CREATE TABLE llm_scenario_state(id INTEGER,body TEXT);'
                         'CREATE TABLE llm_scenario_intents(id TEXT,status TEXT);')
    bodies = [json.dumps({'value': str(i)*3000}) for i in range(8)]
    source.executemany("INSERT INTO llm_scenario_broker_evidence VALUES('account',?,?)", [(i+1, x) for i,x in enumerate(bodies)])
    source.execute('INSERT INTO llm_scenario_state VALUES(1,?)', (json.dumps({'active':None}),))
    source.commit()
    st = path.stat()
    identity = hashlib.sha256(f'{path.resolve()}:{st.st_dev}:{st.st_ino}'.encode()).hexdigest()
    directory = tmp_path/'archive'; directory.mkdir(mode=0o700)
    archive, restored = directory/'backup.typed.gz', directory/'restored.sqlite'
    archive.write_bytes(b'fixture'); archive.chmod(0o600)
    with sqlite3.connect(restored) as dest:
        source.backup(dest)
    restored.chmod(0o600)
    digest = lambda file: hashlib.sha256(file.read_bytes()).hexdigest()
    detail = {'source_identity':identity, 'archive_bytes':archive.stat().st_size,
              'archive_sha256':digest(archive), 'restored_bytes':restored.stat().st_size,
              'restored_sha256':digest(restored)}
    backup_manifest = directory/'verified-manifest.json'; backup_manifest.write_text(json.dumps(detail)); backup_manifest.chmod(0o600)
    receipt = tmp_path/'archive.receipt.json'
    receipt.write_text(json.dumps({'status':'VERIFIED','directory':str(directory),'source_identity':identity,
                                   'manifest_sha256':digest(backup_manifest)})); receipt.chmod(0o600)
    proof = tmp_path/'proof.json'; create_backup_attestation(directory, proof)
    backup_sha = digest(restored)
    monkeypatch.setattr(guards, 'SOURCE', path)
    monkeypatch.setattr(guards, 'resource_guard', lambda *a: True)
    monkeypatch.setattr(retention, '_backup_idle', lambda *a, **kw: True)
    def apply(manifest, seal, proof_sha):
        return guarded_operational_batch(source, manifest, seal, proof_sha)
    result = run_bounded_batches(proof, 10, plan_attested_accounts, apply, max_batches=2)
    assert result['status'] == 'COMPLETE' and result['updated'] == 8 and result['batches'] == 1
    assert [review_text(row[0]) for row in source.execute('SELECT body FROM llm_scenario_broker_evidence ORDER BY rowid')] == bodies
    assert digest(restored) == backup_sha
    from tools.btc_account_compaction_controller import run_once
    def local_apply(manifest, seal, proof_sha, *, commit):
        assert commit == 'a'*40
        return guarded_operational_batch(source, manifest, seal, proof_sha)
    state = tmp_path/'account-state.json'
    resumed = run_once(state, proof, 10, 1254, verify=lambda _: 'a'*40,
                       plan=plan_attested_accounts, apply=local_apply, run_batches=run_bounded_batches)
    assert resumed['status'] == 'COMPLETE' and resumed['unchanged_total'] == 8
    assert resumed['updated_total'] == 0 and resumed['saved_bytes_total'] == 0
    assert digest(restored) == backup_sha


def test_compaction_error_rolls_back_instead_of_advancing_as_unchanged(monkeypatch):
    from tools import btc_account_compaction as compaction
    conn, values, rows = fixture()
    def fail(_):
        raise RuntimeError('compression failed')
    monkeypatch.setattr(compaction, 'review_storage', fail)
    with pytest.raises(RuntimeError, match='compression failed'):
        compaction.compact_batch(conn, rows, lambda: True)
    assert [row[0] for row in conn.execute('SELECT body FROM llm_scenario_broker_evidence ORDER BY rowid')] == values


def test_operational_batch_defers_when_next_tick_is_too_close(tmp_path, monkeypatch):
    import time
    from pathlib import Path
    from tools import btc_review_event_compaction as guards
    from tools.btc_account_compaction import guarded_operational_batch, manifest_sha256
    from utils import btc_review_retention as retention
    import live.shared_entry_coordinator as coordinator
    import contextlib
    path = tmp_path/'fixture.sqlite'
    conn = sqlite3.connect(path, timeout=0)
    conn.execute('CREATE TABLE llm_scenario_broker_evidence(kind TEXT,captured_at REAL,body)')
    raw = json.dumps({'detail':'x'*4000})
    conn.execute("INSERT INTO llm_scenario_broker_evidence VALUES('account',1,?)", (raw,))
    conn.commit()
    st = path.stat()
    identity = hashlib.sha256(f'{path.resolve()}:{st.st_dev}:{st.st_ino}'.encode()).hexdigest()
    manifest = {'source_identity':identity, 'cutoff':3, 'after_rowid':0, 'backup_proof_sha256':'b'*64,
                'rows':[{'rowid':1, 'captured_at':1, 'sha256':hashlib.sha256(raw.encode()).hexdigest()}]}
    monkeypatch.setattr(guards, 'SOURCE', Path(path))
    monkeypatch.setattr(guards, 'resource_guard', lambda *a: True)
    monkeypatch.setattr(retention, '_backup_idle', lambda *a, **kw: True)
    monkeypatch.setattr(retention, '_runtime_safe', lambda *a: True)
    monkeypatch.setattr(coordinator, 'mutation_lock', lambda _: contextlib.nullcontext())
    monkeypatch.setattr(time, 'time', lambda: 1800000181.)
    assert guarded_operational_batch(conn, manifest, manifest_sha256(manifest), 'b'*64)['status'] == 'DEFERRED'
    assert conn.execute('SELECT body FROM llm_scenario_broker_evidence').fetchone()[0] == raw
    monkeypatch.setattr(time, 'time', lambda: 1800000100.)
    assert guarded_operational_batch(conn, manifest, manifest_sha256(manifest), 'b'*64)['updated'] == 1


def test_byte_budget_truncates_backup_plan_without_skipping_next_row(monkeypatch):
    from tools import btc_account_compaction as module
    conn, values, _ = fixture()
    conn.execute('CREATE INDEX llm_scenario_broker_evidence_kind ON llm_scenario_broker_evidence(kind)')
    conn.commit();conn.execute('PRAGMA query_only=ON');conn.execute('BEGIN')
    monkeypatch.setattr(module, 'MAX_BATCH_SOURCE_BYTES', len(values[0].encode()) * 2)
    first = module.plan_old_accounts(conn, 'a'*64, 4)
    assert [r['rowid'] for r in first['rows']] == [1,2]
    second = module.plan_old_accounts(conn, 'a'*64, 4, after_rowid=first['rows'][-1]['rowid'])
    assert [r['rowid'] for r in second['rows']] == [3]


def test_byte_budget_rolls_back_whole_write_batch(monkeypatch):
    from tools import btc_account_compaction as module
    conn, values, rows = fixture()
    monkeypatch.setattr(module, 'MAX_BATCH_SOURCE_BYTES', len(values[0].encode()) * 2)
    with pytest.raises(ValueError, match='batch_size_limit'):
        module.compact_batch(conn, rows, lambda: True)
    assert [row[0] for row in conn.execute('SELECT body FROM llm_scenario_broker_evidence ORDER BY rowid')] == values
