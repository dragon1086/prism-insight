import pandas as pd
import pytest

from analysis.scenario_dataset import digest
from analysis.scenario_replay import run_replay,network_boundary
from backtest.scenario_data import HistoricalScenarioData
from engine.scenario_snapshot import TIMEFRAME_MS


def inputs(duration_ms=600000):
    start=1790899200000;end=start+duration_ms
    index=pd.to_datetime(range(start-86400000,end,60000),unit='ms',utc=True)
    frame=pd.DataFrame(dict(open=100.,high=101.,low=99.,close=100.,volume=100.),index=index)
    warm={}
    for tf,duration in TIMEFRAME_MS.items():
        if tf=='5m':continue
        boundary=start//duration*duration
        idx=pd.to_datetime(range(boundary-60*duration,boundary,duration),unit='ms',utc=True)
        warm[tf]=pd.DataFrame(dict(open=100.,high=101.,low=99.,close=100.,volume=100.),index=idx)
    funding=[dict(timestamp=t,rate=.0001) for t in range(start-28800000,end+28800000,28800000)]
    market=HistoricalScenarioData(frame,source_interval_ms=60000,warmup=warm,mark=frame.copy(),funding=funding)
    bundle=dict(data_hash=digest(market.manifest()),source_interval_ms=60000,symbol='BTCUSDT',
                start_ms=start,end_ms=end,funding=funding)
    return bundle,market


def wait_policy(snapshot,c):
    return dict(schema_version=1,scenario_id='fixture',revision=c['revision']+1,input_id=c['input_id'],
                action_id='wait-'+str(c['now']),action='WAIT',confidence=.2,expires_at=c['now']+60,
                entries=[],take_profits=[],partial_stops=[],rationale='synthetic wait')


def test_full_runtime_record_and_frozen_replay_same_hash(tmp_path):
    b,m=inputs();tape=tmp_path/'decisions.jsonl'
    with network_boundary('frozen') as blocked:
        first=run_replay(b,m,tmp_path/'one',mode='fixture',tape_path=tape,policy=wait_policy)
        replay=run_replay(b,m,tmp_path/'two',mode='frozen',tape_path=tape)
    assert blocked==[]
    assert first['model_decisions']==2 and first['economic']['net_change']==0
    assert first['result_hash']==replay['result_hash']
    assert first['mode']=='SYNTHETIC_FIXTURE'
    assert replay['execution_metadata']['actual_model_calls']==0
    assert set(first['contract']['framing_prompt_hashes']) == {'OPPORTUNITY','TRANSITION','DEFENSIVE'}


def test_missing_mark_or_existing_output_never_runs_policy(tmp_path):
    b,m=inputs();m.mark=None
    with pytest.raises(ValueError,match='mark_history'):
        run_replay(b,m,tmp_path/'bad',mode='fixture',tape_path=tmp_path/'t',policy=wait_policy)


def test_offline_network_boundary_denies_exchange_socket():
    with network_boundary('frozen') as blocked:
        with pytest.raises(RuntimeError,match='network_forbidden'):
            blocked.guard('socket.getaddrinfo',('api-demo.bybit.com',443))
    assert blocked==['blocked_network']


def test_real_runtime_simulated_open_exit_and_exact_tape_replay(tmp_path):
    b,m=inputs();tape=tmp_path/'trade-tape'
    def policy(s,c):
        base=dict(wait_policy(s,c),scenario_id=c['scenario_id'] or 'fixture-trade',
                  action_id='a-'+str(c['now']),expires_at=c['now']+600)
        if c['scenario_id']:
            return dict(base,action='EXIT')
        return dict(base,action='OPEN',side='LONG',leverage=10,confidence=.5,hard_stop=98,
                    entries=[dict(id='e1',price=100,quantity=.1)],take_profits=[],partial_stops=[],
                    chase=dict(max_bps=0,max_reprices=0))
    with network_boundary('frozen') as blocked:
        first=run_replay(b,m,tmp_path/'open',mode='fixture',tape_path=tape,policy=policy)
        second=run_replay(b,m,tmp_path/'copy',mode='frozen',tape_path=tape)
    assert not blocked
    assert first['economic']['completed_scenarios']==1
    assert first['economic']['fees']>0 and first['economic']['open_quantity']==0
    assert first['result_hash']==second['result_hash']


