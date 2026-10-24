"""Assembled judgment paths establish entry edge before choosing size."""
import json
from types import SimpleNamespace

import pytest

from live.scenario_calculated_review import research_review
from live.scenario_llm import propose, SYSTEM_PROMPT, FLAT_ENTRY_PROMPT
from live.scenario_preview import response_contract
from tests.test_scenario_calculated_review import context,wire


def assert_sequence(text):
    text=' '.join(text.split())
    assert "Evaluate a new entry's structural edge before its size" in text
    assert 'size cannot make entry preferable to WAIT' in text
    assert 'smaller risk-scaled exploratory entry' not in text
    assert 'when mixed, compare smaller risk-scaled exposure with WAIT' not in text
    assert 'Allocate less new-entry risk to weaker evidence' not in text
    assert 'Missing fee evidence stays UNKNOWN, not zero and not an automatic WAIT instruction' in text
    assert 'risk cap including costs' in text or 'host calculates size including costs' in text


@pytest.mark.parametrize('mode',['legacy','allocation-calculated','economic-reject-calculated-v1',
                                'action-allocation-single-v1'])
def test_actual_calculated_and_single_request_assembly_keeps_edge_first(mode):
    ctx=context()
    ctx.update(accounting_status='confirmed',protection_ok=True,legacy_fenced=False)
    calls=[]
    def generate(**request):
        calls.append(request)
        schema=request['response_schema']['properties']
        candidate=wire(ctx,'LONG')
        if mode!='legacy':
            candidate['entries'][0].pop('quantity')
            candidate['entries'][0]['allocation_fraction']=.2
        if 'selection_token' in schema:
            token=next(t for t in schema['selection_token']['enum'] if t.startswith('WAIT:'))
            result=dict(selection_token=token,rationale='Synthetic comparison')
        elif 'wait_proposal' in schema:
            result=dict(wait_proposal=wire(ctx),candidates=[candidate],no_candidate_reason='')
        else:
            result={'proposal':candidate}
        return SimpleNamespace(text=json.dumps(result))
    research_review(dict(valid=True,as_of_ms=1000000),ctx,response_contract(ctx),
                    generate=generate,record=lambda e:None,clock=lambda:1000,research_mode=mode)
    assert len(calls)==(1 if 'single' in mode else 2)
    for call in calls:
        assert_sequence(call['system_prompt'])
    if len(calls)==2:
        assert 'Selectable is not evidence of an entry edge' in calls[-1]['system_prompt']


def test_single_propose_uses_same_sequence_without_new_wire_fields():
    ctx=context()
    calls=[]
    def generate(**request):
        calls.append(request)
        return SimpleNamespace(text=json.dumps(wire(ctx)))
    propose(dict(valid=True,as_of_ms=1000000),ctx,response_contract(ctx),generate=generate,clock=lambda:1000)
    assert_sequence(calls[0]['system_prompt'])
    assert set(calls[0]['response_schema']['properties'])==set(wire(ctx))


def test_breakout_gap_reentry_and_holding_exceptions_remain_explicit():
    text=' '.join((SYSTEM_PROMPT+FLAT_ENTRY_PROMPT).split())
    for clause in ('without a mandatory candle close or retest', 'supported MA10-to-MA35 traverse',
                   'Contraction alone is not a veto', 'independent current price progress',
                   'A prior loss, weak opposite trade or elapsed time alone neither grants nor forbids entry',
                   'Holding is not a fresh-entry test', 'Do not use 15m',
                   'first post-halt observation is a baseline', 'A changed number proves change, not improvement'):
        assert clause in text
