import hashlib
import json
import sqlite3

import pytest

from utils.btc_review_retention import candidates, fetch_deliveries, matching_evidence, snapshot_index, verified_archive_index, verify_remote_archive


def test_only_old_complete_flat_waits_become_candidates():
    conn = sqlite3.connect(':memory:')
    conn.executescript('CREATE TABLE llm_scenario_review_tapes(slot INTEGER PRIMARY KEY, body TEXT);'
                       'CREATE TABLE llm_scenario_decisions(slot INTEGER PRIMARY KEY, context TEXT, proposal TEXT, outcome TEXT);')
    context = {'scenario_id': None, 'positions': [], 'pending_entries': [], 'unknown_intents': False,
               'historical_unsettled': [], 'incident_epoch_blocked': False, 'recovery_observing': False}
    variants = [context, dict(context, positions=[{'quantity': 1}]),
                {key: value for key, value in context.items() if key != 'unknown_intents'},
                dict(context, incident_epoch_blocked=True), dict(context, recovery_observing=True),
                dict(context, unknown_intents=True), context]
    for slot, ctx in zip((1, 2, 3, 4, 5, 6, 20), variants):
        conn.execute('INSERT INTO llm_scenario_review_tapes VALUES(?,?)', (slot, '{"tape":1}'))
        conn.execute('INSERT INTO llm_scenario_decisions VALUES(?,?,?,?)',
                     (slot, json.dumps(ctx), json.dumps({'action': 'WAIT'}), json.dumps({'status': 'wait'})))
    now = 7 * 86400 + 10 * 300
    result = candidates(conn, now)
    assert result['cutoff_slot'] == 10
    assert result['last_scanned_slot'] == 6
    assert result['candidates'] == [{'rowid': 1, 'slot': 1,
                                     'tape_sha256': hashlib.sha256(b'{"tape":1}').hexdigest(),
                                     'decision_sha256': result['candidates'][0]['decision_sha256']}]
    assert candidates(conn, now, after_slot=1)['candidates'] == []
    assert candidates(conn, now, limit=1)['last_scanned_slot'] == 1
    index = snapshot_index(conn, now)
    assert index['scanned'] == 6 and index['candidates'] == result['candidates']
    assert conn.execute('SELECT COUNT(*) FROM llm_scenario_review_tapes').fetchone()[0] == 7
    old_digest = result['candidates'][0]['decision_sha256']
    conn.execute('UPDATE llm_scenario_decisions SET context=? WHERE slot=1',
                 (json.dumps(dict(context, now=1)),))
    assert candidates(conn, now)['candidates'][0]['decision_sha256'] != old_digest
    from utils.btc_review_references import MAGIC
    conn.execute('UPDATE llm_scenario_review_tapes SET body=? WHERE slot=1', (MAGIC + b'corrupt',))
    with pytest.raises(ValueError):
        candidates(conn, now)


@pytest.mark.parametrize('kwargs', [{'now': float('nan')}, {'now': 1}, {'now': True},
                                    {'now': 700000, 'limit': 0}, {'now': 700000, 'after_slot': True}])
def test_invalid_boundaries_fail_closed(kwargs):
    with sqlite3.connect(':memory:') as conn:
        with pytest.raises(ValueError, match='retention_input_invalid'):
            candidates(conn, **kwargs)


