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
    conn.execute('INSERT INTO llm_scenario_recovery_events VALUES(?,?,?,?)', ('x' * 129, 1, 'OTHER', '{}'))
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


def test_snapshot_index_is_bounded_and_does_not_skip_candidates(monkeypatch):
    conn = sqlite3.connect(':memory:')
    conn.executescript('CREATE TABLE llm_scenario_review_tapes(slot INTEGER PRIMARY KEY,body TEXT);'
                       'CREATE TABLE llm_scenario_decisions(slot INTEGER PRIMARY KEY,snapshot TEXT,context TEXT,proposal TEXT,outcome TEXT);'
                       'CREATE TABLE llm_scenario_review_events(slot INTEGER,sequence INTEGER,body);'
                       'CREATE TABLE llm_scenario_recovery_events(event_id TEXT,slot INTEGER,kind TEXT,body);')
    context = json.dumps({'scenario_id': None, 'positions': [], 'pending_entries': [], 'unknown_intents': False,
                          'historical_unsettled': [], 'incident_epoch_blocked': False, 'recovery_observing': False})
    noop = {'schema_version': 1, 'action': 'WAIT', 'entries': [], 'take_profits': [],
            'partial_stops': [], 'cancel_entry_ids': []}
    for slot in range(1, 402):
        conn.execute('INSERT INTO llm_scenario_review_tapes VALUES(?,?)', (slot, '{}'))
        conn.execute('INSERT INTO llm_scenario_decisions VALUES(?,?,?,?,?)', (slot, '{}', context, json.dumps(noop), '{"status":"wait"}'))
        conn.execute('INSERT INTO llm_scenario_review_events VALUES(?,?,?)', (slot, 0, '{"kind":"MODEL_RAW"}'))
    conn.execute('UPDATE llm_scenario_review_events SET body=? WHERE slot=2', ('not-json',))
    conn.execute('UPDATE llm_scenario_decisions SET proposal=? WHERE slot=3',
                 (json.dumps(dict(noop, cancel_entry_ids=['entry'])),))
    conn.commit()
    now = 7 * 86400 + 1000 * 300
    with pytest.raises(ValueError, match='proof_snapshot_transaction_required'):
        snapshot_event_index(conn, now)
    conn.execute('BEGIN')
    first = snapshot_event_index(conn, now)
    assert len(first['items']) == 400 and first['truncated'] and first['last_scanned_slot'] == 400
    assert first['items'][1]['proof'] == {'slot': 2, 'unavailable': True}
    assert first['items'][2]['proof'] == {'slot': 3, 'unavailable': True}
    assert first['items'][3]['proof']['events']
    second = snapshot_event_index(conn, now, after_slot=first['last_scanned_slot'])
    assert [item['candidate']['slot'] for item in second['items']] == [401]
    assert not second['truncated']
    from utils import btc_review_event_proof as proof_module
    monkeypatch.setattr(proof_module, 'MAX_INDEX_BYTES', 2500)
    bounded = snapshot_event_index(conn, now)
    assert bounded['truncated'] and 0 < len(bounded['items']) < 400
    assert sum(len(json.dumps(item, sort_keys=True, separators=(',', ':')).encode()) for item in bounded['items']) <= 2500
    next_page = snapshot_event_index(conn, now, after_slot=bounded['last_scanned_slot'])
    assert next_page['items'][0]['candidate']['slot'] == bounded['last_scanned_slot'] + 1
    monkeypatch.setattr(proof_module, 'MAX_INDEX_BYTES', 700_000)
    def worst_case(_conn, slot):
        return {'slot': slot,
                'events': [{'rowid': slot * 1000 + seq, 'sequence': seq, 'sha256': 'f' * 64} for seq in range(128)],
                'recovery': [{'event_id': str(i).zfill(128), 'kind': 'x' * 80, 'sha256': 'e' * 64} for i in range(16)]}
    monkeypatch.setattr(proof_module, 'event_proof', worst_case)
    dense = snapshot_event_index(conn, now)
    assert dense['truncated'] and 0 < len(dense['items']) < 400
    assert sum(len(json.dumps(item, sort_keys=True, separators=(',', ':')).encode()) for item in dense['items']) <= 700_000
    base = [{'slot': n, 'rowid': n, 'tape_sha256': 'a' * 64, 'decision_sha256': 'b' * 64}
            for n in range(1, 2001)]
    manifest = {'btc_review_event_proof_index': dense, 'btc_retention_index': {'candidates': base}}
    assert len(json.dumps(manifest, indent=2).encode()) < 4 * 1024 * 1024
    resumed = snapshot_event_index(conn, now, after_slot=dense['last_scanned_slot'])
    assert resumed['items'][0]['candidate']['slot'] == dense['last_scanned_slot'] + 1
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
    plan = json.dumps({'schema_version': 1, 'action': 'WAIT', 'side': None, 'hard_stop': None, 'chase': None,
                       'entries': [], 'take_profits': [], 'partial_stops': [], 'cancel_entry_ids': []})
    for slot in (1, 2):
        source.execute('INSERT INTO llm_scenario_review_tapes VALUES(?,?)', (slot, '{}'))
        source.execute('INSERT INTO llm_scenario_decisions VALUES(?,?,?,?,?)', (slot, '{}', context, plan, '{"status":"wait"}'))
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


