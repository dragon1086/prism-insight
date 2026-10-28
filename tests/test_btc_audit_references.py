import json
import sqlite3

import pytest

from utils.btc_audit_references import TAG, canonical, compact, read_audit, sha


def fixture():
    snapshot = {'frame': {'last': 'x' * 5000}, 'korean': '가격'}
    context = {'input_id': 'fixture', 'state': 'y' * 5000}
    event = {'snapshot': snapshot, 'context': context, 'input_id': 'fixture',
             'snapshot_hash': sha(canonical(snapshot)), 'context_hash': sha(canonical(context)),
             'input_id_hash': sha(canonical('fixture')), 'schema_version': 1}
    conn = sqlite3.connect(':memory:')
    conn.execute('CREATE TABLE llm_scenario_decisions(slot INTEGER,snapshot TEXT,context TEXT)')
    conn.execute('INSERT INTO llm_scenario_decisions VALUES(?,?,?)',
                 (100, json.dumps(snapshot, sort_keys=True), json.dumps(context, sort_keys=True)))
    return conn, event


def test_exact_roundtrip_and_input_id_remains_queryable():
    conn, event = fixture()
    raw = canonical(event)
    snapshot, context = conn.execute('SELECT snapshot,context FROM llm_scenario_decisions').fetchone()
    encoded = compact(raw, snapshot, context)
    assert len(encoded) < len(raw) and json.loads(encoded)[TAG] == 1
    assert conn.execute("SELECT json_extract(?, '$.input_id')", (encoded,)).fetchone() == ('fixture',)
    assert read_audit(encoded, conn, 100) == raw


@pytest.mark.parametrize('change', ['missing', 'changed_snapshot', 'changed_context', 'duplicate'])
def test_missing_ambiguous_or_changed_source_fails_closed(change):
    conn, event = fixture()
    snapshot, context = conn.execute('SELECT snapshot,context FROM llm_scenario_decisions').fetchone()
    encoded = compact(canonical(event), snapshot, context)
    if change == 'missing':
        conn.execute('DELETE FROM llm_scenario_decisions')
    elif change == 'changed_snapshot':
        conn.execute("UPDATE llm_scenario_decisions SET snapshot='{}'")
    elif change == 'changed_context':
        conn.execute("UPDATE llm_scenario_decisions SET context='{}'")
    else:
        conn.execute('INSERT INTO llm_scenario_decisions VALUES(?,?,?)', (100, snapshot, context))
    with pytest.raises(ValueError):
        read_audit(encoded, conn, 100)


def test_malformed_and_tampered_reference_fail_closed():
    conn, event = fixture()
    s, c = conn.execute('SELECT snapshot,context FROM llm_scenario_decisions').fetchone()
    encoded = compact(canonical(event), s, c)
    body = json.loads(encoded)
    body['input_id'] = 'modified'
    with pytest.raises(ValueError):
        read_audit(canonical(body), conn, 100)
    body = json.loads(encoded)
    body['snapshot'] = {'$decision_field': 'context'}
    with pytest.raises(ValueError):
        read_audit(canonical(body), conn, 100)
    with pytest.raises(ValueError):
        read_audit(encoded, conn, True)


def test_nonmatching_or_noncanonical_source_is_never_factored():
    conn, event = fixture()
    s, c = conn.execute('SELECT snapshot,context FROM llm_scenario_decisions').fetchone()
    raw = canonical(event)
    assert compact(raw, '{}', c) == raw
    event['snapshot_hash'] = 'bad'
    assert compact(canonical(event), s, c) == canonical(event)
    raw = json.dumps(event, indent=2)
    assert compact(raw, s, c) == raw
    legacy = canonical({'note': 'mention ' + TAG})
    assert read_audit(legacy, conn, 100) == legacy


def test_exporter_preserves_cursor_and_exact_original_hash(tmp_path):
    from observability import btc_scenario_export as export
    db, state, spool = (tmp_path / n for n in ('source.sqlite', 'state.sqlite', 'spool.jsonl'))
    original_conn, event = fixture()
    snapshot, context = original_conn.execute('SELECT snapshot,context FROM llm_scenario_decisions').fetchone()
    raw = canonical(event)
    encoded = compact(raw, snapshot, context)
    with sqlite3.connect(db) as conn:
        conn.execute('CREATE TABLE llm_scenario_decisions(slot INTEGER,snapshot TEXT,context TEXT)')
        conn.execute('CREATE TABLE llm_scenario_audit_events(kind TEXT,decision_slot INTEGER,observed_at REAL,body TEXT)')
        conn.execute('INSERT INTO llm_scenario_decisions VALUES(?,?,?)', (100, snapshot, context))
    assert export.export_once(db, state, spool)['status'] == 'OK'
    with sqlite3.connect(db) as conn:
        conn.execute('INSERT INTO llm_scenario_audit_events VALUES(?,?,?,?)', ('decision_input', 100, 1, raw))
    assert export.export_once(db, state, spool)['captured'] == 1
    records = [json.loads(line) for line in spool.read_text().splitlines() if line.strip()]
    row = next(item for item in records if item.get('attributes', {}).get('source_kind') == 'decision_input')
    assert row['attributes']['body_sha256'] == export._hash(raw)
    assert 'fixture' not in spool.read_text() and 'x' * 100 not in spool.read_text()
    with sqlite3.connect(db) as conn:
        conn.execute('UPDATE llm_scenario_audit_events SET body=?', (encoded,))
    assert export.export_once(db, state, spool)['status'] == 'OK'
    with sqlite3.connect(db) as conn:
        conn.execute("UPDATE llm_scenario_decisions SET snapshot='{}'")
    assert export.export_once(db, state, spool)['status'] == 'UNAVAILABLE'
