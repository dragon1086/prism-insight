import hashlib
import json
import sqlite3
from types import SimpleNamespace

import pytest

from live.scenario_review_storage import review_text
from observability.btc_scenario_export import _anchor_hash
from tools import btc_review_event_compaction as subject
from utils.btc_recovery_references import canonical, compact, pack, read_recovery


def fixture(tmp_path):
    conn = sqlite3.connect(tmp_path / 'data.sqlite', timeout=0)
    conn.row_factory = sqlite3.Row
    conn.executescript('CREATE TABLE llm_scenario_review_events(slot INTEGER,sequence INTEGER,body TEXT);'
                       'CREATE TABLE llm_scenario_recovery_events(slot INTEGER,kind TEXT,body BLOB);')
    event = {'kind':'MODEL_REQUEST', 'request':'abc한글' * 3000}
    raw = json.dumps(event, sort_keys=True)
    recovery = canonical(dict(event, calculated_review_version=1, input_id='fixture'))
    reference = pack(compact(raw, recovery, 7, 3))
    conn.execute('INSERT INTO llm_scenario_review_events VALUES(?,?,?)', (7, 3, raw))
    conn.execute('INSERT INTO llm_scenario_recovery_events VALUES(?,?,?)', (7, 'MODEL_REQUEST', reference))
    conn.commit()
    return conn, raw, recovery


def run(conn, raw, **override):
    args = dict(rowid=1, slot=7, sequence=3, expected_type='text', expected_sha256=hashlib.sha256(raw.encode()).hexdigest(), guard=lambda:True)
    args.update(override)
    return subject.compact_one(conn, **args)


def test_backed_event_preserves_recovery_and_export_anchor(tmp_path):
    conn, raw, recovery = fixture(tmp_path)
    def row():
        return conn.execute('SELECT rowid AS source_rowid,slot,sequence,body FROM llm_scenario_review_events').fetchone()
    def recovery_text():
        body = conn.execute('SELECT body FROM llm_scenario_recovery_events').fetchone()[0]
        return read_recovery(body, conn, 7, decode_legacy=review_text, expected_kind='MODEL_REQUEST')
    anchor = _anchor_hash('llm_scenario_review_events', row(), conn)
    assert run(conn, raw) == 'UPDATED'
    assert row()['source_rowid'] == 1 and review_text(row()['body']) == raw
    assert _anchor_hash('llm_scenario_review_events', row(), conn) == anchor
    assert recovery_text() == recovery
    assert run(conn, raw) == 'UNCHANGED'
    conn.close()


def test_mismatch_and_limits_do_not_mutate(tmp_path):
    conn, raw, _ = fixture(tmp_path)
    for override in ({'slot':8}, {'sequence':4}, {'expected_sha256':'0' * 64}):
        assert run(conn, raw, **override) == 'SOURCE_CHANGED'
    assert run(conn, raw, guard=lambda:False) == 'RESOURCE_LIMIT'
    checks = iter([True, False])
    assert run(conn, raw, guard=lambda:next(checks)) == 'RESOURCE_LIMIT'
    assert conn.execute('SELECT body FROM llm_scenario_review_events').fetchone()[0] == raw
    conn.close()


def test_concurrent_update_stops_before_overwrite(tmp_path, monkeypatch):
    conn, raw, _ = fixture(tmp_path)
    original = subject.review_storage
    def mutate(value):
        other = sqlite3.connect(tmp_path / 'data.sqlite')
        other.execute("UPDATE llm_scenario_review_events SET body='changed' WHERE rowid=1")
        other.commit()
        other.close()
        return original(value)
    monkeypatch.setattr(subject, 'review_storage', mutate)
    assert run(conn, raw) == 'SOURCE_CHANGED'
    assert conn.execute('SELECT body FROM llm_scenario_review_events').fetchone()[0] == 'changed'
    conn.close()


def test_concurrent_change_to_uncompressed_noop_stops(tmp_path, monkeypatch):
    conn, _, _ = fixture(tmp_path)
    raw = 'tiny'
    conn.execute('UPDATE llm_scenario_review_events SET body=? WHERE rowid=1', (raw,))
    conn.commit()
    def mutate(value):
        other = sqlite3.connect(tmp_path / 'data.sqlite')
        other.execute("UPDATE llm_scenario_review_events SET body='changed' WHERE rowid=1")
        other.commit()
        other.close()
        return value
    monkeypatch.setattr(subject, 'review_storage', mutate)
    assert run(conn, raw) == 'SOURCE_CHANGED'
    assert conn.execute('SELECT body FROM llm_scenario_review_events').fetchone()[0] == 'changed'
    conn.close()


