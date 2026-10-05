import json
import sqlite3
import time
import subprocess
import sys

import pytest

from live import scenario_provenance as audit


def test_bounded_and_serialization_failure_do_not_escape(tmp_path):
    conn = sqlite3.connect(tmp_path / 'test.sqlite')
    with audit.run_capture(conn):
        audit.record('decision_input', {'bad': object()})
        audit.record('decision_input', {'large': 'a' * (audit.MAX_RECORD_BYTES + 1)})
        audit.record('decision_input', {'input_id': 'ok'}, decision_slot=42)
    rows = conn.execute('SELECT body FROM llm_scenario_audit_events').fetchall()
    assert len(rows) == 1
    assert json.loads(rows[0][0])['input_id'] == 'ok'


def test_flush_busy_preserves_business_transaction(tmp_path):
    conn = sqlite3.connect(tmp_path / 'test.sqlite')
    conn.execute('CREATE TABLE business (value TEXT)')
    with audit.run_capture(conn):
        conn.execute("INSERT INTO business VALUES ('pending')")
        audit.record('decision_input', {'input_id': 'ok'})
    assert conn.in_transaction
    conn.rollback()
    assert conn.execute('SELECT * FROM business').fetchall() == []


def test_capture_keeps_original_exception(tmp_path):
    conn = sqlite3.connect(tmp_path / 'test.sqlite')
    try:
        with audit.run_capture(conn):
            audit.record('exchange_call', {'status': 'UNKNOWN'})
            raise ValueError('business')
    except ValueError as exc:
        assert str(exc) == 'business'
    assert conn.execute('SELECT count(*) FROM llm_scenario_audit_events').fetchone()[0] == 1


def test_captured_body_is_immutable(tmp_path):
    conn = sqlite3.connect(tmp_path / 'test.sqlite')
    body = {'nested': {'quantity': 1}}
    with audit.run_capture(conn):
        audit.record('test', body)
        body['nested']['quantity'] = 99
    assert json.loads(conn.execute('SELECT body FROM llm_scenario_audit_events').fetchone()[0])['nested']['quantity'] == 1


@pytest.mark.parametrize('failure', ['manifest', 'flush', 'record_size', 'count'])
def test_real_runner_business_result_and_calls_unchanged(tmp_path, monkeypatch, failure):
    from live.scenario_runner import run_once
    from tests.test_scenario_runtime import Broker, proposal
    def run(name):
        conn = sqlite3.connect(tmp_path / name)
        broker = Broker()
        result = run_once(conn, broker, execute=True,
            snapshot=lambda: {'valid': True, 'as_of_ms': time.time()*1000},
            proposal=lambda snapshot, context, contract: proposal(snapshot, context))
        return result, broker.executed, broker.reconciles
    expected = run('control.db')
    def fail(*args, **kwargs):
        raise ValueError('secret must not leak')
    if failure == 'manifest':
        monkeypatch.setattr(audit, '_manifest', fail)
    elif failure == 'flush':
        monkeypatch.setattr(audit, '_flush', fail)
    elif failure == 'record_size':
        monkeypatch.setattr(audit, 'MAX_RECORD_BYTES', 1)
    else:
        monkeypatch.setattr(audit, 'MAX_EVENTS', 0)
    assert run('failure.db') == expected
    assert expected[1] == ['action-1']


def test_new_settlement_only_and_commit_verified(tmp_path):
    from live.scenario_runtime import ScenarioRuntime
    from tests.test_scenario_runtime import Broker, proposal, terminal, settlement, wait_proposal
    conn = sqlite3.connect(tmp_path / 'runtime.db')
    broker, now = Broker(), [1800000000.]
    runtime = ScenarioRuntime(conn, broker, proposal,
        lambda: {'valid': True, 'as_of_ms': now[0]*1000}, clock=lambda: now[0])
    runtime.tick()
    broker.evidence = dict(intents=[terminal()], settlement=settlement())
    runtime.propose = wait_proposal
    now[0] += 300
    with audit.run_capture(conn):
        runtime.tick()
    rows = conn.execute("SELECT observed_at,body FROM llm_scenario_audit_events WHERE kind='settlement_recorded'").fetchall()
    assert len(rows) == 1
    assert rows[0][0] == json.loads(rows[0][1])['detected_at']
    now[0] += 300
    with audit.run_capture(conn):
        runtime.tick()
    assert conn.execute("SELECT count(*) FROM llm_scenario_audit_events WHERE kind='settlement_recorded'").fetchone()[0] == 1