def test_recovery_reference_is_preserved_before_event_deletion():
    from utils.btc_review_event_proof import delete_confirmed_slot
    from observability.btc_scenario_export import _anchor_hash
    from observability.review_storage import review_text
    from utils.btc_recovery_references import read_recovery
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
    assert result == {'status': 'DEFERRED_RECOVERY_EXPORT'}
    assert source.execute('SELECT COUNT(*) FROM llm_scenario_review_events').fetchone() == (2,)
    stored = source.execute('SELECT body FROM llm_scenario_recovery_events WHERE event_id=?', ('e',)).fetchone()[0]
    anchor = _anchor_hash('llm_scenario_recovery_events', {'slot': 1, 'kind': 'MODEL_REQUEST', 'body': stored}, source)
    exporter.execute('INSERT INTO cursors VALUES(?,?)', ('llm_scenario_recovery_events', 1))
    exporter.execute('INSERT INTO anchors VALUES(?,?)', ('llm_scenario_recovery_events', anchor))
    exporter.commit()
    exporter.execute('UPDATE anchors SET ref=? WHERE stream=?', ('0' * 64, 'llm_scenario_recovery_events'))
    exporter.commit()
    with pytest.raises(ValueError, match='event_delete_export_anchor_changed'):
        delete_confirmed_slot(source, exporter, archived, tape, events, identity, now,
                              guard=lambda: True, lease_alive=lambda: True)
    assert source.execute('SELECT body FROM llm_scenario_recovery_events WHERE event_id=?', ('e',)).fetchone() == (stored,)
    assert source.execute('SELECT COUNT(*) FROM llm_scenario_review_events').fetchone() == (2,)
    exporter.execute('UPDATE anchors SET ref=? WHERE stream=?', (anchor, 'llm_scenario_recovery_events'))
    exporter.commit()
    result = delete_confirmed_slot(source, exporter, archived, tape, events, identity, now,
                                   guard=lambda: True, lease_alive=lambda: True)
    assert result == {'status': 'REMOVED', 'events': 0}
    preserved = source.execute('SELECT body FROM llm_scenario_recovery_events WHERE event_id=?', ('e',)).fetchone()[0]
    assert preserved == stored
    assert read_recovery(preserved, source, 1, decode_legacy=review_text, expected_kind='MODEL_REQUEST') == recovery
    assert _anchor_hash('llm_scenario_recovery_events', {'slot': 1, 'kind': 'MODEL_REQUEST', 'body': preserved}, source) == anchor
    assert source.execute('SELECT COUNT(*) FROM llm_scenario_review_events WHERE slot=1').fetchone() == (1,)


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
        assert event_id in kwargs['input'] and 'readonly' in kwargs['input'] and 'LIMIT 1025' in kwargs['input']
        return SimpleNamespace(stdout=json.dumps(response) + '\n')
    monkeypatch.setattr(proof_module.subprocess, 'run', execute)
    assert proof_module.fetch_event_deliveries(proof, identity) == [response]
    assert len(calls) == 1 and calls[0][0] == '/usr/bin/ssh'
    with pytest.raises(ValueError, match='event_delivery_query_invalid'):
        proof_module.fetch_event_deliveries({'events': [{'rowid': '1 OR 1=1'}]}, identity)


@pytest.mark.parametrize('delivered,cursor_changed,unavailable', [(True, False, False), (False, False, False),
                                                                  (True, True, False), (False, False, True)])
