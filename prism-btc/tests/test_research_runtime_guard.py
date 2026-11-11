"""Research HTTP must fail closed before it can reach even a local proxy."""
from io import BytesIO
import json
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools.btc_research_call_guard import CallGuard
from tools import btc_research_model_call as transport


BASE = 1800000000.0
HEAD = 'a' * 40
CRON = 'b' * 64
ENV = {'PRISM_BTC_RESEARCH_EXPECTED_HEAD': HEAD,
       'PRISM_BTC_RESEARCH_EXPECTED_CRONTAB_SHA256': CRON}


def clock(*values):
    values = iter(values)
    return lambda: next(values)


def runner(*, head=True, cron=True, schedule=True, idle=True, remote=BASE + 10.2, code=0):
    calls = []
    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        return SimpleNamespace(returncode=code, stdout=json.dumps(
            {'head_ok': head, 'cron_ok': cron, 'schedule_ok': schedule, 'idle': idle, 'checked_at': remote}))
    return run, calls


def remote_proof(*, remote=BASE+10.2, elapsed=.4, **flags):
    run,_=runner(remote=remote,**flags)
    return transport._runtime_readiness('DRAFT',75,_clock=lambda:pytest.fail('local UTC is not the cron clock'),
        _monotonic=clock(100.,100.+elapsed),_run=run,
        _environ=dict(ENV,PRISM_BTC_RESEARCH_CLOCK_POLICY='REMOTE_BOUNDED_V1'))


def test_remote_clock_keeps_head_cron_idle_and_conservative_deadline_without_local_skew():
    proof=remote_proof()
    assert proof['remaining_seconds_lower_bound']==300-(BASE+10.2)%300-.4
    transport._readiness_still_safe(proof,'DRAFT',75,_monotonic=lambda:101.)


@pytest.mark.parametrize('flags,reason', [({'head':False},'server_head_changed'),
    ({'cron':False},'crontab_changed'),({'schedule':False},'unsupported_decision_schedule'),
    ({'idle':False},'decision_running')])
def test_remote_clock_never_bypasses_other_guards(flags,reason):
    with pytest.raises(transport.RuntimeReadinessError,match=reason):remote_proof(**flags)


@pytest.mark.parametrize('elapsed', [-.1,8.1,float('nan')])
def test_remote_clock_rejects_unbounded_or_backward_roundtrip(elapsed):
    with pytest.raises(transport.RuntimeReadinessError,match='remote_clock_roundtrip_invalid'):
        remote_proof(elapsed=elapsed)


def test_remote_clock_subtracts_roundtrip_and_dispatch_delay_at_boundary():
    with pytest.raises(transport.RuntimeReadinessError,match='scheduled_tick_near'):
        remote_proof(remote=BASE+109.,elapsed=2.)
    proof=remote_proof(remote=BASE+109.,elapsed=.4)
    with pytest.raises(transport.RuntimeReadinessError,match='schedule_changed_after_check'):
        transport._readiness_still_safe(proof,'DRAFT',75,_monotonic=lambda:101.1)


def test_remote_proof_tampering_and_old_dispatch_are_rejected():
    proof=remote_proof()
    for key,value in [('roundtrip_seconds',0.),('idle',False),('minimum_seconds',100),('checked_at',1.)]:
        bad=dict(proof,**{key:value})
        with pytest.raises(transport.RuntimeReadinessError,match='invalid_readiness_proof'):
            transport._readiness_still_safe(bad,'DRAFT',75,_monotonic=lambda:101.)
    with pytest.raises(transport.RuntimeReadinessError,match='schedule_changed_after_check'):
        transport._readiness_still_safe(proof,'DRAFT',75,_monotonic=lambda:109.)