def test_documented_optional_recovery_flag_and_sealed_history():
    with sqlite3.connect(':memory:') as conn:
        conn.executescript('CREATE TABLE llm_scenario_review_tapes(slot INTEGER PRIMARY KEY, body TEXT);'
                           'CREATE TABLE llm_scenario_decisions(slot INTEGER PRIMARY KEY, context TEXT, proposal TEXT, outcome TEXT);')
        context = {'scenario_id': None, 'positions': [], 'pending_entries': [], 'unknown_intents': False,
                   'historical_unsettled': [{'status': 'HISTORICAL_UNSETTLED', 'excluded_from_performance': True}],
                   'incident_epoch_blocked': False}
        for slot, ctx in ((1, context), (2, dict(context, recovery_observing=True)),
                          (3, dict(context, historical_unsettled=[{'status': 'UNKNOWN'}]))):
            conn.execute('INSERT INTO llm_scenario_review_tapes VALUES(?,?)', (slot, '{}'))
            conn.execute('INSERT INTO llm_scenario_decisions VALUES(?,?,?,?)',
                         (slot, json.dumps(ctx), '{"action":"WAIT"}', '{"status":"wait"}'))
        result = candidates(conn, 7 * 86400 + 10 * 300)
        assert [row['slot'] for row in result['candidates']] == [1]


def test_missing_or_partial_schema_is_not_an_empty_verified_index():
    with sqlite3.connect(':memory:') as conn:
        assert snapshot_index(conn, 700000) is None
        conn.execute('CREATE TABLE llm_scenario_review_tapes(slot INTEGER PRIMARY KEY,body TEXT)')
        with pytest.raises(ValueError, match='retention_schema_incomplete'):
            snapshot_index(conn, 700000)


def test_snapshot_index_has_bounded_scan():
    with sqlite3.connect(':memory:') as conn:
        conn.executescript('CREATE TABLE llm_scenario_review_tapes(slot INTEGER PRIMARY KEY, body TEXT);'
                           'CREATE TABLE llm_scenario_decisions(slot INTEGER PRIMARY KEY, context TEXT, proposal TEXT, outcome TEXT);')
        conn.executemany('INSERT INTO llm_scenario_review_tapes VALUES(?,?)', ((i, '{}') for i in range(1, 2002)))
        index = snapshot_index(conn, 7 * 86400 + 300 * 3000)
        assert index['scanned'] == 2000 and index['last_scanned_slot'] == 2000
        assert index['truncated'] and index['candidates'] == []
        next_index = snapshot_index(conn, 7 * 86400 + 300 * 3000, after_slot=index['last_scanned_slot'])
        assert next_index['scanned'] == 1 and next_index['last_scanned_slot'] == 2001
        assert next_index['truncated'] is False
        def stop():
            raise TimeoutError('fixture_limit')
        with pytest.raises(TimeoutError, match='fixture_limit'):
            snapshot_index(conn, 7 * 86400 + 300 * 3000, guard=stop)
        assert conn.execute('SELECT COUNT(*) FROM llm_scenario_review_tapes').fetchone()[0] == 2001


def test_cursor_is_private_bound_to_source_and_wraps(tmp_path, monkeypatch):
    from utils import btc_review_retention as retention
    root = tmp_path / 'backups'
    root.mkdir(mode=0o700)
    monkeypatch.setattr(retention, 'BACKUP_ROOT', root)
    cursor = root / '.btc-retention-cursor.json'
    identity = 'a' * 64
    assert retention.read_cursor(cursor, identity) == -1
    retention._save_cursor({'truncated': True, 'last_scanned_slot': 42}, identity)
    assert retention.read_cursor(cursor, identity) == 42
    assert retention.read_cursor(cursor, 'b' * 64) == -1
    retention._save_cursor({'truncated': False, 'last_scanned_slot': 99}, identity)
    assert retention.read_cursor(cursor, identity) == -1
    cursor.chmod(0o644)
    assert retention.read_cursor(cursor, identity) == -1


