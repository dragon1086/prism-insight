import json
import hashlib
from copy import deepcopy
import sqlite3

import pytest

from utils.btc_recovery_references import canonical, compact, pack
from utils.btc_review_event_proof import delivered_event_proof, event_proof, snapshot_event_index, verified_event_index


def fixture():
    conn = sqlite3.connect(':memory:')
    conn.executescript('CREATE TABLE llm_scenario_review_events(slot INTEGER,sequence INTEGER,body);'
                       'CREATE TABLE llm_scenario_recovery_events(event_id TEXT,slot INTEGER,kind TEXT,body);')
    review = {'kind': 'MODEL_REQUEST', 'prompt': 'x' * 3000}
    recovery = dict(review, calculated_review_version=1, input_id='fixture')
    review_text, recovery_text = canonical(review), canonical(recovery)
    ref = compact(review_text, recovery_text, 1, 0)
    assert ref != recovery_text
    conn.execute('INSERT INTO llm_scenario_review_events VALUES(?,?,?)', (1, 0, review_text))
    conn.execute('INSERT INTO llm_scenario_recovery_events VALUES(?,?,?,?)', ('e', 1, 'MODEL_REQUEST', pack(ref)))
    conn.commit()
    return conn


def test_resolves_recovery_reference_and_changes_when_event_changes():
    conn = fixture()
    before_changes = conn.total_changes
    proof = event_proof(conn, 1)
    assert conn.total_changes == before_changes
    assert len(proof['events']) == len(proof['recovery']) == 1
    assert 'prompt' not in json.dumps(proof)
    conn.execute('UPDATE llm_scenario_review_events SET body=?', ('{"kind":"MODEL_REQUEST"}',))
    with pytest.raises(ValueError):
        event_proof(conn, 1)


def test_missing_or_malformed_evidence_fails_closed():
    conn = fixture()
    conn.execute('DELETE FROM llm_scenario_review_events')
    with pytest.raises(ValueError, match='proof_event_count_invalid'):
        event_proof(conn, 1)
    conn.execute('INSERT INTO llm_scenario_review_events VALUES(?,?,?)', (1, 1, '{}'))
    with pytest.raises(ValueError, match='proof_event_sequence_invalid'):
        event_proof(conn, 1)
    with pytest.raises(ValueError, match='proof_slot_invalid'):
        event_proof(conn, True)


def test_duplicate_recovery_and_event_bound_fail_closed():
    conn = fixture()
    conn.execute('INSERT INTO llm_scenario_recovery_events VALUES(?,?,?,?)', ('e', 1, 'OTHER', '{}'))
    with pytest.raises(ValueError, match='proof_recovery_identity_invalid'):
        event_proof(conn, 1)
    conn.execute('DELETE FROM llm_scenario_recovery_events')
    conn.executemany('INSERT INTO llm_scenario_review_events VALUES(?,?,?)',
                     [(1, i, '{}') for i in range(1, 129)])
    with pytest.raises(ValueError, match='proof_event_count_invalid'):
        event_proof(conn, 1)


def test_backup_and_every_delivery_must_match_exactly():
    proof = event_proof(fixture(), 1)
    identity = 'a' * 64
    event = proof['events'][0]
    event_id = hashlib.sha256(f'{identity}:llm_scenario_review_events:{event["rowid"]}'.encode()).hexdigest()[:32]
    receipt = {'event_id': event_id, 'event_type': 'btc.research.review', 'source_identity': identity,
               'body_sha256': event['sha256'], 'source_rowid': event['rowid'],
               'source_sequence': event['sequence'], 'decision_slot': proof['slot']}
    assert delivered_event_proof(proof, deepcopy(proof), [receipt], identity)
    assert not delivered_event_proof(proof, deepcopy(proof), [], identity)
    assert not delivered_event_proof(proof, deepcopy(proof), [dict(receipt, body_sha256='0' * 64)], identity)
    assert not delivered_event_proof(proof, deepcopy(proof), [dict(receipt, decision_slot=True)], identity)
    changed = deepcopy(proof)
    changed['recovery'][0]['sha256'] = '0' * 64
    assert not delivered_event_proof(proof, changed, [receipt], identity)
    with pytest.raises(ValueError, match='proof_source_invalid'):
        delivered_event_proof(proof, proof, [receipt], 'invalid')


