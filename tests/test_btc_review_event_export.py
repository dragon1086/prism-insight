import json
import sqlite3
import hashlib

import pytest

from observability import btc_scenario_export as export
from tests.test_btc_scenario_export import lane as lane, events


def create_review(db):
    with sqlite3.connect(db) as conn:
        conn.execute('CREATE TABLE llm_scenario_review_events(slot INTEGER,sequence INTEGER,body TEXT,PRIMARY KEY(slot,sequence))')


def add_review(db, sequence, body):
    with sqlite3.connect(db) as conn:
        conn.execute('INSERT INTO llm_scenario_review_events VALUES(?,?,?)',(100,sequence,json.dumps(body)))


def test_new_stream_backfills_without_resetting_existing_cursors(lane):
    export.export_once(*lane)
    with sqlite3.connect(lane[1]) as conn:
        old=dict(conn.execute('SELECT stream,seq FROM cursors'))
    create_review(lane[0])
    add_review(lane[0],1,dict(kind='MODEL_REQUEST',phase='candidate',started_at=1700000000,
        request_sha256='a'*64,request=dict(model='gpt-6-luna',reasoning_effort='high',fast_tier=True,
            user_prompt='SECRET PROMPT',system_prompt='SECRET SYSTEM',api_key='SECRET KEY')))
    assert export.export_once(*lane)['captured']==0
    assert export.export_once(*lane)['captured']==1
    with sqlite3.connect(lane[1]) as conn:
        current=dict(conn.execute('SELECT stream,seq FROM cursors'))
    assert all(current[k]==v for k,v in old.items())
    event=events(lane[2])[-1]
    assert event['attributes']['source_sequence']==1
    assert event['attributes']['phase']=='candidate'
    assert 'SECRET' not in lane[2].read_text()
    assert event['git_sha'] is None
    assert export.export_once(*lane)['captured']==0


def review_events(lane):
    return [e for e in events(lane[2]) if e['event_type']=='btc.research.review']


def test_phase_request_raw_and_candidates_are_bounded_private_and_distinct(lane):
    create_review(lane[0])
    export.export_once(*lane)
    request=dict(model='gpt-6-luna',reasoning_effort='high',fast_tier=True,user_prompt='SECRET')
    request_hash=export._hash(export._json(request))
    add_review(lane[0],1,dict(kind='MODEL_RAW',phase='selection',actual_request=request,
        actual_request_sha256=request_hash,prepared_request_sha256='a'*64,request_sha256='a'*64,
        raw='SECRET RESPONSE',elapsed_seconds=1.25))
    candidate=dict(id='c0',eligible=True,receipt_sha256='b'*64,
        raw_proposal=dict(action='OPEN',side='SHORT',input_id='SECRET INPUT',rationale='SECRET RATIONALE'))
    add_review(lane[0],2,dict(kind='CALCULATED_CANDIDATES',body=dict(candidates=[candidate]*3)))
    assert export.export_once(*lane)['captured']==2
    raw, candidates=review_events(lane)
    a=raw['attributes']
    assert a['phase']=='selection' and a['actual_request_hash_status']=='MATCH'
    assert a['actual_request_metadata']['effort']=='high'
    assert a['actual_request_metadata']['fast'] is True
    assert a['raw_sha256']==export._hash('SECRET RESPONSE')
    assert a['source_time_basis']=='UNKNOWN_EVENT_TIME_EXPORT_OBSERVATION_ONLY'
    assert 'source_event_time' in a and a['source_event_time'] is None
    assert candidates['attributes']['candidate_count']==3
    assert len(candidates['attributes']['candidates'])==2
    assert candidates['attributes']['candidate_summaries_complete'] is False
    assert 'SECRET' not in lane[2].read_text()


