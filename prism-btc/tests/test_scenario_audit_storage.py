import json
from pathlib import Path
import sqlite3
import sys

import pytest

sys.path.append(str(Path(__file__).resolve().parents[2]))
from analysis.scenario_replay import _runtime_event_hash  # noqa: E402
from utils.btc_audit_references import canonical, compact, sha  # noqa: E402


def test_audit_reference_preserves_replay_hash_and_detects_missing_source():
    snapshot, context = {'payload': 'x' * 5000}, {'input_id': 'fixture', 'payload': 'y' * 5000}
    event = {'snapshot': snapshot, 'context': context, 'input_id': 'fixture',
             'snapshot_hash': sha(canonical(snapshot)), 'context_hash': sha(canonical(context))}
    raw = canonical(event)
    s, c = json.dumps(snapshot, sort_keys=True), json.dumps(context, sort_keys=True)
    conn = sqlite3.connect(':memory:')
    conn.execute('CREATE TABLE llm_scenario_decisions(slot INTEGER,snapshot TEXT,context TEXT)')
    conn.execute('CREATE TABLE llm_scenario_audit_events(kind TEXT,decision_slot INTEGER,body TEXT)')
    conn.execute('INSERT INTO llm_scenario_decisions VALUES(?,?,?)', (1, s, c))
    conn.execute('INSERT INTO llm_scenario_audit_events VALUES(?,?,?)', ('decision_input', 1, raw))
    before = _runtime_event_hash(conn)
    conn.execute('UPDATE llm_scenario_audit_events SET body=?', (compact(raw, s, c),))
    assert _runtime_event_hash(conn) == before
    conn.execute('DELETE FROM llm_scenario_decisions')
    with pytest.raises(ValueError):
        _runtime_event_hash(conn)
