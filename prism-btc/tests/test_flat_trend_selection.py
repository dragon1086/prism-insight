from copy import deepcopy

import pytest

from live.scenario_calculated_review import _flat_selection_prompt
from tests.test_flat_select_prompt_trim import flat, system
from tests.test_scenario_calculated_review import run


def test_flat_prepared_selection_separates_entry_evidence_from_future_targets():
    ctx=flat();ctx['prepared_entry_plans_version']=1;before=deepcopy(ctx)
    prompt=_flat_selection_prompt(system(),ctx)
    assert 'weigh missed participation against supplied risk' in prompt
    assert 'future trade risk, not automatically\nmissing entry evidence' in prompt
    assert 'WAIT for absent edge or material counterevidence' in prompt
    assert 'never select implausible targets for ratio alone' in prompt
    for rule in ['eligible=false is excluded','Keep the candidate\'s original',
                 'Pure wire/economic validation is NOT recovery authorization',
                 'Partial SL remains conditional reduceOnly protection']:
        assert rule in prompt
    assert ctx==before


@pytest.mark.parametrize('flag',[None,False,True,2,'1'])
def test_legacy_or_unknown_flag_does_not_change_selection(flag):
    ctx=flat();ctx['prepared_entry_plans_version']=flag
    assert _flat_selection_prompt(system(),ctx)==_flat_selection_prompt(system(),flat())


@pytest.mark.parametrize(('key','value'),[
    ('scenario_id','held'),('positions',[{'quantity':.01}]),
    ('pending_entries',[{'id':'pending'}]),('unknown_intents',True),
    ('protection_ok',False),('new_risk_blocked',True),('accounting_status','pending')])
def test_active_pending_or_unsafe_state_keeps_original_prompt(key,value):
    ctx=flat();ctx.update(prepared_entry_plans_version=1);ctx[key]=value
    assert _flat_selection_prompt(system(),ctx)==system()


def test_two_phase_review_changes_selection_only():
    _,_,old=run(ctx=flat())
    ctx=flat();ctx['prepared_entry_plans_version']=1
    result,_,new=run(ctx=ctx)
    assert result['orders_submitted']==0
    assert new[0]['system_prompt']==old[0]['system_prompt']
    assert new[0]['response_schema']==old[0]['response_schema']
    assert 'weigh missed participation'not in new[0]['system_prompt']
    assert 'weigh missed participation'in new[1]['system_prompt']
    normalized=[]
    for request in [old[1],new[1]]:
        schema=deepcopy(request['response_schema'])
        tokens=schema['properties']['selection_token']['enum']
        schema['properties']['selection_token']['enum']=[v.split(':',1)[0]for v in tokens]
        normalized.append(schema)
    assert normalized[0]==normalized[1]