def manifest_tables(db):
    with sqlite3.connect(db) as c:
        c.executescript('CREATE TABLE llm_scenario_audit_manifests(manifest_id TEXT,body TEXT); CREATE TABLE llm_scenario_audit_events(kind TEXT,decision_slot INTEGER,manifest_id TEXT,body TEXT,observed_at REAL);')
        for name,char in [('m1','a'),('m2','b')]:
            c.execute('INSERT INTO llm_scenario_audit_manifests VALUES(?,?)',(name,json.dumps(dict(
                git_revision=char*40,policy_hash=char*64,loaded_code_status='VERIFIED'))))
        c.execute('INSERT INTO llm_scenario_audit_events VALUES(?,?,?,?,?)',('decision_input',100,'m1',json.dumps(dict(input_id='input')),1700000000))
        c.execute('INSERT INTO llm_scenario_audit_events VALUES(?,?,?,?,?)',('protection',100,'m2','{}',1700000001))


@pytest.mark.parametrize('conflict',[None,'manifest','input'])
def test_only_exact_decision_input_manifest_not_protect_or_exporter_head(lane,conflict):
    manifest_tables(lane[0])
    create_review(lane[0])
    export.export_once(*lane)
    if conflict=='manifest':
        with sqlite3.connect(lane[0]) as c:
            c.execute('INSERT INTO llm_scenario_audit_events VALUES(?,?,?,?,?)',('decision_input',100,'m2',json.dumps(dict(input_id='input')),1700000001))
    add_review(lane[0],1,dict(kind='MODEL_REQUEST',phase='candidate',request=dict(
        user_prompt=json.dumps(dict(contract_context=dict(input_id='wrong' if conflict=='input' else 'input'))))))
    export.export_once(*lane)
    event=review_events(lane)[-1]
    assert event['git_sha']==('a'*40 if conflict is None else None)
    assert event['attributes']['policy_evidence'].startswith('UNKNOWN') == (conflict is not None)


def test_review_identity_anchor_detects_changed_sequence(lane):
    create_review(lane[0])
    export.export_once(*lane)
    add_review(lane[0],1,dict(kind='MODEL_ERROR'))
    export.export_once(*lane)
    with sqlite3.connect(lane[0]) as c:
        c.execute('UPDATE llm_scenario_review_events SET sequence=2')
    assert export.export_once(*lane)['status']=='UNAVAILABLE'


def test_malformed_review_cursor_stays_and_other_stream_drains(lane):
    create_review(lane[0])
    export.export_once(*lane)
    with sqlite3.connect(lane[0]) as c:
        c.execute("INSERT INTO llm_scenario_review_events VALUES(100,1,'not json')")
        c.execute("INSERT INTO llm_scenario_outbox VALUES('notice','FILL','private','SENT',1)")
    result=export.export_once(*lane)
    assert result['status']=='DEGRADED' and result['captured']==1
    with sqlite3.connect(lane[1]) as c:
        assert c.execute("SELECT seq FROM cursors WHERE stream='llm_scenario_review_events'").fetchone()[0]==0


def test_oversize_review_is_not_skipped_as_success(lane):
    create_review(lane[0])
    export.export_once(*lane)
    add_review(lane[0],1,dict(kind='MODEL_RAW',raw='x'*(4*1024*1024)))
    result=export.export_once(*lane)
    assert result['status']=='DEGRADED' and result['captured']==0
    assert result['source_errors'][0]['error_type']=='SOURCE_BYTE_BUDGET'


def test_review_append_crash_preserves_id_and_bytes(lane,monkeypatch):
    create_review(lane[0])
    export.export_once(*lane)
    add_review(lane[0],1,dict(kind='MODEL_ERROR'))
    append=export._append
    def crash(event,path):
        append(event,path)
        raise OSError('after durable write')
    monkeypatch.setattr(export,'_append',crash)
    assert export.export_once(*lane)['status']=='UNAVAILABLE'
    first=review_events(lane)[-1]
    monkeypatch.setattr(export,'_append',append)
    result=export.export_once(*lane)
    assert result['captured']==0 and result['emitted']==1
    assert review_events(lane)[-1]==first


