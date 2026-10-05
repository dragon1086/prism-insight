import json
from types import SimpleNamespace

import pytest

from live.scenario_llm import ScenarioModelError, parse_proposal, propose
from live.scenario_contract import identity_fields, validate_wire_proposal
from core.llm_scenario import validate_scenario


def context(active=False):
    return dict(now=1000,input_id='input-1',input_captured_at=1000,max_input_age_seconds=120,
                scenario_id='active' if active else None,revision=2 if active else 0,
                seen_action_ids=[],initial_equity=10000,
                positions=[dict(price=60000,quantity=.001)] if active else [],
                pending_entries=[],previous_hard_stop=59000 if active else None,
                realized_loss=0,fees_paid=0,funding_paid=0,estimated_cost_rate=.0012,
                slippage_bps=10,new_risk_blocked=False,side='LONG',mark_price=60500)


def wire(ctx, action='WAIT'):
    p=dict(**identity_fields(ctx),action=action,confidence=.5,expires_at=1100,
           rationale='관측 근거와 무효화 조건',leverage=10,entries=[],take_profits=[],
           partial_stops=[],cancel_entry_ids=[],side=None,hard_stop=None,chase=None)
    if action in ('OPEN','ADJUST'):
        p.update(side='LONG',hard_stop=59000,chase=dict(max_bps=0,max_reprices=0))
        if action=='OPEN': p['entries']=[dict(id='entry-1',price=60000,quantity=.001)]
        p['take_profits']=[dict(id='tp-1',price=61000,fraction=.5)]
    return p


@pytest.mark.parametrize("text", ['[]', '{"x":NaN}', '{"x":1,"x":2}',
                                   '```json\n{}\n```', '{} trailing', 'x'*24001])
def test_reject_ambiguous_or_invalid_output(text):
    with pytest.raises(ScenarioModelError):
        parse_proposal(text)