@pytest.mark.parametrize('phase,minimum', [('DRAFT', 190), ('SELECT', 100), ('SINGLE', 100)])
def test_real_gate_checks_remote_and_both_clocks(phase, minimum):
    run, calls = runner()
    proof = transport._runtime_readiness(phase, 75, _clock=clock(BASE+10, BASE+10.4), _run=run, _environ=ENV)
    assert proof['status'] == 'PASSED' and proof['minimum_seconds'] == minimum
    assert proof['idle'] is True and proof['expected_head'] == HEAD and proof['crontab_sha256'] == CRON
    assert len(calls) == 1
    argv, kwargs = calls[0]
    assert argv[:7] == ['ssh', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=5', '-o', 'LogLevel=ERROR']
    assert argv[7:] == ['prism-db', '/usr/bin/python3', '-', HEAD, CRON]
    assert kwargs['timeout'] == 12 and kwargs['input'] == transport._REMOTE_READINESS


@pytest.mark.parametrize('env,reason', [
    ({}, 'expected_head_missing_or_invalid'),
    ({'PRISM_BTC_RESEARCH_EXPECTED_HEAD': HEAD}, 'expected_crontab_hash_missing_or_invalid'),
    ({'PRISM_BTC_RESEARCH_EXPECTED_HEAD': '-x', 'PRISM_BTC_RESEARCH_EXPECTED_CRONTAB_SHA256': CRON}, 'expected_head_missing_or_invalid'),
])
def test_missing_or_invalid_pins_never_call_ssh(env, reason):
    run, calls = runner()
    with pytest.raises(transport.RuntimeReadinessError, match=reason):
        transport._runtime_readiness('DRAFT', 75, _clock=clock(BASE+10), _run=run, _environ=env)
    assert not calls


def test_near_cron_stops_before_ssh():
    run, calls = runner()
    with pytest.raises(transport.RuntimeReadinessError, match='scheduled_tick_near'):
        transport._runtime_readiness('DRAFT', 75, _clock=clock(BASE+111), _run=run, _environ=ENV)
    assert not calls


@pytest.mark.parametrize('changes,reason', [
    ({'head': False}, 'server_head_changed'),
    ({'cron': False}, 'crontab_changed'),
    ({'idle': False}, 'decision_running'),
    ({'schedule': False}, 'unsupported_decision_schedule'),
    ({'code': 1}, 'remote_check_failed'),
    ({'remote': BASE+20}, 'remote_clock_skew'),
])
def test_remote_guard_fails_closed(changes, reason):
    run, calls = runner(**changes)
    with pytest.raises(transport.RuntimeReadinessError, match=reason):
        transport._runtime_readiness('DRAFT', 75, _clock=clock(BASE+10, BASE+10.4), _run=run, _environ=ENV)
    assert len(calls) == 1


def test_no_remote_retry_on_transport_error():
    calls = []
    def run(*args, **kwargs):
        calls.append(1)
        raise subprocess.TimeoutExpired('ssh', 12)
    with pytest.raises(transport.RuntimeReadinessError, match='remote_check_failed'):
        transport._runtime_readiness('DRAFT', 75, _clock=clock(BASE+10), _run=run, _environ=ENV)
    assert len(calls) == 1


@pytest.mark.parametrize('stdout', ['{}', 'not-json', 'x'*2049, json.dumps({'head_ok': 'true', 'cron_ok': True, 'schedule_ok': True, 'idle': True, 'checked_at': BASE+10.2})])
def test_remote_response_must_be_bounded_exact_and_typed(stdout):
    run = lambda *a, **k: SimpleNamespace(returncode=0, stdout=stdout)
    with pytest.raises(transport.RuntimeReadinessError, match='remote_response_invalid'):
        transport._runtime_readiness('DRAFT', 75, _clock=clock(BASE+10), _run=run, _environ=ENV)


def test_crossing_boundary_during_remote_check_does_not_reuse_old_proof():
    run, _ = runner()
    with pytest.raises(transport.RuntimeReadinessError, match='local_clock_changed'):
        transport._runtime_readiness('DRAFT', 75, _clock=clock(BASE+10, BASE+301), _run=run, _environ=ENV)


def test_recording_delay_can_invalidate_readiness_before_http():
    proof = {'status':'PASSED','head_ok':True,'cron_ok':True,'schedule_ok':True,'idle':True,'phase':'DRAFT','expected_head':HEAD,'crontab_sha256':CRON,
             'checked_at':BASE+10,'remote_checked_at':BASE+10,'local_remaining_seconds':290,
             'remote_remaining_seconds':290,'minimum_seconds':190}
    transport._readiness_still_safe(proof, 'DRAFT', 75, _clock=clock(BASE+20))
    with pytest.raises(transport.RuntimeReadinessError, match='schedule_changed_after_check'):
        transport._readiness_still_safe(proof, 'DRAFT', 75, _clock=clock(BASE+111))


def test_remote_process_scan_accepts_only_protection_runner(tmp_path):
    # Execute only the imported function prefix; no SSH, subprocess or live /proc.
    ns = {}
    exec(transport._REMOTE_READINESS.split('try:\n    actual_head=', 1)[0].replace('head,cron_hash=sys.argv[1:3]', "head,cron_hash='fixture','fixture'"), ns)
    idle = ns['idle']
    process = tmp_path / '123'
    process.mkdir()
    cmd = process / 'cmdline'
    cmd.write_bytes(b'python3\0-m\0live.scenario_runner\0--protect-only\0')
    assert idle(tmp_path) is True
    cmd.write_bytes(b'python3\0-m\0live.scenario_runner\0--execute\0')
    assert idle(tmp_path) is False
    cmd.write_bytes(b'python3\0/root/prism-insight/prism-btc/live/scenario_runner.py\0--protect-only\0')
    assert idle(tmp_path) is True
    cmd.write_bytes(b'python3\0/root/prism-insight/prism-btc/live/scenario_runner.py\0--execute\0')
    assert idle(tmp_path) is False


class Response(BytesIO):
    status = 200
    def geturl(self):
        return 'http://127.0.0.1:18741/v1/responses'


def request(schema):
    return dict(system_prompt='fixture', user_prompt='fixture', model='gpt-6-luna',
                reasoning_effort='high', fast_tier=False, timeout=10, retry_transport=False,
                response_schema={'type':'object','properties':schema})


def test_transport_blocks_before_actual_opener_when_server_busy(tmp_path, monkeypatch):
    monkeypatch.setenv('PRISM_BTC_SCENARIO_OAUTH_URL','http://127.0.0.1:18741/v1/responses')
    def blocked(*args):
        raise transport.RuntimeReadinessError('decision_running')
    monkeypatch.setattr(transport, '_runtime_readiness', blocked)
    opened=[];events=[];g=CallGuard(tmp_path,'a'*64,4)
    call=transport.GuardedModelCall(g,'case',events.append,open_url=lambda *a,**k: opened.append(1))
    with pytest.raises(ValueError):
        call(**request({'wait_proposal':{}}))
    assert not opened and g.snapshot()['actual_http_calls']==0 and g.snapshot()['stopped']
    assert events[-1]['kind']=='HTTP_ERROR' and events[-1]['runtime_guard_reason']=='decision_running'


def test_both_phases_call_builtin_gate_and_attach_readiness(tmp_path, monkeypatch):
    monkeypatch.setenv('PRISM_BTC_SCENARIO_OAUTH_URL','http://127.0.0.1:18741/v1/responses')
    checked=[];events=[]
    def readiness(phase, timeout):
        checked.append(phase)
        return {'status':'PASSED','phase':phase}
    monkeypatch.setattr(transport, '_runtime_readiness', readiness)
    monkeypatch.setattr(transport, '_readiness_still_safe', lambda proof, phase, timeout: None)
    raw=json.dumps({'status':'completed','model':'gpt-6-luna','output':[{'type':'message','role':'assistant','content':[{'type':'output_text','text':'{"action":"WAIT"}'}]}]}).encode()
    g=CallGuard(tmp_path,'a'*64,4);call=transport.GuardedModelCall(g,'case',events.append,open_url=lambda *a,**k:Response(raw))
    call(**request({'wait_proposal':{}}));call(**request({'selection_token':{}}))
    assert checked==['DRAFT','SELECT'] and g.snapshot()['actual_http_calls']==2
    assert [x['runtime_readiness']['phase'] for x in events if x['kind']=='HTTP_REQUEST']==['DRAFT','SELECT']


def test_late_after_durable_receipt_still_blocks_http(tmp_path, monkeypatch):
    monkeypatch.setenv('PRISM_BTC_SCENARIO_OAUTH_URL','http://127.0.0.1:18741/v1/responses')
    monkeypatch.setattr(transport,'_runtime_readiness',lambda *a:{'status':'PASSED'})
    def late(proof, phase, timeout):
        raise transport.RuntimeReadinessError('schedule_changed_after_check')
    monkeypatch.setattr(transport,'_readiness_still_safe',late)
    opened=[];events=[];g=CallGuard(tmp_path,'a'*64,4);call=transport.GuardedModelCall(g,'case',events.append,open_url=lambda *a,**k:opened.append(1))
    with pytest.raises(ValueError):call(**request({'wait_proposal':{}}))
    assert not opened and g.snapshot()['actual_http_calls']==0 and g.snapshot()['stopped']
    assert events[-1]['runtime_guard_reason']=='schedule_changed_after_check'


def test_placeholder_readiness_never_counts_as_proof():
    with pytest.raises(transport.RuntimeReadinessError, match='invalid_readiness_proof'):
        transport._readiness_still_safe({'status': 'PASSED', 'idle': True, 'checked_at': BASE+10,
                                         'minimum_seconds': 0}, 'DRAFT', 75, _clock=clock(BASE+10))


def test_second_phase_rechecks_and_blocks_before_http(tmp_path, monkeypatch):
    monkeypatch.setenv('PRISM_BTC_SCENARIO_OAUTH_URL', 'http://127.0.0.1:18741/v1/responses')
    phases=[];events=[];opened=[]
    def readiness(phase, timeout):
        phases.append(phase)
        if phase == 'SELECT':
            raise transport.RuntimeReadinessError('decision_running')
        return {'status':'PASSED','phase':phase}
    monkeypatch.setattr(transport, '_runtime_readiness', readiness)
    monkeypatch.setattr(transport, '_readiness_still_safe', lambda proof, phase, timeout: None)
    raw=json.dumps({'status':'completed','model':'gpt-6-luna','output':[{'type':'message','role':'assistant','content':[{'type':'output_text','text':'{\"action\":\"WAIT\"}'}]}]}).encode()
    def opener(*args, **kwargs):
        opened.append(1)
        return Response(raw)
    guard=CallGuard(tmp_path,'a'*64,4);call=transport.GuardedModelCall(guard,'case',events.append,open_url=opener)
    call(**request({'wait_proposal':{}}))
    with pytest.raises(ValueError):
        call(**request({'selection_token':{}}))
    assert phases==['DRAFT','SELECT'] and len(opened)==1
    assert guard.snapshot()['actual_http_calls']==1 and guard.snapshot()['stopped']
    assert events[-1]['runtime_guard_reason']=='decision_running'


def test_event_recorder_cannot_mutate_checked_readiness(tmp_path, monkeypatch):
    monkeypatch.setenv('PRISM_BTC_SCENARIO_OAUTH_URL', 'http://127.0.0.1:18741/v1/responses')
    proof={'status':'PASSED','phase':'DRAFT','minimum_seconds':190}
    monkeypatch.setattr(transport,'_runtime_readiness',lambda *a:proof)
    def check_saved(value, phase, timeout):
        assert value['minimum_seconds']==190
    monkeypatch.setattr(transport,'_readiness_still_safe',check_saved)
    def record(event):
        if event['kind']=='HTTP_REQUEST':
            event['runtime_readiness']['minimum_seconds']=0
    raw=json.dumps({'status':'completed','model':'gpt-6-luna','output':[{'type':'message','role':'assistant','content':[{'type':'output_text','text':'{\"action\":\"WAIT\"}'}]}]}).encode()
    guard=CallGuard(tmp_path,'a'*64,4);call=transport.GuardedModelCall(guard,'case',record,open_url=lambda *a,**k:Response(raw))
    call(**request({'wait_proposal':{}}))
    assert proof['minimum_seconds']==190 and guard.snapshot()['actual_http_calls']==1


@pytest.mark.parametrize('phase,minimum', [('SELECT', 190), ('DRAFT', 100)])
def test_wrong_phase_or_understated_window_proof_is_rejected(phase, minimum):
    proof={'status':'PASSED','head_ok':True,'cron_ok':True,'schedule_ok':True,'idle':True,'phase':phase,'expected_head':HEAD,'crontab_sha256':CRON,
           'checked_at':BASE+10,'remote_checked_at':BASE+10,'local_remaining_seconds':290,
           'remote_remaining_seconds':290,'minimum_seconds':minimum}
    with pytest.raises(transport.RuntimeReadinessError, match='invalid_readiness_proof'):
        transport._readiness_still_safe(proof,'DRAFT',75,_clock=clock(BASE+11))


@pytest.mark.parametrize('phase,timeout', [('DRAFT', float('nan')), ('DRAFT', 100), ('UNKNOWN', 5), ('SELECT', True)])
def test_invalid_or_unfittable_timeout_is_never_sent(phase, timeout):
    run, calls = runner()
    with pytest.raises(transport.RuntimeReadinessError):
        transport._runtime_readiness(phase, timeout, _clock=clock(BASE+10), _run=run, _environ=ENV)
    assert not calls


def test_default_transport_without_pins_blocks_even_if_caller_preflight_passed(tmp_path, monkeypatch):
    monkeypatch.setenv('PRISM_BTC_SCENARIO_OAUTH_URL', 'http://127.0.0.1:18741/v1/responses')
    monkeypatch.delenv('PRISM_BTC_RESEARCH_EXPECTED_HEAD', raising=False)
    monkeypatch.delenv('PRISM_BTC_RESEARCH_EXPECTED_CRONTAB_SHA256', raising=False)
    caller_preflight = lambda _context: True
    assert caller_preflight({'phase':'candidate'}) is True
    opened=[];events=[];guard=CallGuard(tmp_path,'a'*64,4)
    call=transport.GuardedModelCall(guard,'case',events.append,open_url=lambda *a,**k:opened.append(1))
    with pytest.raises(ValueError):
        call(**request({'wait_proposal':{}}))
    assert not opened and guard.snapshot()['actual_http_calls']==0 and guard.snapshot()['stopped']
    assert events[-1]['runtime_guard_reason']=='expected_head_missing_or_invalid'


def test_remote_cron_requires_verified_five_minute_decision_schedule():
    ns = {}
    exec(transport._REMOTE_READINESS.split('try:\n    actual_head=', 1)[0].replace('head,cron_hash=sys.argv[1:3]', "head,cron_hash='fixture','fixture'"), ns)
    check = ns['schedule_ok']
    decision = b'*/5 * * * * python3 -m live.scenario_runner --execute\n'
    protection = b'* * * * * python3 -m live.scenario_runner --protect-only\n'
    assert check(decision + protection) is True
    assert check(protection) is False
    assert check(b'* * * * * python3 -m live.scenario_runner --execute\n') is False
    assert check(decision + b'2 * * * * python3 -m live.scenario_runner --execute\n') is False
    assert check(decision + b'* * * * * python3 -m live.scenario_runner --protect-only --execute\n') is False
    assert check(b'*/5 * * * * python3 -m live.scenario_runner --protect-only && python3 -m live.scenario_runner --execute\n') is False
    assert check(b'*/5 * * * * python3 -m live.scenario_runner --execute # --protect-only\n') is True
    assert check(b'* * * * * python3 -m live.scenario_runner --execute # --protect-only\n') is False
    assert check(b'*/5 * * * * bash -c \"python3 -m live.scenario_runner --execute\"\n') is False
    assert check(b'\xff') is False


def test_stubbed_caller_preflight_cannot_bypass_actual_remote_busy_check(tmp_path, monkeypatch):
    monkeypatch.setenv('PRISM_BTC_SCENARIO_OAUTH_URL', 'http://127.0.0.1:18741/v1/responses')
    real_check = transport._runtime_readiness
    run, ssh_calls = runner(idle=False)
    monkeypatch.setattr(transport, '_runtime_readiness',
                        lambda phase, timeout: real_check(phase, timeout, _clock=clock(BASE+10, BASE+10.4),
                                                          _run=run, _environ=ENV))
    caller_preflight = lambda _context: True
    assert caller_preflight({'phase':'candidate'}) is True
    opened=[];events=[];guard=CallGuard(tmp_path,'a'*64,4)
    call=transport.GuardedModelCall(guard,'case',events.append,open_url=lambda *a,**k:opened.append(1))
    with pytest.raises(ValueError):
        call(**request({'wait_proposal':{}}))
    assert len(ssh_calls)==1 and not opened and guard.snapshot()['actual_http_calls']==0
    assert guard.snapshot()['stopped'] and events[-1]['runtime_guard_reason']=='decision_running'
