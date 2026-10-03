import json
from types import SimpleNamespace

import pytest

from live.scenario_preview import preview, collect_snapshot


def test_snapshot_model_validation_end_to_end_no_broker():
    def generate(**kw):
        c=json.loads(kw['user_prompt'])['contract_context']
        return SimpleNamespace(text=json.dumps(dict(schema_version=1,scenario_id='s1',revision=1,
            input_id=c['input_id'],action_id='a1',action='WAIT',confidence=.2,expires_at=1100,
            rationale='관망',entries=[],take_profits=[],partial_stops=[])))
    r=preview(10000,snapshot=dict(valid=True,as_of_ms=1000000),generate=generate,clock=lambda:1000)
    assert r['orders_submitted']==0
    assert r['account_source']=='HYPOTHETICAL_NOT_BROKER'
    assert r['proposal']['action']=='WAIT'


def test_collection_failure_no_model_or_orders():
    with pytest.raises(ValueError,match='empty_public_data'):
        collect_snapshot(fetch=lambda tf:[],clock=lambda:1000)


@pytest.mark.parametrize('equity',[0,-1,float('nan'),True])
def test_invalid_hypothetical_equity(equity):
    with pytest.raises(ValueError):
        preview(equity)