def test_cleanup_links_proof_receipts_and_lease_before_deletion(tmp_path, monkeypatch, delivered, cursor_changed, unavailable):
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
    if unavailable:
        archived['proof'] = {'slot': 1, 'unavailable': True}
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
    monkeypatch.setattr(proof_module, 'fetch_event_deliveries', lambda *_: events if delivered else [])
    monkeypatch.setattr(retention, '_resource_guard', lambda *_: True)
    monkeypatch.setattr(retention, '_runtime_safe', lambda *_: True)
    saved = []
    cursor_values = iter((-1, 99 if cursor_changed else -1))
    monkeypatch.setattr(retention, 'read_cursor', lambda *_: next(cursor_values))
    monkeypatch.setattr(retention, '_save_cursor', lambda current, source_identity: saved.append((current, source_identity)))
    @contextmanager
    def locked(actual_ack, *, require_event_proof):
        assert not unavailable
        assert actual_ack == ack and require_event_proof is True
        yield lambda: True
    monkeypatch.setattr(retention, 'locked_remote_archive', locked)
    result = proof_module.cleanup_events(tmp_path, now=now)
    assert result['status'] == ('PARTIAL' if cursor_changed else 'COMPLETE' if delivered else 'DEFERRED')
    assert result['removed_slots'] == result['removed_events'] == int(delivered)
    if unavailable:
        assert result['outcomes'] == {'DEFERRED_EVENT_PROOF': 1}
    assert saved == ([] if cursor_changed else [(index, identity)])
    with sqlite3.connect(source_path) as check:
        expected = [(2,)] if delivered else [(1,), (2,)]
        assert check.execute('SELECT slot FROM llm_scenario_review_tapes').fetchall() == expected
        assert check.execute('SELECT slot FROM llm_scenario_review_events').fetchall() == expected
        assert check.execute('SELECT COUNT(*) FROM llm_scenario_decisions').fetchone() == (2,)


def test_recovery_source_retention_rolls_back_when_lease_is_lost():
    from observability.btc_scenario_export import _anchor_hash
    from utils import btc_review_event_proof as proof_module
    source, exporter, archived, tape, events, identity, now = deletion_fixture()
    review = canonical({'kind': 'MODEL_REQUEST', 'prompt': 'x' * 3000})
    recovery = canonical({'kind': 'MODEL_REQUEST', 'prompt': 'x' * 3000, 'calculated_review_version': 1,
                          'input_id': 'fixture'})
    encoded = pack(compact(review, recovery, 1, 0))
    source.execute('UPDATE llm_scenario_review_events SET body=? WHERE slot=1', (review,))
    source.execute('INSERT INTO llm_scenario_recovery_events VALUES(?,?,?,?)', ('e', 1, 'MODEL_REQUEST', encoded))
    source.commit()
    archived['proof'] = event_proof(source, 1)
    events[0]['body_sha256'] = archived['proof']['events'][0]['sha256']
    anchor = _anchor_hash('llm_scenario_recovery_events', {'slot': 1, 'kind': 'MODEL_REQUEST', 'body': encoded}, source)
    exporter.execute('INSERT INTO cursors VALUES(?,?)', ('llm_scenario_recovery_events', 1))
    exporter.execute('INSERT INTO anchors VALUES(?,?)', ('llm_scenario_recovery_events', anchor))
    exporter.commit()
    checks = iter((True, True, False))
    with pytest.raises(ValueError, match='event_delete_guard_failed'):
        proof_module.delete_confirmed_slot(source, exporter, archived, tape, events, identity, now,
                                           guard=lambda: True, lease_alive=lambda: next(checks))
    assert source.execute('SELECT body FROM llm_scenario_recovery_events WHERE event_id=?', ('e',)).fetchone() == (encoded,)
    assert source.execute('SELECT COUNT(*) FROM llm_scenario_review_events').fetchone() == (2,)
    assert source.execute('SELECT COUNT(*) FROM llm_scenario_review_tapes').fetchone() == (2,)


def test_recovery_sources_are_retained_without_duplicating_them():
    import gzip
    from observability.btc_scenario_export import _anchor_hash
    from observability.review_storage import MAGIC
    from utils import btc_review_event_proof as proof_module
    source, exporter, archived, tape, events, identity, now = deletion_fixture()
    prompt = ''.join(hashlib.sha256(str(i).encode()).hexdigest() for i in range(300))
    review = canonical({'kind': 'MODEL_REQUEST', 'prompt': prompt})
    recovery = canonical({'kind': 'MODEL_REQUEST', 'prompt': prompt, 'calculated_review_version': 1,
                          'input_id': 'fixture'})
    event_blob = MAGIC + gzip.compress(review.encode(), compresslevel=1, mtime=0)
    ref = pack(compact(review, recovery, 1, 0))
    source.execute('UPDATE llm_scenario_review_events SET body=? WHERE slot=1', (event_blob,))
    source.executemany('INSERT INTO llm_scenario_recovery_events VALUES(?,?,?,?)',
                       [('e1', 1, 'MODEL_REQUEST', ref), ('e2', 1, 'MODEL_REQUEST', ref)])
    source.commit()
    archived['proof'] = event_proof(source, 1)
    events[0]['body_sha256'] = archived['proof']['events'][0]['sha256']
    anchor = _anchor_hash('llm_scenario_recovery_events', {'slot': 1, 'kind': 'MODEL_REQUEST', 'body': ref}, source)
    exporter.execute('INSERT INTO cursors VALUES(?,?)', ('llm_scenario_recovery_events', 2))
    exporter.execute('INSERT INTO anchors VALUES(?,?)', ('llm_scenario_recovery_events', anchor))
    exporter.commit()
    result = proof_module.delete_confirmed_slot(source, exporter, archived, tape, events, identity, now,
                                                 guard=lambda: True, lease_alive=lambda: True)
    assert result == {'status': 'REMOVED', 'events': 0}
    assert source.execute('SELECT body FROM llm_scenario_review_events WHERE slot=1').fetchone() == (event_blob,)
    assert source.execute('SELECT body FROM llm_scenario_recovery_events ORDER BY event_id').fetchall() == [(ref,), (ref,)]
    assert source.execute('SELECT COUNT(*) FROM llm_scenario_review_tapes').fetchone() == (1,)