def tape_digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()


def test_sparse_tape_backfill_hash_linkage_and_privacy(lane):
    inputs=dict(context=dict(input_id='PRIVATE INPUT'),bindings=dict(policy='economic-reject-calculated-v1',
        source_sha256={'private_path':'a'*64},loaded_parity={'private_path':'VERIFIED'}))
    body=dict(version=1,inputs=inputs,inputs_sha256=tape_digest(inputs),calls=1,
        operations=[dict(kind='generate',request=dict(user_prompt='PRIVATE PROMPT'),text='PRIVATE RESPONSE')],
        outcome=dict(status='COMPLETE',result=dict(selected=dict(id='WAIT'),bundle_sha256='b'*64)))
    body['sha256']=tape_digest(body)
    with sqlite3.connect(lane[0]) as c:
        c.execute('CREATE TABLE llm_scenario_review_tapes(slot INTEGER PRIMARY KEY,body TEXT)')
        for slot in [100,900000]:
            c.execute('INSERT INTO llm_scenario_review_tapes VALUES(?,?)',(slot,json.dumps(body)))
    before=lane[0].read_bytes()
    export.export_once(*lane)
    assert export.export_once(*lane,limit=1)['captured']==1
    assert export.export_once(*lane,limit=1)['captured']==1
    tapes=[e for e in events(lane[2]) if e['event_type']=='btc.research.review_tape']
    assert [e['attributes']['decision_slot'] for e in tapes]==[100,900000]
    assert tapes[0]['attributes']['tape_hash_status']=='MATCH'
    assert tapes[0]['attributes']['inputs_hash_status']=='MATCH'
    assert tapes[0]['attributes']['source_event_time'] is None
    assert len(tapes[0]['attributes']['request_refs'])==1
    assert 'PRIVATE' not in lane[2].read_text() and 'private_path' not in lane[2].read_text()
    assert before==lane[0].read_bytes()
    assert export.export_once(*lane)['captured']==0


def test_manifest_cache_is_transaction_local_and_identity_checks_are_per_event(lane,monkeypatch):
    manifest_tables(lane[0])
    create_review(lane[0])
    export.export_once(*lane)
    calls=[]
    query=export._query
    def counted(connection,name,**kwargs):
        if name=='review_manifests':
            calls.append(kwargs['parameters'])
        return query(connection,name,**kwargs)
    monkeypatch.setattr(export,'_query',counted)
    for index,ident in enumerate(['input','wrong'],1):
        add_review(lane[0],index,dict(kind='MODEL_REQUEST',request=dict(
            user_prompt=json.dumps(dict(contract_context=dict(input_id=ident))))))
    assert export.export_once(*lane)['captured']==2
    assert calls==[(100,)]
    good,bad=review_events(lane)
    assert good['git_sha']=='a'*40 and bad['git_sha'] is None
    with sqlite3.connect(lane[0]) as c:
        c.execute('INSERT INTO llm_scenario_audit_events VALUES(?,?,?,?,?)',('decision_input',100,'m2',json.dumps(dict(input_id='input')),1700000001))
    add_review(lane[0],3,dict(kind='MODEL_ERROR'))
    export.export_once(*lane)
    assert calls==[(100,),(100,)]
    assert review_events(lane)[-1]['attributes']['policy_evidence']=='UNKNOWN_CONFLICTING_MANIFESTS'