def test_concurrent_change_to_previously_compressed_noop_stops(tmp_path, monkeypatch):
    conn, raw, _ = fixture(tmp_path)
    assert run(conn, raw) == 'UPDATED'
    decode = subject.review_text
    def mutate(value):
        result = decode(value)
        other = sqlite3.connect(tmp_path / 'data.sqlite')
        other.execute("UPDATE llm_scenario_review_events SET body='changed' WHERE rowid=1")
        other.commit()
        other.close()
        return result
    monkeypatch.setattr(subject, 'review_text', mutate)
    assert run(conn, raw) == 'SOURCE_CHANGED'
    assert conn.execute('SELECT body FROM llm_scenario_review_events').fetchone()[0] == 'changed'
    conn.close()


@pytest.mark.parametrize('override', [{'rowid':True}, {'slot':-1}, {'sequence':2**63}, {'expected_type':'other'},
                                      {'expected_sha256':'invalid'}, {'guard':None}])
def test_invalid_context_is_rejected(tmp_path, override):
    conn, raw, _ = fixture(tmp_path)
    with pytest.raises(ValueError, match='invalid_compaction_context'):
        run(conn, raw, **override)
    assert conn.execute('SELECT body FROM llm_scenario_review_events').fetchone()[0] == raw
    conn.close()


def test_batch_prevalidates_and_resumes_after_resource_stop(tmp_path):
    conn, raw, _ = fixture(tmp_path)
    conn.execute('INSERT INTO llm_scenario_review_events VALUES(?,?,?)', (8, 0, raw))
    conn.commit()
    base = {'expected_type':'text', 'expected_sha256':hashlib.sha256(raw.encode()).hexdigest()}
    first = dict(base, rowid=1, slot=7, sequence=3)
    second = dict(base, rowid=2, slot=8, sequence=0)
    manifest = {'source_identity':'a' * 64, 'rows':[first,second]}
    broken = {'source_identity':'a' * 64, 'rows':[first,dict(second, sequence=-1)]}
    with pytest.raises(ValueError, match='invalid_manifest'):
        subject.compact_batch(conn, broken, lambda: True)
    assert isinstance(conn.execute('SELECT body FROM llm_scenario_review_events WHERE rowid=1').fetchone()[0], str)
    checks = iter([True, True, False])
    first_pass = subject.compact_batch(conn, manifest, lambda: next(checks))
    assert first_pass['counts']['UPDATED'] == 1 and first_pass['counts']['RESOURCE_LIMIT'] == 1
    assert first_pass['last_rowid'] == 1
    resumed = subject.compact_batch(conn, manifest, lambda:True)
    assert resumed['counts']['UNCHANGED'] == 1 and resumed['counts']['UPDATED'] == 1
    conn.close()


def test_source_identity_changes_on_file_replacement(tmp_path):
    path = tmp_path / 'source.sqlite'
    path.write_bytes(b'original')
    before = subject.source_identity(path)
    other = tmp_path / 'replacement.sqlite'
    other.write_bytes(b'original')
    other.replace(path)
    assert subject.source_identity(path) != before


def test_batch_stops_and_reports_unexpected_codec_failure(tmp_path, monkeypatch):
    conn, raw, _ = fixture(tmp_path)
    row = {'rowid':1,'slot':7,'sequence':3,'expected_type':'text',
           'expected_sha256':hashlib.sha256(raw.encode()).hexdigest()}
    def fail(_):
        raise RuntimeError('codec failed')
    monkeypatch.setattr(subject, 'review_storage', fail)
    result = subject.compact_batch(conn, {'source_identity':'a'*64,'rows':[row]}, lambda: True)
    assert result['counts']['ERROR'] == 1 and result['last_rowid'] is None
    assert conn.execute('SELECT body FROM llm_scenario_review_events').fetchone()[0] == raw
    conn.close()


