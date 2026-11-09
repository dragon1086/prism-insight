"""Current replay flags and observed-rate seeding never modify legacy contracts."""
from copy import deepcopy
import sqlite3
import pytest
from analysis.scenario_replay import contract_for, validated_fee_seed, run_replay
from tests.test_scenario_replay import inputs


def seed(at):
    return dict(observed_at=at,source_sha256='a'*64,
        provenance='OBSERVED_HISTORICAL_ACCOUNT_CONTEXT',
        evidence=dict(status='AVAILABLE',basis='HISTORICAL_VERIFIED_EXECUTIONS',
            source_sha256='b'*64,rates={'TAKER':dict(count=1,minimum=.00055,
                maximum=.00055,earliest_at=at-10,latest_at=at-10)}))


def test_current_profile_has_host_gate_without_changing_legacy():
    b,_=inputs();s=seed(b['start_ms']/1000-1);original=deepcopy(s)
    c=contract_for(b,'OHLC',1.,10000.,policy_profile='deployed-main-v3',initial_fee_context=s)
    assert c['broker_flags']=={'entry_economics_enabled':True}
    assert c['identity_scheme']=='EXACT_REPLAY_UUID5_CALLSITE_PID0_V2'
    assert c['context_contracts']['entry_contract_version']==2
    assert all(c['runtime_flags'][k] for k in ['entry_contract_enabled','entry_goal_enabled',
        'pending_renewal_enabled','convergence_entry_enabled'])
    assert c['initial_fee_context']==s and s==original
    assert 'broker_flags' not in contract_for(b,'OHLC',1.,10000.,policy_profile='economic-reject-goal-v2')
    with pytest.raises(ValueError,match='explicit_profile'):
        contract_for(b,'OHLC',1.,10000.,initial_fee_context=s)


@pytest.mark.parametrize('change',[lambda s:s.update(observed_at=s['observed_at']+100),
    lambda s:s.update(source_sha256='bad'),lambda s:s.update(provenance='ASSUMED'),
    lambda s:s['evidence']['rates']['TAKER'].update(earliest_at=1),
    lambda s:s['evidence']['rates']['TAKER'].update(latest_at=s['observed_at']+1),
    lambda s:s['evidence']['rates']['TAKER'].update(maximum=0)])
def test_future_stale_unverified_seed_rejected(change):
    s=seed(100000);change(s)
    with pytest.raises(ValueError):validated_fee_seed(s,100001000)


def test_wait_is_outside_simulated_model_deadline_and_seed_has_no_trades(tmp_path):
    b,m=inputs(300000);ticks=[0.];seen=[]
    def before(event):
        ticks[0]+=120;seen.append(event)
    def generate(**kw):
        # Invalid model output deliberately stops the real pipeline without orders.
        from types import SimpleNamespace
        return SimpleNamespace(text='{}')
    result=run_replay(b,m,tmp_path/'run',mode='fixture',tape_path=tmp_path/'tape',
        policy_profile='deployed-main-v3',initial_fee_context=seed(b['start_ms']/1000-1),
        clock_monotonic=lambda:ticks[0],before_decision=before,model_generator=generate)
    assert len(seen)==1 and seen[0]['at_ms']==b['start_ms']
    with sqlite3.connect(tmp_path/'run'/'replay.sqlite') as conn:
        import json
        context=json.loads(conn.execute('SELECT context FROM llm_scenario_decisions').fetchone()[0])
        assert context['now']==b['start_ms']/1000
        assert context['positions']==[] and context['pending_entries']==[]
        assert context['observed_execution_fee_evidence']['status']=='AVAILABLE'
    assert result['economic']['net_change']==0
    with pytest.raises(ValueError,match='external_decision_wait'):
        run_replay(b,m,tmp_path/'frozen',mode='frozen',tape_path=tmp_path/'tape',before_decision=before)
