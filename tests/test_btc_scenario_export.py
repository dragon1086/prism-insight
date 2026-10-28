import json
import os
import sqlite3

import pytest

from observability import btc_scenario_export as export


@pytest.fixture
def lane(tmp_path):
    db, state, spool = (tmp_path / name for name in ('source.sqlite', 'state.sqlite', 'spool.jsonl'))
    with sqlite3.connect(db) as c:
        c.executescript('CREATE TABLE llm_scenario_recovery_events(event_id TEXT PRIMARY KEY,slot INTEGER,scenario_id TEXT,kind TEXT,observed_at REAL,body TEXT); CREATE TABLE llm_scenario_decisions(slot INTEGER PRIMARY KEY,snapshot TEXT,context TEXT,proposal TEXT,outcome TEXT); CREATE TABLE llm_scenario_outbox(event_id TEXT PRIMARY KEY,kind TEXT,body TEXT,status TEXT,message_id INTEGER);')
    return db, state, spool


def add(db, n, kind='OUTCOME', body=None):
    with sqlite3.connect(db) as c:
        c.execute('INSERT INTO llm_scenario_recovery_events VALUES(?,?,?,?,?,?)',
                  (str(n), n, 'scenario', kind, 1700000000+n,
                   json.dumps(body or {'status': 'blocked', 'reason': 'input_size', 'token': 'SECRET'})))


def events(spool):
    return [json.loads(line) for line in spool.read_text().splitlines() if line.strip()]


def test_cutover_no_backfill_restart_and_secret_safety(lane):
    db, state, spool = lane
    add(db, 1)
    assert export.export_once(*lane)['captured'] == 0
    add(db, 2)
    assert export.export_once(*lane)['captured'] == 1
    assert export.export_once(*lane)['captured'] == 0
    rows = events(spool)
    assert len([r for r in rows if r['event_type'] == 'btc.research.source']) == 1
    assert 'SECRET' not in spool.read_text()
    assert rows[-1]['timestamp'].startswith('2023-')
    assert rows[-1]['git_sha'] is None
    assert rows[-1]['policy_version'] is None
    assert os.stat(state).st_mode & 0o777 == 0o600


def test_append_before_checkpoint_crash_reuses_id(lane, monkeypatch):
    export.export_once(*lane)
    add(lane[0], 1)
    original = export._append
    def crash(event, path):
        original(event, path)
        raise OSError('crash after durable append')
    monkeypatch.setattr(export, '_append', crash)
    assert export.export_once(*lane)['status'] == 'UNAVAILABLE'
    first = events(lane[2])[-1]['event_id']
    first_body = events(lane[2])[-1]
    monkeypatch.setattr(export, '_append', original)
    assert export.export_once(*lane)['emitted'] == 1
    assert events(lane[2])[-1]['event_id'] == first
    assert events(lane[2])[-1] == first_body


def test_rolled_back_notice_not_exported_and_receipt_is_observation(lane):
    export.export_once(*lane)
    with sqlite3.connect(lane[0]) as c:
        c.execute("INSERT INTO llm_scenario_outbox VALUES('rollback','FILL','secret notice','QUEUED',NULL)")
        c.rollback()
    assert export.export_once(*lane)['captured'] == 0
    with sqlite3.connect(lane[0]) as c:
        c.execute("INSERT INTO llm_scenario_outbox VALUES('notice','FILL','secret notice','QUEUED',NULL)")
    export.export_once(*lane)
    with sqlite3.connect(lane[0]) as c:
        c.execute("UPDATE llm_scenario_outbox SET status='SENT',message_id=100 WHERE event_id='notice'")
    export.export_once(*lane)
    final = events(lane[2])[-1]['attributes']
    assert final['notice_status'] == 'SENT'
    assert final['actual_delivery_time'] == 'UNKNOWN'
    assert 'secret notice' not in lane[2].read_text()


def test_source_replacement_and_anchor_change_fail_closed(lane):
    export.export_once(*lane)
    add(lane[0], 1)
    export.export_once(*lane)
    with sqlite3.connect(lane[0]) as c:
        c.execute("UPDATE llm_scenario_recovery_events SET body='{}'")
    assert export.export_once(*lane)['status'] == 'UNAVAILABLE'
    old = lane[0].with_suffix('.old')
    lane[0].rename(old)
    with sqlite3.connect(lane[0]) as c:
        c.execute('CREATE TABLE dummy(id)')
    assert export.export_once(*lane)['status'] == 'UNAVAILABLE'


