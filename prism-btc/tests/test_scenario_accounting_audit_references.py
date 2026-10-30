import json
import sqlite3

import pytest

from utils.btc_accounting_audit_references import CHILDREN_REF, TAG, ORIGINAL_HASH, canonical, compact, compact_children, read_accounting, sha


def fixture(conn):
    conn.executescript('CREATE TABLE llm_scenario_broker_evidence(kind TEXT,captured_at REAL,body TEXT);'
                       'CREATE TABLE llm_scenario_audit_events(event_id TEXT,run_id TEXT,kind TEXT,observed_at REAL,'
                       'scenario_id TEXT,intent_id TEXT,decision_slot INTEGER,manifest_id TEXT,body TEXT);')
    account = {'captured_at': 1, 'wallet': {'equity': '100', 'detail': 'a' * 2000}, 'position': {'size': '0'}}
    financial = {'raw_executions': [], 'raw_transactions': [], 'trades': [], 'funding': [], 'detail': 'b' * 2000}
    conn.executemany('INSERT INTO llm_scenario_broker_evidence VALUES(?,?,?)',
                     [('account', 1, json.dumps(account, sort_keys=True)),
                      ('financial', 1, json.dumps(financial, sort_keys=True))])
    event = {'schema_version': 1, 'observation': account, 'financial_evidence': financial,
             'result': {'status': 'confirmed'}, 'children': []}
    raw = canonical(event)
    encoded = dict(event, **{TAG: 1, ORIGINAL_HASH: sha(raw)})
    encoded['observation'] = {'rowid': 1, 'captured_at': 1, 'sha256': sha(canonical(account))}
    encoded['financial_evidence'] = {'rowid': 2, 'captured_at': 1, 'sha256': sha(canonical(financial))}
    return raw, canonical(encoded)


@pytest.mark.parametrize('compact_account', [False, True])
def test_compact_removes_only_exact_earlier_duplicate_fields(compact_account):
    with sqlite3.connect(':memory:') as conn:
        raw, _ = fixture(conn)
        if compact_account:
            body = conn.execute("SELECT body FROM llm_scenario_broker_evidence WHERE kind='account'").fetchone()[0]
            conn.execute("UPDATE llm_scenario_broker_evidence SET body=? WHERE kind='account'", (canonical(json.loads(body)),))
        encoded = compact(raw, conn, 2)
        assert len(encoded) < len(raw) and read_accounting(encoded, conn) == raw
        assert json.loads(encoded)[TAG] == 1


@pytest.mark.parametrize('failure', ['missing', 'changed', 'future', 'outside_bound'])
def test_compact_keeps_original_when_sources_are_unusable(failure):
    with sqlite3.connect(':memory:') as conn:
        raw, _ = fixture(conn)
        if failure == 'missing': conn.execute('DELETE FROM llm_scenario_broker_evidence WHERE kind="account"')
        if failure == 'changed': conn.execute('UPDATE llm_scenario_broker_evidence SET body="{}" WHERE kind="account"')
        if failure == 'future': conn.execute('UPDATE llm_scenario_broker_evidence SET captured_at=3')
        if failure == 'outside_bound':
            conn.executemany('INSERT INTO llm_scenario_broker_evidence VALUES(?,?,?)', [('account', 1, '{}')] * 8)
        assert compact(raw, conn, 2) == raw


def test_reference_restores_exact_original_and_accepts_legacy():
    with sqlite3.connect(':memory:') as conn:
        raw, encoded = fixture(conn)
        assert read_accounting(raw, conn) == raw
        assert read_accounting(encoded, conn) == raw


def children_reference_fixture(conn):
    raw, encoded = fixture(conn)
    source = json.loads(encoded)
    children = source.pop('children')
    conn.execute("INSERT INTO llm_scenario_audit_events VALUES('source','r','accounting_observation',2,NULL,NULL,1,NULL,?)", (encoded,))
    source[CHILDREN_REF] = {'event_id': 'source', 'sha256': sha(canonical(children))}
    return raw, canonical(source)


def test_children_reference_restores_original_and_export_anchor():
    from observability.btc_scenario_export import _anchor_hash
    with sqlite3.connect(':memory:') as conn:
        raw, encoded = children_reference_fixture(conn)
        assert read_accounting(encoded, conn) == raw
        before = {'kind': 'accounting_observation', 'body': raw}
        after = {'kind': 'accounting_observation', 'body': encoded}
        assert _anchor_hash('llm_scenario_audit_events', before, conn) == _anchor_hash('llm_scenario_audit_events', after, conn)


