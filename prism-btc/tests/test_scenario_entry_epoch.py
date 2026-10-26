"""Second incident cutover on actual broker/runtime; no network or real orders."""
from copy import deepcopy
import json
import pytest
from tests.test_scenario_management_quarantine_runtime import incident  # noqa: F401

from live import scenario_incident_epoch as policy
from live.scenario_control import read_control
from tests.test_scenario_incident_epoch import flat_incident, transition, new_runtime, restarted  # noqa: F401


@pytest.fixture
def entry_incident(flat_incident, monkeypatch):
    i = flat_incident
    first = transition(i)
    i['now'][0] += 300
    i['exchange'].now = i['now'][0]
    runtime, broker, price, calls = new_runtime(i)
    assert runtime.tick()['status'] == 'wait'
    original = i['exchange'].place_order
    def lost(**kw):
        raise TimeoutError('ambiguous offline transport')
    monkeypatch.setattr(i['exchange'], 'place_order', lost)
    i['now'][0] += 300
    i['exchange'].now = i['now'][0]
    price[0] += 1
    assert runtime.tick()['status'] == 'intent_pending'
    child = broker.children()[0]
    assert child['kind']=='entry' and child['status']=='UNKNOWN'
    monkeypatch.setattr(i['exchange'], 'place_order', original)
    monkeypatch.setattr(policy, 'ENTRY_SCENARIO', child['scenario_id'])
    monkeypatch.setattr(policy, 'ENTRY_UNKNOWN', child['link_id'])
    i['now'][0] += 301
    i['exchange'].now = i['now'][0]
    proof = deepcopy(i['proof'])
    proof.update(captured_at=i['now'][0], launcher_manifest=dict(epoch_protocol=1, code_sha256=policy.code_digest()))
    approval = deepcopy(i['approval'])
    approval.update(approval_id='entry-separation', epoch_id='entry-observation-epoch',
        scenario_id=child['scenario_id'], unknown_link_id=child['link_id'],
        previous_receipt_sha256=policy.digest(first), unknown_request_sha256=policy.digest(child['request']),
        code_sha256=policy.code_digest(), issued_at=i['now'][0], expires_at=i['now'][0]+3600,
        incident_sha256=policy.digest(policy._snapshot(broker, child['scenario_id'])),
        quiescence_sha256=policy.digest(proof))
    i.update(entry_broker=broker, entry_runtime=runtime, entry_child=child, entry_proof=proof,
        entry_approval=approval, first_receipt=first,
        all_intents=list(broker.conn.execute('SELECT * FROM llm_scenario_intents')),
        all_children=list(broker.conn.execute('SELECT * FROM llm_scenario_children')))
    return i


def extend(i):
    return policy.cutover(i['entry_broker'], approval=i['entry_approval'], quiescence_probe=lambda:i['entry_proof'])


def test_append_only_two_incidents_resume_observation_and_fence_old_worker(entry_incident):
    i=entry_incident;old=i['entry_broker'];result=extend(i)
    assert len(policy.receipts(old.conn))==2 and policy.receipts(old.conn)[0]==i['first_receipt']
    assert result['previous_receipt_sha256']==policy.digest(i['first_receipt'])
    assert list(old.conn.execute('SELECT * FROM llm_scenario_intents'))==i['all_intents']
    assert list(old.conn.execute('SELECT * FROM llm_scenario_children'))==i['all_children']
    assert policy.pending(old.conn)==[] and len(policy.historical(old.conn))==2
    assert old.children()==[]
    assert extend(i)==result
    before=deepcopy(i['exchange'].writes)
    with pytest.raises(ValueError,match='stale_worker'):
        old._write('place_order',reduceOnly=False,orderLinkId='stale')
    assert i['exchange'].writes==before
    i['now'][0]+=300;i['exchange'].now=i['now'][0]
    runtime,broker,price,calls=new_runtime(i)
    original=runtime.propose
    runtime.propose=lambda snap,ctx: dict(original(snap,ctx),scenario_id=ctx.get('scenario_id') or 'third-scenario')
    assert runtime.tick()['status']=='wait'
    i['now'][0]+=300;i['exchange'].now=i['now'][0];price[0]+=1
    assert runtime.tick()['status']=='intent_pending'
    assert runtime.state()['active']['scenario_id']=='third-scenario'
    assert len(broker.children())==1 and broker.children()[0]['scenario_id']=='third-scenario'
    assert not policy.halted(broker.conn)


@pytest.mark.parametrize('which',['original','entry'])
def test_late_order_in_either_incident_halts_without_rewriting_original(entry_incident,which):
    i=entry_incident;extend(i);broker=restarted(i)
    child=i['original']['children'] if which=='original' else i['entry_child']
    i['exchange'].orders[child['link_id']]=dict(child['request'],orderId='late',orderStatus='New',cumExecQty='0',leavesQty=child['request']['qty'])
    before=list(broker.conn.execute('SELECT * FROM llm_scenario_children'))
    policy.monitor(broker,refresh=True)
    assert policy.halted(broker.conn) and policy.new_risk_blocked(broker)
    assert list(broker.conn.execute('SELECT * FROM llm_scenario_children'))==before