def test_account_ticker_observation_exports_only_market_fields_and_times(lane):
    db, state, spool = lane
    with sqlite3.connect(db) as conn:
        conn.execute('CREATE TABLE llm_scenario_broker_evidence(kind TEXT,captured_at REAL,body TEXT)')
    export.export_once(db, state, spool)
    body = {'ticker': {'symbol': 'BTCUSDT', 'markPrice': '100.1', 'lastPrice': '100.2',
                       'bid1Price': '100.0', 'ask1Price': '100.3', 'private': 'SECRET'},
            'ticker_timing': {'request_started_at': 1700000000.1, 'response_received_at': 1700000000.3,
                              'exchange_response_time_ms': 1699999000000},
            'wallet': {'api_key': 'SECRET'}}
    with sqlite3.connect(db) as conn:
        conn.execute('INSERT INTO llm_scenario_broker_evidence VALUES(?,?,?)', ('account', 1700000001, json.dumps(body)))
    assert export.export_once(db, state, spool)['captured'] == 1
    row = next(r for r in events(spool) if r.get('attributes', {}).get('source_kind') == 'account')
    observed = row['attributes']['ticker_observation']
    assert observed == {'status': 'OBSERVED', 'basis': 'HTTP_RESPONSE_NOT_QUOTE_UPDATE_OR_FILL',
                        'prices': {'markPrice': '100.1', 'lastPrice': '100.2', 'bid1Price': '100.0', 'ask1Price': '100.3'},
                        'local_request_started_at': 1700000000.1, 'local_response_received_at': 1700000000.3,
                        'exchange_response_time_ms': 1699999000000}
    assert 'SECRET' not in spool.read_text()
    from observability.shipper import build_otlp_payload
    payload = build_otlp_payload([row])
    record = payload['resourceLogs'][0]['scopeLogs'][0]['logRecords'][0]
    assert json.loads(record['body']['stringValue'])['attributes']['ticker_observation'] == observed
    assert 'SECRET' not in json.dumps(payload)


def test_invalid_missing_and_crossed_ticker_stay_unknown_or_partial():
    assert export._ticker_observation({}) == {'status': 'UNKNOWN', 'basis': 'HTTP_RESPONSE_NOT_QUOTE_UPDATE_OR_FILL'}
    body = {'ticker': {'symbol': 'BTCUSDT', 'markPrice': 'NaN', 'lastPrice': '-1',
                       'bid1Price': '102', 'ask1Price': '101'},
            'ticker_timing': {'request_started_at': 2, 'response_received_at': 1,
                              'exchange_response_time_ms': True}}
    observed = export._ticker_observation(body)
    assert observed['status'] == 'UNKNOWN' and observed['prices'] == {}
    assert observed['book_status'] == 'CROSSED_UNKNOWN'
    assert 'local_response_received_at' not in observed and 'exchange_response_time_ms' not in observed
    body['ticker']['markPrice'] = '100'
    assert export._ticker_observation(body)['status'] == 'PARTIAL'


def test_quote_observation_does_not_use_server_clock_as_local_clock():
    body = {'ticker': {'symbol': 'BTCUSDT', 'markPrice': '100', 'lastPrice': '100',
                       'bid1Price': '99', 'ask1Price': '101'},
            'ticker_timing': {'request_started_at': 100, 'response_received_at': 101,
                              'exchange_response_time_ms': 999999999999}}
    observed = export._ticker_observation(body)
    assert observed['status'] == 'OBSERVED'
    assert observed['local_response_received_at'] == 101
    assert observed['exchange_response_time_ms'] == 999999999999


def test_busy_source_has_no_business_write(lane):
    before = lane[0].read_bytes()
    c = sqlite3.connect(lane[0])
    c.execute('BEGIN EXCLUSIVE')
    try:
        assert export.export_once(*lane)['status'] == 'UNAVAILABLE'
    finally:
        c.rollback()
        c.close()
    assert before == lane[0].read_bytes()