def test_early_unknown_review_can_join_later_exact_audit_without_rewriting_history(lane):
    manifest_tables(lane[0])
    with sqlite3.connect(lane[0]) as c:
        c.execute('DELETE FROM llm_scenario_audit_events')
    create_review(lane[0])
    export.export_once(*lane)
    add_review(lane[0],1,dict(kind='MODEL_REQUEST',phase='candidate',request=dict(
        user_prompt=json.dumps(dict(contract_context=dict(input_id='input'))))))
    export.export_once(*lane)
    early=review_events(lane)[-1]
    assert early['git_sha'] is None
    with sqlite3.connect(lane[0]) as c:
        c.execute('INSERT INTO llm_scenario_audit_events VALUES(?,?,?,?,?)',('decision_input',100,'m1',json.dumps(dict(input_id='input')),1700000000))
    assert export.export_once(*lane)['captured']==1
    late=[e for e in events(lane[2]) if e['event_type']=='btc.research.source'][-1]
    assert late['git_sha']=='a'*40
    for key in ('source_identity','decision_slot','input_id_ref'):
        assert early['attributes'][key]==late['attributes'][key]
    assert review_events(lane)==[early]


def test_compressed_review_events_keep_original_hash_and_cursor(lane):
    import gzip
    from observability.review_storage import MAGIC
    create_review(lane[0])
    export.export_once(*lane)
    original = json.dumps(dict(kind='MODEL_ERROR', reason='x' * 1024))
    packed = MAGIC + gzip.compress(original.encode(), compresslevel=1, mtime=0)
    with sqlite3.connect(lane[0]) as conn:
        conn.execute('INSERT INTO llm_scenario_review_events VALUES(?,?,?)', (100, 1, packed))
    assert export.export_once(*lane)['captured'] == 1
    event = review_events(lane)[-1]
    assert event['attributes']['body_sha256'] == export._hash(original)
    assert export.export_once(*lane)['status'] != 'UNAVAILABLE'


def test_bad_compressed_review_keeps_cursor(lane):
    from observability.review_storage import MAGIC
    create_review(lane[0])
    export.export_once(*lane)
    with sqlite3.connect(lane[0]) as conn:
        conn.execute('INSERT INTO llm_scenario_review_events VALUES(?,?,?)', (100, 1, MAGIC + b'broken'))
    result = export.export_once(*lane)
    assert result['status'] == 'DEGRADED' and result['captured'] == 0
    with sqlite3.connect(lane[1]) as conn:
        assert conn.execute("SELECT seq FROM cursors WHERE stream='llm_scenario_review_events'").fetchone()[0] == 0


def test_compressed_tape_preserves_seal_hash_and_anchor(lane):
    import gzip
    from observability.review_storage import MAGIC
    inputs = dict(context=dict(input_id='private'), bindings={})
    body = dict(version=1, inputs=inputs, inputs_sha256=tape_digest(inputs), calls=0,
                operations=[], outcome=dict(status='COMPLETE', result={}))
    body['sha256'] = tape_digest(body)
    original = json.dumps(body)
    with sqlite3.connect(lane[0]) as conn:
        conn.execute('CREATE TABLE llm_scenario_review_tapes(slot INTEGER PRIMARY KEY,body TEXT)')
        conn.execute('INSERT INTO llm_scenario_review_tapes VALUES(?,?)',
                     (100, MAGIC + gzip.compress(original.encode(), mtime=0)))
    export.export_once(*lane)
    assert export.export_once(*lane)['captured'] == 1
    result = [e for e in events(lane[2]) if e['event_type'] == 'btc.research.review_tape'][-1]
    assert result['attributes']['tape_hash_status'] == 'MATCH'
    assert result['attributes']['body_sha256'] == export._hash(original)
    assert export.export_once(*lane)['status'] != 'UNAVAILABLE'


def test_compressed_source_budget_counts_decompressed_bytes(lane):
    import gzip
    from observability.review_storage import MAGIC
    create_review(lane[0])
    export.export_once(*lane)
    original = json.dumps(dict(kind='MODEL_RAW', raw='x' * (4 * 1024 * 1024)))
    with sqlite3.connect(lane[0]) as conn:
        conn.execute('INSERT INTO llm_scenario_review_events VALUES(?,?,?)',
                     (100, 1, MAGIC + gzip.compress(original.encode(), mtime=0)))
    result = export.export_once(*lane)
    assert result['status'] == 'DEGRADED' and result['captured'] == 0
    assert result['source_errors'][0]['error_type'] == 'SOURCE_BYTE_BUDGET'