@pytest.mark.parametrize('fault',['wrong_predecessor','same_epoch','wrong_request','no_risk_acceptance','worker_alive','position','history'])
def test_extension_denies_missing_or_changed_authority(entry_incident,fault):
    i=entry_incident;b=i['entry_broker']
    if fault=='wrong_predecessor':i['entry_approval']['previous_receipt_sha256']='0'*64
    elif fault=='same_epoch':i['entry_approval']['epoch_id']=i['first_receipt']['epoch_id']
    elif fault=='wrong_request':i['entry_approval']['unknown_request_sha256']='0'*64
    elif fault=='no_risk_acceptance':i['entry_approval']['residual_hidden_order_risk_accepted']=False
    elif fault=='worker_alive':
        import os
        i['entry_proof']['stopped_workers'][0]['pid']=os.getpid()
        i['entry_approval']['quiescence_sha256']=policy.digest(i['entry_proof'])
    elif fault=='position':i['exchange'].size=.01
    else:
        child=i['entry_child']
        i['exchange'].orders[child['link_id']]=dict(child['request'],orderId='found',orderStatus='Cancelled',cumExecQty='0',leavesQty='0')
    before=read_control(b.conn)
    with pytest.raises((ValueError,AssertionError)):
        extend(i)
    assert len(policy.receipts(b.conn))==1 and read_control(b.conn)==before


@pytest.mark.parametrize('table',['llm_scenario_broker_evidence','llm_scenario_state','llm_scenario_control'])
def test_extension_commit_failure_preserves_old_epoch(entry_incident,table):
    i=entry_incident;c=i['entry_broker'].conn
    if table=='llm_scenario_broker_evidence':
        c.execute("CREATE TRIGGER fail_transition BEFORE INSERT ON llm_scenario_broker_evidence WHEN NEW.kind='incident_entry_epoch_v1' BEGIN SELECT RAISE(ABORT,'fixture'); END")
    else:
        c.execute(f"CREATE TRIGGER fail_transition BEFORE UPDATE ON {table} BEGIN SELECT RAISE(ABORT,'fixture'); END")
    c.commit();before=read_control(c)
    with pytest.raises(Exception):extend(i)
    assert len(policy.receipts(c))==1 and read_control(c)==before
    assert list(c.execute('SELECT * FROM llm_scenario_children'))==i['all_children']


@pytest.mark.parametrize('which',['original','entry'])
def test_link_only_late_execution_from_either_history_is_detected(entry_incident,which):
    i=entry_incident;extend(i);broker=restarted(i)
    child=i['original']['children'] if which=='original' else i['entry_child']
    i['now'][0]+=11;i['exchange'].now=i['now'][0]
    i['exchange'].fills.append(dict(execId='late-proof',orderId='unknown-order-id',orderLinkId=child['link_id'],
        symbol='BTCUSDT',side=child['request']['side'],execType='Trade',execQty='.001',execPrice='100',
        execFee='.01',execTime=str(int(i['now'][0]*1000))))
    policy.monitor(broker,refresh=True)
    assert policy.halted(broker.conn)
    assert any('financial_sources' in r.get('evidence',{}) for r in policy._events(broker.conn,policy.HALT))


def test_health_reports_two_historical_incidents_not_current_order_stall(entry_incident):
    from datetime import datetime, timezone
    from live.healthcheck import _check_execution_stalled
    i=entry_incident;extend(i)
    issue=_check_execution_stalled(i['entry_broker'].conn,'demo',datetime.fromtimestamp(i['now'][0]+3600,timezone.utc))
    assert issue['code']=='historical_unsettled_excluded' and issue['level']=='warn'
    assert '2건' in issue['msg']


@pytest.mark.parametrize('fault',['remove_extension','rehash_predecessor'])
def test_receipt_chain_tamper_fails_closed(entry_incident,fault):
    i=entry_incident;extend(i);c=i['entry_broker'].conn
    if fault=='remove_extension':c.execute('DELETE FROM llm_scenario_broker_evidence WHERE kind=?',(policy.ENTRY_KIND,))
    else:
        event=policy._events(c,policy.ENTRY_KIND)[0]
        event['body']['previous_receipt_sha256']='0'*64;event['sha256']=policy.digest(event['body'])
        c.execute('UPDATE llm_scenario_broker_evidence SET body=? WHERE kind=?',(json.dumps(event),policy.ENTRY_KIND))
    c.commit()
    with pytest.raises(ValueError):policy.epoch(c)