def test_outcome_exact_slot_and_model_refs_no_raw_wire(lane):
    export.export_once(*lane)
    add(lane[0], 1, 'MODEL_REQUEST', {'user_prompt': 'DO NOT EXPORT RAW'})
    with sqlite3.connect(lane[0]) as c:
        c.execute('INSERT INTO llm_scenario_decisions VALUES(?,?,?,?,?)',
                  (2, '{}', json.dumps({'accounting_status':'confirmed', 'positions':[{'quantity':.01,'price':80000}]}),
                   json.dumps({'action':'WAIT'}), json.dumps({'status':'ok'})))
    add(lane[0], 2)
    export.export_once(*lane)
    attrs = events(lane[2])[-1]['attributes']
    assert attrs['context_facts']['ledger_status'] == 'confirmed'
    assert attrs['proposal_facts']['action'] == 'WAIT'
    assert attrs['model_evidence_refs'] == {}  # Different slot is never joined.
    assert 'DO NOT EXPORT RAW' not in lane[2].read_text()


def test_manifest_policy_and_confirmed_execution_refs_not_ack(lane):
    db, _, spool = lane
    with sqlite3.connect(db) as c:
        c.executescript('CREATE TABLE llm_scenario_audit_events(event_id,run_id,kind,observed_at,scenario_id,intent_id,decision_slot,manifest_id,body); CREATE TABLE llm_scenario_audit_manifests(manifest_id PRIMARY KEY,body);')
        c.execute('INSERT INTO llm_scenario_audit_manifests VALUES(?,?)',
                  ('manifest', json.dumps({'git_revision':'a'*40, 'policy_hash':'b'*64, 'loaded_code_status':'VERIFIED'})))
    export.export_once(*lane)
    with sqlite3.connect(db) as c:
        for i, ledger in enumerate(({'status':'confirmed','execution_ids':['execution-private']},
                                    {'status':'ACK','execution_ids':['not-a-fill']})):
            c.execute('INSERT INTO llm_scenario_audit_events VALUES(?,?,?,?,?,?,?,?,?)',
                      (str(i),'run','accounting_observation',1700000000,'scenario',None,None,'manifest',json.dumps({'result':ledger})))
    export.export_once(*lane)
    rows = [r for r in events(spool) if r['event_type'] == 'btc.research.source']
    assert rows[0]['git_sha'] == 'a'*40 and rows[0]['policy_version'] == 'b'*64
    assert rows[0]['attributes']['confirmed_ledger_execution_refs'] == [export._hash('execution-private')]
    assert rows[1]['attributes']['ledger_execution_evidence'] == 'UNKNOWN_OR_UNCONFIRMED'
    assert 'execution-private' not in spool.read_text() and 'not-a-fill' not in spool.read_text()


@pytest.mark.parametrize('fault', ['spool_source', 'state_source', 'spool_state',
    'state_source_hardlink', 'spool_source_hardlink', 'spool_state_hardlink',
    'state_source_wal', 'spool_source_shm', 'spool_state_journal', 'state_source_symlink'])
def test_alias_guard_precedes_all_writes(lane, fault):
    db, state, spool = lane
    if fault == 'spool_source':
        spool = db
    elif fault == 'state_source':
        state = db
    elif fault == 'spool_state':
        spool = state
    elif fault == 'state_source_hardlink':
        os.link(db, state)
    elif fault == 'spool_source_hardlink':
        os.link(db, spool)
    elif fault == 'spool_state_hardlink':
        state.write_bytes(b'existing sidecar')
        os.link(state, spool)
    elif fault == 'state_source_wal':
        state = db.with_name(db.name + '-wal')
    elif fault == 'spool_source_shm':
        spool = db.with_name(db.name + '-shm')
    elif fault == 'spool_state_journal':
        spool = state.with_name(state.name + '-journal')
    else:
        state.symlink_to(db)
    before = {p: (p.read_bytes(), p.stat().st_mode) for p in db.parent.iterdir() if p.is_file()}
    result = export.export_once(db, state, spool)
    assert result['status'] == 'UNAVAILABLE'
    assert {p: (p.read_bytes(), p.stat().st_mode) for p in db.parent.iterdir() if p.is_file()} == before