def test_children_writer_uses_one_anchor_across_many_events_and_preserves_original():
    with sqlite3.connect(':memory:') as conn:
        raw, encoded = fixture(conn)
        original, event = json.loads(raw), json.loads(encoded)
        original['children'] = event['children'] = [{'id': 'one', 'detail': 'x' * 2000}]
        raw = canonical(original)
        event[ORIGINAL_HASH] = sha(raw)
        encoded = canonical(event)
        assert compact_children(encoded, conn) == encoded
        conn.execute("INSERT INTO llm_scenario_audit_events VALUES('anchor','r','accounting_observation',2,NULL,NULL,1,NULL,?)", (encoded,))
        for n in range(12):
            candidate = compact_children(encoded, conn)
            assert len(candidate) < len(encoded)
            assert json.loads(candidate)[CHILDREN_REF]['event_id'] == 'anchor'
            assert read_accounting(candidate, conn) == raw
            conn.execute("INSERT INTO llm_scenario_audit_events VALUES(?,'r','accounting_observation',2,NULL,NULL,1,NULL,?)", (str(n), candidate))
        conn.execute("DELETE FROM llm_scenario_audit_events WHERE event_id='anchor'")
        assert compact_children(encoded, conn) == encoded


def test_audit_flush_writes_direct_reference_and_preserves_both_originals(tmp_path, monkeypatch):
    from live import scenario_ledger, scenario_provenance
    monkeypatch.setattr(scenario_ledger, 'forward_audits', lambda events: None)
    path = tmp_path / 'audit.sqlite'
    with sqlite3.connect(path) as conn:
        raw, _ = fixture(conn)
        original = json.loads(raw)
        original['children'] = [{'id': 'one', 'detail': 'x' * 2000}]
        raw = canonical(original)
    events = [(str(n), 'run', 'accounting_observation', 2, None, None, 1, 'manifest', raw) for n in range(2)]
    scenario_provenance._flush({'path': path, 'manifest_id': 'manifest', 'manifest': '{}', 'events': events})
    with sqlite3.connect(path) as conn:
        rows = conn.execute('SELECT event_id,body FROM llm_scenario_audit_events ORDER BY rowid').fetchall()
        assert len(rows) == 2
        assert CHILDREN_REF not in json.loads(rows[0][1])
        assert json.loads(rows[1][1])[CHILDREN_REF]['event_id'] == rows[0][0]
        assert all(read_accounting(body, conn) == raw for _, body in rows)


@pytest.mark.parametrize('damage', ['missing', 'wrong_kind', 'changed', 'chain', 'digest', 'marker', 'duplicate_children', 'missing_tag'])
def test_children_reference_missing_or_tampered_fails_closed(damage):
    with sqlite3.connect(':memory:') as conn:
        _, encoded = children_reference_fixture(conn)
        value = json.loads(encoded)
        if damage == 'missing': conn.execute('DELETE FROM llm_scenario_audit_events')
        if damage == 'wrong_kind': conn.execute("UPDATE llm_scenario_audit_events SET kind='other'")
        if damage == 'changed': conn.execute("UPDATE llm_scenario_audit_events SET body=?", (canonical({'children': [1]}),))
        if damage == 'chain': conn.execute("UPDATE llm_scenario_audit_events SET body=?", (encoded,))
        if damage == 'digest': value[CHILDREN_REF]['sha256'] = 'bad'
        if damage == 'marker': value[CHILDREN_REF]['extra'] = 1
        if damage == 'duplicate_children': value['children'] = []
        if damage == 'missing_tag': value.pop(TAG)
        with pytest.raises(ValueError): read_accounting(canonical(value), conn)


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
        candidate = compact(raw, conn, 2)
        assert len(candidate) < len(raw) and read_accounting(candidate, conn) == raw


@pytest.mark.parametrize('damage', ['missing', 'changed', 'wrong_kind', 'timestamp', 'unknown_version', 'wrong_digest'])
def test_missing_changed_or_invalid_reference_fails_closed(damage):
    with sqlite3.connect(':memory:') as conn:
        _, encoded = fixture(conn)
        data = json.loads(encoded)
        if damage == 'missing': conn.execute('DELETE FROM llm_scenario_broker_evidence WHERE rowid=1')
        if damage == 'changed': conn.execute("UPDATE llm_scenario_broker_evidence SET body='{}' WHERE rowid=1")
        if damage == 'wrong_kind': conn.execute("UPDATE llm_scenario_broker_evidence SET kind='other' WHERE rowid=1")
        if damage == 'timestamp': conn.execute("UPDATE llm_scenario_broker_evidence SET captured_at=3 WHERE rowid=1")
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