def test_resource_guard_fails_closed_on_active_process_or_space(tmp_path, monkeypatch):
    path = tmp_path / 'source.sqlite'
    path.write_bytes(b'fixture')
    identity = subject.source_identity(path)
    monkeypatch.setattr(subject.time, 'strftime', lambda *_: '30')
    monkeypatch.setattr(subject.os, 'statvfs', lambda *_: SimpleNamespace(f_bavail=5 * 1024**3, f_frsize=1))
    monkeypatch.setattr(subject, 'scenario_runner_idle', lambda: True)
    assert subject.resource_guard(path, identity) is True
    monkeypatch.setattr(subject, 'scenario_runner_idle', lambda: False)
    assert subject.resource_guard(path, identity) is False
    monkeypatch.setattr(subject, 'scenario_runner_idle', lambda: True)
    monkeypatch.setattr(subject.os, 'statvfs', lambda *_: SimpleNamespace(f_bavail=subject.MIN_FREE-1, f_frsize=1))
    assert subject.resource_guard(path, identity) is False


def test_process_scan_detects_python_and_scheduled_shell(tmp_path):
    proc = tmp_path / 'proc'
    proc.mkdir()
    first = proc / '101'
    first.mkdir()
    (first / 'cmdline').write_bytes(b'python\x00-m\x00unrelated\x00')
    assert subject.scenario_runner_idle(proc) is True
    (first / 'cmdline').write_bytes(b'python\x00-m\x00live.scenario_runner\x00--protect-only\x00')
    assert subject.scenario_runner_idle(proc) is False
    (first / 'cmdline').write_bytes(b'bash\x00-c\x00sleep 15; python -m live.scenario_runner --protect-only\x00')
    assert subject.scenario_runner_idle(proc) is False
    assert subject.scenario_runner_idle(tmp_path / 'missing') is False


def backed_model_pair(tmp_path):
    import random
    from live.scenario_review_storage import review_storage
    conn = sqlite3.connect(tmp_path / 'pair.sqlite', timeout=0)
    conn.executescript('CREATE TABLE llm_scenario_review_events(slot INTEGER,sequence INTEGER,body BLOB,PRIMARY KEY(slot,sequence));'
                       'CREATE TABLE llm_scenario_recovery_events(slot INTEGER,kind TEXT,body BLOB);')
    request = {'kind':'MODEL_REQUEST','phase':'single','request':{
        'prompt':random.Random(13).randbytes(16000).hex(),'timeout':50}}
    raw = {'kind':'MODEL_RAW','phase':'single','actual_request':dict(request['request'],timeout=49),'raw':'fixture'}
    source_text = json.dumps(request, sort_keys=True, allow_nan=False)
    raw_text = json.dumps(raw, sort_keys=True, allow_nan=False)
    source_body, raw_body = review_storage(source_text), review_storage(raw_text)
    conn.execute('INSERT INTO llm_scenario_review_events VALUES(?,?,?)', (7,0,source_body))
    conn.execute('INSERT INTO llm_scenario_review_events VALUES(?,?,?)', (7,1,raw_body))
    recovery = canonical(dict(raw, calculated_review_version=1, input_id='fixture'))
    conn.execute('INSERT INTO llm_scenario_recovery_events VALUES(?,?,?)', (7,'MODEL_RAW',pack(compact(raw_text,recovery,7,1))))
    conn.commit()
    def proof(rowid, sequence, body):
        data = body.encode() if isinstance(body,str) else body
        return {'rowid':rowid,'slot':7,'sequence':sequence,
                'expected_type':'text' if isinstance(body,str) else 'blob',
                'expected_sha256':hashlib.sha256(data).hexdigest()}
    return conn, proof(2,1,raw_body), proof(1,0,source_body), raw_text, recovery


