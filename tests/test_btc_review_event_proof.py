import json
import hashlib
from copy import deepcopy
import sqlite3

import pytest

from utils.btc_recovery_references import canonical, compact, pack
from utils.btc_review_event_proof import delivered_event_proof, event_proof, snapshot_event_index


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