def test_recovery_keeps_transitive_event_source_and_deletes_unreferenced_event():
    from observability.btc_scenario_export import _anchor_hash
    from observability.review_storage import review_text
    from utils.btc_review_event_proof import delete_confirmed_slot
    from utils.btc_review_event_references import MAGIC as EVENT_MAGIC, compact as compact_event
    from utils.btc_recovery_references import read_recovery
    source, exporter, archived, tape, _, identity, now = deletion_fixture()
    prompt = ''.join(hashlib.sha256(str(i).encode()).hexdigest() for i in range(300))
    request = {'prompt': prompt, 'timeout': 30}
    request_text = json.dumps({'kind': 'MODEL_REQUEST', 'phase': 'candidate', 'request': request}, sort_keys=True)
    raw_text = json.dumps({'kind': 'MODEL_RAW', 'phase': 'candidate', 'actual_request': request, 'raw': 'ok'}, sort_keys=True)
    raw_ref = compact_event(raw_text, [(0, request_text)], 1, 1)
    assert isinstance(raw_ref, bytes) and raw_ref.startswith(EVENT_MAGIC)
    recovery_text = canonical(dict(json.loads(raw_text), calculated_review_version=1, input_id='fixture'))
    recovery_ref = pack(compact(raw_text, recovery_text, 1, 1))
    source.execute('UPDATE llm_scenario_review_events SET rowid=5 WHERE slot=2')
    source.execute('UPDATE llm_scenario_review_events SET body=? WHERE slot=1 AND sequence=0', (request_text,))
    source.execute('INSERT INTO llm_scenario_review_events(rowid,slot,sequence,body) VALUES(?,?,?,?)', (3, 1, 1, raw_ref))
    source.execute('INSERT INTO llm_scenario_review_events(rowid,slot,sequence,body) VALUES(?,?,?,?)', (4, 1, 2, '{"kind":"RESEARCH_RESULT"}'))
    source.execute('INSERT INTO llm_scenario_recovery_events VALUES(?,?,?,?)', ('e', 1, 'MODEL_RAW', recovery_ref))
    source.commit()
    exporter.execute('UPDATE cursors SET seq=5 WHERE stream=?', ('llm_scenario_review_events',))
    anchor = _anchor_hash('llm_scenario_recovery_events', {'slot': 1, 'kind': 'MODEL_RAW', 'body': recovery_ref}, source)
    exporter.execute('INSERT INTO cursors VALUES(?,?)', ('llm_scenario_recovery_events', 1))
    exporter.execute('INSERT INTO anchors VALUES(?,?)', ('llm_scenario_recovery_events', anchor))
    exporter.commit()
    archived['proof'] = event_proof(source, 1)
    deliveries = []
    for event in archived['proof']['events']:
        event_id = hashlib.sha256(f'{identity}:llm_scenario_review_events:{event["rowid"]}'.encode()).hexdigest()[:32]
        deliveries.append({'event_id': event_id, 'event_type': 'btc.research.review', 'source_identity': identity,
                           'body_sha256': event['sha256'], 'source_rowid': event['rowid'],
                           'source_sequence': event['sequence'], 'decision_slot': 1})
    result = delete_confirmed_slot(source, exporter, archived, tape, deliveries, identity, now,
                                   guard=lambda: True, lease_alive=lambda: True)
    assert result == {'status': 'REMOVED', 'events': 1}
    assert source.execute('SELECT sequence FROM llm_scenario_review_events WHERE slot=1 ORDER BY sequence').fetchall() == [(0,), (1,)]
    stored = source.execute('SELECT body FROM llm_scenario_recovery_events WHERE event_id=?', ('e',)).fetchone()[0]
    assert stored == recovery_ref
    assert read_recovery(stored, source, 1, decode_legacy=review_text, expected_kind='MODEL_RAW') == recovery_text