def test_snapshot_index_is_bounded_and_does_not_skip_candidates():
    conn = sqlite3.connect(':memory:')
    conn.executescript('CREATE TABLE llm_scenario_review_tapes(slot INTEGER PRIMARY KEY,body TEXT);'
                       'CREATE TABLE llm_scenario_decisions(slot INTEGER PRIMARY KEY,snapshot TEXT,context TEXT,proposal TEXT,outcome TEXT);'
                       'CREATE TABLE llm_scenario_review_events(slot INTEGER,sequence INTEGER,body);'
                       'CREATE TABLE llm_scenario_recovery_events(event_id TEXT,slot INTEGER,kind TEXT,body);')
    context = json.dumps({'scenario_id': None, 'positions': [], 'pending_entries': [], 'unknown_intents': False,
                          'historical_unsettled': [], 'incident_epoch_blocked': False, 'recovery_observing': False})
    for slot in range(1, 24):
        conn.execute('INSERT INTO llm_scenario_review_tapes VALUES(?,?)', (slot, '{}'))
        conn.execute('INSERT INTO llm_scenario_decisions VALUES(?,?,?,?,?)', (slot, '{}', context, '{"action":"WAIT"}', '{"status":"wait"}'))
        conn.execute('INSERT INTO llm_scenario_review_events VALUES(?,?,?)', (slot, 0, '{"kind":"MODEL_RAW"}'))
    conn.commit()
    now = 7 * 86400 + 100 * 300
    with pytest.raises(ValueError, match='proof_snapshot_transaction_required'):
        snapshot_event_index(conn, now)
    conn.execute('BEGIN')
    first = snapshot_event_index(conn, now)
    assert len(first['items']) == 20 and first['truncated'] and first['last_scanned_slot'] == 20
    second = snapshot_event_index(conn, now, after_slot=first['last_scanned_slot'])
    assert [item['candidate']['slot'] for item in second['items']] == [21, 22, 23]
    assert not second['truncated']
    conn.rollback()


