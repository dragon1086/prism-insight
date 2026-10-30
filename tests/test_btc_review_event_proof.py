import json
import sqlite3

import pytest

from utils.btc_recovery_references import canonical, compact, pack
from utils.btc_review_event_proof import event_proof


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