def test_noncontiguous_recovery_source_survives_future_archive_scan(tmp_path):
    from observability.btc_scenario_export import _anchor_hash
    from observability.review_storage import review_text
    from utils import btc_offsite_archive
    from utils.btc_review_event_proof import delete_confirmed_slot
    from utils.btc_recovery_references import read_recovery
    source, exporter, archived, tape, _, identity, now = deletion_fixture()
    review = canonical({'kind': 'MODEL_REQUEST', 'source_commit': 'c' * 40, 'prompt': 'x' * 3000})
    recovery = canonical(dict(json.loads(review), calculated_review_version=1, input_id='fixture'))
    ref = pack(compact(review, recovery, 1, 2))
    source.execute('UPDATE llm_scenario_review_events SET rowid=5 WHERE slot=2')
    source.execute('INSERT INTO llm_scenario_review_events(rowid,slot,sequence,body) VALUES(?,?,?,?)',
                   (3, 1, 1, '{"kind":"RESEARCH_RESULT"}'))
    source.execute('INSERT INTO llm_scenario_review_events(rowid,slot,sequence,body) VALUES(?,?,?,?)', (4, 1, 2, review))
    source.execute('INSERT INTO llm_scenario_recovery_events VALUES(?,?,?,?)', ('e', 1, 'MODEL_REQUEST', ref))
    source.commit()
    exporter.execute('UPDATE cursors SET seq=5 WHERE stream=?', ('llm_scenario_review_events',))
    anchor = _anchor_hash('llm_scenario_recovery_events', {'slot': 1, 'kind': 'MODEL_REQUEST', 'body': ref}, source)
    exporter.execute('INSERT INTO cursors VALUES(?,?)', ('llm_scenario_recovery_events', 1))
    exporter.execute('INSERT INTO anchors VALUES(?,?)', ('llm_scenario_recovery_events', anchor))
    exporter.commit()
    archived['proof'] = event_proof(source, 1)
    deliveries = []
    for event in archived['proof']['events']:
        event_id = hashlib.sha256(f'{identity}:llm_scenario_review_events:{event["rowid"]}'.encode()).hexdigest()[:32]
        deliveries.append({'event_id': event_id, 'event_type': 'btc.research.review', 'source_identity': identity,
                           'body_sha256': event['sha256'], 'source_rowid': event['rowid'],
                           'source_sequence': event['sequence'], 'decision_slot': 1})
    assert delete_confirmed_slot(source, exporter, archived, tape, deliveries, identity, now,
                                 guard=lambda: True, lease_alive=lambda: True) == {'status': 'REMOVED', 'events': 2}
    assert source.execute('SELECT sequence FROM llm_scenario_review_events WHERE slot=1').fetchall() == [(2,)]
    stored = source.execute('SELECT body FROM llm_scenario_recovery_events WHERE event_id="e"').fetchone()[0]
    assert read_recovery(stored, source, 1, decode_legacy=review_text, expected_kind='MODEL_REQUEST') == recovery
    path = tmp_path / 'small.sqlite'
    with sqlite3.connect(path) as snapshot:
        source.backup(snapshot)
    commits, coverage = btc_offsite_archive.referenced_commits(path)
    assert 'c' * 40 in commits and coverage['malformed_evidence_rows'] == 0


def test_cleanup_batches_delivery_queries_without_mixing_slots(tmp_path, monkeypatch):
    from contextlib import contextmanager
    from utils import btc_review_event_proof as proof_module, btc_review_retention as retention
    identity = 'a' * 64
    items = []
    for slot in (1, 2):
        rows = [{'rowid': (slot - 1) * 70 + i + 1, 'sequence': i, 'sha256': 'f' * 64} for i in range(70)]
        items.append({'candidate': {'slot': slot, 'rowid': slot}, 'proof': {'slot': slot, 'events': rows, 'recovery': []}})
    index = {'after_slot': -1, 'last_scanned_slot': 2, 'truncated': False, 'items': items}
    source_path, exporter_path = tmp_path / 'source.sqlite', tmp_path / 'export.sqlite'
    source_path.touch(); exporter_path.touch()
    monkeypatch.setattr(retention, 'SOURCE', source_path)
    monkeypatch.setattr(retention, 'EXPORT_STATE', exporter_path)
    monkeypatch.setattr(retention, '_backup_evidence', lambda _: (tmp_path, identity, b'ignored', {}, {}))
    monkeypatch.setattr(proof_module, 'verified_event_index', lambda *_: index)
    monkeypatch.setattr(retention, 'read_cursor', lambda *_: -1)
    monkeypatch.setattr(retention, '_save_cursor', lambda *_: None)
    monkeypatch.setattr(retention, '_resource_guard', lambda *_: True)
    monkeypatch.setattr(retention, '_runtime_safe', lambda *_: True)
    calls = []
    def tapes(rows, _identity):
        calls.append(('tape', len(rows)))
        return []
    def events(proof, source_identity):
        calls.append(('events', len(proof['events'])))
        return [{'event_id': hashlib.sha256(f'{source_identity}:llm_scenario_review_events:{event["rowid"]}'.encode()).hexdigest()[:32]}
                for event in proof['events']]
    def delete(_conn, _export, item, _tapes, deliveries, source_identity, _now, **_kwargs):
        expected = {hashlib.sha256(f'{source_identity}:llm_scenario_review_events:{event["rowid"]}'.encode()).hexdigest()[:32]
                    for event in item['proof']['events']}
        assert {row['event_id'] for row in deliveries} == expected
        return {'status': 'REMOVED', 'events': len(deliveries)}
    @contextmanager
    def locked(_ack, *, require_event_proof):
        assert require_event_proof
        yield lambda: True
    monkeypatch.setattr(retention, 'fetch_deliveries', tapes)
    monkeypatch.setattr(proof_module, 'fetch_event_deliveries', events)
    monkeypatch.setattr(proof_module, 'delete_confirmed_slot', delete)
    monkeypatch.setattr(retention, 'locked_remote_archive', locked)
    result = proof_module.cleanup_events(tmp_path, now=700000)
    assert result['status'] == 'COMPLETE' and result['removed_events'] == 140
    assert calls == [('tape', 2), ('events', 140)]