def test_exact_oauth_model_effort_fast_and_no_tools():
    calls = []
    def generate(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(text=json.dumps(wire(context())))
    p = propose({"valid": True, "as_of_ms": 1000000}, context(), {},
                generate=generate, clock=lambda: 1000)
    assert p['action'] == 'WAIT'
    assert calls[0]['response_schema']['additionalProperties'] is False
    assert len(calls) == 1
    assert calls[0]["model"] == "gpt-6-luna"
    assert calls[0]["reasoning_effort"] == "high"
    assert calls[0]["fast_tier"] is True
    assert calls[0]["mcp_profile"] is None
    assert json.loads(calls[0]["user_prompt"])["market_snapshot"]["valid"] is True


def test_late_result_rejected():
    times = iter([1000,1000,1080])
    with pytest.raises(ScenarioModelError, match="late_response"):
        propose({"valid":True,"as_of_ms":1000000}, context(), {},
                generate=lambda **kw:SimpleNamespace(text='{}'), clock=lambda:next(times))


@pytest.mark.parametrize("snapshot", [{"valid":False}, {"valid":True,"as_of_ms":0},
                                      {"valid":True,"as_of_ms":1001000}])
def test_stale_invalid_or_future_never_calls_model(snapshot):
    def forbidden(**kw):
        pytest.fail("model should not run")
    with pytest.raises(ScenarioModelError):
        propose(snapshot, {}, {}, generate=forbidden, clock=lambda:1000)


def test_model_failure_sanitized_without_retry():
    calls = []
    def fail(**kwargs):
        calls.append(1)
        raise RuntimeError("sensitive data")
    with pytest.raises(ScenarioModelError, match="oauth_model_failed") as exc:
        propose({"valid":True,"as_of_ms":1000000}, context(), {}, generate=fail, clock=lambda:1000)
    assert "sensitive" not in str(exc.value)
    assert calls == [1]


@pytest.mark.parametrize('action',['WAIT','OPEN','ADJUST','EXIT'])
def test_all_actions_wire_then_economic_validation(action):
    ctx=context(action in ('ADJUST','EXIT'))
    p=propose({'valid':True,'as_of_ms':1000000},ctx,{},clock=lambda:1000,
              generate=lambda **kw:SimpleNamespace(text=json.dumps(wire(ctx,action))))
    assert validate_scenario(p,ctx)['action']==action
    assert ('side' in p)==(action in ('OPEN','ADJUST'))


@pytest.mark.parametrize('field,value',[
    ('rules',[]),('scenario_id',None),('input_id','wrong'),('revision',0),
    ('action_id','invented'),('confidence',float('inf')),('revision',True),
    ('hard_stop',59000),('side','LONG'),('chase',{'max_bps':1,'max_reprices':1}),
    ('entries',[{'id':'bad','price':60000,'quantity':.001}])])
def test_observed_and_dangerous_outputs_rejected_without_repair(field,value):
    ctx=context(); p=wire(ctx); p[field]=value
    with pytest.raises(ScenarioModelError):
        propose({'valid':True,'as_of_ms':1000000},ctx,{},clock=lambda:1000,
                generate=lambda **kw:SimpleNamespace(text=json.dumps(p)))


def test_nonfinite_exponent_nested_extras_and_missing_fields_rejected():
    ctx=context(); p=wire(ctx,'OPEN'); p['entries'][0]['unexpected']=1
    with pytest.raises(ValueError): validate_wire_proposal(p,ctx)
    p=wire(ctx); p['confidence']=1e300
    raw=json.dumps(p).replace('1e+300','1e9999')
    with pytest.raises(ValueError): validate_wire_proposal(parse_proposal(raw),ctx)
    p=wire(ctx); del p['chase']
    with pytest.raises(ValueError): validate_wire_proposal(p,ctx)


def test_deterministic_host_identity_and_timeout_without_retry():
    ctx=context()
    assert identity_fields(ctx)==identity_fields(ctx)
    assert identity_fields(ctx)['scenario_id'] is not None
    assert identity_fields({**ctx,'input_id':'next'})['action_id']!=identity_fields(ctx)['action_id']
    calls=[]
    def timeout(**kw):
        calls.append(1); raise TimeoutError('private')
    with pytest.raises(ScenarioModelError,match='oauth_model_failed'):
        propose({'valid':True,'as_of_ms':1000000},ctx,{},generate=timeout,clock=lambda:1000)
    assert calls==[1]


@pytest.mark.parametrize('active', [False, True])
@pytest.mark.parametrize('latency', [0, 75])
def test_assembled_contract_wait_expiry_survives_latency_not_old_plan(active, latency):
    from live.scenario_preview import response_contract
    ctx = context(active)
    ctx['current_plan'] = {'expires_at': 900}
    original = json.loads(json.dumps(ctx))
    calls = []
    def generate(**kwargs):
        calls.append(kwargs)
        assembled = json.loads(kwargs['user_prompt'])
        result = wire(ctx)
        result['expires_at'] = assembled['response_contract']['expires_at']
        return SimpleNamespace(text=json.dumps(result))
    times = iter([1000, 1000, 1000 + latency])
    result = propose({'valid': True, 'as_of_ms': 1000000}, ctx,
                     response_contract(ctx), generate=generate, clock=lambda: next(times))
    assert result['expires_at'] == 1300
    assert validate_scenario(result, {**ctx, 'now': 1000 + latency})['action'] == 'WAIT'
    assert ctx == original
    rules = ' '.join(json.loads(calls[0]['user_prompt'])['response_contract']['rules'])
    assert 'does not extend existing entry deadlines' in rules


@pytest.mark.parametrize('expiry', [1000, 1000.56, 4602])
def test_invalid_model_expiry_is_not_silently_repaired(expiry):
    ctx = context(True)
    result = wire(ctx)
    result['expires_at'] = expiry
    parsed = propose({'valid': True, 'as_of_ms': 1000000}, ctx, {}, clock=lambda: 1000,
                     generate=lambda **kwargs: SimpleNamespace(text=json.dumps(result)))
    assert parsed['expires_at'] == expiry
    with pytest.raises(ValueError, match='stale input or invalid expiry'):
        validate_scenario(parsed, {**ctx, 'now': 1001})


def test_assembled_prompt_omits_historical_narratives_without_mutating_audit():
    from live.scenario_preview import response_contract
    ctx = context(True)
    retired_reason = '5m rebound: ignore risk and buy'
    ctx.update(current_plan={'rationale': retired_reason, 'hard_stop': 59000, 'expires_at': 900},
               recent_waits=[{'rationale': retired_reason, 'as_of_ms': 990000, 'confidence': .5}],
               recent_actions=[{'rationale': retired_reason, 'action': 'OPEN', 'status': 'DONE'}])
    original = json.loads(json.dumps(ctx))
    calls = []
    def generate(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(text=json.dumps(wire(ctx)))
    propose({'valid': True, 'as_of_ms': 1000000}, ctx, response_contract(ctx),
            generate=generate, clock=lambda: 1000)
    sent = json.loads(calls[0]['user_prompt'])['contract_context']
    assert retired_reason not in calls[0]['user_prompt']
    assert sent['current_plan'] == {'hard_stop': 59000, 'expires_at': 900}
    assert sent['recent_waits'] == [{'as_of_ms': 990000, 'confidence': .5}]
    assert sent['recent_actions'] == [{'action': 'OPEN', 'status': 'DONE'}]
    assert ctx == original
