import pandas as pd
import pytest

from analysis.scenario_dataset import digest
from analysis.scenario_replay import run_replay,network_boundary
from backtest.scenario_data import HistoricalScenarioData
from engine.scenario_snapshot import TIMEFRAME_MS, candle_start


def inputs(duration_ms=600000):
    start=1790899200000;end=start+duration_ms
    index=pd.to_datetime(range(start-86400000,end,60000),unit='ms',utc=True)
    frame=pd.DataFrame(dict(open=100.,high=101.,low=99.,close=100.,volume=100.),index=index)
    warm={}
    for tf,duration in TIMEFRAME_MS.items():
        boundary=candle_start(start,duration)
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
    assert first['economic']['halted_field_basis']=='HISTORICAL_BREAKER_LATCH_NOT_EFFECTIVE_ENTRY_PERMISSION'


def test_assembled_prompt_hashes_use_actual_wire_utf8_basis():
    import hashlib
    from analysis.scenario_replay import contract_for
    from live.scenario_llm import SYSTEM_PROMPT, MA_STRUCTURE_PROMPT, FLAT_ENTRY_PROMPT
    b,_=inputs()
    contract=contract_for(b,'OHLC',1.,10000.)
    assert contract['assembled_prompt_hash_basis']=='sha256_utf8'
    assert contract['assembled_prompt_hashes']['active']==hashlib.sha256((SYSTEM_PROMPT+MA_STRUCTURE_PROMPT).encode()).hexdigest()
    assert contract['assembled_prompt_hashes']['flat']==hashlib.sha256((SYSTEM_PROMPT+MA_STRUCTURE_PROMPT+FLAT_ENTRY_PROMPT).encode()).hexdigest()


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


