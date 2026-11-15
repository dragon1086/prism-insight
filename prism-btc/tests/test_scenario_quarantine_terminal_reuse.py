"""Plan: reuse only same-instance exact terminal proofs during quarantine refresh.

Ordinary reconciliation, live/native queries and fresh account protection stay live.
"""
import json
from types import SimpleNamespace

import pytest

from live.scenario_broker import ScenarioDemoBroker
from live import scenario_management_quarantine as quarantine
from tests.test_scenario_management_quarantine_runtime import incident as incident_fixture
from tests.test_scenario_review_guard import model_receipt_hash


@pytest.fixture(name='incident')
def setup(tmp_path):
    generator = incident_fixture.__wrapped__(tmp_path, SimpleNamespace(param='LONG'))
    data = next(generator)
    data['authorize']()
    yield data
    try:
        next(generator)
    except StopIteration:
        pass


def track_queries(broker):
    original = broker._call
    calls = []
    def tracked(method, **params):
        if method in {'get_open_orders', 'get_order_history', 'get_executions'} and ('orderId' in params or 'orderLinkId' in params):
            calls.append((method, dict(params)))
        return original(method, **params)
    broker._call = tracked
    return calls


def queried(calls, child):
    return [p for _, p in calls if p.get('orderLinkId') == child['link_id'] or
            (child['order_id'] and p.get('orderId') == child['order_id'])]


def test_same_instance_refresh_reuses_exact_terminal_only(incident):
    broker = incident['broker']
    broker.reconcile()
    children = broker.children('s1')
    terminals = [c for c in children if c['status'] == 'TERMINAL' and c['kind'] != 'native_sl']
    native = next(c for c in children if c['kind'] == 'native_sl' and c['status'] == 'LIVE')
    assert len(terminals) >= 2
    calls = track_queries(broker)
    for _ in range(2):
        assert broker.context()['management_quarantine']['status'] == 'AUTHORIZED'
    assert all(not queried(calls, c) for c in terminals)
    assert queried(calls, native)
    calls.clear()
    broker._query_child(terminals[0])  # General exact query never uses the shortcut.
    assert queried(calls, terminals[0])


def test_restart_has_no_terminal_observation_proof(incident):
    old = incident['broker']
    broker = ScenarioDemoBroker(old.conn, session=incident['exchange'], expected_main_uid='123',
                               clock=old.clock, execution_enabled=True)
    terminals = [c for c in broker.children('s1') if c['status'] == 'TERMINAL' and c['kind'] != 'native_sl']
    calls = track_queries(broker)
    quarantine.refresh_known(broker)
    assert all(queried(calls, c) for c in terminals)


@pytest.mark.parametrize('change', ['request', 'evidence', 'status', 'order_id', 'created_at', 'uid', 'endpoint'])
def test_changed_row_or_account_cannot_reuse_old_observation(incident, change):
    broker = incident['broker']
    child = next(c for c in broker.children('s1') if c['status'] == 'TERMINAL' and c['kind'] != 'native_sl')
    broker._query_child(child)
    if change in {'request', 'evidence'}:
        value = dict(child[change], changed=True)
        broker.conn.execute(f'UPDATE llm_scenario_children SET {change}=? WHERE link_id=?', (json.dumps(value), child['link_id']))
    elif change in {'status', 'order_id', 'created_at'}:
        value = {'status': 'UNKNOWN', 'order_id': 'different', 'created_at': 1}[change]
        broker.conn.execute(f'UPDATE llm_scenario_children SET {change}=? WHERE link_id=?', (value, child['link_id']))
    elif change == 'uid':
        broker.expected_main_uid = '456'
    else:
        broker.session.endpoint = 'https://api.bybit.com'
    calls = []
    original = broker._query_child
    def query(row):
        calls.append(row['link_id'])
        return original(row)
    broker._query_child = query
    try:
        quarantine.refresh_known(broker)
    except Exception:
        pass
    assert child['link_id'] in calls


def test_failed_exact_recheck_invalidates_previous_terminal_observation(incident):
    broker = incident['broker']
    child = next(c for c in broker.children('s1') if c['status'] == 'TERMINAL' and c['kind'] != 'native_sl')
    broker._query_child(child)
    original = broker._call
    def failed(*args, **kwargs):
        raise RuntimeError('exact read failed')
    broker._call = failed
    with pytest.raises(Exception):
        broker._query_child(child)
    broker._call = original
    calls = track_queries(broker)
    quarantine.refresh_known(broker)
    assert queried(calls, child)