def test_snapshot_and_delivery_must_match_each_candidate():
    identity = 'a' * 64
    first = {'rowid': 7, 'slot': 7, 'tape_sha256': 'b' * 64, 'decision_sha256': 'c' * 64}
    second = {'rowid': 8, 'slot': 8, 'tape_sha256': 'd' * 64, 'decision_sha256': 'e' * 64}
    event_id = hashlib.sha256(f'{identity}:llm_scenario_review_tapes:7'.encode()).hexdigest()[:32]
    receipt = {'event_id': event_id, 'event_type': 'btc.research.review_tape', 'source_identity': identity,
               'body_sha256': first['tape_sha256'], 'tape_hash_status': 'MATCH'}
    current = {'candidates': [first, second]}
    archived = {'candidates': [first, dict(second, decision_sha256='f' * 64)]}
    assert matching_evidence(current, archived, [receipt], identity) == [first]
    assert matching_evidence(current, archived, [dict(receipt, body_sha256='0' * 64)], identity) == []
    assert matching_evidence(current, archived, [], identity) == []
    with pytest.raises(ValueError, match='delivery_ambiguous'):
        matching_evidence(current, archived, [receipt, dict(receipt, body_sha256='0' * 64)], identity)
    with pytest.raises(ValueError, match='identity_invalid'):
        matching_evidence(current, archived, [receipt], 'invalid')


