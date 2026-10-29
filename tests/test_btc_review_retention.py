import hashlib
import json
import sqlite3

import pytest

from utils.btc_review_retention import candidates


def test_only_old_complete_flat_waits_become_candidates():
    conn = sqlite3.connect(':memory:')
    conn.executescript('CREATE TABLE llm_scenario_review_tapes(slot INTEGER PRIMARY KEY, body TEXT);'
                       'CREATE TABLE llm_scenario_decisions(slot INTEGER PRIMARY KEY, context TEXT, proposal TEXT, outcome TEXT);')
    context = {'scenario_id': None, 'positions': [], 'pending_entries': [], 'unknown_intents': [],
               'historical_unsettled': [], 'incident_epoch_blocked': False, 'recovery_observing': False}
    variants = [context, dict(context, positions=[{'quantity': 1}]),
                {key: value for key, value in context.items() if key != 'unknown_intents'},
                dict(context, incident_epoch_blocked=True), dict(context, recovery_observing=True), context]
    for slot, ctx in zip((1, 2, 3, 4, 5, 20), variants):
        conn.execute('INSERT INTO llm_scenario_review_tapes VALUES(?,?)', (slot, '{"tape":1}'))
        conn.execute('INSERT INTO llm_scenario_decisions VALUES(?,?,?,?)',
                     (slot, json.dumps(ctx), json.dumps({'action': 'WAIT'}), json.dumps({'status': 'wait'})))
    now = 7 * 86400 + 10 * 300
    result = candidates(conn, now)
    assert result['cutoff_slot'] == 10
    assert result['last_scanned_slot'] == 5
    assert result['candidates'] == [{'rowid': 1, 'slot': 1,
                                     'tape_sha256': hashlib.sha256(b'{"tape":1}').hexdigest(),
                                     'decision_sha256': result['candidates'][0]['decision_sha256']}]
    assert candidates(conn, now, after_slot=1)['candidates'] == []
    assert candidates(conn, now, limit=1)['last_scanned_slot'] == 1
    assert conn.execute('SELECT COUNT(*) FROM llm_scenario_review_tapes').fetchone()[0] == 6
    old_digest = result['candidates'][0]['decision_sha256']
    conn.execute('UPDATE llm_scenario_decisions SET context=? WHERE slot=1',
                 (json.dumps(dict(context, now=1)),))
    assert candidates(conn, now)['candidates'][0]['decision_sha256'] != old_digest
    from utils.btc_review_references import MAGIC
    conn.execute('UPDATE llm_scenario_review_tapes SET body=? WHERE slot=1', (MAGIC + b'corrupt',))
    with pytest.raises(ValueError):
        candidates(conn, now)


@pytest.mark.parametrize('kwargs', [{'now': float('nan')}, {'now': 1}, {'now': True},
                                    {'now': 700000, 'limit': 0}, {'now': 700000, 'after_slot': True}])
def test_invalid_boundaries_fail_closed(kwargs):
    with sqlite3.connect(':memory:') as conn:
        with pytest.raises(ValueError, match='retention_input_invalid'):
            candidates(conn, **kwargs)
