from contextlib import contextmanager
import multiprocessing
import sqlite3
import time

import pytest

import live.scenario_runtime as module
from live.entry_reservations import LockBusy
from live.shared_entry_coordinator import mutation_lock
from tests.test_scenario_runtime import setup, lock_clock, wait_proposal  # noqa: F401


def hold_lock(path, ready):
    conn=sqlite3.connect(path)
    with mutation_lock(conn):
        ready.send(True)
        time.sleep(.6)
    conn.close()


def test_real_process_snapshot_contention_releases_then_model_once(setup):  # noqa: F811
    r,b,now,path=setup
    b.ctx['account_captured_at']=now[0]
    parent,child=multiprocessing.get_context('spawn').Pipe()
    process=multiprocessing.get_context('spawn').Process(target=hold_lock,args=(str(path),child))
    calls=[]
    def snapshot():
        process.start()
        assert parent.poll(10) and parent.recv()
        return dict(valid=True,as_of_ms=now[0]*1000)
    r.snapshot=snapshot
    r.propose=lambda s,c:(calls.append(True) or wait_proposal(s,c))
    try:
        assert r.tick()['status']=='wait'
        assert calls==[True] and not b.executed
    finally:
        process.join(10)
        if process.is_alive():
            process.terminate()
            process.join()
        parent.close()
        child.close()


def contend(r, monkeypatch, elapsed, until):
    original=module.mutation_lock
    active=[]
    attempts=[]
    collect=r.snapshot
    r.snapshot=lambda:(active.append(True) or collect())
    @contextmanager
    def lock(conn):
        if active:
            attempts.append(True)
            if elapsed[0]<until:
                assert not conn.in_transaction
                raise LockBusy('test')
        with original(conn):
            yield
    monkeypatch.setattr(module,'mutation_lock',lock)
    return attempts


def test_snapshot_timeout_burns_slot_without_model(setup, monkeypatch, lock_clock):  # noqa: F811
    r,b,_,_=setup
    elapsed,sleeps=lock_clock
    contend(r,monkeypatch,elapsed,99)
    r.propose=lambda *_:pytest.fail('no model allowed')
    result=r.tick()
    assert result['status']=='lock_busy' and result['failure_stage']=='snapshot_persist'
    assert result['lock_wait_seconds']==20
    assert elapsed[0]==20 and max(sleeps)<=.25
    assert r.conn.execute('SELECT status FROM llm_scenario_slots').fetchone()[0]=='CLAIMED'
    assert not b.executed


@pytest.mark.parametrize('hold_seconds',[.25,9,14])
def test_transient_snapshot_wait_no_extra_broker_reads(setup,monkeypatch,lock_clock,hold_seconds):  # noqa: F811
    r,b,now,_=setup
    b.ctx['account_captured_at']=now[0]
    attempts=contend(r,monkeypatch,lock_clock[0],hold_seconds)
    calls=[]
    r.propose=lambda s,c:(calls.append(c.copy()) or wait_proposal(s,c))
    assert r.tick()['status']=='wait'
    assert len(calls)==1 and not b.executed
    assert calls[0]['account_captured_at']==now[0]
    assert b.reconciles==2
    assert len(attempts)==int(hold_seconds/.25)+2  # snapshot acquisition + post-model lock


@pytest.mark.parametrize('change,reason',[('state','snapshot_state_changed'),('slot','snapshot_slot_changed'),
    ('snapshot','snapshot_stale'),('account','snapshot_account_stale'),('missing_account','snapshot_account_stale')])