def recovery_inputs(*, native_stops=False):
    b,m=inputs(3600000)
    for i,stamp in enumerate(m.source.index):
        value=100+(i%60)*.001
        for frame in (m.source,m.mark):
            low=97.5 if native_stops and (stamp.value//1_000_000-b['start_ms'])//60000%5==1 else 99.8
            frame.loc[stamp,['open','high','low','close']]=[value,100.2,low,value]
    b['data_hash']=digest(m.manifest())
    return b,m


def cycle_policy(calls, *, nofill=False,native_stops=False):
    def policy(s,c):
        calls.append(c)
        p=dict(wait_policy(s,c),scenario_id=c['scenario_id'] or 's-'+str(int(c['now'])),
               expires_at=c['now']+120)
        rec=c.get('recovery')
        if c['scenario_id']:
            return dict(p,action='EXIT')
        if rec:
            first=rec['first_observation']
            p['recovery']=dict(decision='OBSERVE' if first else 'PROBE',reason='synthetic cycle',
                changed_evidence=[] if first else list(rec['changed_evidence'])[:1],
                counterevidence='synthetic adverse path',invalidation='native hard stop')
            if first:return p
        return dict(p,action='OPEN',side='LONG',leverage=10,confidence=.5,
                    hard_stop=96 if rec and nofill else 98,
                    entries=[dict(id='entry-'+str(int(c['now'])),price=97 if rec and nofill else 100.3 if native_stops else 100.1,quantity=.1)],
                    take_profits=[dict(id='tp-'+str(c['now']),price=103,fraction=.5)] if native_stops else [],
                    chase=dict(max_bps=0,max_reprices=0))
    return policy


@pytest.mark.parametrize('nofill,native_stops',[(False,False),(True,False),(False,True)])
def test_live_v2_actual_three_losses_probe_settlement_rearm_and_frozen(tmp_path,nofill,native_stops):
    import json
    import sqlite3
    b,m=recovery_inputs(native_stops=native_stops);calls=[];tape=tmp_path/'v2-tape'
    with network_boundary('frozen') as blocked:
        first=run_replay(b,m,tmp_path/'record',mode='fixture',tape_path=tape,
                         policy=cycle_policy(calls,nofill=nofill,native_stops=native_stops))
        frozen=run_replay(b,m,tmp_path/'frozen',mode='frozen',tape_path=tape)
    assert not blocked
    assert first['result_hash']==frozen['result_hash']
    assert first['execution_metadata']['actual_model_calls']==0
    assert first['execution_metadata']['synthetic_policy_calls']==len(calls)
    assert all('recovery' not in c for c in calls[:3 if native_stops else 6])
    recovery_calls=[c for c in calls if c.get('recovery')]
    assert recovery_calls and recovery_calls[0]['recovery']['first_observation'] is True
    assert recovery_calls[0]['scenario_risk_fraction']==.005
    assert any(not c['recovery'].get('first_observation',False) for c in recovery_calls)
    with sqlite3.connect(tmp_path/'record'/'replay.sqlite') as conn:
        state=json.loads(conn.execute('SELECT body FROM llm_scenario_state').fetchone()[0])
        events=[r[0] for r in conn.execute('SELECT kind FROM llm_scenario_recovery_events')]
        plans=[json.loads(r[0]) for r in conn.execute('SELECT payload FROM llm_scenario_intents')]
    assert 'MIGRATION' in events and 'REARM' in events
    assert state['recovery']['policy_version']==2
    opened=[p for p in plans if p['action']=='OPEN']
    assert len(opened)>=4
    assert opened[0]['risk']['budget']==200
    assert opened[3]['risk']['budget']==pytest.approx(recovery_calls[1]['initial_equity']*.005)
    for c in recovery_calls:
        if c['scenario_id']:
            assert c['recovery']['budget']==pytest.approx(c['initial_equity']*.005)
    assert all(p['hard_stop'] in (96,98) for p in opened)
    if native_stops:
        assert all(p['take_profits'][0]['price']==103 for p in opened)
    assert first['economic']['fees']>0
    assert first['economic']['completed_scenarios'] >= 3
    if nofill:
        assert first['economic']['completed_scenarios']==3


def test_replay_policy_profile_explicit_and_frozen_cannot_relabel(tmp_path):
    from backtest.scenario_tape import TapeMismatch
    b,m=inputs();tape=tmp_path/'legacy'
    first=run_replay(b,m,tmp_path/'oldflags',mode='fixture',tape_path=tape,
                     policy=wait_policy,policy_profile='legacy-no-recovery')
    assert first['contract']['runtime_flags']==dict(recovery_enabled=False,automatic_normalization_enabled=False)
    with pytest.raises(TapeMismatch,match='contract_mismatch'):
        run_replay(b,m,tmp_path/'wrong',mode='frozen',tape_path=tape)
    replay=run_replay(b,m,tmp_path/'right',mode='frozen',tape_path=tape,policy_profile='legacy-no-recovery')
    assert first['result_hash']==replay['result_hash']


def test_unknown_recovery_submission_never_rearms_or_posts_twice(tmp_path,monkeypatch):
    import json
    import sqlite3
    from backtest.scenario_exchange import OfflineBybitSession
    original=OfflineBybitSession.place_order
    attempts=[]
    def ambiguous(self,**params):
        if not params.get('reduceOnly'):
            self._test_entry_attempts=getattr(self,'_test_entry_attempts',0)+1
            if self._test_entry_attempts==4:
                attempts.append(params['orderLinkId'])
                raise TimeoutError('synthetic unknown submission, no terminal evidence')
        return original(self,**params)
    monkeypatch.setattr(OfflineBybitSession,'place_order',ambiguous)
    b,m=recovery_inputs();calls=[];tape=tmp_path/'unknown-tape'
    with network_boundary('frozen'):
        first=run_replay(b,m,tmp_path/'record',mode='fixture',tape_path=tape,policy=cycle_policy(calls))
        second=run_replay(b,m,tmp_path/'frozen',mode='frozen',tape_path=tape)
    assert first['result_hash']==second['result_hash']
    assert len(attempts)==2  # Once in each isolated run, never retried.
    assert first['economic']['pending_intents']==1
    assert first['economic']['completed_scenarios']==3
    with sqlite3.connect(tmp_path/'record'/'replay.sqlite') as conn:
        state=json.loads(conn.execute('SELECT body FROM llm_scenario_state').fetchone()[0])
        assert not conn.execute("SELECT 1 FROM llm_scenario_recovery_events WHERE kind='REARM'").fetchone()
    assert state['recovery']['phase']=='CONSUMED'


def test_exact_assembled_request_envelope_and_identity_scope_restore(tmp_path):
    import json
    import sqlite3
    from types import SimpleNamespace
    from live import scenario_runtime, scenario_recovery
    from live.scenario_contract import identity_fields
    original=(scenario_runtime.uuid,scenario_recovery.uuid)
    b,m=inputs();calls=[];tape=tmp_path/'audit-tape'
    def generate(**kwargs):
        calls.append(kwargs)
        c=json.loads(kwargs['user_prompt'])['contract_context']
        p=dict(identity_fields(c),action='WAIT',confidence=.2,expires_at=c['now']+60,
               leverage=10,side=None,hard_stop=None,chase=None,entries=[],take_profits=[],
               partial_stops=[],cancel_entry_ids=[],rationale='synthetic wire fixture')
        return SimpleNamespace(text=json.dumps(p))
    with network_boundary('frozen'):
        first=run_replay(b,m,tmp_path/'audit',mode='fresh',tape_path=tape,model_generator=generate)
        replay=run_replay(b,m,tmp_path/'audit-frozen',mode='frozen',tape_path=tape)
    assert first['result_hash']==replay['result_hash']
    assert (scenario_runtime.uuid,scenario_recovery.uuid)==original
    records=[json.loads(line) for line in tape.read_text().splitlines()][1:]
    assert len(records)==len(calls)
    for row,call in zip(records,calls):
        request=row['raw_response']['request']
        assert request['system_prompt']==call['system_prompt']
        assert request['user_prompt']==call['user_prompt']
        assert 'ma_structure' in json.loads(request['user_prompt'])['contract_context']
        assert request['input_projection']
        assert row['raw_response']['wire_metadata']==dict(model='gpt-6-luna',effort='high',fast=True)
    journals=[]
    for name in ('audit','audit-frozen'):
        with sqlite3.connect(tmp_path/name/'replay.sqlite') as conn:
            journals.append([(kind,json.loads(body)) for kind,body in conn.execute(
                "SELECT kind,body FROM llm_scenario_recovery_events WHERE kind IN ('MODEL_REQUEST','MODEL_RAW') ORDER BY rowid")])
    assert journals[0]==journals[1]
    with pytest.raises(ValueError):
        run_replay(b,m,tmp_path/'badmode',mode='invalid',tape_path=tmp_path/'unused')
    assert (scenario_runtime.uuid,scenario_recovery.uuid)==original


def test_replay_identity_scope_rejects_overlap_without_mutating_global_uuid():
    import uuid
    from analysis.scenario_replay import replay_identity_scope
    from live import scenario_runtime,scenario_recovery
    original=uuid.uuid4
    with replay_identity_scope():
        assert uuid.uuid4 is original
        with pytest.raises(ValueError,match='concurrent_replay'):
            with replay_identity_scope():
                pytest.fail('overlapping replay scope accepted')
    assert scenario_runtime.uuid is scenario_recovery.uuid is uuid


def test_input_preparation_failure_frozen_preserves_code_without_fake_wire(tmp_path,monkeypatch):
    import json
    import sqlite3
    from live.scenario_broker import ScenarioDemoBroker
    original=ScenarioDemoBroker.context
    def oversized(self):
        return {**original(self),'synthetic_unprojectable_input':'x'*120000}
    monkeypatch.setattr(ScenarioDemoBroker,'context',oversized)
    b,m=inputs();tape=tmp_path/'oversized-tape'
    def never_called(**kwargs):
        pytest.fail('input preparation must fail before model call')
    with network_boundary('frozen'):
        first=run_replay(b,m,tmp_path/'fresh',mode='fresh',tape_path=tape,model_generator=never_called)
        replay=run_replay(b,m,tmp_path/'frozen',mode='frozen',tape_path=tape)
    assert first['result_hash']==replay['result_hash']
    assert first['execution_metadata']['actual_model_calls']==0
    assert first['runtime_outcome_reasons']=={'blocked:input_size':2}
    assert first['model_failures']==0 and first['input_preparation_failures']==2
    assert 'INPUT_PREPARATION_FAILURES' in first['readiness']['insufficiency_reasons']
    assert 'MODEL_FAILURES' not in first['readiness']['insufficiency_reasons']
    rows=[json.loads(line) for line in tape.read_text().splitlines()][1:]
    assert all(row['error']=='ScenarioModelError:input_size' for row in rows)
    assert all(row['raw_response']['request'] is None and row['raw_response']['text'] is None
               and row['raw_response']['wire_recorded'] is False for row in rows)
    for name in ('fresh','frozen'):
        with sqlite3.connect(tmp_path/name/'replay.sqlite') as conn:
            assert not conn.execute("SELECT 1 FROM llm_scenario_recovery_events WHERE kind IN ('MODEL_REQUEST','MODEL_RAW')").fetchone()
            outcomes=[json.loads(row[0]) for row in conn.execute('SELECT outcome FROM llm_scenario_decisions')]
        assert all(row['reason']=='llm_input_preparation_failed' and row['model_called'] is False for row in outcomes)


def test_old_tape_schema_rejected_without_relabeling(tmp_path):
    import json
    from backtest.scenario_tape import TapeMismatch
    b,m=inputs();tape=tmp_path/'old-schema'
    tape.write_text(json.dumps({'contract':{'schema':1,'decision_origin':'fixture'}})+'\n')
    with pytest.raises(TapeMismatch,match='requires_original_code'):
        run_replay(b,m,tmp_path/'out',mode='frozen',tape_path=tape,policy_profile='legacy-no-recovery')
    assert not (tmp_path/'out').exists()


@pytest.mark.parametrize('case',['unobserved_native_stop','exit_cancel_pending'])
def test_real_broker_uncertain_lifecycle_is_not_fabricated_as_settlement(tmp_path,case):
    """Known execution gaps stay explicit; replay must not heal production state."""
    b,m=recovery_inputs();calls=[];base=cycle_policy(calls)
    def policy(s,c):
        p=base(s,c)
        if p['action']=='OPEN':
            p['take_profits']=[dict(id='tp-'+str(c['now']),price=103,fraction=.5)]
            if case=='unobserved_native_stop':
                p['hard_stop']=99.9  # Native created and filled between protection polls.
        return p
    with network_boundary('frozen'):
        report=run_replay(b,m,tmp_path/case,mode='fixture',tape_path=tmp_path/'tape',policy=policy)
    assert report['economic']['completed_scenarios']==0
    assert report['economic']['unclosed_scenario'] is True
    assert 'UNSETTLED_END_STATE' in report['readiness']['insufficiency_reasons']
    if case=='unobserved_native_stop':
        assert report['decision_outcomes']['fenced']>0
        assert report['economic']['open_quantity']==0
    else:
        assert report['economic']['pending_intents']==1
        assert report['economic']['open_quantity']>0


def test_partial_tp_then_observed_native_stop_is_exactly_settled_and_frozen(tmp_path):
    import json
    import sqlite3
    b,m=inputs();calls=[]
    for minute,high,low in [(0,100.2,99.8),(1,100.2,99.8),(2,103.5,99.8),(3,100.2,97.5)]:
        stamp=pd.Timestamp(b['start_ms']+minute*60000,unit='ms',tz='UTC')
        for frame in (m.source,m.mark):
            frame.loc[stamp,['high','low']]=[high,low]
    b['data_hash']=digest(m.manifest())
    base=cycle_policy(calls,native_stops=True)
    def policy(s,c):
        if calls:
            calls.append(c)
            return wait_policy(s,c)
        return base(s,c)
    tape=tmp_path/'partial-tape'
    with network_boundary('frozen'):
        first=run_replay(b,m,tmp_path/'record',mode='fixture',tape_path=tape,policy=policy)
        second=run_replay(b,m,tmp_path/'frozen',mode='frozen',tape_path=tape)
    assert first['result_hash']==second['result_hash']
    assert first['economic']['completed_scenarios']==1
    assert first['economic']['open_quantity']==0
    assert first['economic']['pending_intents']==0
    with sqlite3.connect(tmp_path/'record'/'replay.sqlite') as conn:
        children={kind:json.loads(evidence) for kind,evidence in conn.execute(
            "SELECT kind,evidence FROM llm_scenario_children WHERE kind IN ('tp','native_sl')")}
        settlement=json.loads(conn.execute('SELECT evidence FROM llm_scenario_settlements').fetchone()[0])
    assert {kind:sum(float(e['execQty']) for e in proof['executions']) for kind,proof in children.items()}=={'tp':.05,'native_sl':.05}
    assert settlement['net_pnl']==pytest.approx(settlement['gross_pnl']-settlement['fees']+settlement['funding_net'])
    assert settlement['fees']==pytest.approx(first['economic']['fees'])


def test_explicit_synthetic_stage0_starts_with_baseline_wait_then_probe(tmp_path):
    import json
    import sqlite3
    b,m=recovery_inputs(native_stops=True);calls=[];tape=tmp_path/'stage0-tape'
    with network_boundary('frozen'):
        first=run_replay(b,m,tmp_path/'record',mode='fixture',tape_path=tape,
                         policy=cycle_policy(calls,native_stops=True),initial_state='recovery-stage0')
        replay=run_replay(b,m,tmp_path/'frozen',mode='frozen',tape_path=tape,initial_state='recovery-stage0')
    assert first['result_hash']==replay['result_hash']
    assert first['contract']['initial_state']==dict(profile='recovery-stage0',
        provenance='SYNTHETIC_INITIAL_SOFT_LATCH_NO_PRIOR_PNL',live_ledger_imported=False,
        prior_pnl_imported=False,prior_settlements_imported=False)
    assert calls[0]['recovery']['first_observation'] is True
    assert calls[0]['scenario_risk_fraction']==.005
    assert calls[1]['recovery']['first_observation'] is False
    assert all(c['scenario_risk_fraction']==.005 for c in calls)
    with sqlite3.connect(tmp_path/'record'/'replay.sqlite') as conn:
        first_plan=json.loads(conn.execute('SELECT payload FROM llm_scenario_intents ORDER BY rowid LIMIT 1').fetchone()[0])
        first_decision=json.loads(conn.execute('SELECT proposal FROM llm_scenario_decisions ORDER BY slot LIMIT 1').fetchone()[0])
    assert first_decision['action']=='WAIT'
    assert first_plan['risk']['budget']==50
    assert first['economic']['completed_scenarios']>0


def test_legacy_profile_cannot_claim_stage0_and_unknown_seed_rejected(tmp_path):
    b,m=inputs()
    for state in ('recovery-stage0','arbitrary-live-clone'):
        with pytest.raises(ValueError,match='requires_current_live_v2|initial_state_required'):
            run_replay(b,m,tmp_path/state,mode='fixture',tape_path=tmp_path/(state+'-tape'),
                       policy=wait_policy,policy_profile='legacy-no-recovery',initial_state=state)
        assert not (tmp_path/state).exists()


def test_measured_late_model_reply_keeps_live_error_classification(tmp_path,monkeypatch):
    import json
    from types import SimpleNamespace
    from analysis import scenario_replay
    from live.scenario_contract import identity_fields
    ticks=iter(range(0,1000,100))
    monkeypatch.setattr(scenario_replay,'time',SimpleNamespace(monotonic=lambda:next(ticks)))
    b,m=inputs();tape=tmp_path/'late-tape'
    def generate(**kwargs):
        c=json.loads(kwargs['user_prompt'])['contract_context']
        return SimpleNamespace(text=json.dumps(dict(identity_fields(c),action='WAIT',confidence=.2,
            expires_at=c['now']+120,leverage=10,side=None,hard_stop=None,chase=None,entries=[],
            take_profits=[],partial_stops=[],cancel_entry_ids=[],rationale='synthetic late reply')))
    with network_boundary('frozen'):
        first=run_replay(b,m,tmp_path/'record',mode='fresh',tape_path=tape,model_generator=generate)
        replay=run_replay(b,m,tmp_path/'frozen',mode='frozen',tape_path=tape)
    assert first['result_hash']==replay['result_hash']
    assert first['runtime_outcome_reasons']=={'blocked:llm_call_failed':2}
    assert first['input_preparation_failures']==0 and first['model_failures']==2
    assert all(json.loads(row)['error']=='ScenarioModelError:late_response' for row in tape.read_text().splitlines()[1:])


def test_actual_winning_cycles_offer_then_commit_both_normalization_promotions(tmp_path):
    """Synthetic rising tape; actual fills/costs/settlement, never injected PnL."""
    import json
    import math
    import sqlite3
    b,m=inputs(2*60*60*1000)
    for minute in range(120):
        stamp=pd.Timestamp(b['start_ms']+minute*60000,unit='ms',tz='UTC')
        opening=round(100+minute*.2,1)
        for frame in (m.source,m.mark):
            frame.loc[stamp,['open','high','low','close']]=[
                opening,round(opening+.3,1),round(opening-.1,1),round(opening+.2,1)]
    b['data_hash']=digest(m.manifest())
    calls=[]
    def policy(snapshot,c):
        calls.append(c)
        p=dict(wait_policy(snapshot,c),scenario_id=c['scenario_id'] or 'win-'+str(int(c['now'])),
               expires_at=c['now']+120)
        if c['scenario_id']:
            return dict(p,action='EXIT')
        rec=c['recovery'];first=rec['first_observation']
        p['recovery']=dict(decision='OBSERVE' if first else 'PROBE',reason='synthetic rising path',
            changed_evidence=[] if first else list(rec['changed_evidence'])[:1],
            counterevidence='synthetic pullback risk',invalidation='native hard stop')
        if first:return p
        price=math.ceil((c['mark_price']+.3)*10)/10
        return dict(p,action='OPEN',side='LONG',leverage=10,confidence=.5,hard_stop=round(price-2,1),
                    entries=[dict(id='entry-'+str(int(c['now'])),price=price,quantity=.1)],
                    take_profits=[],partial_stops=[],chase=dict(max_bps=0,max_reprices=0))
    tape=tmp_path/'winning-stage-tape'
    with network_boundary('frozen') as blocked:
        first=run_replay(b,m,tmp_path/'record',mode='fixture',tape_path=tape,policy=policy,
                         initial_state='recovery-stage0')
        frozen=run_replay(b,m,tmp_path/'frozen',mode='frozen',tape_path=tape,
                          initial_state='recovery-stage0')
    assert blocked==[]
    assert first['result_hash']==frozen['result_hash']
    assert first['economic']['wins']==first['economic']['completed_scenarios']==8
    assert first['economic']['losses']==0 and first['economic']['fees']>0
    assert first['economic']['open_quantity']==0 and first['economic']['pending_intents']==0
    assert first['recovery_state']['stage']==2
    offered=[c for c in calls if c['recovery'].get('first_observation')
             and c['recovery']['offered_stage']>c['recovery']['current_stage']]
    assert [(c['recovery']['current_stage'],c['recovery']['offered_stage']) for c in offered]==[(0,1),(1,2)]
    assert [c['scenario_risk_fraction'] for c in offered]==[.01,.02]
    with sqlite3.connect(tmp_path/'record'/'replay.sqlite') as conn:
        decisions={slot:json.loads(p) for slot,p in conn.execute('SELECT slot,proposal FROM llm_scenario_decisions')}
        transitions=[json.loads(body) for body, in conn.execute(
            "SELECT body FROM llm_scenario_recovery_events WHERE kind='STAGE_TRANSITION' ORDER BY rowid")]
        plans=[json.loads(p) for p, in conn.execute('SELECT payload FROM llm_scenario_intents ORDER BY rowid')]
        settlements=[json.loads(e) for e, in conn.execute('SELECT evidence FROM llm_scenario_settlements ORDER BY rowid')]
    assert [(e['before'],e['after']) for e in transitions]==[(0,1),(1,2)]
    for c in offered:
        assert decisions[int(c['now']//300)]['action']=='WAIT'
        assert decisions[int(c['now']//300)+1]['action']=='OPEN'
    opened=[p for p in plans if p['action']=='OPEN']
    for index,p in enumerate(opened):
        fraction=(.005,.01,.02)[min(index//3,2)]
        matching=next(c for c in calls if c['input_id']==p['input_id'])
        assert p['risk']['budget']==pytest.approx(matching['initial_equity']*fraction)
        held=next(c for c in calls if c['scenario_id']==p['scenario_id'])
        assert held['initial_equity']==matching['initial_equity']
        assert held['recovery']['budget']==p['risk']['budget']
    assert all(e['execution_ids'] and e['net_pnl']>0 for e in settlements)
    assert sum(e['fees'] for e in settlements)==pytest.approx(first['economic']['fees'])
