"""Process-local audit metadata must be explicit and replay-only."""
import os

import pytest

from analysis.scenario_replay import replay_identity_scope, contract_for
from live import scenario_execution
from tests.test_scenario_replay import inputs
from tests.test_scenario_review_guard import model_receipt_hash


def test_scoped_process_identity_never_changes_shared_os_and_restores_on_error():
    original = scenario_execution.os
    actual = os.getpid()
    with pytest.raises(RuntimeError, match='fixture'):
        with replay_identity_scope(economic=True, stable_process_id=True):
            assert scenario_execution.os.getpid() == 0
            assert os.getpid() == actual
            raise RuntimeError('fixture')
    assert scenario_execution.os is original
    with replay_identity_scope(economic=True):
        assert scenario_execution.os.getpid() == actual
    assert scenario_execution.os is original


def test_process_identity_contract_is_explicit_for_goal_profile_only():
    bundle, _ = inputs()
    old = contract_for(bundle, 'OHLC', 1, 10000, policy_profile='economic-reject-calculated-v1')
    new = contract_for(bundle, 'OHLC', 1, 10000, policy_profile='economic-reject-goal-v2')
    assert old['identity_scheme'] == 'EXACT_REPLAY_UUID5_MODULE_CALLSITE_SEQUENCE_V1'
    assert new['identity_scheme'] == 'EXACT_REPLAY_UUID5_CALLSITE_PID0_V2'


def _run_process(directory, mode):
    import json
    import sqlite3
    from pathlib import Path
    from types import SimpleNamespace
    from analysis.scenario_replay import run_replay, network_boundary
    from live.scenario_model_input import restore_model_input
    from tests.test_scenario_calculated_replay import wire
    root = Path(directory)
    bundle, market = inputs()
    def generate(**request):
        data = restore_model_input(json.loads(request['user_prompt']))
        ctx = data['contract_context']
        if 'selection_token' in request['response_schema']['properties']:
            choices = data['calculated_candidates']
            chosen = next((c for c in choices['candidates'] if c['eligible']), choices['wait'])
            return SimpleNamespace(text=json.dumps(dict(selection_token=f"{chosen['id']}:{model_receipt_hash(chosen)}", rationale='fixture')))
        wait = wire(ctx, 'WAIT')
        wait.update(entry_contract=None, thesis_assessment=None)
        if ctx.get('pending_renewal_version') == 1:
            wait['pending_renewal'] = None
        if ctx.get('scenario_id'):
            wait['thesis_assessment'] = dict(contract_id=ctx['registered_entry_contract']['contract_id'],status='INTACT',reason='fixture')
            candidates = []
        else:
            plan = wire(ctx, 'OPEN')
            plan['entries'][0].pop('quantity')
            plan['entries'][0]['allocation_fraction'] = .01
            plan.update(entry_contract=dict(version=2,regime='TREND',premise='fixture',goal=dict(mode='NO_MA_BOUND',reference=None),
                explicit_failure=dict(source='MARK_PRICE',operator='le',price=99,minimum_observations=2)),thesis_assessment=None)
            if ctx.get('pending_renewal_version') == 1:
                plan['pending_renewal'] = None
            candidates = [plan]
        return SimpleNamespace(text=json.dumps(dict(wait_proposal=wait,candidates=candidates,no_candidate_reason='fixture')))
    with network_boundary('frozen') as attempts:
        report = run_replay(bundle,market,root/mode,mode=mode,tape_path=root/'tape',policy_profile='economic-reject-goal-v2',
            **dict(model_generator=generate,clock_monotonic=lambda:0.) if mode=='fixture' else {})
    assert not attempts and report['execution_status']=='COMPLETE'
    with sqlite3.connect(root/mode/'replay.sqlite') as conn:
        evidence = [json.loads(row[0]) for row in conn.execute("SELECT body FROM llm_scenario_broker_evidence WHERE kind='incident_epoch_transport_v1'")]
    owners = [row['owner_pid'] for row in evidence if 'owner_pid' in row]
    assert owners and set(owners)=={0}
    (root/(mode+'-identity.json')).write_text(json.dumps(dict(actual_pid=os.getpid(),result_hash=report['result_hash'],owners=owners)))


def test_new_profile_matches_full_audit_hash_across_real_processes(tmp_path):
    import json
    import subprocess
    import sys
    from pathlib import Path
    package = Path(__file__).resolve().parents[1]
    env = dict(os.environ, PRISM_DISABLE_SIGNAL_PUBLISH='1',
               PYTHONPATH=os.pathsep.join([str(package),str(package/'tests')]))
    script = 'import sys; from test_replay_process_identity import _run_process; _run_process(sys.argv[1],sys.argv[2])'
    for mode in ('fixture','frozen'):
        result = subprocess.run([sys.executable,'-c',script,str(tmp_path),mode],env=env,capture_output=True,text=True,timeout=120)
        assert result.returncode==0,result.stderr[-1500:]
    a,b=[json.loads((tmp_path/(mode+'-identity.json')).read_text()) for mode in ('fixture','frozen')]
    assert a['actual_pid']!=b['actual_pid']
    assert a['result_hash']==b['result_hash']
