from copy import deepcopy
import json

import pytest

from live import scenario_llm
from live.scenario_calculated_review import SELECT_PROMPT
from tests.test_scenario_calculated_review import context,run


def flat():
    ctx=context();ctx.update(target_status=[],unknown_intents=False,legacy_fenced=False,
        protection_ok=True,accounting_status='confirmed')
    return ctx


def system(ctx=None):
    return scenario_llm.system_instructions(flat()if ctx is None else ctx)+SELECT_PROMPT


def test_flat_policy_is_assembled_without_holding_or_string_splicing():
    ctx=flat();before=deepcopy(ctx);text=system(ctx)
    assert 'Flat-entry comparison only' in text
    assert 'SUSTAINED DIRECTION' not in text
    assert len(text.encode())<=12000
    assert 'API response_schema' in text and 'host alone authorizes and executes' in text
    assert ctx==before


@pytest.mark.parametrize(('key','value'),[
    ('scenario_id','active'),('positions',[{'quantity':.01}]),('pending_entries',[{'id':'pending'}]),
    ('unknown_intents',True),('unknown_intents',None),('legacy_fenced',True),
    ('protection_ok',False),('new_risk_blocked',True),('accounting_status','pending')])
def test_policy_never_changes_host_permissions_or_context(key,value):
    ctx=flat();ctx[key]=value;before=deepcopy(ctx);text=system(ctx)
    assert 'host alone authorizes and executes' in text
    assert 'missing evidence stays UNKNOWN' in text
    if key in {'scenario_id','positions','pending_entries'}:
        assert 'Flat-entry comparison only' not in text
    assert ctx==before


@pytest.mark.parametrize('key',['scenario_id','positions','pending_entries'])
def test_missing_lifecycle_facts_do_not_get_flat_policy(key):
    ctx=flat();del ctx[key]
    assert 'Flat-entry comparison only'not in system(ctx)


def test_real_review_uses_one_scoped_policy_and_authoritative_api_schema():
    ctx=flat();before=deepcopy(ctx);result,events,calls=run(ctx=ctx)
    assert len(calls)==2 and result['orders_submitted']==0
    base=scenario_llm.system_instructions(ctx)
    for call in calls:
        assert call['system_prompt'].startswith(base)
        assert 'SUSTAINED DIRECTION'not in call['system_prompt']
        assert len(call['system_prompt'].encode())<=12000
        payload=json.loads(call['user_prompt'])
        assert payload['response_contract']['authority'].startswith('API response_schema')
        assert 'properties'not in payload['response_contract']
        assert 'properties'in call['response_schema']
    assert ctx==before


def test_flat_review_replays_and_revalidates_without_order():
    from tests.test_scenario_review_guard import context as guard_context,taped,check
    ctx=guard_context();ctx['unknown_intents']=False
    result=check(taped(ctx=ctx))
    assert result['validated']['action']=='OPEN'and result['receipt']['orders_submitted']==0


def test_model_view_omits_only_stale_plan_risk_and_duplicate_api_schema():
    from live.scenario_calculated_review import _model_payload
    from live.scenario_model_input import restore_model_input
    ctx=flat();ctx['current_plan']=dict(side='SHORT',hard_stop=85000,risk={'available':999999})
    before=deepcopy(ctx)
    schema={'type':'object','properties':{'unchanged':{'type':'number'}},'required':['unchanged']}
    view=restore_model_input(_model_payload(dict(valid=True,as_of_ms=1000000),ctx,schema))
    assert view['contract_context']['current_plan']==dict(side='SHORT',hard_stop=85000)
    assert view['response_contract']['authority'].startswith('API response_schema')
    assert ctx==before and schema['required']==['unchanged']


def test_recovery_view_preserves_permission_and_changes_without_pnl_bookkeeping():
    from live.scenario_calculated_review import _model_payload
    from live.scenario_model_input import restore_model_input
    ctx=flat();ctx.update(recovery_contract_version=2,last_closed_trade={'net_pnl':-999})
    ctx['recovery']=dict(phase='OBSERVING',eligible=True,first_observation=False,
        entry_deadline=1200,changed_evidence={'price':{'before':1,'now':2}},
        promotion_evidence={'net_pnl':100},historical_outcomes=[{'net_pnl':-999}],risk_fraction=.5)
    before=deepcopy(ctx)
    view=restore_model_input(_model_payload(dict(valid=True,as_of_ms=1000000),ctx,{}))['contract_context']
    assert view['last_closed_trade']==ctx['last_closed_trade']
    assert view['recovery']=={k:v for k,v in ctx['recovery'].items()
                             if k in {'phase','eligible','first_observation','entry_deadline','changed_evidence'}}
    assert ctx==before
