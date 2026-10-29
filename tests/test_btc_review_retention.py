import hashlib
import json
import sqlite3

import pytest

from utils.btc_review_retention import candidates, fetch_deliveries, matching_evidence, snapshot_index, verified_archive_index


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