def test_backed_model_pair_keeps_export_and_recovery_evidence(tmp_path):
    from utils.btc_review_event_references import MAGIC as REF_MAGIC, read_event
    conn, raw_proof, source_proof, raw, recovery = backed_model_pair(tmp_path)
    conn.row_factory = sqlite3.Row
    row = lambda: conn.execute('SELECT rowid AS source_rowid,slot,sequence,body FROM llm_scenario_review_events WHERE rowid=2').fetchone()
    before = _anchor_hash('llm_scenario_review_events', row(), conn)
    assert subject.compact_backed_model_pair(conn, raw_proof, source_proof, guard=lambda:True) == 'UPDATED'
    assert row()['body'].startswith(REF_MAGIC)
    assert read_event(row()['body'],conn,7,1,decode_legacy=review_text) == raw
    assert _anchor_hash('llm_scenario_review_events',row(),conn) == before
    recover_body = conn.execute('SELECT body FROM llm_scenario_recovery_events').fetchone()[0]
    assert read_recovery(recover_body,conn,7,decode_legacy=review_text,expected_kind='MODEL_RAW') == recovery
    assert subject.compact_backed_model_pair(conn,raw_proof,source_proof,guard=lambda:True) == 'UNCHANGED'
    assert subject.compact_backed_model_pair(conn,dict(raw_proof,expected_sha256='0'*64),source_proof,
                                             guard=lambda:True) == 'SOURCE_CHANGED'
    conn.close()


def test_backed_model_pair_rejects_changed_source_and_lost_guard(tmp_path):
    conn, raw_proof, source_proof, _, _ = backed_model_pair(tmp_path)
    original = conn.execute('SELECT body FROM llm_scenario_review_events WHERE rowid=2').fetchone()[0]
    wrong = dict(source_proof,expected_sha256='0'*64)
    assert subject.compact_backed_model_pair(conn,raw_proof,wrong,guard=lambda:True) == 'SOURCE_CHANGED'
    checks = iter((True,False))
    assert subject.compact_backed_model_pair(conn,raw_proof,source_proof,guard=lambda:next(checks)) == 'RESOURCE_LIMIT'
    assert conn.execute('SELECT body FROM llm_scenario_review_events WHERE rowid=2').fetchone()[0] == original
    conn.close()


def test_backed_model_pair_rejects_missing_unique_slot_index(tmp_path):
    conn, raw_proof, source_proof, _, _ = backed_model_pair(tmp_path)
    conn.executescript('ALTER TABLE llm_scenario_review_events RENAME TO old_events;'
                       'CREATE TABLE llm_scenario_review_events(slot INTEGER,sequence INTEGER,body BLOB);'
                       'INSERT INTO llm_scenario_review_events SELECT * FROM old_events ORDER BY rowid;'
                       'DROP TABLE old_events;')
    before = conn.execute('SELECT body FROM llm_scenario_review_events WHERE rowid=2').fetchone()[0]
    assert subject.compact_backed_model_pair(conn,raw_proof,source_proof,guard=lambda:True) == 'SOURCE_CHANGED'
    assert conn.execute('SELECT body FROM llm_scenario_review_events WHERE rowid=2').fetchone()[0] == before
    conn.close()


def test_backed_model_pair_holds_write_lock_on_source(tmp_path, monkeypatch):
    from utils import btc_review_event_references as refs
    conn, raw_proof, source_proof, raw, _ = backed_model_pair(tmp_path)
    original_compact = refs.compact
    checked = []
    def racing(*args):
        with sqlite3.connect(tmp_path / 'pair.sqlite', timeout=0) as other:
            with pytest.raises(sqlite3.OperationalError, match='locked'):
                other.execute("UPDATE llm_scenario_review_events SET body='{}' WHERE slot=7 AND sequence=0")
        checked.append(True)
        return original_compact(*args)
    monkeypatch.setattr(refs, 'compact', racing)
    assert subject.compact_backed_model_pair(conn,raw_proof,source_proof,guard=lambda:True) == 'UPDATED'
    assert checked == [True]
    value = conn.execute('SELECT body FROM llm_scenario_review_events WHERE slot=7 AND sequence=1').fetchone()[0]
    assert refs.read_event(value,conn,7,1,decode_legacy=review_text) == raw
    conn.close()


