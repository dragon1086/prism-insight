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


def test_live_collection_uses_exact_requested_priority_without_lower_frames():
    import pandas as pd
    from tests.test_scenario_snapshot import frames,NOW
    from live.scenario_analysis_frames import analysis_model_view,ANALYSIS_FRAMES
    now=NOW+300000
    history,forming=frames(now)
    requested=[]
    def fetch(tf):
        requested.append(tf)
        rows=pd.concat([history[tf],forming[tf]])
        return [[int(at.timestamp()*1000),*[str(row[k])for k in ['open','high','low','close','volume']],'0']for at,row in rows.iterrows()]
    snap=collect_snapshot(fetch=fetch,clock=lambda:now/1000)
    assert requested==['1h','30m','4h','1d','1w']
    assert snap['valid']
    view=analysis_model_view(dict(market_snapshot=snap,contract_context={}))
    assert list(view['market_snapshot']['timeframes'])==list(ANALYSIS_FRAMES)
    assert view['contract_context']['analysis_frame_policy']['version']==3