def test_invalid_identity_stays_blocked_in_frozen_runtime(tmp_path):
    b,m=inputs();tape=tmp_path/'bad-id-tape'
    def invalid(s,c):return {**wait_policy(s,c),'input_id':'wrong'}
    with network_boundary('frozen'):
        first=run_replay(b,m,tmp_path/'bad-id',mode='fixture',tape_path=tape,policy=invalid)
        second=run_replay(b,m,tmp_path/'bad-id-copy',mode='frozen',tape_path=tape)
    assert first['decision_outcomes']==second['decision_outcomes']=={'blocked':2}
    assert first['result_hash']==second['result_hash']


@pytest.mark.parametrize('invalid',[False,True])
def test_strict_wire_fresh_and_frozen_preserve_trade_or_rejection(tmp_path,invalid):
    import json
    import sqlite3
    from types import SimpleNamespace
    from live.scenario_contract import identity_fields
    b,m=inputs();tape=tmp_path/'strict-tape'
    def generate(**kw):
        c=json.loads(kw['user_prompt'])['contract_context']
        p=dict(identity_fields(c),action='EXIT' if c['scenario_id'] else 'OPEN',
               confidence=.5,expires_at=c['now']+600,leverage=10,
               side=None,hard_stop=None,chase=None,entries=[],take_profits=[],
               partial_stops=[],cancel_entry_ids=[],rationale='synthetic contract test')
        if p['action']=='OPEN':
            p.update(side='LONG',hard_stop=98,chase=dict(max_bps=0,max_reprices=0),
                     entries=[dict(id='e1',price=100,quantity=.1)])
        if invalid:p['input_id']='wrong'
        return SimpleNamespace(text=json.dumps(p))
    with network_boundary('frozen') as blocked:
        first=run_replay(b,m,tmp_path/'fresh',mode='fresh',tape_path=tape,model_generator=generate)
        second=run_replay(b,m,tmp_path/'frozen',mode='frozen',tape_path=tape)
    assert not blocked
    assert first['result_hash']==second['result_hash']
    assert first['economic']['completed_scenarios']==(0 if invalid else 1)
    if invalid:
        outcomes=[]
        for path in ['fresh','frozen']:
            with sqlite3.connect(tmp_path/path/'replay.sqlite') as conn:
                outcomes.append([json.loads(row[0]) for row in conn.execute('SELECT outcome FROM llm_scenario_decisions ORDER BY slot')])
        assert outcomes[0]==outcomes[1]==[{'status':'blocked','reason':'llm_output_contract_failed'}]*2


def test_cancel_replace_abort_settles_without_fill_and_allows_next_decision(tmp_path):
    b,m=inputs(1200000);calls=[]
    def policy(s,c):
        calls.append(c)
        p=wait_policy(s,c)
        p.update(scenario_id=c['scenario_id'] or 'cancel-replace',expires_at=c['now']+600)
        if len(calls)==1:
            p.update(action='OPEN',side='LONG',confidence=.5,hard_stop=96,
                     entries=[dict(id='old-entry',price=98,quantity=.1)],
                     chase=dict(max_bps=0,max_reprices=0))
        elif len(calls)==2:
            p.update(action='ADJUST',side='LONG',confidence=.5,hard_stop=96,
                     cancel_entry_ids=['old-entry'],entries=[dict(id='replacement',price=98.5,quantity=.1)],
                     chase=dict(max_bps=0,max_reprices=0))
        return p
    with network_boundary('frozen') as blocked:
        report=run_replay(b,m,tmp_path/'cancel-replace',mode='fixture',tape_path=tmp_path/'tape',policy=policy)
    assert not blocked
    assert len(calls)==4 and calls[2]['scenario_id'] is None
    assert report['economic']['completed_scenarios']==0
    assert report['economic']['fees']==0
    assert report['economic']['pending_intents']==0
    assert report['economic']['unclosed_scenario'] is False