def test_compressed_decode_rejects_truncation_trailing_and_size():
    import gzip
    from observability.review_storage import MAGIC, MAX_BYTES, review_text
    for body in (MAGIC + b'bad', MAGIC + gzip.compress(b'{}')[:-1],
                 MAGIC + gzip.compress(b'{}') + b'junk',
                 MAGIC + gzip.compress(b'x' * (MAX_BYTES + 1))):
        with pytest.raises(ValueError):
            review_text(body)
    assert review_text('legacy') == 'legacy'
    assert review_text(b'legacy') == 'legacy'


@pytest.mark.parametrize('stream', ['llm_scenario_review_events', 'llm_scenario_review_tapes'])
def test_compressed_and_legacy_rows_share_export_anchor(stream):
    import gzip
    from observability.review_storage import MAGIC
    original = json.dumps(dict(kind='MODEL_ERROR', reason='private'))
    row = dict(slot=100, sequence=1, body=original)
    packed = dict(row, body=MAGIC + gzip.compress(original.encode(), mtime=0))
    assert export._anchor_hash(stream, row) == export._anchor_hash(stream, packed)


def test_referenced_tape_restores_export_anchor_and_fails_on_missing_source(lane):
    from utils.btc_review_references import canonical, compact, pack

    snapshot = {'z': 1, 'a': {'second': 2, 'first': 1}}
    context = {'input_id': 'fixture'}
    event = {'kind': 'MODEL_RAW', 'raw': 'private fixture'}
    inputs = {'snapshot': snapshot, 'context': context, 'bindings': {}}
    body = {'version': 1, 'inputs': inputs, 'inputs_sha256': tape_digest(inputs), 'calls': 0,
            'operations': [{'kind': 'record', 'event': event}],
            'outcome': {'status': 'COMPLETE', 'result': {}}}
    body['sha256'] = tape_digest(body)
    original = json.dumps(body)
    snapshot_text, context_text, event_text = map(canonical, (snapshot, context, event))
    packed = pack(compact(original, snapshot_text, context_text, [event_text]))
    create_review(lane[0])
    with sqlite3.connect(lane[0]) as conn:
        conn.execute('INSERT INTO llm_scenario_decisions VALUES(?,?,?,?,?)',
                     (100, snapshot_text, context_text, None, None))
        conn.execute('INSERT INTO llm_scenario_review_events VALUES(?,?,?)', (100, 0, event_text))
        conn.execute('CREATE TABLE llm_scenario_review_tapes(slot INTEGER PRIMARY KEY,body TEXT)')
        conn.execute('INSERT INTO llm_scenario_review_tapes VALUES(?,?)', (100, packed))
        plain = {'slot': 100, 'body': original}
        compressed = {'slot': 100, 'body': packed}
        assert export._anchor_hash('llm_scenario_review_tapes', plain, conn) == export._anchor_hash(
            'llm_scenario_review_tapes', compressed, conn)
    export.export_once(*lane)
    result = export.export_once(*lane)
    assert result['status'] != 'UNAVAILABLE'
    tape = [item for item in events(lane[2]) if item['event_type'] == 'btc.research.review_tape'][-1]
    assert tape['attributes']['tape_hash_status'] == 'MATCH'
    assert tape['attributes']['body_sha256'] == export._hash(original)
    assert 'private fixture' not in lane[2].read_text()
    with sqlite3.connect(lane[0]) as conn:
        conn.execute('DELETE FROM llm_scenario_review_events WHERE slot=100 AND sequence=0')
    assert export.export_once(*lane)['status'] == 'UNAVAILABLE'