@pytest.mark.parametrize('change', ['cancel', 'renewal', 'missing_entries', 'stop', 'side', 'missing_version'])
def test_wait_with_order_changes_or_missing_fields_is_not_deleted(change):
    from utils.btc_review_event_proof import delete_confirmed_slot
    from utils.btc_review_retention import candidates
    source, exporter, archived, tape, events, identity, now = deletion_fixture()
    plan = json.loads(source.execute('SELECT proposal FROM llm_scenario_decisions WHERE slot=1').fetchone()[0])
    if change == 'cancel':
        plan['cancel_entry_ids'] = ['old-entry']
    elif change == 'renewal':
        plan['pending_renewal'] = {'offer_id': 'renew'}
    elif change == 'missing_entries':
        del plan['entries']
    elif change == 'stop':
        plan['stop_revision'] = {'reason': 'changed'}
    elif change == 'missing_version':
        del plan['schema_version']
    else:
        plan['side'] = 'LONG'
    source.execute('UPDATE llm_scenario_decisions SET proposal=? WHERE slot=1', (json.dumps(plan),))
    source.commit()
    archived['candidate'] = candidates(source, now)['candidates'][0]
    result = delete_confirmed_slot(source, exporter, archived, tape, events, identity, now,
                                   guard=lambda: True, lease_alive=lambda: True)
    assert result == {'status': 'DEFERRED_NONNOOP_PLAN'}
    assert source.execute('SELECT COUNT(*) FROM llm_scenario_review_tapes').fetchone() == (2,)
    assert source.execute('SELECT COUNT(*) FROM llm_scenario_review_events').fetchone() == (2,)


def test_normalized_v1_wait_can_omit_fields_removed_by_wire_validator():
    from utils.btc_review_event_proof import delete_confirmed_slot
    from utils.btc_review_retention import candidates
    source, exporter, archived, tape, events, identity, now = deletion_fixture()
    plan = json.loads(source.execute('SELECT proposal FROM llm_scenario_decisions WHERE slot=1').fetchone()[0])
    for key in ('side', 'hard_stop', 'chase'):
        del plan[key]
    source.execute('UPDATE llm_scenario_decisions SET proposal=? WHERE slot=1', (json.dumps(plan),))
    source.commit()
    archived['candidate'] = candidates(source, now)['candidates'][0]
    result = delete_confirmed_slot(source, exporter, archived, tape, events, identity, now,
                                   guard=lambda: True, lease_alive=lambda: True)
    assert result == {'status': 'REMOVED', 'events': 1}


