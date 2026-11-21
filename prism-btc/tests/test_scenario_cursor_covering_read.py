"""A faster access path must retain every historical cursor and its exact order."""
import json
import pytest
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


@pytest.mark.parametrize('damage',['hash','epoch'])
def test_nonlatest_corrupt_cursor_still_blocks_monitor(flat_incident,damage):
    i=flat_incident;transition(i);b=i['broker']
    assert epoch.monitor(b,refresh=True)is not None
    previous=_events(b.conn,epoch.FINANCE_CURSOR)[-1]
    bad=dict(previous,sha256='0'*64)
    if damage=='epoch':
        bad=dict(previous,body=dict(previous['body'],epoch_id='unapproved-epoch'))
        bad['sha256']=epoch.digest(bad['body'])
    for value in [bad,previous]:
        b.conn.execute('INSERT INTO llm_scenario_broker_evidence VALUES(?,?,?)',(epoch.FINANCE_CURSOR,i['now'][0],json.dumps(value)))
    b.conn.commit()
    assert epoch.monitor(b,refresh=True)is None
    assert epoch.halted(b.conn)



def test_cursor_iterator_decodes_only_as_consumed_and_preserves_remaining_rows(live,monkeypatch):
    from live.scenario_management_quarantine import _iter_events
    from live import scenario_management_quarantine as policy
    broker,_=live;c=broker.conn
    for i in range(30):
        c.execute('INSERT INTO llm_scenario_broker_evidence VALUES(?,?,?)',
                  (epoch.FINANCE_CURSOR,1000-i,json.dumps({'i':i})))
    original=policy.json.loads;seen=[]
    def decode(raw):
        value=original(raw);seen.append(value['i']);return value
    monkeypatch.setattr(policy.json,'loads',decode)
    values=_iter_events(c,epoch.FINANCE_CURSOR)
    assert seen==[]
    assert next(values)=={'i':0} and seen==[0]
    assert list(values)==[{'i':i}for i in range(1,30)]
    assert seen==list(range(30))
