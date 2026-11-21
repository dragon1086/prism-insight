"""The cleanup gate reads actual BTC incident and position state."""
import json
import sqlite3

from utils.btc_review_retention import _runtime_safe


def test_actual_runtime_guard_rejects_active_or_pending_but_preserves_incident():
    with sqlite3.connect(':memory:') as conn:
        conn.executescript('CREATE TABLE llm_scenario_state(id INTEGER PRIMARY KEY,body TEXT);'
                           'CREATE TABLE llm_scenario_intents(id TEXT,status TEXT);'
                           'CREATE TABLE llm_scenario_broker_evidence(kind TEXT,captured_at REAL,body TEXT);')
        assert not _runtime_safe(conn)
        conn.execute('INSERT INTO llm_scenario_state VALUES(1,?)', (json.dumps({'active': None}),))
        assert _runtime_safe(conn)
        conn.execute('UPDATE llm_scenario_state SET body=? WHERE id=1', (json.dumps({'active': {'scenario_id': 'open'}}),))
        assert not _runtime_safe(conn)
        conn.execute('UPDATE llm_scenario_state SET body=? WHERE id=1', (json.dumps({'active': None}),))
        conn.execute('INSERT INTO llm_scenario_intents VALUES(?,?)', ('pending', 'PENDING'))
        assert not _runtime_safe(conn)
        conn.execute("UPDATE llm_scenario_intents SET status='TERMINAL'")
        assert _runtime_safe(conn)
        conn.execute('INSERT INTO llm_scenario_broker_evidence VALUES(?,?,?)',
                     ('incident_epoch_halt_v1', 1, json.dumps({'reason': 'historical_evidence_changed'})))
        assert _runtime_safe(conn)
        assert conn.execute('SELECT COUNT(*) FROM llm_scenario_broker_evidence').fetchone()[0] == 1
