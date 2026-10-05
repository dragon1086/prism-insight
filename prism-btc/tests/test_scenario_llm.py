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


@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
@pytest.mark.parametrize('choice', ['open_split', 'adjust_split', 'retain_far', 'missing_levels'])
def test_assembled_obstacle_review_and_valid_management_choices(side, choice):
    """Contract/transport test with scripted responses, not an LLM behavior test."""
    from live.scenario_preview import response_contract
    sign = 1 if side == 'LONG' else -1
    ctx = context(choice != 'open_split')
    ctx.update(side=side, mark_price=60000, previous_hard_stop=60000-sign*500,
               positions=[] if choice == 'open_split' else [dict(price=60000, quantity=.01)])
    action = 'OPEN' if choice == 'open_split' else 'ADJUST'
    if choice in ('retain_far', 'missing_levels'):
        action = 'WAIT'
    result = wire(ctx, action)
    if action in ('OPEN', 'ADJUST'):
        result.update(side=side, hard_stop=60000-sign*400,
                      take_profits=[dict(id='near', price=60000+sign*190, fraction=.4),
                                    dict(id='extended', price=60000+sign*500, fraction=.3)])
    ctx['current_plan'] = {'take_profits': [dict(id='old-far', price=60000+sign*500, fraction=1)]}
    snapshot = {'valid': True, 'as_of_ms': 1000000, 'timeframes': {}}
    if choice != 'missing_levels':
        snapshot['timeframes']['4h'] = {'forming': {'ma35': 60000+sign*200}}
    original = json.loads(json.dumps(ctx))
    calls = []
    def generate(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(text=json.dumps(result))
    parsed = propose(snapshot, ctx, response_contract(ctx), generate=generate, clock=lambda: 1000)
    accepted = validate_scenario(parsed, ctx)
    assert accepted['action'] == action
    assert ctx == original
    sent = json.loads(calls[0]['user_prompt'])
    assert sent['market_snapshot'] == snapshot
    assert sent['contract_context']['current_plan'] == ctx['current_plan']
    policy = ' '.join(calls[0]['system_prompt'].split())
    for rule in (
        'On every OPEN, holding review and ADJUST',
        'available 4h/12h/1d/1w MA10/35',
        'NOT a veto does not mean ignore exit obstacles',
        'potential reaction zone, not guaranteed strong support/resistance',
        'trivial moving-MA drift',
        'For SHORT, a nearer TP is higher and an extended TP lower; reverse for LONG',
        'retaining an all-size TP beyond a material obstacle',
        'including a holding WAIT that leaves that TP unchanged',
        'name the relevant available higher-frame obstacle',
        'Missing levels do not force ADJUST',
    ):
        assert rule in policy
    if action in ('OPEN', 'ADJUST'):
        assert sum(tp['fraction'] for tp in accepted['take_profits']) == pytest.approx(.7)
        assert accepted['risk']['budget'] == 200
        assert sign * (accepted['take_profits'][0]['price']-60000) < 200
        assert sign * (accepted['take_profits'][1]['price']-60000) > 200


@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
def test_obstacle_tp_revision_keeps_halted_protection_and_stop_guard(side):
    sign = 1 if side == 'LONG' else -1
    ctx = context(True)
    ctx.update(side=side, mark_price=60000, previous_hard_stop=60000-sign*500,
               positions=[dict(price=60000, quantity=.01)], new_risk_blocked=True)
    result = wire(ctx, 'ADJUST')
    result.update(side=side, hard_stop=60000-sign*400,
                  take_profits=[dict(id='near', price=60000+sign*190, fraction=.5)])
    def check():
        parsed = propose({'valid': True, 'as_of_ms': 1000000}, ctx, {}, clock=lambda: 1000,
                         generate=lambda **kwargs: SimpleNamespace(text=json.dumps(result)))
        return validate_scenario(parsed, ctx)
    assert check()['action'] == 'ADJUST'
    result['entries'] = [dict(id='new-risk', price=60000, quantity=.001)]
    with pytest.raises(ValueError):
        check()
    result['entries'] = []
    result['hard_stop'] = 60000-sign*600
    with pytest.raises(ValueError):
        check()


def test_assembled_prompt_keeps_reassessment_before_stop_and_safety_priority():
    calls = []
    ctx = context(True)
    ctx.update(accounting_status='pending', new_risk_blocked=True)
    def generate(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(text=json.dumps(wire(ctx)))
    accepted = propose({'valid': True, 'as_of_ms': 1000000}, ctx, {}, clock=lambda: 1000,
                       generate=generate)
    assert validate_scenario(accepted, ctx)['action'] == 'WAIT'
    policy = ' '.join(calls[0]['system_prompt'].split())
    assert 'SHORT upward thresholds below the effective hard stop' in policy
    assert 'LONG downward thresholds above the effective hard stop' in policy
    assert 'at or beyond that stop belongs to post-exit/new-scenario assessment' in policy
    assert 'Never delay the hard stop for a reassessment condition' in policy
    assert 'Safety/accounting restrictions take priority over this explanation' in policy


@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
def test_assembled_stop_geometry_uses_proposed_not_superseded_stop(side):
    """Specification regression: prose geometry is not a new host validator."""
    sign = 1 if side == 'LONG' else -1
    old_stop = 60000-sign*800
    proposed_stop = 60000-sign*500
    stale_review = 60000-sign*650
    valid_review = 60000-sign*400
    assert sign*(stale_review-old_stop) > 0
    assert sign*(stale_review-proposed_stop) < 0
    assert sign*(valid_review-proposed_stop) > 0
    ctx = context(True)
    ctx.update(side=side, mark_price=60000, previous_hard_stop=old_stop,
               positions=[dict(price=60000, quantity=.01)])
    result = wire(ctx, 'ADJUST')
    result.update(side=side, hard_stop=proposed_stop,
                  take_profits=[dict(id='tp', price=60000+sign*500, fraction=.5)])
    calls = []
    def generate(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(text=json.dumps(result))
    parsed = propose({'valid': True, 'as_of_ms': 1000000}, ctx, {}, clock=lambda: 1000,
                     generate=generate)
    assert validate_scenario(parsed, ctx)['hard_stop'] == proposed_stop
    policy = ' '.join(calls[0]['system_prompt'].split())
    assert 'WAIT uses retained protection; OPEN/ADJUST uses the proposed hard stop' in policy
    assert 'never the superseded stop when tightening' in policy
    assert 'ordering does not guarantee a five-minute review before SL' in policy
    assert 'MarkPrice can differ from the observed trade price' in policy
