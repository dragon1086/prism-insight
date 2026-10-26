"""Fresh-entry framing and existing goal mechanics; no model behavior claims."""
from copy import deepcopy
import json
from types import SimpleNamespace

import pytest

from live import scenario_calculated_review as review
from live.scenario_entry_goal import prepare_goal_source
from live.scenario_model_input import restore_model_input
from live.scenario_preview import response_contract
from tests.test_scenario_allocation_review import allocation_wire
from tests.test_scenario_entry_contract_wire import definition
from tests.test_scenario_ma_context import NOW, point, snapshot
from tests.test_scenario_review_guard import context, wire


def inputs(side='LONG'):
    ctx = context()
    ctx.update(entry_contract_version=2, now=NOW/1000, input_captured_at=NOW/1000,
               account_captured_at=NOW/1000, entry_goal_decision_slot=6666666)
    snap = snapshot('4h', before=point('4h', 2, slow=83800 if side=='LONG' else 83000))
    snap['valid'] = True
    prepare_goal_source(snap, ctx)
    return snap, ctx


def run_pair(side='LONG', count=2):
    snap, ctx = inputs(side)
    wait = wire(ctx, 'WAIT')
    wait.update(entry_contract=None, thesis_assessment=None, expires_at=ctx['now']+100)
    candidates = []
    for mode, price in [('BOUNDED_BEFORE', 83700 if side=='LONG' else 83100),
                        ('REFERENCE_EXTENSION', 84000 if side=='LONG' else 82800)][:count]:
        raw = allocation_wire(ctx)
        thesis = definition(side)
        thesis.pop('expected_progress')
        thesis.update(version=2, goal=dict(mode=mode, reference=dict(frame='4h', phase='confirmed', ma='ma35')))
        raw.update(side=side, hard_stop=82900 if side=='LONG' else 84000,
                   entry_contract=thesis, thesis_assessment=None, expires_at=ctx['now']+100,
                   take_profits=[dict(id='target', price=price, fraction=1 if mode=='BOUNDED_BEFORE' else .5)])
        candidates.append(raw)
    before = deepcopy(candidates)
    calls = []
    def generate(**request):
        calls.append(request)
        if len(calls)==1:
            reply = dict(wait_proposal=wait, candidates=candidates,
                         no_candidate_reason='' if candidates else 'No supported price structure')
        else:
            bundle = restore_model_input(json.loads(request['user_prompt']))['calculated_candidates']
            assert len(bundle['candidates']) == count
            assert all(c['eligible'] for c in bundle['candidates'])
            chosen = bundle['candidates'][-1]
            reply = dict(selection_token=f"{chosen['id']}:{chosen['receipt_sha256']}", rationale='Fixture selection only')
        return SimpleNamespace(text=json.dumps(reply))
    result = review.research_review(snap, ctx, response_contract(ctx), generate=generate,
        record=lambda _: None, clock=lambda: ctx['now'], research_mode='economic-reject-calculated-v1')
    assert candidates == before
    return result, calls


@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
def test_new_goals_compare_without_rewriting_prices_or_select(side, monkeypatch):
    result, calls = run_pair(side)
    marker = review.FLAT_PURPOSE_PROMPT
    assert marker in calls[0]['system_prompt'] and marker not in calls[1]['system_prompt']
    assert result['selected']['raw_allocation_proposal']['entry_contract']['goal']['mode']=='REFERENCE_EXTENSION'
    assert result['orders_submitted']==0 and result['executable'] is False
    assert len(calls)==2 and calls[0]['response_schema']['properties']['candidates']['maxItems']==2
    monkeypatch.setattr(review, 'FLAT_PURPOSE_PROMPT', '')
    baseline, old_calls = run_pair(side)
    assert baseline['selected'] == result['selected']
    assert old_calls[1] == calls[1]
    assert old_calls[0]['user_prompt'] == calls[0]['user_prompt']
    assert old_calls[0]['response_schema'] == calls[0]['response_schema']
    assert calls[0]['system_prompt'].replace(marker, '', 1) == old_calls[0]['system_prompt']


@pytest.mark.parametrize('count', [0, 1])
def test_one_or_no_candidate_stays_valid(count):
    result, calls = run_pair(count=count)
    assert len(calls)==(2 if count else 1)
    assert len(result['candidates']['candidates'])==count
    assert result['orders_submitted']==0


@pytest.mark.parametrize('state', ['holding', 'pending', 'first_observation', 'blocked', 'unknown_permission', 'legacy'])
def test_framing_excluded_outside_fresh_v2_open(state):
    snap, ctx = inputs()
    if state in ('holding', 'pending'):
        ctx.update(scenario_id='active', revision=1, side='LONG', previous_hard_stop=82900)
        ctx['positions' if state=='holding' else 'pending_entries']=[dict(id='existing',price=83410,quantity=.01)]
    elif state=='first_observation':
        ctx['recovery']=dict(first_observation=True)
    elif state=='blocked':
        ctx['new_risk_blocked']=True
    elif state=='unknown_permission':
        ctx.pop('new_risk_blocked')
    else:
        ctx['entry_contract_version']=1
    calls=[]
    def capture(**request):
        calls.append(request)
        raise RuntimeError('stop before model')
    with pytest.raises(ValueError, match='research_model_call_failed'):
        review.research_review(snap, ctx, response_contract(ctx), generate=capture,
            record=lambda _: None, clock=lambda: ctx['now'], research_mode='economic-reject-calculated-v1')
    assert len(calls)==1 and review.FLAT_PURPOSE_PROMPT not in calls[0]['system_prompt']


@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
def test_band_location_objectives_and_conditional_entry_reach_draft(side):
    _, calls = run_pair(side, count=1)
    prompt = ' '.join(calls[0]['system_prompt'].split())
    for phrase in (
        'Inside the band, the design hypothesis is greater range/chop risk',
        'Missing measured win-rate data remains unknown',
        'Outside the band, when the existing directional trend-following conditions',
        'seek favorable whole-trade payoff and participation in a larger move',
        'Band exit alone is not an entry signal',
        'a loose limit cap outside the band alone does not establish an outside entry',
        'its activation must require the claimed passage',
        'MA10-to-MA35 gap traversal remains bounded',
    ):
        assert phrase in prompt
    assert review.FLAT_PURPOSE_PROMPT not in calls[1]['system_prompt']
