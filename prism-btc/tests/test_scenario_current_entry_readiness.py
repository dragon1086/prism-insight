import json
from functools import partial
from types import SimpleNamespace

import pytest

from analysis.scenario_review_tape import record_review,replay_review
from live.scenario_calculated_review import research_review
from live.scenario_preview import response_contract
from tests.test_scenario_entry_plans import sample
from tests.test_scenario_review_guard import wire


@pytest.mark.parametrize('enabled',[True,False])
def test_current_flow_framing_reaches_only_prepared_entry_draft(monkeypatch,enabled):
    snapshot,context,structure=sample()
    context['prepared_entry_plans_version']=1 if enabled else False
    from live import scenario_calculated_review as module
    monkeypatch.setattr(module,'build_ma_structure_context',lambda s,c:structure)
    calls=[]
    def generate(**request):
        calls.append(request)
        wait=wire(context,'WAIT');wait['expires_at']=context['now']+60
        return SimpleNamespace(text=json.dumps(dict(wait_proposal=wait,candidates=[],
            no_candidate_reason='Current continuation still needs evidence.')))
    review=partial(research_review,research_mode='economic-reject-calculated-v1')
    contract=response_contract(context)
    tape=record_review(review,snapshot,context,contract,generate=generate,record=lambda event:None,clock=lambda:context['now'])
    assert tape['outcome']['status']=='COMPLETE'
    assert len(calls)==1
    system=calls[0]['system_prompt']
    assert ('opposing rebound with slowing participation'in system)is enabled
    if enabled:
        assert 'Supported progress needs no extra candle/retest'in system
        assert 'low volume alone is no veto'in system
        assert len(system[system.index('\nPrepared prices'):].encode())<=810
    assert replay_review(tape,review,snapshot,context,contract)==tape['outcome']
