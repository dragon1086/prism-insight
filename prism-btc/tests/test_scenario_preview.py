import json
from types import SimpleNamespace

import pytest

from live.scenario_preview import preview, collect_snapshot


def test_snapshot_model_validation_end_to_end_no_broker():
    def generate(**kw):
        c={key:kw['response_schema']['properties'][key]['enum'][0]
           for key in ('schema_version','scenario_id','revision','input_id','action_id')}
        return SimpleNamespace(text=json.dumps(dict(schema_version=c['schema_version'],scenario_id=c['scenario_id'],revision=c['revision'],
            input_id=c['input_id'],action_id=c['action_id'],action='WAIT',confidence=.2,expires_at=1100,
            rationale='관망',entries=[],take_profits=[],partial_stops=[],cancel_entry_ids=[],
            side=None,hard_stop=None,chase=None,leverage=10)))
    r=preview(10000,snapshot=dict(valid=True,as_of_ms=1000000),generate=generate,clock=lambda:1000)
    assert r['orders_submitted']==0
    assert r['account_source']=='HYPOTHETICAL_NOT_BROKER'
    assert r['proposal']['action']=='WAIT'
    assert (r['model'],r['effort']) == ('gpt-6.1-terra','low')


def test_collection_failure_no_model_or_orders():
    with pytest.raises(ValueError,match='empty_public_data'):
        collect_snapshot(fetch=lambda tf:[],clock=lambda:1000)


@pytest.mark.parametrize('equity',[0,-1,float('nan'),True])
def test_invalid_hypothetical_equity(equity):
    with pytest.raises(ValueError):
        preview(equity)