def test_backed_pair_batch_validates_every_pair_before_any_write(tmp_path):
    conn, raw_proof, source_proof, _, _ = backed_model_pair(tmp_path)
    before = conn.execute('SELECT body FROM llm_scenario_review_events WHERE rowid=2').fetchone()[0]
    pair = {'raw':raw_proof, 'source':source_proof}
    invalid = {'raw':dict(raw_proof,rowid=3,sequence=-1), 'source':source_proof}
    with pytest.raises(ValueError, match='invalid_manifest'):
        subject.compact_backed_pairs(conn,{'source_identity':'a'*64,'pairs':[pair,invalid]},lambda:True)
    assert conn.execute('SELECT body FROM llm_scenario_review_events WHERE rowid=2').fetchone()[0] == before
    result = subject.compact_backed_pairs(conn,{'source_identity':'a'*64,'pairs':[pair]},lambda:True)
    assert result['counts']['UPDATED'] == 1 and result['last_rowid'] == 2
    resumed = subject.compact_backed_pairs(conn,{'source_identity':'a'*64,'pairs':[pair]},lambda:True)
    assert resumed['counts']['UNCHANGED'] == 1 and resumed['last_rowid'] == 2
    conn.close()


def test_backed_pair_preserves_export_cursor_and_following_events(tmp_path):
    from observability.btc_scenario_export import export_once
    conn, raw_proof, source_proof, _, _ = backed_model_pair(tmp_path)
    conn.execute('DROP TABLE llm_scenario_recovery_events')
    conn.commit()
    source, state, spool = tmp_path/'pair.sqlite', tmp_path/'exporter.sqlite', tmp_path/'events.jsonl'
    assert export_once(source,state,spool)['status'] == 'OK'
    assert export_once(source,state,spool)['captured'] == 2
    with sqlite3.connect(state) as saved:
        before = saved.execute("SELECT seq,ref FROM cursors JOIN anchors USING(stream) "
                               "WHERE stream='llm_scenario_review_events'").fetchone()
    assert subject.compact_backed_model_pair(conn,raw_proof,source_proof,guard=lambda:True) == 'UPDATED'
    assert export_once(source,state,spool)['status'] == 'OK'
    with sqlite3.connect(state) as saved:
        assert saved.execute("SELECT seq,ref FROM cursors JOIN anchors USING(stream) "
                             "WHERE stream='llm_scenario_review_events'").fetchone() == before
    conn.execute('INSERT INTO llm_scenario_review_events VALUES(?,?,?)',
                 (7,2,json.dumps({'kind':'MODEL_ERROR','reason':'fixture'})))
    conn.commit()
    result = export_once(source,state,spool)
    assert result['status'] == 'OK' and result['captured'] == 1
    rows = [json.loads(line) for line in spool.read_text().splitlines() if line]
    events = [row for row in rows if row['event_type'] == 'btc.research.review']
    assert len(events) == 3 and len({row['event_id'] for row in events}) == 3
    conn.close()


def test_backed_pair_cli_uses_identity_and_bounded_manifest(tmp_path, monkeypatch, capsys):
    import io
    from utils import btc_review_retention
    conn, raw_proof, source_proof, _, _ = backed_model_pair(tmp_path)
    conn.close()
    source = tmp_path / 'pair.sqlite'
    manifest = {'source_identity':subject.source_identity(source), 'pairs':[{'raw':raw_proof,'source':source_proof}]}
    monkeypatch.setattr(subject, 'SOURCE', source)
    monkeypatch.setattr(subject, 'resource_guard', lambda *a:True)
    monkeypatch.setattr(btc_review_retention, '_backup_idle', lambda *a:True)
    monkeypatch.setenv('PRISM_BTC_STORAGE_APPROVED', '1')
    monkeypatch.setattr(subject.sys, 'stdin', io.TextIOWrapper(io.BytesIO(json.dumps(manifest).encode())))
    subject.main()
    result = json.loads(capsys.readouterr().out)
    assert result['status'] == 'COMPLETE' and result['counts']['UPDATED'] == 1


def test_backed_pair_cli_defers_while_backup_active(tmp_path, monkeypatch, capsys):
    import io
    from utils import btc_review_retention
    conn, raw_proof, source_proof, _, _ = backed_model_pair(tmp_path)
    before = conn.execute('SELECT body FROM llm_scenario_review_events WHERE rowid=2').fetchone()[0]
    conn.close()
    source = tmp_path / 'pair.sqlite'
    manifest = {'source_identity':subject.source_identity(source), 'pairs':[{'raw':raw_proof,'source':source_proof}]}
    monkeypatch.setattr(subject, 'SOURCE', source)
    monkeypatch.setattr(subject, 'resource_guard', lambda *a:True)
    monkeypatch.setattr(btc_review_retention, '_backup_idle', lambda *a:False)
    monkeypatch.setenv('PRISM_BTC_STORAGE_APPROVED', '1')
    monkeypatch.setattr(subject.sys, 'stdin', io.TextIOWrapper(io.BytesIO(json.dumps(manifest).encode())))
    subject.main()
    assert json.loads(capsys.readouterr().out)['status'] == 'RESOURCE_LIMIT'
    with sqlite3.connect(source) as db:
        assert db.execute('SELECT body FROM llm_scenario_review_events WHERE rowid=2').fetchone()[0] == before