@pytest.mark.parametrize('slots,event_count,expected_slots,expected_calls,expected_status', [
    (288, 5, 288, 6, 'COMPLETE'),
    (190, 128, 184, 48, 'BUDGET_LIMIT'),
    (190, 128, 0, 48, 'ERROR'),
])
def test_daily_cleanup_capacity_and_query_cap(tmp_path, monkeypatch, slots, event_count, expected_slots,
                                              expected_calls, expected_status):
    from contextlib import contextmanager
    from utils import btc_review_event_proof as proof_module, btc_review_retention as retention
    identity = 'a' * 64
    items = []
    for slot in range(1, slots + 1):
        events = [{'rowid': (slot - 1) * event_count + i + 1, 'sequence': i, 'sha256': 'f' * 64}
                  for i in range(event_count)]
        items.append({'candidate': {'slot': slot, 'rowid': slot},
                      'proof': {'slot': slot, 'events': events, 'recovery': []}})
    index = {'after_slot': -1, 'last_scanned_slot': slots, 'truncated': False, 'items': items}
    source_path, exporter_path = tmp_path / 'source.sqlite', tmp_path / 'export.sqlite'
    source_path.touch(); exporter_path.touch()
    monkeypatch.setattr(retention, 'SOURCE', source_path)
    monkeypatch.setattr(retention, 'EXPORT_STATE', exporter_path)
    monkeypatch.setattr(retention, '_backup_evidence', lambda _: (tmp_path, identity, b'ignored', {}, {}))
    monkeypatch.setattr(proof_module, 'verified_event_index', lambda *_: index)
    monkeypatch.setattr(retention, 'read_cursor', lambda *_: -1)
    saved = []
    monkeypatch.setattr(retention, '_save_cursor', lambda current, _: saved.append(current))
    monkeypatch.setattr(retention, '_resource_guard', lambda *_: True)
    monkeypatch.setattr(retention, '_runtime_safe', lambda *_: True)
    calls = []
    def tapes(rows, _identity):
        calls.append(('tape', len(rows)))
        return []
    def events(proof, source_identity):
        calls.append(('events', len(proof['events'])))
        if expected_status == 'ERROR' and len(calls) == expected_calls:
            raise OSError('simulated delivery failure')
        return [{'event_id': hashlib.sha256(f'{source_identity}:llm_scenario_review_events:{event["rowid"]}'.encode()).hexdigest()[:32]}
                for event in proof['events']]
    def delete(_conn, _export, item, _tapes, deliveries, source_identity, _now, **_kwargs):
        assert expected_status != 'ERROR'
        assert len(deliveries) == len(item['proof']['events'])
        return {'status': 'REMOVED', 'events': len(deliveries)}
    @contextmanager
    def locked(_ack, *, require_event_proof):
        assert require_event_proof
        yield lambda: True
    monkeypatch.setattr(retention, 'fetch_deliveries', tapes)
    monkeypatch.setattr(proof_module, 'fetch_event_deliveries', events)
    monkeypatch.setattr(proof_module, 'delete_confirmed_slot', delete)
    monkeypatch.setattr(retention, 'locked_remote_archive', locked)
    if expected_status == 'ERROR':
        with pytest.raises(OSError, match='simulated delivery failure'):
            proof_module.cleanup_events(tmp_path, now=700000)
        assert len(calls) == expected_calls <= 48 and not saved
        return
    result = proof_module.cleanup_events(tmp_path, now=700000)
    assert result['status'] == expected_status and result['removed_slots'] == expected_slots
    assert len(calls) == expected_calls <= 48
    if expected_status == 'BUDGET_LIMIT':
        assert saved == [{'last_scanned_slot': expected_slots, 'truncated': True}]
    else:
        assert saved == [index]


