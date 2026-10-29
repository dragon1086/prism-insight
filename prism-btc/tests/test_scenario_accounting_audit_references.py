import json
import sqlite3

import pytest

from utils.btc_accounting_audit_references import TAG, ORIGINAL_HASH, canonical, read_accounting, sha


def fixture(conn):
    conn.executescript('CREATE TABLE llm_scenario_broker_evidence(kind TEXT,captured_at REAL,body TEXT);'
                       'CREATE TABLE llm_scenario_audit_events(event_id TEXT,run_id TEXT,kind TEXT,observed_at REAL,'
                       'scenario_id TEXT,intent_id TEXT,decision_slot INTEGER,manifest_id TEXT,body TEXT);')
    account = {'captured_at': 1, 'wallet': {'equity': '100'}, 'position': {'size': '0'}}
    financial = {'raw_executions': [], 'raw_transactions': [], 'trades': [], 'funding': []}
    conn.executemany('INSERT INTO llm_scenario_broker_evidence VALUES(?,?,?)',
                     [('account', 1, json.dumps(account, sort_keys=True)),
                      ('financial', 1, json.dumps(financial, sort_keys=True))])
    event = {'schema_version': 1, 'observation': account, 'financial_evidence': financial,
             'result': {'status': 'confirmed'}, 'children': []}
    raw = canonical(event)
    encoded = dict(event, **{TAG: 1, ORIGINAL_HASH: sha(raw)})
    encoded['observation'] = {'rowid': 1, 'sha256': sha(canonical(account))}
    encoded['financial_evidence'] = {'rowid': 2, 'sha256': sha(canonical(financial))}
    return raw, canonical(encoded)


def test_reference_restores_exact_original_and_accepts_legacy():
    with sqlite3.connect(':memory:') as conn:
        raw, encoded = fixture(conn)
        assert read_accounting(raw, conn) == raw
        assert read_accounting(encoded, conn) == raw


def test_reference_accepts_already_compacted_financial_source():
    from utils.btc_financial_references import compact as compact_financial
    execution = {'execId': 'e', 'detail': 'x' * 2000}
    transaction = {'id': 't', 'tradeId': 'e', 'detail': 'y' * 2000}
    financial = {'raw_executions': [execution], 'raw_transactions': [transaction],
                 'trades': [{'execution_id': 'e', 'raw_execution': execution, 'raw_transaction': transaction}],
                 'funding': []}
    with sqlite3.connect(':memory:') as conn:
        raw, encoded = fixture(conn)
        data = json.loads(raw)
        data['financial_evidence'] = financial
        raw = canonical(data)
        refs = json.loads(encoded)
        refs[ORIGINAL_HASH] = sha(raw)
        refs['financial_evidence']['sha256'] = sha(canonical(financial))
        source = json.dumps(financial, sort_keys=True, allow_nan=False)
        compacted = compact_financial(source)
        assert len(compacted) < len(source)
        conn.execute('UPDATE llm_scenario_broker_evidence SET body=? WHERE rowid=2', (compacted,))
        assert read_accounting(canonical(refs), conn) == raw


@pytest.mark.parametrize('damage', ['missing', 'changed', 'wrong_kind', 'unknown_version', 'wrong_digest'])
def test_missing_changed_or_invalid_reference_fails_closed(damage):
    with sqlite3.connect(':memory:') as conn:
        _, encoded = fixture(conn)
        data = json.loads(encoded)
        if damage == 'missing': conn.execute('DELETE FROM llm_scenario_broker_evidence WHERE rowid=1')
        if damage == 'changed': conn.execute("UPDATE llm_scenario_broker_evidence SET body='{}' WHERE rowid=1")
        if damage == 'wrong_kind': conn.execute("UPDATE llm_scenario_broker_evidence SET kind='other' WHERE rowid=1")
        if damage == 'unknown_version': data[TAG] = 2
        if damage == 'wrong_digest': data[ORIGINAL_HASH] = 'bad'
        with pytest.raises(ValueError): read_accounting(canonical(data), conn)


def test_replay_and_export_anchor_remain_identical():
    from analysis.scenario_replay import _runtime_event_hash
    from observability import btc_scenario_export as export
    with sqlite3.connect(':memory:') as conn:
        raw, encoded = fixture(conn)
        conn.execute("INSERT INTO llm_scenario_audit_events VALUES('e','r','accounting_observation',2,NULL,NULL,1,NULL,?)", (raw,))
        before = _runtime_event_hash(conn)
        row = {'kind': 'accounting_observation', 'body': raw}
        anchor = export._anchor_hash('llm_scenario_audit_events', row, conn)
        conn.execute('UPDATE llm_scenario_audit_events SET body=?', (encoded,))
        assert _runtime_event_hash(conn) == before
        row['body'] = encoded
        assert export._source_text('llm_scenario_audit_events', row, conn) == raw
        assert export._anchor_hash('llm_scenario_audit_events', row, conn) == anchor


def test_forward_packet_sees_original_body(tmp_path):
    from tools.build_btc_scenario_forward_packet import _read
    path = tmp_path / 'fixture.sqlite'
    with sqlite3.connect(path) as conn:
        raw, encoded = fixture(conn)
        for name in ('control', 'decisions', 'intents', 'children', 'settlements', 'outbox'):
            conn.execute('CREATE TABLE llm_scenario_' + name + '(id TEXT)')
        conn.execute('CREATE TABLE btc_meta(mode TEXT,key TEXT,value TEXT)')
        conn.execute("INSERT INTO llm_scenario_audit_events VALUES('e','r','accounting_observation',2,NULL,NULL,1,NULL,?)", (encoded,))
    data = _read(path)
    assert data['llm_scenario_audit_events'][0]['body'] == raw


def test_export_emits_restored_accounting_and_keeps_its_anchor(tmp_path):
    from observability import btc_scenario_export as export
    db, state, spool = (tmp_path / name for name in ('source.sqlite', 'state.sqlite', 'spool.jsonl'))
    with sqlite3.connect(db) as conn:
        raw, encoded = fixture(conn)
    assert export.export_once(db, state, spool)['status'] == 'OK'
    with sqlite3.connect(db) as conn:
        conn.execute("INSERT INTO llm_scenario_audit_events VALUES('e','r','accounting_observation',2,NULL,NULL,1,NULL,?)", (encoded,))
    result = export.export_once(db, state, spool)
    assert result['status'] == 'OK' and result['captured'] == 1
    events = [json.loads(line) for line in spool.read_text().splitlines() if line.strip()]
    row = next(event for event in events if event.get('attributes', {}).get('source_kind') == 'accounting_observation')
    assert row['attributes']['body_sha256'] == sha(raw)
    assert export.export_once(db, state, spool)['status'] == 'OK'