def test_pair_planner_requires_read_only_and_matches_backed_rows(tmp_path):
    conn, raw_proof, source_proof, _, _ = backed_model_pair(tmp_path)
    with pytest.raises(ValueError, match='invalid_manifest'):
        subject.plan_backed_pairs(conn, 'a'*64)
    conn.execute('PRAGMA query_only=ON')
    plan = subject.plan_backed_pairs(conn, 'a'*64)
    assert plan['manifest']['pairs'] == [{'raw':raw_proof,'source':source_proof}]
    assert plan['scanned'] == 2 and plan['unreadable'] == 0 and plan['last_scanned_rowid'] == 2
    assert plan['unreadable_rowids'] == [] and plan['unreferenced_raw_rowids'] == []
    assert not subject.plan_backed_pairs(conn, 'a'*64, after_rowid=2)['manifest']['pairs']
    for invalid in (-1, True, '2', 9223372036854775808):
        with pytest.raises(ValueError, match='invalid_manifest'):
            subject.plan_backed_pairs(conn, 'a'*64, after_rowid=invalid)
    conn.close()


def test_pair_planner_limits_batches_and_reports_unreadable_rows(tmp_path):
    import random
    from live.scenario_review_storage import review_storage
    path = tmp_path/'planner.sqlite'
    with sqlite3.connect(path) as conn:
        conn.execute('CREATE TABLE llm_scenario_review_events(slot INTEGER,sequence INTEGER,body BLOB,PRIMARY KEY(slot,sequence))')
        prompt = random.Random(29).randbytes(8000).hex()
        for slot in range(21):
            request = {'kind':'MODEL_REQUEST','phase':'single','request':{'prompt':prompt,'timeout':50}}
            raw = {'kind':'MODEL_RAW','phase':'single','actual_request':dict(request['request'],timeout=49),'raw':'x'}
            conn.execute('INSERT INTO llm_scenario_review_events VALUES(?,?,?)',
                         (slot,0,review_storage(json.dumps(request,sort_keys=True,allow_nan=False))))
            conn.execute('INSERT INTO llm_scenario_review_events VALUES(?,?,?)',
                         (slot,1,review_storage(json.dumps(raw,sort_keys=True,allow_nan=False))))
        conn.execute('INSERT INTO llm_scenario_review_events VALUES(?,?,?)',(99,0,'not json'))
    with sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True) as conn:
        conn.execute('PRAGMA query_only=ON')
        first = subject.plan_backed_pairs(conn,'b'*64)
        assert len(first['manifest']['pairs']) == 20 and first['scanned'] == 40 and first['last_scanned_rowid'] == 40
        second = subject.plan_backed_pairs(conn,'b'*64,after_rowid=first['last_scanned_rowid'])
        assert len(second['manifest']['pairs']) == 1 and second['scanned'] == 3 and second['unreadable'] == 1
        assert second['unreadable_rowids'] == [43]


def test_pair_planner_reports_raw_without_unique_matching_request(tmp_path):
    path = tmp_path/'unmatched.sqlite'
    with sqlite3.connect(path) as conn:
        conn.execute('CREATE TABLE llm_scenario_review_events(slot INTEGER,sequence INTEGER,body BLOB,PRIMARY KEY(slot,sequence))')
        raw = {'kind':'MODEL_RAW','phase':'single','actual_request':{'timeout':1},'raw':'x'}
        conn.execute('INSERT INTO llm_scenario_review_events VALUES(?,?,?)',(1,0,json.dumps(raw,sort_keys=True)))
    with sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True) as conn:
        conn.execute('PRAGMA query_only=ON')
        result = subject.plan_backed_pairs(conn,'c'*64)
        assert result['manifest']['pairs'] == [] and result['unreadable'] == 0
        assert result['unreferenced_raw_rowids'] == [1]