def test_event_index_requires_bound_receipt_and_same_wait_candidate():
    identity, started = 'a' * 64, 700000.5
    row = {'slot': 1, 'rowid': 1, 'tape_sha256': 'e' * 64, 'decision_sha256': 'f' * 64}
    base = {'version': 1, 'as_of': int(started), 'cutoff_slot': int((started - 7 * 86400) // 300),
            'after_slot': -1, 'last_scanned_slot': 1, 'scanned': 1, 'truncated': False, 'candidates': [row]}
    index = {'version': 2, 'cutoff_slot': base['cutoff_slot'], 'after_slot': -1, 'last_scanned_slot': 1,
             'truncated': False, 'items': [{'candidate': deepcopy(row), 'proof': event_proof(fixture(), 1)}]}
    manifest = {'source_identity_sha256': identity, 'started_at': started, 'completed_at': started + 1,
                'raw_sha256': 'b' * 64, 'compressed_sha256': 'c' * 64, 'btc_retention_index': base,
                'btc_review_event_proof_status': 'CAPTURED', 'btc_review_event_proof_index': index}
    def encode(value):
        data = json.dumps(value).encode()
        ack = {'status': 'VERIFIED', 'retention_index_verified': True, 'review_event_proof_index_verified': True,
               'archive': 'archive-' + 'd' * 32, 'raw_sha256': 'b' * 64, 'compressed_sha256': 'c' * 64,
               'manifest_sha256': hashlib.sha256(data).hexdigest()}
        return data, ack
    data, ack = encode(manifest)
    assert verified_event_index(data, ack, identity) == index
    with pytest.raises(ValueError, match='event_proof_archive_unverified'):
        verified_event_index(data, dict(ack, review_event_proof_index_verified=False), identity)
    altered = deepcopy(manifest)
    altered['btc_review_event_proof_index']['items'][0]['candidate']['tape_sha256'] = '0' * 64
    data, ack = encode(altered)
    with pytest.raises(ValueError, match='event_proof_archive_unverified'):
        verified_event_index(data, ack, identity)


def deletion_fixture():
    from observability.btc_scenario_export import _anchor_hash
    from utils.btc_review_retention import candidates
    source, exporter = sqlite3.connect(':memory:'), sqlite3.connect(':memory:')
    source.executescript('CREATE TABLE llm_scenario_review_tapes(slot INTEGER PRIMARY KEY,body TEXT);'
                         'CREATE TABLE llm_scenario_decisions(slot INTEGER PRIMARY KEY,snapshot TEXT,context TEXT,proposal TEXT,outcome TEXT);'
                         'CREATE TABLE llm_scenario_review_events(slot INTEGER,sequence INTEGER,body);'
                         'CREATE TABLE llm_scenario_recovery_events(event_id TEXT,slot INTEGER,kind TEXT,body);')
    context = json.dumps({'scenario_id': None, 'positions': [], 'pending_entries': [], 'unknown_intents': False,
                          'historical_unsettled': [], 'incident_epoch_blocked': False, 'recovery_observing': False})
    for slot in (1, 2):
        source.execute('INSERT INTO llm_scenario_review_tapes VALUES(?,?)', (slot, '{}'))
        source.execute('INSERT INTO llm_scenario_decisions VALUES(?,?,?,?,?)', (slot, '{}', context, '{"action":"WAIT"}', '{"status":"wait"}'))
        source.execute('INSERT INTO llm_scenario_review_events VALUES(?,?,?)', (slot, 0, '{"kind":"MODEL_RAW"}'))
    source.commit()
    exporter.executescript('CREATE TABLE meta(key PRIMARY KEY,value);CREATE TABLE cursors(stream PRIMARY KEY,seq);'
                           'CREATE TABLE anchors(stream PRIMARY KEY,ref);CREATE TABLE outbox(id PRIMARY KEY,body);')
    identity = 'a' * 64
    exporter.execute('INSERT INTO meta VALUES(?,?)', ('source_identity', identity))
    for stream, anchor in (('llm_scenario_review_tapes', {'slot': 2, 'body': '{}'}),
                           ('llm_scenario_review_events', {'slot': 2, 'sequence': 0, 'body': '{"kind":"MODEL_RAW"}'})):
        exporter.execute('INSERT INTO cursors VALUES(?,?)', (stream, 2))
        exporter.execute('INSERT INTO anchors VALUES(?,?)', (stream, _anchor_hash(stream, anchor, source)))
    exporter.commit()
    now = 7 * 86400 + 100 * 300
    candidate = candidates(source, now)['candidates'][0]
    proof = event_proof(source, 1)
    tape_id = hashlib.sha256(f'{identity}:llm_scenario_review_tapes:{candidate["rowid"]}'.encode()).hexdigest()[:32]
    tape = {'event_id': tape_id, 'event_type': 'btc.research.review_tape', 'source_identity': identity,
            'body_sha256': candidate['tape_sha256'], 'tape_hash_status': 'MATCH'}
    event = proof['events'][0]
    event_id = hashlib.sha256(f'{identity}:llm_scenario_review_events:{event["rowid"]}'.encode()).hexdigest()[:32]
    delivery = {'event_id': event_id, 'event_type': 'btc.research.review', 'source_identity': identity,
                'body_sha256': event['sha256'], 'source_rowid': event['rowid'],
                'source_sequence': event['sequence'], 'decision_slot': 1}
    return source, exporter, {'candidate': candidate, 'proof': proof}, [tape], [delivery], identity, now


def test_delete_verified_wait_and_events_preserves_anchor_and_decision():
    from utils.btc_review_event_proof import delete_confirmed_slot
    source, exporter, archived, tape, events, identity, now = deletion_fixture()
    result = delete_confirmed_slot(source, exporter, archived, tape, events, identity, now,
                                   guard=lambda: True, lease_alive=lambda: True)
    assert result == {'status': 'REMOVED', 'events': 1}
    assert source.execute('SELECT slot FROM llm_scenario_review_tapes').fetchall() == [(2,)]
    assert source.execute('SELECT slot FROM llm_scenario_review_events').fetchall() == [(2,)]
    assert source.execute('SELECT slot FROM llm_scenario_decisions ORDER BY slot').fetchall() == [(1,), (2,)]


def test_missing_delivery_or_lost_lease_never_deletes():
    from utils.btc_review_event_proof import delete_confirmed_slot
    source, exporter, archived, tape, events, identity, now = deletion_fixture()
    assert delete_confirmed_slot(source, exporter, archived, tape, [], identity, now,
                                 guard=lambda: True, lease_alive=lambda: True)['status'] == 'DEFERRED_EVENT_EVIDENCE'
    checks = iter((True, True, False))
    with pytest.raises(ValueError, match='event_delete_guard_failed'):
        delete_confirmed_slot(source, exporter, archived, tape, events, identity, now,
                              guard=lambda: True, lease_alive=lambda: next(checks))
    assert source.execute('SELECT COUNT(*) FROM llm_scenario_review_tapes').fetchone() == (2,)
    assert source.execute('SELECT COUNT(*) FROM llm_scenario_review_events').fetchone() == (2,)


def test_recovery_reference_defers_event_deletion():
    from utils.btc_review_event_proof import delete_confirmed_slot
    source, exporter, archived, tape, events, identity, now = deletion_fixture()
    review = canonical({'kind': 'MODEL_REQUEST', 'prompt': 'x' * 3000})
    recovery = canonical({'kind': 'MODEL_REQUEST', 'prompt': 'x' * 3000, 'calculated_review_version': 1,
                          'input_id': 'fixture'})
    source.execute('UPDATE llm_scenario_review_events SET body=? WHERE slot=1', (review,))
    source.execute('INSERT INTO llm_scenario_recovery_events VALUES(?,?,?,?)',
                   ('e', 1, 'MODEL_REQUEST', pack(compact(review, recovery, 1, 0))))
    source.commit()
    archived['proof'] = event_proof(source, 1)
    events[0]['body_sha256'] = archived['proof']['events'][0]['sha256']
    result = delete_confirmed_slot(source, exporter, archived, tape, events, identity, now,
                                   guard=lambda: True, lease_alive=lambda: True)
    assert result == {'status': 'DEFERRED_RECOVERY_REFERENCE'}
    assert source.execute('SELECT COUNT(*) FROM llm_scenario_review_events').fetchone() == (2,)


def test_changed_export_anchor_never_deletes():
    from utils.btc_review_event_proof import delete_confirmed_slot
    source, exporter, archived, tape, events, identity, now = deletion_fixture()
    source.execute('UPDATE llm_scenario_review_events SET body=? WHERE slot=2', ('{"kind":"CHANGED"}',))
    source.commit()
    with pytest.raises(ValueError, match='event_delete_export_anchor_changed'):
        delete_confirmed_slot(source, exporter, archived, tape, events, identity, now,
                              guard=lambda: True, lease_alive=lambda: True)
    assert source.execute('SELECT COUNT(*) FROM llm_scenario_review_tapes').fetchone() == (2,)
    assert source.execute('SELECT COUNT(*) FROM llm_scenario_review_events').fetchone() == (2,)


@pytest.mark.parametrize('damage', ['foreign_key', 'missing_anchor'])
def test_unknown_dependency_or_missing_anchor_never_deletes(damage):
    from utils.btc_review_event_proof import delete_confirmed_slot
    source, exporter, archived, tape, events, identity, now = deletion_fixture()
    if damage == 'foreign_key':
        source.execute('CREATE TABLE other_evidence(ref INTEGER REFERENCES llm_scenario_review_tapes(slot))')
    else:
        source.execute('DELETE FROM llm_scenario_review_events WHERE slot=2')
        exporter.execute("UPDATE anchors SET ref=NULL WHERE stream='llm_scenario_review_events'")
    source.commit()
    exporter.commit()
    with pytest.raises(ValueError, match='event_delete_schema_unsupported|event_delete_export_anchor_changed'):
        delete_confirmed_slot(source, exporter, archived, tape, events, identity, now,
                              guard=lambda: True, lease_alive=lambda: True)
    assert source.execute('SELECT slot FROM llm_scenario_review_tapes ORDER BY slot').fetchall() == [(1,), (2,)]
    assert source.execute('SELECT COUNT(*) FROM llm_scenario_review_events WHERE slot=1').fetchone() == (1,)


def test_event_delivery_lookup_is_read_only_and_bounded(monkeypatch):
    from types import SimpleNamespace
    from utils import btc_offsite_archive, btc_review_event_proof as proof_module
    proof = event_proof(fixture(), 1)
    identity = 'a' * 64
    rowid = proof['events'][0]['rowid']
    event_id = hashlib.sha256(f'{identity}:llm_scenario_review_events:{rowid}'.encode()).hexdigest()[:32]
    response = {'event_id': event_id, 'event_type': 'btc.research.review'}
    calls = []
    monkeypatch.setattr(btc_offsite_archive, 'backend_host', lambda _: 'fixture.local')
    def execute(command, **kwargs):
        calls.append(command)
        assert event_id in kwargs['input'] and 'readonly' in kwargs['input'] and 'LIMIT 257' in kwargs['input']
        return SimpleNamespace(stdout=json.dumps(response) + '\n')
    monkeypatch.setattr(proof_module.subprocess, 'run', execute)
    assert proof_module.fetch_event_deliveries(proof, identity) == [response]
    assert len(calls) == 1 and calls[0][0] == '/usr/bin/ssh'
    with pytest.raises(ValueError, match='event_delivery_query_invalid'):
        proof_module.fetch_event_deliveries({'events': [{'rowid': '1 OR 1=1'}]}, identity)


def test_cleanup_links_proof_receipts_and_lease_before_deletion(tmp_path, monkeypatch):
    from contextlib import contextmanager
    from utils import btc_review_event_proof as proof_module, btc_review_retention as retention
    source, exporter, archived, tape, events, identity, now = deletion_fixture()
    source_path, exporter_path = tmp_path / 'source.sqlite', tmp_path / 'export.sqlite'
    with sqlite3.connect(source_path) as target:
        source.backup(target)
    with sqlite3.connect(exporter_path) as target:
        exporter.backup(target)
    started = now
    base = {'version': 1, 'as_of': int(started), 'cutoff_slot': int((started - 7 * 86400) // 300),
            'after_slot': -1, 'last_scanned_slot': 2, 'scanned': 2, 'truncated': False,
            'candidates': [archived['candidate']]}
    index = {'version': 2, 'cutoff_slot': base['cutoff_slot'], 'after_slot': -1, 'last_scanned_slot': 2,
             'truncated': False, 'items': [archived]}
    manifest = json.dumps({'source_identity_sha256': identity, 'started_at': started, 'completed_at': started + 1,
                           'raw_sha256': 'b' * 64, 'compressed_sha256': 'c' * 64, 'btc_retention_index': base,
                           'btc_review_event_proof_status': 'CAPTURED', 'btc_review_event_proof_index': index}).encode()
    ack = {'status': 'VERIFIED', 'retention_index_verified': True, 'review_event_proof_index_verified': True,
           'archive': 'archive-' + 'd' * 32, 'raw_sha256': 'b' * 64, 'compressed_sha256': 'c' * 64,
           'manifest_sha256': hashlib.sha256(manifest).hexdigest()}
    monkeypatch.setattr(retention, 'SOURCE', source_path)
    monkeypatch.setattr(retention, 'EXPORT_STATE', exporter_path)
    monkeypatch.setattr(retention, '_backup_evidence', lambda _: (tmp_path, identity, manifest, ack, base))
    monkeypatch.setattr(retention, 'fetch_deliveries', lambda *_: tape)
    monkeypatch.setattr(proof_module, 'fetch_event_deliveries', lambda *_: events)
    monkeypatch.setattr(retention, '_resource_guard', lambda *_: True)
    monkeypatch.setattr(retention, '_runtime_safe', lambda *_: True)
    @contextmanager
    def locked(actual_ack, *, require_event_proof):
        assert actual_ack == ack and require_event_proof is True
        yield lambda: True
    monkeypatch.setattr(retention, 'locked_remote_archive', locked)
    result = proof_module.cleanup_events(tmp_path, now=now)
    assert result['status'] == 'COMPLETE' and result['removed_slots'] == result['removed_events'] == 1
    with sqlite3.connect(source_path) as check:
        assert check.execute('SELECT slot FROM llm_scenario_review_tapes').fetchall() == [(2,)]
        assert check.execute('SELECT slot FROM llm_scenario_review_events').fetchall() == [(2,)]
        assert check.execute('SELECT COUNT(*) FROM llm_scenario_decisions').fetchone() == (2,)
