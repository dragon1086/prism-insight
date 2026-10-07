import json
import sqlite3
from types import SimpleNamespace

import pytest

from live.scenario_contract import response_schema, validate_wire_proposal
from live.scenario_llm import propose, ScenarioModelError
from live.scenario_preview import response_contract
from live.scenario_recovery import ensure_schema, capture_wire
from tests.test_scenario_llm import context, wire


def recovery_context():
    c=context()
    c.update(recovery_contract_version=1,scenario_risk_fraction=.005,
             recovery=dict(phase='OBSERVING',first_observation=False,eligible=True,
                           entry_deadline=1200,changed_evidence={'timeframes.30m.forming.ma10':{'before':100,'now':101}}))
    return c


@pytest.mark.parametrize('stage,fraction',[(0,.005),(1,.01),(2,.02)])
def test_automatic_normalization_contract_uses_host_offered_cap(stage,fraction):
    c=recovery_context()
    c.update(recovery_contract_version=2,scenario_risk_fraction=fraction)
    c['recovery'].update(current_stage=max(0,stage-1),offered_stage=stage,automatic_normal_resume=True)
    schema=response_schema(c)
    assert 'recovery' in schema['properties']
    contract=response_contract(c);rules=' '.join(contract['rules'])
    assert 'No later adds/retries or automatic normal resume' not in rules
    assert f'initial_equity*{fraction:g}' in rules
    assert 'host-offered' in rules
    p=wire(c);p['recovery']=dict(decision='OBSERVE',reason='관찰',changed_evidence=[],
                               counterevidence='변화 부족',invalidation='근거 훼손')
    assert validate_wire_proposal(p,c)['recovery']==p['recovery']
    c['recovery']['first_observation']=True
    assert response_schema(c)['properties']['action']['enum']==['WAIT']


def test_normal_stage_holding_contract_does_not_keep_one_shot_add_ban():
    c=context(True)
    c.update(recovery_contract_version=2,scenario_risk_fraction=.02,
             recovery={'phase':'CONSUMED','current_stage':2,'entry_deadline':900})
    contract=response_contract(c);rules=' '.join(contract['rules'])
    assert contract['expires_at']==c['now']+300
    assert 'same-scenario additions' in rules
    assert 'Preserve 0.005 scenario budget' not in rules
    p=wire(c,'EXIT');p['recovery']={'bad':'optional holding metadata'}
    assert validate_wire_proposal(p,c)['action']=='EXIT'


def test_assembled_v2_policy_separates_stage_authority_and_market_proposal():
    c=recovery_context();c['recovery_contract_version']=2;calls=[]
    def generate(**kwargs):
        calls.append(kwargs)
        p=wire(c);p['recovery']=dict(decision='OBSERVE',reason='관찰',changed_evidence=[],
                                   counterevidence='추세 미확인',invalidation='근거 훼손')
        return SimpleNamespace(text=json.dumps(p))
    propose({'valid':True,'as_of_ms':1000000},c,response_contract(c),generate=generate,clock=lambda:1000)
    policy=' '.join(calls[0]['system_prompt'].split())
    for text in ('When recovery_contract_version=2','host-offered scenario_risk_fraction',
                 'does not expire the entire recovery program','NORMAL stage 2',
                 'not a statistical proof','never raises the risk budget of an active scenario'):
        assert text in policy


def test_recovery_contract_budget_and_deadline_are_host_owned():
    c=recovery_context();contract=response_contract(c)
    assert contract['expires_at']==1200
    assert 'recovery' in response_schema(c)['properties']
    assert '0.005' in ' '.join(contract['rules'])
    assert 'PROBE' in json.dumps(contract['recovery'])
    c['recovery']['first_observation']=True
    assert response_schema(c)['properties']['action']['enum']==['WAIT']


def test_actual_request_and_invalid_raw_are_durable_before_parse(tmp_path):
    c=recovery_context();conn=sqlite3.connect(tmp_path/'audit.sqlite');ensure_schema(conn)
    conn.execute('CREATE TABLE llm_scenario_decisions(slot INTEGER,context TEXT)')
    conn.execute('INSERT INTO llm_scenario_decisions VALUES(?,?)',(3,json.dumps(c)));conn.commit()
    with capture_wire(conn,3,lambda:1000):
        with pytest.raises(ScenarioModelError):
            propose({'valid':True,'as_of_ms':1000000},c,response_contract(c),
                    generate=lambda **kw:SimpleNamespace(text='not JSON'),clock=lambda:1000)
    rows={kind:json.loads(body) for kind,body in conn.execute('SELECT kind,body FROM llm_scenario_recovery_events')}
    assert rows['MODEL_RAW']['raw']=='not JSON'
    assert json.loads(rows['MODEL_REQUEST']['user_prompt'])['contract_context']['input_id']==c['input_id']
    assert rows['MODEL_REQUEST']['model']=='gpt-6-luna'
    assert 'MODEL_ERROR' in rows


def test_bad_recovery_metadata_does_not_change_valid_exit_fields():
    c=context(True);c.update(recovery_contract_version=1,recovery={'phase':'CONSUMED'})
    p=wire(c,'EXIT');p['recovery']={'bad':'optional holding metadata'}
    result=validate_wire_proposal(p,c)
    assert result['action']=='EXIT'
    assert result['recovery']==p['recovery']


def test_recovery_still_rejects_malformed_economic_field():
    c=recovery_context();p=wire(c);p['recovery']=None;p['leverage']=100
    with pytest.raises(ValueError):validate_wire_proposal(p,c)


def test_expired_probe_permission_does_not_expire_holding_responses():
    c=context(True);c.update(recovery_contract_version=1,scenario_risk_fraction=.005,
                            recovery={'phase':'CONSUMED','entry_deadline':900})
    contract=response_contract(c)
    assert contract['expires_at']==1300
    rules=' '.join(contract['rules'])
    assert 'expires_at no later than recovery.entry_deadline' not in rules
    assert 'even after the original entry deadline' in rules
