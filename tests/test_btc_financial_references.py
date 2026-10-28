import json
import sqlite3

import pytest

from utils.btc_financial_references import MAX_BYTES, TAG, compact, restore


def fixture():
    execution = {'execId': 'execution-1', 'detail': 'x' * 2000}
    trade = {'id': 'transaction-1', 'tradeId': 'execution-1', 'detail': 'y' * 2000}
    funding = {'id': 'funding-1', 'type': 'SETTLEMENT', 'detail': 'z' * 2000}
    return {'raw_executions': [execution], 'raw_transactions': [trade, funding],
            'trades': [{'execution_id': 'execution-1', 'raw_execution': execution, 'raw_transaction': trade}],
            'funding': [{'transaction_id': 'funding-1', 'raw': funding}], 'unmatched_ids': []}


def serialized(obj):
    return json.dumps(obj, allow_nan=False, sort_keys=True)


def test_both_trade_and_funding_restore_exactly():
    original = serialized(fixture())
    encoded = compact(original)
    assert json.loads(encoded)[TAG] == 1
    assert len(encoded) < len(original)
    assert restore(encoded) == original


@pytest.mark.parametrize('change', ['duplicate_execution', 'duplicate_trade', 'duplicate_funding',
                                   'different_raw', 'missing_trade', 'missing_funding'])
def test_ambiguous_or_missing_raw_remains_original(change):
    data = fixture()
    if change == 'duplicate_execution':
        data['raw_executions'].append(dict(data['raw_executions'][0]))
    elif change == 'duplicate_trade':
        data['raw_transactions'].append(dict(data['raw_transactions'][0]))
    elif change == 'duplicate_funding':
        data['raw_transactions'].append(dict(data['raw_transactions'][1]))
    elif change == 'different_raw':
        data['trades'][0]['raw_execution'] = {'execId': 'execution-1', 'detail': 'different'}
    elif change == 'missing_trade':
        data['raw_executions'].clear()
    else:
        data['raw_transactions'].pop()
    original = serialized(data)
    assert compact(original) == original


def test_changed_or_missing_reference_fails_closed():
    encoded = compact(serialized(fixture()))
    data = json.loads(encoded)
    data['body']['raw_executions'][0]['detail'] = 'modified'
    with pytest.raises(ValueError):
        restore(serialized(data))
    data = json.loads(encoded)
    data['body']['raw_transactions'].clear()
    with pytest.raises(ValueError):
        restore(serialized(data))


def test_plain_and_overlarge_evidence_do_not_change():
    original = serialized({'other': 'ordinary'})
    assert compact(original) == restore(original) == original
    overlarge = '{"' + TAG + '":' + 'x' * (MAX_BYTES + 1)
    assert compact(overlarge) == overlarge
    with pytest.raises(ValueError):
        restore(overlarge)


def test_export_restores_original_and_keeps_anchor(tmp_path):
    from observability import btc_scenario_export as export
    db, state, spool = (tmp_path / name for name in ('source.sqlite', 'state.sqlite', 'spool.jsonl'))
    with sqlite3.connect(db) as conn:
        conn.execute('CREATE TABLE llm_scenario_broker_evidence(kind,captured_at,body)')
    export.export_once(db, state, spool)
    original = serialized(fixture())
    encoded = compact(original)
    assert export._anchor_hash('llm_scenario_broker_evidence', {'body': original}) == export._anchor_hash(
        'llm_scenario_broker_evidence', {'body': encoded})
    with sqlite3.connect(db) as conn:
        conn.execute('INSERT INTO llm_scenario_broker_evidence VALUES(?,?,?)', ('financial', 1700000000, encoded))
    assert export.export_once(db, state, spool)['captured'] == 1
    assert export.export_once(db, state, spool)['status'] != 'UNAVAILABLE'
    events = [json.loads(line) for line in spool.read_text().splitlines() if line.strip()]
    record = next(row for row in events if row.get('attributes', {}).get('source_kind') == 'financial')
    assert record['attributes']['body_sha256'] == export._hash(original)
    assert 'execution-1' not in spool.read_text()
    broken = json.loads(encoded)
    broken['body']['raw_executions'][0]['detail'] = 'bad'
    with sqlite3.connect(db) as conn:
        conn.execute('UPDATE llm_scenario_broker_evidence SET body=?', (serialized(broken),))
    assert export.export_once(db, state, spool)['status'] == 'UNAVAILABLE'