def test_rolled_back_settlement_is_not_recorded(tmp_path):
    conn = sqlite3.connect(tmp_path / 'test.sqlite')
    conn.execute('CREATE TABLE llm_scenario_settlements(scenario_id TEXT,evidence TEXT)')
    with audit.run_capture(conn):
        audit.record('settlement_recorded', {'settlement': {'net_pnl': 1}, 'detected_at': 123}, scenario_id='missing')
    assert conn.execute('SELECT count(*) FROM llm_scenario_audit_events').fetchone()[0] == 0


def test_policy_manifest_loaded_parity_detects_stale_and_prompt_changes(monkeypatch):
    from live import scenario_llm
    source = open(scenario_llm.__file__).read()
    assert audit._loaded_parity('live/scenario_llm.py', source) == 'VERIFIED'
    monkeypatch.setattr(scenario_llm, 'SYSTEM_PROMPT', scenario_llm.SYSTEM_PROMPT + ' changed')
    assert audit._loaded_parity('live/scenario_llm.py', source) == 'MIXED'


def test_bytecode_parity_ignores_paths_and_line_numbers():
    a = compile('def sample(x):\n return x + 1\n', 'before.py', 'exec').co_consts[0]
    b = compile('\n\ndef sample(x):\n return x + 1\n', 'after.py', 'exec').co_consts[0]
    c = compile('def sample(x):\n return x + 2\n', 'before.py', 'exec').co_consts[0]
    assert audit._code_signature(a) == audit._code_signature(b)
    assert audit._code_signature(a) != audit._code_signature(c)


def test_compiled_qualpaths_work_without_python311_code_qualname():
    source = '''
def outer():
    def nested():
        return 1
    class Inner:
        def method(self):
            return 2
    return nested, Inner
class Example:
    def method(self):
        def nested():
            return 3
        return nested
'''
    code = compile(source, '<test>', 'exec')
    paths = audit._compiled_functions(code)
    assert set(paths) == {'outer', 'outer.<locals>.nested', 'outer.<locals>.Inner',
        'outer.<locals>.Inner.method', 'Example', 'Example.method', 'Example.method.<locals>.nested'}
    for name, function_code in paths.items():
        assert function_code.co_name == name.split('.')[-1]
        if hasattr(function_code, 'co_qualname'):
            assert function_code.co_qualname == name


def test_new_exchange_attempt_captures_unknown_not_fill(tmp_path):
    from live.scenario_execution import ScenarioExecution
    class Execution(ScenarioExecution):
        execution_enabled = True
        def _identity(self):
            calls.append('identity')
        def _fail(self, reason):
            raise ValueError(reason)
    class Session:
        def place_order(self, **params):
            calls.append(('place_order', params))
            raise TimeoutError('secret')
    calls = []
    conn = sqlite3.connect(tmp_path / 'test.sqlite')
    executor = Execution()
    executor.session = Session()
    with audit.run_capture(conn):
        with pytest.raises(ValueError, match='submission_unknown'):
            executor._write('place_order', orderLinkId='child-1', qty='1')
    assert calls == ['identity', ('place_order', {'orderLinkId': 'child-1', 'qty': '1'})]
    body = json.loads(conn.execute('SELECT body FROM llm_scenario_audit_events').fetchone()[0])
    assert body['status'] == 'UNKNOWN'
    assert body['request']['orderLinkId'] == 'child-1'
    assert 'secret' not in json.dumps(body)


def test_optional_audit_clock_failure_never_blocks_exchange_ack(monkeypatch):
    from live.scenario_execution import ScenarioExecution
    calls = []
    class Execution(ScenarioExecution):
        execution_enabled = True
        def _identity(self):
            calls.append('identity')
    class Session:
        def place_order(self, **params):
            calls.append('place_order')
            return {'retCode': 0, 'result': {'orderId': 'ack'}}
    def fail():
        raise RuntimeError('audit clock unavailable')
    executor = Execution()
    executor.session = Session()
    monkeypatch.setattr(audit.time, 'time', fail)
    assert executor._write('place_order')['result']['orderId'] == 'ack'
    assert calls == ['identity', 'place_order']


