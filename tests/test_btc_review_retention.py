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
        def stop():
            raise TimeoutError('fixture_limit')
        with pytest.raises(TimeoutError, match='fixture_limit'):
            snapshot_index(conn, 7 * 86400 + 300 * 3000, guard=stop)
        assert conn.execute('SELECT COUNT(*) FROM llm_scenario_review_tapes').fetchone()[0] == 2001


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
             'cutoff_slot': int((started - 7 * 86400) // 300), 'candidates': []}
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
