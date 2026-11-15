"""Selection prose is audit evidence, never a replacement for candidate rationale."""
from copy import deepcopy
from functools import partial
import json
from types import SimpleNamespace

import pytest

from analysis.scenario_review_tape import record_review
from live.scenario_calculated_review import research_review
from live.scenario_contract import validate_wire_proposal
from live.scenario_preview import response_contract
from live.scenario_review_guard import guard_review
from tests.test_scenario_review_guard import context, wire
from tests.test_scenario_review_guard import model_receipt_hash


@pytest.mark.parametrize('action,selection_reason', [
    ('OPEN', '진입하지 않고 관망합니다.'), ('ADJUST', '전량 종료합니다.'),
    ('EXIT', 'SHORT 잔여 물량은 기존 보호 주문을 유지합니다.'), ('WAIT', '전량 종료합니다.'),
])
def test_selected_candidate_reason_survives_contradictory_selection_prose(action, selection_reason):
    ctx = context()
    if action != 'OPEN':
        ctx.update(scenario_id='active', side='LONG', revision=1,
            positions=[dict(price=83410, quantity=.01)], previous_hard_stop=82900)
    snapshot = dict(valid=True, as_of_ms=1000000)
    contract = response_contract(ctx)
    calls=[]
    def generate(**request):
        calls.append(request)
        if len(calls)==1:
            proposal = wire(ctx, action if action != 'WAIT' else 'EXIT')
            proposal['rationale'] = '선택 후보 고유 근거: ' + proposal['action']
            wait = wire(ctx, 'WAIT')
            wait['rationale'] = '기존 계획 변경 없음'
            value = dict(wait_proposal=wait, candidates=[proposal], no_candidate_reason='')
        else:
            data=json.loads(request['user_prompt'])['calculated_candidates']
            chosen=data['wait'] if action=='WAIT' else data['candidates'][0]
            value=dict(selection_token=f"{chosen['id']}:{model_receipt_hash(chosen)}", rationale=selection_reason)
        return SimpleNamespace(text=json.dumps(value))
    envelope=record_review(research_review, snapshot, ctx, contract, generate=generate,
        record=lambda _:None, clock=lambda:1000)
    original=deepcopy(envelope)
    assert envelope['outcome']['status']=='COMPLETE'
    checked=guard_review(snapshot,ctx,contract,envelope,dict(ctx,now=1001,account_captured_at=1001))
    selected=envelope['outcome']['result']['selected']['raw_proposal']
    assert checked['payload']==validate_wire_proposal(selected,ctx)
    assert checked['payload']['action']==action
    assert checked['validated']['rationale']==selected['rationale']
    assert envelope['outcome']['result']['selection']['rationale']==selection_reason
    assert envelope==original
    assert checked['receipt']['execution_authorized'] is False


def test_failed_exit_is_not_blocked_or_rewritten_by_hold_selection_prose():
    from tests.test_scenario_entry_contract_wire import taped
    def mutate(raw,phase):
        if phase==2:
            raw['rationale']='현재 포지션과 보호 주문을 유지합니다.'
    args,_=taped(mutate=mutate)
    fresh=dict(args[1],now=1001,account_captured_at=1001)
    checked=guard_review(*args,fresh,bindings={'test':'entry-contract'},research_mode='economic-reject-calculated-v1')
    assert checked['payload']['action']=='EXIT'
    assert checked['payload']['thesis_assessment']['status']=='FAILED'
    assert checked['payload']['rationale']==args[3]['outcome']['result']['selected']['raw_proposal']['rationale']


@pytest.mark.parametrize('mode',['legacy','economic-reject-single-v1'])
def test_no_selection_call_preserves_own_wait_reason(mode):
    ctx=context()
    snapshot=dict(valid=True,as_of_ms=1000000)
    contract=response_contract(ctx)
    wait=wire(ctx,'WAIT')
    wait['rationale']='원래 WAIT 근거'
    raw=(dict(wait_proposal=wait,candidates=[],no_candidate_reason='별도 후보 부재 설명')
         if mode=='legacy' else dict(proposal=wait))
    review=partial(research_review,research_mode=mode)
    envelope=record_review(review,snapshot,ctx,contract,generate=lambda **_:SimpleNamespace(text=json.dumps(raw)),
        record=lambda _:None,clock=lambda:1000)
    assert envelope['outcome']['status']=='COMPLETE'
    assert envelope['calls']==1
    checked=guard_review(snapshot,ctx,contract,envelope,dict(ctx,now=1001,account_captured_at=1001),research_mode=mode)
    assert checked['payload']['rationale']=='원래 WAIT 근거'


def test_selection_prose_remains_hash_and_replay_protected_audit_evidence():
    from backtest.scenario_tape import digest
    from tests.test_scenario_review_guard import taped
    args=taped()
    envelope=deepcopy(args[3])
    envelope['outcome']['result']['selection']['rationale']='변조된 선택 설명'
    envelope['sha256']=digest({k:v for k,v in envelope.items() if k!='sha256'})
    with pytest.raises(ValueError):
        guard_review(*args[:3],envelope,dict(args[1],now=1001,account_captured_at=1001),bindings={'code':'test'})