def test_actual_lifecycle_self_contained_funding_and_exact_ids(tmp_path, monkeypatch):
    from tests.test_scenario_execution import live as fixture
    from tests.test_scenario_execution import test_runtime_broker_partial_adjust_exit_settlement_pipeline as pipeline
    from live.scenario_accounting import reconcile_scenario
    live = fixture.__wrapped__(tmp_path)
    broker, exchange = live
    monkeypatch.setattr(audit.time, 'time', lambda: exchange.now)
    with audit.run_capture(broker.conn):
        pipeline(live)
    events = broker.conn.execute('SELECT kind,scenario_id,intent_id,body FROM llm_scenario_audit_events').fetchall()
    observations = [json.loads(body) for kind, sid, _, body in events if kind == 'accounting_observation']
    assert observations
    for observation in observations:
        assert all(child['scenario_id'] == 's1' for child in observation['children'])
        assert observation['funding_source']['instruments'][0]['fundingInterval']
        assert isinstance(observation['funding_source']['raw_rows'], list)
        assert observation['funding_source']['next_funding_time'] > observation['funding_schedule']['end_ms']
        assert reconcile_scenario('s1', observation['financial_evidence'], observation['owned_orders'],
            observation['observation'], funding_schedule=observation['funding_schedule']) == observation['result']
    calls = [json.loads(body) for kind, _, _, body in events if kind == 'exchange_call']
    assert len(calls) == len(exchange.writes)
    assert all(event['request_hash'] == audit.digest(event['request']) for event in calls)
    intents = [(sid, intent) for kind, sid, intent, _ in events if kind == 'intent_committed']
    assert len(intents) == 3 and all(sid == 's1' for sid, _ in intents)
    assert len([1 for kind, _, _, _ in events if kind == 'settlement_recorded']) == 1


def test_current_loaded_manifest_is_verified_after_policy_imports():
    from live import scenario_contract  # noqa: F401
    manifest = audit._manifest()
    assert manifest['loaded_code_status'] == 'VERIFIED', manifest['loaded_code_checks']
    assert manifest['git_revision_status'] == 'OBSERVED_ONLY'


def test_fresh_protection_process_uses_execution_parity_without_importing_judgment():
    script = '''
import json
import socket
import sys
def forbidden(*args, **kwargs):
    raise AssertionError("No network allowed")
socket.socket.connect = forbidden
socket.getaddrinfo = forbidden
from live import scenario_runner, scenario_broker, scenario_provenance
assert "live.scenario_contract" not in sys.modules
assert "live.scenario_accounting" not in sys.modules
before = scenario_provenance._manifest()
assert before["execution_loaded_code_status"] == "UNKNOWN"
# The actual protected accounting path imports this module, not the auditor.
from live import scenario_accounting
manifest = scenario_provenance._manifest()
assert "live.scenario_contract" not in sys.modules
print(json.dumps(manifest))
'''
    completed = subprocess.run([sys.executable, '-c', script], capture_output=True,
                               text=True, check=True, timeout=15)
    manifest = json.loads(completed.stdout)
    assert manifest['loaded_code_status'] == 'UNKNOWN'
    assert manifest['judgment_loaded_code_status'] == 'UNKNOWN'
    assert manifest['execution_loaded_code_status'] == 'VERIFIED'


def test_notice_only_edit_does_not_change_judgment_hash(monkeypatch):
    before = audit._manifest()
    original = audit.Path.read_text
    def altered(path, *args, **kwargs):
        source = original(path, *args, **kwargs)
        if path.name == 'scenario_broker.py':
            signature = '    def _notices(self,active,children,observed,protected,settlement,pending=False,accounting=None):'
            assert signature in source
            source = source.replace(signature, signature + '\n        pass  # notice-only test edit')
        return source
    monkeypatch.setattr(audit.Path, 'read_text', altered)
    after = audit._manifest()
    assert before['policy_hash'] == after['policy_hash']
    assert before['execution_hash'] == after['execution_hash']
    assert before['source_hashes'] != after['source_hashes']


def test_manifest_work_occurs_once_after_capture(tmp_path, monkeypatch):
    conn = sqlite3.connect(tmp_path / 'test.sqlite')
    calls = []
    original = audit._manifest
    def manifest():
        calls.append('manifest')
        return original()
    monkeypatch.setattr(audit, '_manifest', manifest)
    with audit.run_capture(conn):
        for n in range(20):
            audit.record('test', {'n': n})
        assert calls == []
    assert calls == ['manifest']