def test_wait_rechecks_inputs_without_calling_model(setup,monkeypatch,lock_clock,change,reason):  # noqa: F811
    r,b,now,_=setup
    b.ctx['account_captured_at']=now[0]-119 if change=='account' else now[0]
    if change=='missing_account':
        b.ctx.pop('account_captured_at')
    contend(r,monkeypatch,lock_clock[0],.25)
    original_sleep=module.time.sleep
    def sleep(seconds):
        original_sleep(seconds)
        if change=='state':
            state=r.state()
            state['version']+=1
            r._save(state)
        elif change=='slot':
            now[0]+=300
        elif change=='snapshot':
            now[0]+=121
        elif change=='account':
            now[0]+=2
    monkeypatch.setattr(module.time,'sleep',sleep)
    r.propose=lambda *_:pytest.fail('no model allowed')
    result=r.tick()
    assert result['status']=='stale_proposal' and result['reason']==reason
    assert result['model_called'] is False and not b.executed
    assert r.conn.execute('SELECT snapshot FROM llm_scenario_decisions').fetchone()[0] is None


def test_body_lockbusy_is_never_retried(setup,monkeypatch,lock_clock):  # noqa: F811
    r,b,_,_=setup
    state=r.state
    calls=[]
    def body():
        calls.append(True)
        if len(calls)==2:
            raise LockBusy('body failure')
        return state()
    r.state=body
    r.propose=lambda *_:pytest.fail('no model allowed')
    result=r.tick()
    assert result['status']=='lock_busy' and result['failure_stage']=='snapshot_persist'
    assert len(calls)==2 and not lock_clock[1] and not b.executed


def test_initial_lock_is_still_nonblocking(setup,monkeypatch,lock_clock):  # noqa: F811
    r,b,_,_=setup
    @contextmanager
    def busy(_):
        raise LockBusy('initial')
        yield
    monkeypatch.setattr(module,'mutation_lock',busy)
    assert r.tick()==dict(status='lock_busy',reason='another_protection_or_execution_tick')
    assert not lock_clock[1] and not b.executed
    assert r.conn.execute('SELECT COUNT(*) FROM llm_scenario_slots').fetchone()[0]==0


def test_uncontended_path_has_no_monotonic_wait_dependency(setup,monkeypatch):  # noqa: F811
    r,b,_,_=setup
    def forbidden():
        pytest.fail('no snapshot monotonic call without contention')
    monkeypatch.setattr(module.time,'monotonic',forbidden)
    # Isolate the unchanged post-model wait helper, whose timer predates this fix.
    monkeypatch.setattr(module,'_post_model_mutation_lock',module.mutation_lock)
    r.propose=wait_proposal
    result=r.tick()
    assert result['status']=='wait' and 'lock_wait_seconds' not in result
    assert not b.executed


def test_late_lock_acquisition_releases_without_running_body(monkeypatch,lock_clock):  # noqa: F811
    conn=sqlite3.connect(':memory:')
    attempts=[]
    released=[]
    @contextmanager
    def delayed(_):
        attempts.append(True)
        if len(attempts)==1:
            raise LockBusy('first acquisition')
        lock_clock[0][0]=20
        try:
            yield
        finally:
            released.append(True)
    monkeypatch.setattr(module,'mutation_lock',delayed)
    diagnostics={}
    with pytest.raises(LockBusy):
        with module._snapshot_mutation_lock(conn,diagnostics):
            pytest.fail('late acquisition must not run model or persistence body')
    assert released==[True] and diagnostics['lock_wait_seconds']==20


def test_contention_never_sleeps_inside_database_transaction(monkeypatch,lock_clock):  # noqa: F811
    conn=sqlite3.connect(':memory:')
    conn.execute('CREATE TABLE fixture(value INTEGER)')
    conn.execute('INSERT INTO fixture VALUES(1)')
    assert conn.in_transaction
    @contextmanager
    def busy(_):
        raise LockBusy('busy with transaction')
        yield
    monkeypatch.setattr(module,'mutation_lock',busy)
    with pytest.raises(LockBusy):
        with module._snapshot_mutation_lock(conn,{}):
            pytest.fail('no body')
    assert not lock_clock[1]
    conn.rollback()