def test_short_write_retry_seals_prefix_and_keeps_complete_event(lane, monkeypatch):
    export.export_once(*lane)
    add(lane[0], 1)
    real_write = os.write
    def short_write(fd, data):
        return real_write(fd, data[:len(data)//2])
    monkeypatch.setattr(export.os, 'write', short_write)
    assert export.export_once(*lane)['status'] == 'UNAVAILABLE'
    with sqlite3.connect(lane[1]) as c:
        expected = json.loads(c.execute('SELECT body FROM outbox').fetchone()[0])
    monkeypatch.setattr(export.os, 'write', real_write)
    assert export.export_once(*lane)['emitted'] == 1
    valid = []
    for line in lane[2].read_text().splitlines():
        try:
            valid.append(json.loads(line))
        except json.JSONDecodeError:
            pass  # Existing shipper also skips the damaged prefix/blank separator.
    assert expected in valid
    with sqlite3.connect(lane[1]) as c:
        assert c.execute('SELECT COUNT(*) FROM outbox').fetchone()[0] == 0


def test_malformed_row_does_not_block_other_stream_or_pending_drain(lane, monkeypatch):
    export.export_once(*lane)
    add(lane[0], 1)
    original = export._append
    def fail(*args):
        raise OSError('spool unavailable')
    monkeypatch.setattr(export, '_append', fail)
    assert export.export_once(*lane)['status'] == 'UNAVAILABLE'
    with sqlite3.connect(lane[0]) as c:
        c.execute("INSERT INTO llm_scenario_recovery_events VALUES('bad',2,'s','OUTCOME',1700000002,'malformed')")
        c.execute("INSERT INTO llm_scenario_outbox VALUES('good','FILL','notice','QUEUED',NULL)")
    monkeypatch.setattr(export, '_append', original)
    result = export.export_once(*lane)
    assert result['status'] == 'DEGRADED' and result['captured'] == 1
    assert result['emitted'] == 2 and result['source_errors']
    with sqlite3.connect(lane[1]) as c:
        assert c.execute("SELECT seq FROM cursors WHERE stream='llm_scenario_recovery_events'").fetchone()[0] == 1
    assert events(lane[2])[-1]['event_type'] == 'btc.research.notice'


def test_reason_free_text_is_hashed_not_exported():
    facts = export._facts({'reason':'English private free text', 'failure_code':'input_size'})
    assert facts['reason_text_omitted'] is True
    assert facts['reason_sha256'] == export._hash('English private free text')
    assert facts['failure_code'] == 'input_size'
    assert 'English private free text' not in json.dumps(facts)


@pytest.mark.parametrize('name', ['SELECT 1', 'unknown',
    ('llm_scenario_outbox; DROP TABLE outbox;--', 0), ('llm_scenario_outbox', -1),
    ('llm_scenario_outbox', True), ('llm_scenario_outbox', 3)])
def test_sql_registry_rejects_unregistered_statements(name):
    with sqlite3.connect(':memory:') as conn:
        with pytest.raises((KeyError, ValueError)):
            export._query(conn, name)


def test_bound_malicious_value_is_literal_data_not_sql():
    malicious = "x'); DROP TABLE outbox; --"
    with sqlite3.connect(':memory:') as conn:
        conn.execute('CREATE TABLE outbox(id PRIMARY KEY,body)')
        export._query(conn, 'queue', parameters=(malicious, malicious))
        assert export._query(conn, 'queued', parameters=(10,)).fetchall() == [(malicious, malicious)]
        export._query(conn, 'delete_queued', parameters=(malicious,))
        assert conn.execute('SELECT COUNT(*) FROM outbox').fetchone()[0] == 0


def test_stream_query_text_is_fixed_for_exact_four_sources():
    assert set(export.STREAM_SQL) == set(export.STREAMS)
    for statements in export.STREAM_SQL.values():
        assert len(statements) == 3 and all(type(s) is str for s in statements)
        assert statements[1].endswith('WHERE rowid=?')
        assert statements[2].endswith('WHERE rowid>? ORDER BY rowid LIMIT ?')

def test_compressed_recovery_anchor_and_corruption(lane):
    import gzip
    from observability.review_storage import MAGIC
    export.export_once(*lane)
    add(lane[0], 1, 'MODEL_REQUEST', {'user_prompt': 'do not expose'})
    with sqlite3.connect(lane[0]) as c:
        body = c.execute('SELECT body FROM llm_scenario_recovery_events').fetchone()[0]
        c.execute('UPDATE llm_scenario_recovery_events SET body=?', (MAGIC+gzip.compress(body.encode(), mtime=0),))
    assert export.export_once(*lane)['captured'] == 1
    assert export.export_once(*lane)['status'] != 'UNAVAILABLE'
    with sqlite3.connect(lane[0]) as c:
        c.execute('UPDATE llm_scenario_recovery_events SET body=?', (MAGIC+b'bad',))
    assert export.export_once(*lane)['status'] == 'UNAVAILABLE'
