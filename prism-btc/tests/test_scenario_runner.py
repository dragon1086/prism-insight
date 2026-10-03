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
