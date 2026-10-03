import sqlite3
from types import SimpleNamespace

from live.scenario_runner import run_once


def test_default_does_not_construct_runtime_or_call_broker(tmp_path):
    c=sqlite3.connect(tmp_path/'state.db')
    assert run_once(c,SimpleNamespace(environment='demo',lane='MAIN'))['status']=='execution_disabled'
    assert not c.execute("select name from sqlite_master where name like 'llm_%'").fetchall()
    c.close()


def test_wrong_account_never_reconciles(tmp_path):
    c=sqlite3.connect(tmp_path/'state.db')
    b=SimpleNamespace(environment='live',lane='MAIN',reconcile=lambda:1/0)
    assert run_once(c,b,protect_only=True)['reason']=='demo_main_required'
    c.close()


def test_protection_only_never_calls_model_or_market(tmp_path):
    c=sqlite3.connect(tmp_path/'state.db')
    calls=[]
    def reconcile():
        calls.append('protect')
        return {'protection_confirmed':True}
    b=SimpleNamespace(environment='demo',lane='MAIN',reconcile=reconcile)
    r=run_once(c,b,protect_only=True,snapshot=lambda:1/0,proposal=lambda *a:1/0)
    assert r['status']=='protection_checked' and calls==['protect'] and not r['model_called']
    c.close()


def test_readonly_adapter_is_not_claimed_as_protection(tmp_path):
    c=sqlite3.connect(tmp_path/'state.db')
    b=SimpleNamespace(environment='demo',lane='MAIN',reconcile=lambda:{'protection_status':'not_managed_by_scenario_adapter'})
    assert run_once(c,b,protect_only=True)['status']=='blocked'
    c.close()


def test_failed_protection_private_incident_dedup_and_verified_recovery(tmp_path):
    from live.scenario_runner import notify_runtime_status
    c=sqlite3.connect(tmp_path/'state.db')
    failure={'status':'blocked','reason':'protection_unconfirmed'}
    notify_runtime_status(c,failure,clock=lambda:1000)
    notify_runtime_status(c,failure,clock=lambda:1010)
    assert c.execute('SELECT count(*) FROM llm_scenario_outbox').fetchone()[0]==1
    notify_runtime_status(c,{'status':'wait'},clock=lambda:1020)
    assert c.execute('SELECT active FROM llm_scenario_runner_incident').fetchone()[0]==1
    notify_runtime_status(c,{'status':'protection_checked'},clock=lambda:1030)
    assert c.execute('SELECT count(*) FROM llm_scenario_outbox').fetchone()[0]==2
    assert c.execute('SELECT active FROM llm_scenario_runner_incident').fetchone()[0]==0
    c.close()


def test_independent_protection_queues_broker_alarm_without_model(tmp_path):
    c=sqlite3.connect(tmp_path/'state.db')
    b=SimpleNamespace(environment='demo',lane='MAIN',reconcile=lambda:{
        'protection_confirmed':False,'notices':[{'event_id':'p1','kind':'PENDING','timestamp':1000}]})
    assert run_once(c,b,protect_only=True)['status']=='blocked'
    assert c.execute('SELECT event_id FROM llm_scenario_outbox').fetchone()[0]=='p1'
    c.close()


def test_health_uses_scenario_observation_not_stale_legacy_cursor(tmp_path):
    from datetime import datetime,timezone
    from live import tracking,healthcheck
    from live.scenario_control import begin_transition,activate
    from live.scenario_runner import record_health
    c=tracking.get_connection(tmp_path/'state.db');tracking.ensure_schema(c)
    begin_transition(c,'123',clock=lambda:1000)
    activate(c,proof=lambda:dict(captured_at=1000,main_uid='123',main_flat=True,
        swing_flat=True,all_orders_terminal=True,legacy_clear=True,broker_ready=True),clock=lambda:1000)
    record_health(c,{'status':'wait'},snapshot={'valid':True,'as_of_ms':1000000})
    assert healthcheck._check_price_stale(c,'demo',datetime.fromtimestamp(1010,timezone.utc)) is None
    assert healthcheck._check_price_stale(c,'demo',datetime.fromtimestamp(2000,timezone.utc))['code']=='price_stale'
    c.close()