def test_verified_archive_index_requires_matching_receipt_and_source():
    identity = 'a' * 64
    started = 700000.5
    index = {'version': 1, 'as_of': int(started),
             'cutoff_slot': int((started - 7 * 86400) // 300), 'after_slot': -1,
             'last_scanned_slot': -1, 'scanned': 0, 'truncated': False, 'candidates': []}
    data = json.dumps({'source_identity_sha256': identity, 'started_at': started,
                       'completed_at': started + 1, 'raw_sha256': 'b' * 64,
                       'compressed_sha256': 'c' * 64, 'btc_retention_index': index}).encode()
    ack = {'status': 'VERIFIED', 'retention_index_verified': True, 'archive': 'archive-' + 'd' * 32,
           'raw_sha256': 'b' * 64, 'compressed_sha256': 'c' * 64,
           'manifest_sha256': hashlib.sha256(data).hexdigest()}
    assert verified_archive_index(data, ack, identity) == index
    for invalid in (dict(ack, retention_index_verified=False), dict(ack, archive='../wrong'),
                    dict(ack, raw_sha256='0' * 64), dict(ack, manifest_sha256='0' * 64)):
        with pytest.raises(ValueError, match='retention_archive_unverified'):
            verified_archive_index(data, invalid, identity)
    with pytest.raises(ValueError, match='retention_archive_unverified'):
        verified_archive_index(data, ack, 'e' * 64)


def test_clickstack_lookup_is_read_only_bounded_and_rejects_unexpected_rows(monkeypatch):
    from types import SimpleNamespace
    from utils import btc_offsite_archive, btc_review_retention

    identity = 'a' * 64
    event = hashlib.sha256(f'{identity}:llm_scenario_review_tapes:7'.encode()).hexdigest()[:32]
    receipt = {'event_id': event, 'event_type': 'btc.research.review_tape', 'source_identity': identity,
               'body_sha256': 'b' * 64, 'tape_hash_status': 'MATCH'}
    calls = []
    monkeypatch.setattr(btc_offsite_archive, 'backend_host', lambda _: 'fixture.local')
    def execute(command, **kwargs):
        calls.append((command, kwargs))
        assert event in kwargs['input'] and 'readonly' in kwargs['input'] and 'LIMIT 201' in kwargs['input']
        return SimpleNamespace(stdout=json.dumps(receipt) + '\n')
    monkeypatch.setattr(btc_review_retention.subprocess, 'run', execute)
    assert fetch_deliveries([{'rowid': 7}], identity) == [receipt]
    assert calls[0][0][0] == 'ssh' and calls[0][1]['timeout'] == 45
    assert fetch_deliveries([], identity) == [] and len(calls) == 1
    with pytest.raises(ValueError, match='retention_query_invalid'):
        fetch_deliveries([{'rowid': '7 OR 1=1'}], identity)
    receipt['event_id'] = 'f' * 32
    with pytest.raises(ValueError, match='retention_delivery_invalid'):
        fetch_deliveries([{'rowid': 7}], identity)


def test_remote_archive_rechecks_files_and_rejects_tampering(tmp_path, monkeypatch):
    import fcntl
    import os
    import shlex
    import subprocess
    from utils import btc_offsite_archive, btc_review_retention

    root = (tmp_path / 'archives').resolve()
    root.mkdir(mode=0o700)
    target = root / ('archive-' + 'a' * 32)
    target.mkdir(mode=0o700)
    files = {'snapshot.sqlite.gz': 'compressed_sha256', 'snapshot.manifest.json': 'manifest_sha256',
             'history.bundle': 'bundle_sha256', 'archive.json': 'archive_metadata_sha256'}
    ack = {'status': 'VERIFIED', 'retention_index_verified': True, 'archive': target.name, 'raw_sha256': 'b' * 64}
    for name, key in files.items():
        path = target / name
        path.write_bytes(name.encode())
        path.chmod(0o600)
        ack[key] = hashlib.sha256(path.read_bytes()).hexdigest()
    receipt = target / 'receipt.json'
    receipt.write_text(json.dumps({k: v for k, v in ack.items() if k != 'archive'}))
    receipt.chmod(0o600)
    real_popen = subprocess.Popen
    def local_ssh(args, **kwargs):
        command = shlex.split(args[-1])
        code = command[2].replace('/root/prism-btc-archive', str(root))
        return real_popen([os.sys.executable, '-I', '-c', code], **kwargs)
    monkeypatch.setattr(btc_offsite_archive, 'backend_host', lambda _: 'fixture.local')
    monkeypatch.setattr(btc_review_retention.subprocess, 'Popen', local_ssh)
    with btc_review_retention.locked_remote_archive(ack) as alive:
        assert alive()
        with (root / '.receive.lock').open('r+') as lock:
            with pytest.raises(BlockingIOError):
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    with (root / '.receive.lock').open('r+') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        fcntl.flock(lock, fcntl.LOCK_UN)
    (target / 'snapshot.sqlite.gz').write_bytes(b'tampered')
    with pytest.raises(ValueError, match='retention_archive_unverified'):
        verify_remote_archive(ack)


def transaction_fixture():
    from observability.btc_scenario_export import _anchor_hash
    source, exporter = sqlite3.connect(':memory:'), sqlite3.connect(':memory:')
    source.executescript('CREATE TABLE llm_scenario_review_tapes(slot INTEGER PRIMARY KEY, body TEXT);'
                         'CREATE TABLE llm_scenario_decisions(slot INTEGER PRIMARY KEY, context TEXT, proposal TEXT, outcome TEXT);')
    context = {'scenario_id': None, 'positions': [], 'pending_entries': [], 'unknown_intents': False,
               'historical_unsettled': [], 'incident_epoch_blocked': False, 'recovery_observing': False}
    body = '{"kind":"WAIT"}'
    for slot in (1, 2):
        source.execute('INSERT INTO llm_scenario_review_tapes VALUES(?,?)', (slot, body))
        source.execute('INSERT INTO llm_scenario_decisions VALUES(?,?,?,?)',
                       (slot, json.dumps(context), json.dumps({'action': 'WAIT'}), json.dumps({'status': 'wait'})))
    source.commit()
    identity = 'a' * 64
    exporter.executescript('CREATE TABLE meta(key PRIMARY KEY,value);CREATE TABLE cursors(stream PRIMARY KEY,seq);'
                           'CREATE TABLE anchors(stream PRIMARY KEY,ref);CREATE TABLE outbox(id PRIMARY KEY,body);')
    exporter.execute('INSERT INTO meta VALUES(?,?)', ('source_identity', identity))
    exporter.execute('INSERT INTO cursors VALUES(?,?)', ('llm_scenario_review_tapes', 2))
    anchor = _anchor_hash('llm_scenario_review_tapes', {'slot': 2, 'body': body}, source)
    exporter.execute('INSERT INTO anchors VALUES(?,?)', ('llm_scenario_review_tapes', anchor))
    exporter.commit()
    now = 7 * 86400 + 10 * 300
    archived = candidates(source, now)
    receipts = []
    for row in archived['candidates']:
        event = hashlib.sha256(f'{identity}:llm_scenario_review_tapes:{row["rowid"]}'.encode()).hexdigest()[:32]
        receipts.append({'event_id': event, 'event_type': 'btc.research.review_tape', 'source_identity': identity,
                         'body_sha256': row['tape_sha256'], 'tape_hash_status': 'MATCH'})
    return source, exporter, identity, now, archived, receipts


def test_transaction_deletes_only_delivered_nonanchor_and_preserves_decisions():
    from utils.btc_review_retention import _delete_matched
    source, exporter, identity, now, archived, receipts = transaction_fixture()
    removed = _delete_matched(source, exporter, archived, receipts, identity, now,
                              guard=lambda: True, lease_alive=lambda: True)
    assert removed == [1]
    assert source.execute('SELECT slot FROM llm_scenario_review_tapes').fetchall() == [(2,)]
    assert source.execute('SELECT COUNT(*) FROM llm_scenario_decisions').fetchone()[0] == 2
    assert exporter.execute('SELECT seq FROM cursors').fetchone()[0] == 2
    assert not source.in_transaction and not exporter.in_transaction


def test_transaction_rolls_back_when_guard_or_export_anchor_fails():
    from utils.btc_review_retention import _delete_matched
    source, exporter, identity, now, archived, receipts = transaction_fixture()
    checks = iter((True, True, False))
    with pytest.raises(ValueError, match='retention_guard_failed'):
        _delete_matched(source, exporter, archived, receipts, identity, now,
                        guard=lambda: next(checks), lease_alive=lambda: True)
    assert source.execute('SELECT COUNT(*) FROM llm_scenario_review_tapes').fetchone()[0] == 2
    leases = iter((True, True, False))
    with pytest.raises(ValueError, match='retention_guard_failed'):
        _delete_matched(source, exporter, archived, receipts, identity, now,
                        guard=lambda: True, lease_alive=lambda: next(leases))
    assert source.execute('SELECT COUNT(*) FROM llm_scenario_review_tapes').fetchone()[0] == 2
    exporter.execute("UPDATE anchors SET ref='changed'")
    exporter.commit()
    with pytest.raises(ValueError, match='retention_export_anchor_changed'):
        _delete_matched(source, exporter, archived, receipts, identity, now,
                        guard=lambda: True, lease_alive=lambda: True)
    assert source.execute('SELECT COUNT(*) FROM llm_scenario_review_tapes').fetchone()[0] == 2
    assert not source.in_transaction and not exporter.in_transaction


def test_transaction_preserves_changed_or_undelivered_rows_and_pending_outbox():
    from utils.btc_review_retention import _delete_matched
    source, exporter, identity, now, archived, receipts = transaction_fixture()
    context = json.loads(source.execute('SELECT context FROM llm_scenario_decisions WHERE slot=1').fetchone()[0])
    context['now'] = 1
    source.execute('UPDATE llm_scenario_decisions SET context=? WHERE slot=1', (json.dumps(context),))
    source.commit()
    assert _delete_matched(source, exporter, archived, receipts, identity, now,
                           guard=lambda: True, lease_alive=lambda: True) == []
    exporter.execute('INSERT INTO outbox VALUES(?,?)', ('pending', '{}'))
    exporter.commit()
    with pytest.raises(ValueError, match='retention_export_state_invalid'):
        _delete_matched(source, exporter, archived, receipts, identity, now,
                        guard=lambda: True, lease_alive=lambda: True)
    assert source.execute('SELECT COUNT(*) FROM llm_scenario_review_tapes').fetchone()[0] == 2


def test_cleanup_uses_backup_ack_delivery_lease_and_transaction(tmp_path, monkeypatch):
    import os
    from contextlib import contextmanager
    from utils import btc_review_retention as retention
    from observability.btc_scenario_export import _identity

    source, exporter, _, now, _, _ = transaction_fixture()
    source_path, state_path = tmp_path / 'source.sqlite', tmp_path / 'exporter.sqlite'
    with sqlite3.connect(source_path) as db:
        source.backup(db)
    with sqlite3.connect(state_path) as db:
        exporter.backup(db)
    identity = _identity(source_path)
    with sqlite3.connect(state_path) as db:
        db.execute("UPDATE meta SET value=? WHERE key='source_identity'", (identity,))
    with sqlite3.connect(source_path) as db:
        index = snapshot_index(db, now)
    root, directory = tmp_path / 'backups', tmp_path / 'backups' / 'one'
    root.mkdir(mode=0o700)
    directory.mkdir(mode=0o700)
    manifest = {'source_identity_sha256': identity, 'started_at': now, 'completed_at': now + 1,
                'raw_sha256': 'b' * 64, 'compressed_sha256': 'c' * 64, 'btc_retention_index': index}
    manifest_bytes = json.dumps(manifest).encode()
    manifest_path = directory / 'stock_tracking_db.sqlite.gz.manifest.json'
    manifest_path.write_bytes(manifest_bytes)
    manifest_path.chmod(0o600)
    ack = {'status': 'VERIFIED', 'retention_index_verified': True, 'archive': 'archive-' + 'd' * 32,
           'raw_sha256': 'b' * 64, 'compressed_sha256': 'c' * 64,
           'manifest_sha256': hashlib.sha256(manifest_bytes).hexdigest()}
    ack_path = directory / 'offsite_ack.json'
    ack_path.write_text(json.dumps(ack))
    ack_path.chmod(0o600)
    def delivery(rows, source_identity):
        return [{'event_id': hashlib.sha256(f'{source_identity}:llm_scenario_review_tapes:{row["rowid"]}'.encode()).hexdigest()[:32],
                 'event_type': 'btc.research.review_tape', 'source_identity': source_identity,
                 'body_sha256': row['tape_sha256'], 'tape_hash_status': 'MATCH'} for row in rows]
    @contextmanager
    def lease(_):
        yield lambda: True
    monkeypatch.setattr(retention, 'SOURCE', source_path)
    monkeypatch.setattr(retention, 'EXPORT_STATE', state_path)
    monkeypatch.setattr(retention, 'BACKUP_ROOT', root)
    monkeypatch.setattr(retention, '_resource_guard', lambda *_: True)
    monkeypatch.setattr(retention, '_runtime_safe', lambda *_: True)
    monkeypatch.setattr(retention, 'fetch_deliveries', delivery)
    monkeypatch.setattr(retention, 'locked_remote_archive', lease)
    assert retention.cleanup(directory, now=now) == {'status': 'COMPLETE', 'removed': 1}
    with sqlite3.connect(source_path) as db:
        assert db.execute('SELECT slot FROM llm_scenario_review_tapes').fetchall() == [(2,)]
        assert db.execute('SELECT COUNT(*) FROM llm_scenario_decisions').fetchone()[0] == 2
    assert retention.cleanup(directory, now=now) == {'status': 'COMPLETE', 'removed': 0}
    os.chmod(ack_path, 0o644)
    with pytest.raises(ValueError, match='retention_private_file_invalid'):
        retention.cleanup(directory, now=now)


def test_cleanup_with_independently_restored_archive_and_real_lease(tmp_path, monkeypatch):
    import os
    import shlex
    import subprocess
    from types import SimpleNamespace
    from observability.btc_scenario_export import _identity
    from utils import btc_offsite_archive as archive, btc_review_retention as retention
    from utils.sqlite_snapshot import backup_database

    root = tmp_path.resolve()
    repo = root / 'repo'
    repo.mkdir()
    def git(*args):
        subprocess.run(['/usr/bin/git', *args], cwd=repo, check=True, capture_output=True, text=True)
    git('init')
    git('config', 'user.name', 'fixture')
    git('config', 'user.email', 'fixture@example.invalid')
    (repo / 'source.py').write_text('fixture')
    git('add', 'source.py')
    git('commit', '-m', 'fixture')
    source, exporter, _, now, _, _ = transaction_fixture()
    source_path, state_path = root / 'source.sqlite', root / 'exporter.sqlite'
    with sqlite3.connect(source_path) as db:
        source.backup(db)
    with sqlite3.connect(state_path) as db:
        exporter.backup(db)
    identity = _identity(source_path)
    with sqlite3.connect(state_path) as db:
        db.execute("UPDATE meta SET value=? WHERE key='source_identity'", (identity,))
    backups, directory = root / 'backups', root / 'backups' / 'one'
    backups.mkdir(mode=0o700)
    directory.mkdir(mode=0o700)
    snapshot = directory / 'stock_tracking_db.sqlite.gz'
    backup_database(source_path, snapshot, reserve_bytes=0, capture_references=True)
    remote_root = root / 'remote'
    monkeypatch.setattr(archive, 'backend_host', lambda _: 'fixture.local')
    monkeypatch.setattr(archive.shutil, 'disk_usage', lambda _: SimpleNamespace(free=100 * 1024**3))
    def transfer(_host, needed, *, stream=None):
        return SimpleNamespace(stdout=b'READY' if stream is None else
                               json.dumps(archive.receive(stream, remote_root, needed)).encode())
    monkeypatch.setattr(archive, 'ssh_archive', transfer)
    ack = archive.archive(repo, snapshot, root / 'unused')
    ack_path = directory / 'offsite_ack.json'
    ack_path.write_text(json.dumps(ack))
    ack_path.chmod(0o600)
    def delivery(rows, source_identity):
        return [{'event_id': hashlib.sha256(f'{source_identity}:llm_scenario_review_tapes:{row["rowid"]}'.encode()).hexdigest()[:32],
                 'event_type': 'btc.research.review_tape', 'source_identity': source_identity,
                 'body_sha256': row['tape_sha256'], 'tape_hash_status': 'MATCH'} for row in rows]
    real_popen = subprocess.Popen
    def local_ssh(args, **kwargs):
        command = shlex.split(args[-1])
        code = command[2].replace('/root/prism-btc-archive', str(remote_root))
        return real_popen([os.sys.executable, '-I', '-c', code], **kwargs)
    monkeypatch.setattr(retention.subprocess, 'Popen', local_ssh)
    monkeypatch.setattr(retention, 'SOURCE', source_path)
    monkeypatch.setattr(retention, 'EXPORT_STATE', state_path)
    monkeypatch.setattr(retention, 'BACKUP_ROOT', backups)
    monkeypatch.setattr(retention, '_resource_guard', lambda *_: True)
    monkeypatch.setattr(retention, '_runtime_safe', lambda *_: True)
    monkeypatch.setattr(retention, 'fetch_deliveries', delivery)
    assert retention.cleanup(directory) == {'status': 'COMPLETE', 'removed': 1}
    with sqlite3.connect(source_path) as db:
        assert db.execute('SELECT slot FROM llm_scenario_review_tapes').fetchall() == [(2,)]
    assert (remote_root / ack['archive'] / 'receipt.json').is_file()


def test_runner_reports_result_and_redacts_exception(monkeypatch, capsys):
    from tools import run_btc_review_retention as runner
    monkeypatch.setattr(runner, 'cleanup', lambda _: {'status': 'NO_CANDIDATES', 'removed': 0})
    assert runner.main(['/fixture']) == 0
    assert json.loads(capsys.readouterr().out) == {'status': 'NO_CANDIDATES', 'removed': 0}
    def fail(_):
        raise ValueError('private fixture content')
    monkeypatch.setattr(runner, 'cleanup', fail)
    assert runner.main(['/fixture']) == 1
    output = json.loads(capsys.readouterr().out)
    assert output == {'status': 'ERROR', 'error_type': 'ValueError'}