def test_daily_capacity_uses_real_transactions_without_skipping_anchor(tmp_path, monkeypatch):
    from contextlib import contextmanager
    from observability.btc_scenario_export import _anchor_hash
    from utils import btc_review_event_proof as proof_module, btc_review_retention as retention
    source_path, export_path = tmp_path / 'source.sqlite', tmp_path / 'export.sqlite'
    identity, now = 'a' * 64, 7 * 86400 + 1000 * 300
    context = json.dumps({'scenario_id': None, 'positions': [], 'pending_entries': [], 'unknown_intents': False,
                          'historical_unsettled': [], 'incident_epoch_blocked': False, 'recovery_observing': False})
    plan = json.dumps({'schema_version': 1, 'action': 'WAIT', 'entries': [], 'take_profits': [],
                       'partial_stops': [], 'cancel_entry_ids': []})
    body = '{"kind":"MODEL_RAW"}'
    with sqlite3.connect(source_path) as conn:
        conn.executescript('CREATE TABLE llm_scenario_review_tapes(slot INTEGER PRIMARY KEY,body TEXT);'
                           'CREATE TABLE llm_scenario_decisions(slot INTEGER PRIMARY KEY,snapshot TEXT,context TEXT,proposal TEXT,outcome TEXT);'
                           'CREATE TABLE llm_scenario_review_events(slot INTEGER,sequence INTEGER,body TEXT,PRIMARY KEY(slot,sequence));'
                           'CREATE TABLE llm_scenario_recovery_events(event_id TEXT,slot INTEGER,kind TEXT,body);')
        conn.executemany('INSERT INTO llm_scenario_review_tapes VALUES(?,?)', [(slot, '{}') for slot in range(1, 290)])
        conn.executemany('INSERT INTO llm_scenario_decisions VALUES(?,?,?,?,?)',
                         [(slot, '{}', context, plan, '{"status":"wait"}') for slot in range(1, 290)])
        conn.executemany('INSERT INTO llm_scenario_review_events VALUES(?,?,?)',
                         [(slot, seq, body) for slot in range(1, 290) for seq in range(5)])
    with sqlite3.connect(source_path) as conn, sqlite3.connect(export_path) as exporter:
        exporter.executescript('CREATE TABLE meta(key PRIMARY KEY,value);CREATE TABLE cursors(stream PRIMARY KEY,seq);'
                               'CREATE TABLE anchors(stream PRIMARY KEY,ref);CREATE TABLE outbox(id PRIMARY KEY,body);')
        exporter.execute('INSERT INTO meta VALUES(?,?)', ('source_identity', identity))
        for stream, seq, anchor in (('llm_scenario_review_tapes', 289, {'slot': 289, 'body': '{}'}),
                                    ('llm_scenario_review_events', 1445, {'slot': 289, 'sequence': 4, 'body': body})):
            exporter.execute('INSERT INTO cursors VALUES(?,?)', (stream, seq))
            exporter.execute('INSERT INTO anchors VALUES(?,?)', (stream, _anchor_hash(stream, anchor, conn)))
        conn.execute('BEGIN')
        index = proof_module.snapshot_event_index(conn, now)
        assert len(index['items']) == 289 and not index['truncated']
        conn.rollback()
    monkeypatch.setattr(retention, 'SOURCE', source_path)
    monkeypatch.setattr(retention, 'EXPORT_STATE', export_path)
    monkeypatch.setattr(retention, '_backup_evidence', lambda _: (tmp_path, identity, b'ignored', {}, {}))
    monkeypatch.setattr(proof_module, 'verified_event_index', lambda *_: index)
    monkeypatch.setattr(retention, 'read_cursor', lambda *_: -1)
    monkeypatch.setattr(retention, '_save_cursor', lambda *_: None)
    monkeypatch.setattr(retention, '_resource_guard', lambda *_: True)
    monkeypatch.setattr(retention, '_runtime_safe', lambda *_: True)
    calls = []
    def tapes(rows, source_identity):
        calls.append('tape')
        return [{'event_id': hashlib.sha256(f'{source_identity}:llm_scenario_review_tapes:{row["rowid"]}'.encode()).hexdigest()[:32],
                 'event_type': 'btc.research.review_tape', 'source_identity': source_identity,
                 'body_sha256': row['tape_sha256'], 'tape_hash_status': 'MATCH'} for row in rows]
    def events(proof, source_identity):
        calls.append('events')
        return [{'event_id': hashlib.sha256(f'{source_identity}:llm_scenario_review_events:{row["rowid"]}'.encode()).hexdigest()[:32],
                 'event_type': 'btc.research.review', 'source_identity': source_identity, 'body_sha256': row['sha256'],
                 'source_rowid': row['rowid'], 'source_sequence': row['sequence'], 'decision_slot': (row['rowid'] - 1) // 5 + 1}
                for row in proof['events']]
    @contextmanager
    def locked(_ack, *, require_event_proof):
        assert require_event_proof
        yield lambda: True
    monkeypatch.setattr(retention, 'fetch_deliveries', tapes)
    monkeypatch.setattr(proof_module, 'fetch_event_deliveries', events)
    monkeypatch.setattr(retention, 'locked_remote_archive', locked)
    result = proof_module.cleanup_events(tmp_path, now=now)
    assert result['status'] == 'DEFERRED' and result['removed_slots'] == 288 and result['removed_events'] == 1440
    assert len(calls) == 6
    with sqlite3.connect(source_path) as conn:
        assert conn.execute('SELECT slot FROM llm_scenario_review_tapes').fetchall() == [(289,)]
        assert conn.execute('SELECT count(*) FROM llm_scenario_review_events').fetchone() == (5,)
        assert conn.execute('SELECT count(*) FROM llm_scenario_decisions').fetchone() == (289,)


def test_empty_scanned_page_is_not_reported_as_complete_when_more_remains(tmp_path, monkeypatch):
    from utils import btc_review_event_proof as proof_module, btc_review_retention as retention
    identity = 'a' * 64
    index = {'after_slot': -1, 'last_scanned_slot': 2000, 'truncated': True, 'items': []}
    saved = []
    monkeypatch.setattr(retention, '_backup_evidence', lambda _: (tmp_path, identity, b'ignored', {}, {}))
    monkeypatch.setattr(proof_module, 'verified_event_index', lambda *_: index)
    monkeypatch.setattr(retention, 'read_cursor', lambda *_: -1)
    monkeypatch.setattr(retention, '_save_cursor', lambda progress, _: saved.append(progress))
    result = proof_module.cleanup_events(tmp_path, now=700000)
    assert result['status'] == 'MORE_TO_SCAN' and result['more_to_scan'] is True
    assert saved == [index]