@pytest.mark.parametrize('reuse', [False, True])
@pytest.mark.parametrize('action', ['WAIT', 'EXIT'])
def test_real_two_phase_review_deadline_with_duplicate_terminal_get_latency(incident, monkeypatch, record_property, reuse, action):
    from copy import deepcopy
    from live.scenario_runtime import ScenarioRuntime
    from live.scenario_review_adapter import make_calculated_review_proposer
    from tests.test_scenario_review_runtime import wire
    broker, now = incident['broker'], incident['now']
    if not reuse:
        current_refresh = quarantine.refresh_known
        def old_refresh(*args, **kwargs):
            # Counterfactual old behavior: repeat every terminal query, not a product switch.
            getattr(broker, '_quarantine_terminal_observations', {}).clear()
            return current_refresh(*args, **kwargs)
        monkeypatch.setattr(quarantine, 'refresh_known', old_refresh)
    original_call = broker._call
    def delayed(method, **params):
        if method in {'get_open_orders', 'get_order_history', 'get_executions'} and ('orderId' in params or 'orderLinkId' in params):
            now[0] += 1
        return original_call(method, **params)
    broker._call = delayed
    captured, phases, start = {}, [], []
    rationale = dict(interpretation='원 계약과 현재 보호 상태를 검토합니다.', evidence_refs=[], comparisons=[])
    def plan(selected):
        return dict(wire(captured, selected), expires_at=captured['now'] + 120, entry_contract=None, rationale=rationale,
            thesis_assessment=dict(contract_id=captured['registered_entry_contract']['contract_id'],
                                   status='INTACT', reason='Original premise remains intact.'))
    def generate(**request):
        if 'wait_proposal' in request['response_schema']['properties']:
            result = dict(wait_proposal=plan('WAIT'), candidates=[plan('EXIT')], no_candidate_reason='')
            now[0] += 20
            phases.append('DRAFT')
        else:
            bundle = json.loads(request['user_prompt'])['calculated_candidates']
            chosen = bundle['wait'] if action == 'WAIT' else bundle['candidates'][0]
            result = dict(selection_token=f"{chosen['id']}:{model_receipt_hash(chosen)}", rationale=rationale)
            now[0] += 5
            phases.append('SELECT')
        return SimpleNamespace(text=json.dumps(result))
    adapter = make_calculated_review_proposer(generate=generate, clock=lambda: now[0])
    def propose(snapshot, context, *, record):
        captured.update(deepcopy(context))
        start.append(now[0])
        return adapter(snapshot, context, record=record)
    runtime = ScenarioRuntime(broker.conn, broker, lambda *_: pytest.fail('legacy model forbidden'),
        lambda: dict(valid=True, as_of_ms=now[0]*1000), clock=lambda: now[0], entry_contract_enabled=True,
        calculated_review_enabled=True, calculated_review_propose=propose)
    outcome = runtime.tick()
    record_property('review_elapsed_seconds', now[0] - start[0] if start else None)
    record_property('outcome', outcome['status'])
    assert phases == ['DRAFT', 'SELECT']
    if reuse:
        assert outcome['status'] == ('wait' if action == 'WAIT' else 'intent_pending'), outcome
        assert now[0] - start[0] < 75
    else:
        assert outcome.get('failure_code') == 'review_deadline_failure', outcome
        assert now[0] - start[0] >= 75


def test_registration_still_queries_every_known_terminal_even_when_observed(incident):
    broker = incident['broker']
    event = quarantine._latest(broker)['body']
    terminals = [c for c in broker.children('s1') if c['status'] == 'TERMINAL' and c['kind'] != 'native_sl']
    calls = track_queries(broker)
    quarantine.register(broker, approval_id='separate-explicit-registration', reason='registration verification test',
        **{key: event[key] for key in ('expires_at', 'scenario_id', 'contract_id', 'unknown_link_id',
                                      'unknown_request_sha256', 'pending_intents', 'maximum_quantity')})
    assert all(queried(calls, child) for child in terminals)
