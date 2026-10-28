import json
import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.append(str(Path(__file__).resolve().parents[2]))
from analysis.scenario_replay import _runtime_event_hash
from utils.btc_financial_references import compact


def test_replay_hash_is_independent_of_financial_encoding():
    execution = {'execId': 'trade-1', 'raw': 'x' * 4000}
    transaction = {'tradeId': 'trade-1', 'raw': 'y' * 4000}
    body = {'raw_executions': [execution], 'raw_transactions': [transaction], 'funding': [],
            'trades': [{'execution_id': 'trade-1', 'raw_execution': execution, 'raw_transaction': transaction}]}
    raw = json.dumps(body, sort_keys=True)
    conn = sqlite3.connect(':memory:')
    conn.execute('CREATE TABLE llm_scenario_broker_evidence(kind,captured_at,body)')
    conn.execute('INSERT INTO llm_scenario_broker_evidence VALUES(?,?,?)', ('financial', 1, raw))
    original = _runtime_event_hash(conn)
    encoded = compact(raw)
    assert encoded != raw
    conn.execute('UPDATE llm_scenario_broker_evidence SET body=?', (encoded,))
    assert _runtime_event_hash(conn) == original
    bad = json.loads(encoded)
    bad['body']['raw_executions'] = []
    conn.execute('UPDATE llm_scenario_broker_evidence SET body=?', (json.dumps(bad, sort_keys=True),))
    with pytest.raises(ValueError):
        _runtime_event_hash(conn)


def test_replay_leaves_other_evidence_unmodified():
    conn = sqlite3.connect(':memory:')
    conn.execute('CREATE TABLE llm_scenario_broker_evidence(kind,captured_at,body)')
    conn.execute('INSERT INTO llm_scenario_broker_evidence VALUES(?,?,?)',
                 ('account', 1, '{"$btc_financial_refs_v1": 1}'))
    assert isinstance(_runtime_event_hash(conn), str)