@pytest.mark.parametrize('account_at',[None,float('nan'),float('inf'),1800000001.])
def test_contended_invalid_or_future_account_time_blocks_model(setup,monkeypatch,lock_clock,account_at):  # noqa: F811
    r,b,_,_=setup
    b.ctx['account_captured_at']=account_at
    contend(r,monkeypatch,lock_clock[0],.25)
    r.propose=lambda *_:pytest.fail('no model')
    outcome=r.tick()
    assert outcome['reason']=='snapshot_account_stale'
    assert outcome['model_called'] is False and not b.executed


def test_uncontended_legacy_contract_does_not_gain_account_age_gate(setup):  # noqa: F811
    r,b,now,_=setup
    b.ctx['account_captured_at']=now[0]-300
    calls=[]
    r.propose=lambda s,c:(calls.append(True) or wait_proposal(s,c))
    assert r.tick()['status']=='wait'
    assert calls==[True] and not b.executed


def test_slow_history_precedes_real_market_capture_without_extending_age_limits(setup, monkeypatch):  # noqa: F811
    import live.scenario_position_flow as flow
    r, b, now, _ = setup
    initial = now[0]
    captured = []
    b.context = lambda: dict(b.ctx, account_captured_at=now[0])
    def history(*_):
        now[0] += 65
        return dict(version=1, status='MISSING')
    def snapshot():
        captured.append(now[0])
        return dict(valid=True, as_of_ms=now[0]*1000)
    def model(s, c):
        now[0] += 60
        return wait_proposal(s, c)
    monkeypatch.setattr(flow, 'build_closed_continuity', history)
    r.snapshot, r.propose = snapshot, model
    result = r.tick()
    assert result['status'] == 'wait'
    assert captured == [initial+65] and not b.executed
    import json
    recorded = json.loads(r.conn.execute('SELECT context FROM llm_scenario_decisions').fetchone()[0])
    assert recorded['input_captured_at'] == initial+65
    assert recorded['account_captured_at'] == initial
    assert recorded['max_input_age_seconds'] == 120


def test_fresh_market_after_slow_history_cannot_refresh_expired_account(setup, monkeypatch):  # noqa: F811
    import live.scenario_position_flow as flow
    r, b, now, _ = setup
    b.ctx['account_captured_at'] = now[0]
    def history(*_):
        now[0] += 121
        return dict(version=1, status='MISSING')
    monkeypatch.setattr(flow, 'build_closed_continuity', history)
    r.propose = lambda *_: pytest.fail('expired original account cannot call model')
    result = r.tick()
    assert result['reason'] == 'snapshot_account_stale'
    assert result['model_called'] is False and not b.executed


def test_history_state_change_still_rejects_new_market_snapshot(setup, monkeypatch):  # noqa: F811
    import live.scenario_position_flow as flow
    r, b, _, _ = setup
    def history(*_):
        state = r.state()
        state['version'] += 1
        r._save(state)
        return dict(version=1, status='MISSING')
    monkeypatch.setattr(flow, 'build_closed_continuity', history)
    r.propose = lambda *_: pytest.fail('changed state cannot call model')
    result = r.tick()
    assert result['reason'] == 'snapshot_state_changed'
    assert not b.executed


def test_other_process_can_protect_while_history_is_read(setup, monkeypatch):  # noqa: F811
    import live.scenario_position_flow as flow
    r, b, now, path = setup
    b.ctx['account_captured_at'] = now[0]
    parent, child = multiprocessing.get_context('spawn').Pipe()
    process = multiprocessing.get_context('spawn').Process(target=hold_lock, args=(str(path), child))
    def history(*_):
        process.start()
        assert parent.poll(10) and parent.recv()
        return dict(version=1, status='MISSING')
    monkeypatch.setattr(flow, 'build_closed_continuity', history)
    r.propose = wait_proposal
    try:
        assert r.tick()['status'] == 'wait'
        assert not b.executed
    finally:
        process.join(10)
        if process.is_alive():
            process.terminate()
            process.join()
        parent.close()
        child.close()
