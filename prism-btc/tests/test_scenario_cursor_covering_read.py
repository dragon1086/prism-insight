"""A faster access path must retain every historical cursor and its exact order."""
import json
from live import scenario_incident_epoch as epoch
from live.scenario_management_quarantine import _events
from tests.test_scenario_execution import live
from tests.test_scenario_incident_epoch import flat_incident,transition
from tests.test_scenario_management_quarantine_runtime import incident


def test_covering_read_keeps_identical_history_and_rowid_order(live):
    broker,_=live;c=broker.conn
    kinds=[epoch.FINANCE_CURSOR,'account']
    for i in range(60):
        c.execute('INSERT INTO llm_scenario_broker_evidence VALUES(?,?,?)',
            (kinds[i%2],1000-i,json.dumps({'i':i,'padding':'x'*(300 if i%2==0 else 16000)})))
    expected=[json.loads(r[0])for r in c.execute('SELECT body FROM llm_scenario_broker_evidence WHERE kind=? ORDER BY rowid',(epoch.FINANCE_CURSOR,))]
    traced=[];c.set_trace_callback(traced.append)
    assert _events(c,epoch.FINANCE_CURSOR)==expected
    c.set_trace_callback(None)
    sql=next(q for q in traced if q.startswith('SELECT body FROM llm_scenario_broker_evidence'))
    plan=' '.join(str(r[-1])for r in c.execute('EXPLAIN QUERY PLAN '+sql))
    assert 'COVERING INDEX llm_scenario_broker_cursor_cover' in plan
    assert [r['i']for r in _events(c,'account')]==list(range(1,60,2))


def test_nonlatest_corrupt_cursor_still_blocks_monitor(flat_incident):
    i=flat_incident;transition(i);b=i['broker']
    assert epoch.monitor(b,refresh=True)is not None
    previous=_events(b.conn,epoch.FINANCE_CURSOR)[-1]
    bad=dict(previous,sha256='0'*64)
    for value in [bad,previous]:
        b.conn.execute('INSERT INTO llm_scenario_broker_evidence VALUES(?,?,?)',(epoch.FINANCE_CURSOR,i['now'][0],json.dumps(value)))
    b.conn.commit()
    assert epoch.monitor(b,refresh=True)is None
    assert epoch.halted(b.conn)
