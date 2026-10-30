"""Actual broker/runtime incident recovery; no network or production state."""
import copy
import json
import logging
import os
import sqlite3

import pytest

from live import scenario_incident_epoch as policy
from live.scenario_broker import ScenarioDemoBroker
from live.scenario_control import legacy_entries_allowed, legacy_management_allowed, read_control
from live.scenario_management_quarantine import digest
from live.scenario_runtime import ScenarioRuntime
from tests.test_scenario_management_quarantine_runtime import incident  # noqa: F401


@pytest.fixture
def flat_incident(incident, monkeypatch):  # noqa: F811
    i = incident
    i['authorize']()
    runtime, broker, exchange = i['runtime'], i['broker'], i['exchange']
    runtime.propose = lambda s, c: dict(i['model'](s, c), action='EXIT')
    assert runtime.tick()['status'] == 'intent_pending'
    exit_child = next(c for c in broker.children('s1') if c['kind'] == 'exit')
    exchange.fill(exit_child['link_id'], .05)
    for child in broker.children('s1'):
        if child['link_id'] != i['original']['children']['link_id']:
            broker._query_child(child)
    assert float(exchange.size) == 0
    monkeypatch.setattr(policy, 'SCENARIO', 's1')
    monkeypatch.setattr(policy, 'UNKNOWN', i['original']['children']['link_id'])
    from live import scenario_provenance
    loaded_parity = scenario_provenance._loaded_parity
    # Test-only incident identities differ from disk constants. Do not claim
    # loaded-code verification for that module, or weaken the production guard.
    monkeypatch.setattr(scenario_provenance, '_loaded_parity', lambda name, source, selected=None:
        'UNKNOWN' if name == 'live/scenario_incident_epoch.py' else loaded_parity(name, source, selected))
    # The fake supports BTC only. Empty other categories are explicit complete
    # responses, not an omitted category or a trusted boolean.
    positions, orders = exchange.get_positions, exchange.get_open_orders
    from tests.test_scenario_broker import reply
    exchange.get_positions = lambda **kw: (reply([]) if kw.get('category') != 'linear' or kw.get('settleCoin') == 'USDC' else positions(**kw))
    exchange.get_open_orders = lambda **kw: (reply([]) if kw.get('category') != 'linear' or kw.get('settleCoin') == 'USDC' else orders(**kw))
    executions = exchange.get_executions
    def scoped_executions(**kw):
        response = executions(**kw)
        response['result']['list'] = [r for r in response['result']['list']
            if kw.get('startTime', 0) <= int(r['execTime']) <= kw.get('endTime', 2**63-1)]
        return response
    exchange.get_executions = scoped_executions
    path = broker.conn.execute('PRAGMA database_list').fetchone()[2]
    code = policy.code_digest()
    proof = dict(captured_at=i['now'][0], host=os.uname().nodename, database=path,
        scheduler_before='decision-and-protection-enabled', scheduler_after='both-stopped',
        process_inventory_before='pid=2147483646 command=scenario_runner.py',
        process_inventory_after='no runner processes; fixture controlled process inventory',
        launcher_manifest=dict(epoch_protocol=1, code_sha256=code),
        stopped_workers=[dict(pid=2147483646, command='scenario_runner.py', started_at=1,
                              termination_evidence='waitpid collected (synthetic fixture)')])
    approval = dict(approval_id='separate-cutover-approval', scenario_id='s1',
        unknown_link_id=policy.UNKNOWN, main_uid='123', endpoint=exchange.endpoint,
        database=path, symbol='BTCUSDT', position_idx=0, code_sha256=code,
        residual_hidden_order_risk_accepted=True, issued_at=i['now'][0],
        expires_at=i['now'][0]+3600, epoch_id='new-observation-epoch',
        incident_sha256=digest(policy._snapshot(broker)), quiescence_sha256=digest(proof))
    i.update(approval=approval, proof=proof, old_state=runtime.state(),
             old_intents=list(broker.conn.execute('SELECT * FROM llm_scenario_intents')),
             old_children=copy.deepcopy(broker.children('s1')))
    exchange.writes.clear()
    return i


def transition(i):
    return policy.cutover(i['broker'], approval=i['approval'], quiescence_probe=lambda: i['proof'])


@pytest.fixture
def epoch_funding(flat_incident):
    i = flat_incident
    when = str(int(i['now'][0] * 1000) - 1000)
    execution = dict(execId='epoch-funding', orderId='funding-order', symbol='BTCUSDT',
        side='Buy', execType='Funding', execQty='.001', execFee='.00500415', execTime=when)
    transaction = dict(id='funding-txn', tradeId='epoch-funding', orderId='funding-order',
        symbol='BTCUSDT', currency='USDT', type='SETTLEMENT', side='Buy', qty='.001',
        funding='-.00500415', fee='0', cashFlow='0', transactionTime=when)
    executions, transactions = i['exchange'].get_executions, i['exchange'].get_transaction_log
    def with_funding(**kw):
        response = executions(**kw)
        if (not kw.get('orderId') and not kw.get('orderLinkId')
                and kw.get('startTime', 0) <= int(when) <= kw.get('endTime', 2**63-1)):
            response['result']['list'].append(execution)
        return response
    pairs = [transaction]
    def with_settlement(**kw):
        response = transactions(**kw)
        if kw.get('startTime', 0) <= int(when) <= kw.get('endTime', 2**63-1):
            response['result']['list'].extend(pairs)
        return response
    i['exchange'].get_executions = with_funding
    i['exchange'].get_transaction_log = with_settlement
    return i, execution, transaction, pairs


def test_epoch_preserves_exact_funding_pair_without_inventing_order(epoch_funding):
    i, execution, transaction, _ = epoch_funding
    result = transition(i)
    assert execution in result['financial_sources']['executions']
    assert transaction in result['financial_sources']['transactions']
    assert i['broker'].children('s1') == i['old_children']
    assert i['exchange'].writes == []
    assert policy.monitor(restarted(i), refresh=True) is not None
    assert not policy.halted(i['broker'].conn)


@pytest.mark.parametrize('bad', ['missing', 'duplicate', 'quantity', 'fee', 'type', 'symbol', 'trade'])
def test_epoch_denies_unverified_funding_and_foreign_trade(epoch_funding, bad):
    i, execution, transaction, pairs = epoch_funding
    if bad == 'missing':
        pairs.clear()
    elif bad == 'duplicate':
        pairs.append(dict(transaction, id='other-funding-txn'))
    elif bad == 'quantity':
        transaction['qty'] = '.002'
    elif bad == 'fee':
        transaction['funding'] = '0'
    elif bad == 'type':
        transaction['type'] = 'TRADE'
    elif bad == 'symbol':
        execution['symbol'] = 'ETHUSDT'
    else:
        execution['execType'] = 'Trade'
    with pytest.raises(ValueError, match='funding|unowned_execution'):
        transition(i)
    assert policy.receipt(i['broker'].conn) is None
    assert i['exchange'].writes == []


def restarted(i):
    return ScenarioDemoBroker(i['broker'].conn, session=i['exchange'], expected_main_uid='123',
                              clock=lambda: i['now'][0], execution_enabled=True)


def test_atomic_separation_preserves_unknown_intents_contract_and_daily(flat_incident):
    i = flat_incident
    before_daily = i['broker']._daily(i['broker'].capture_account())
    result = transition(i)
    conn = i['broker'].conn
    assert list(conn.execute('SELECT * FROM llm_scenario_intents')) == i['old_intents']
    assert i['broker'].children('s1') == i['old_children']
    assert conn.execute('SELECT count(*) FROM llm_scenario_settlements').fetchone()[0] == 0
    assert i['runtime'].state()['active'] is None
    assert i['runtime'].state()['breaker'] == i['old_state']['breaker']
    assert result['old_state'] == i['old_state']
    assert read_control(conn)['version'] == 2
    assert not legacy_entries_allowed(conn) and not legacy_management_allowed(conn)
    assert not policy.pending(conn)
    assert policy.historical(conn)[0]['status'] == 'HISTORICAL_UNSETTLED'
    current = restarted(i)
    current.reconcile()  # Explicit observation; context itself never polls history.
    context = current.context()
    assert context['scenario_risk_fraction'] <= .005
    assert not context['incident_epoch_blocked']
    assert current._daily(current.capture_account()) == before_daily
    assert i['exchange'].writes == []


def test_idempotent_approval_returns_receipt_without_reprobing(flat_incident):
    i = flat_incident
    first = transition(i)
    assert policy.cutover(i['broker'], approval=i['approval'], quiescence_probe=None) == first
    altered = dict(i['approval'], approval_id='different')
    with pytest.raises(ValueError, match='approval_reused'):
        policy.cutover(i['broker'], approval=altered, quiescence_probe=None)


def test_idle_deployment_needs_inventory_but_no_invented_stopped_worker(flat_incident):
    i = flat_incident
    i['proof']['stopped_workers'] = []
    i['proof']['process_inventory_before'] = 'fixture inventory: no runner processes'
    i['approval']['quiescence_sha256'] = digest(i['proof'])
    assert transition(i)['deployment_proof']['stopped_workers'] == []
    assert i['exchange'].writes == []
    assert i['broker'].children('s1') == i['old_children']


@pytest.mark.parametrize('field,value', [
    ('stopped_workers', None), ('stopped_workers', {}),
    ('process_inventory_before', ''), ('process_inventory_after', ''),
    ('scheduler_after', 'decision-and-protection-enabled'),
])
def test_idle_deployment_still_requires_complete_stop_proof(flat_incident, field, value):
    i = flat_incident
    i['proof']['stopped_workers'] = []
    i['proof'][field] = value
    i['approval']['quiescence_sha256'] = digest(i['proof'])
    with pytest.raises(ValueError, match='deployment_proof_incomplete'):
        transition(i)
    assert policy.receipt(i['broker'].conn) is None
    assert i['exchange'].writes == []


def test_snapshot_scenario_filter_remains_a_bound_value(flat_incident, monkeypatch):
    broker = flat_incident['broker']
    baseline = policy._snapshot(broker)
    monkeypatch.setattr(policy, 'SCENARIO', "s1' OR 1=1 --")
    snapshot = policy._snapshot(broker)
    assert all(not rows for table, rows in snapshot.items() if table.startswith('llm_scenario_'))
    assert snapshot['management'] == baseline['management']


@pytest.mark.parametrize('table', [
    'llm_scenario_intents', 'llm_scenario_children', 'llm_scenario_reductions',
    'llm_scenario_entry_contracts', 'llm_scenario_pending_leases',
])
def test_snapshot_requires_every_fixed_schema(flat_incident, table):
    broker = flat_incident['broker']
    statements = {
        'llm_scenario_intents': 'DROP TABLE llm_scenario_intents',
        'llm_scenario_children': 'DROP TABLE llm_scenario_children',
        'llm_scenario_reductions': 'DROP TABLE llm_scenario_reductions',
        'llm_scenario_entry_contracts': 'DROP TABLE llm_scenario_entry_contracts',
        'llm_scenario_pending_leases': 'DROP TABLE llm_scenario_pending_leases',
    }
    broker.conn.execute(statements[table])
    with pytest.raises(ValueError, match='incident_epoch_schema_missing'):
        policy._snapshot(broker)


@pytest.mark.parametrize('mutation', ['expired', 'hash', 'uid', 'endpoint', 'risk_acceptance', 'probe', 'worker_alive', 'inflight', 'extra_intent'])
def test_denials_do_not_separate_or_post(flat_incident, mutation):
    i = flat_incident
    if mutation == 'expired':
        i['approval']['expires_at'] = i['now'][0]
    elif mutation == 'hash':
        i['approval']['incident_sha256'] = '0'*64
    elif mutation == 'uid':
        i['approval']['main_uid'] = '999'
    elif mutation == 'endpoint':
        i['approval']['endpoint'] = 'https://api.bybit.com'
    elif mutation == 'risk_acceptance':
        i['approval']['residual_hidden_order_risk_accepted'] = False
    elif mutation == 'probe':
        i['proof']['process_inventory_after'] = ''
    elif mutation == 'worker_alive':
        i['proof']['stopped_workers'][0]['pid'] = os.getpid()
        i['approval']['quiescence_sha256'] = digest(i['proof'])
    elif mutation == 'inflight':
        i['broker']._incident_transport_active = True
    else:
        i['broker'].conn.execute("INSERT INTO llm_scenario_intents VALUES('foreign','foreign','{}','PENDING',NULL)")
        i['broker'].conn.commit()
    with pytest.raises(ValueError):
        transition(i)
    assert not policy.receipt(i['broker'].conn)
    assert i['runtime'].state()['active'] == i['old_state']['active']
    assert i['exchange'].writes == []


@pytest.mark.parametrize('method,params', [
    ('place_order', dict(orderLinkId='old', reduceOnly=True)),
    ('cancel_order', dict(orderLinkId='old')),
    ('set_trading_stop', dict(stopLoss='98')),
    ('amend_order', dict(orderLinkId='old')),
])
def test_old_worker_all_posts_are_sealed_after_cutover(flat_incident, method, params):
    i = flat_incident
    transition(i)
    with pytest.raises(ValueError, match='stale_worker'):
        i['broker']._write(method, **params)
    assert i['exchange'].writes == []


def test_epoch_read_failure_never_uses_reduce_or_cancel_fallback(flat_incident, monkeypatch):
    i = flat_incident
    transition(i)
    broker = restarted(i)
    monkeypatch.setattr(policy, 'epoch', lambda conn: (_ for _ in ()).throw(ValueError('read failure')))
    for method in ('place_order', 'cancel_order', 'set_trading_stop'):
        with pytest.raises(ValueError, match='read failure'):
            broker._write(method, reduceOnly=True)
    assert i['exchange'].writes == []


@pytest.mark.parametrize('crash_table', ['llm_scenario_broker_evidence', 'llm_scenario_state', 'llm_scenario_control', 'llm_scenario_recovery_events'])
def test_each_atomic_write_failure_rolls_back_without_nested_commits(flat_incident, crash_table):
    i = flat_incident
    conn = i['broker'].conn
    operation = 'INSERT' if crash_table.endswith(('evidence', 'events')) else 'UPDATE'
    # Evidence collectors may append before BEGIN; only interrupt the cutover
    # event's INSERT, never their separate durable observation records.
    condition = "WHEN NEW.kind='incident_epoch_v1'" if crash_table.endswith('evidence') else ''
    from live.scenario_recovery import ensure_schema
    ensure_schema(conn)
    conn.execute(f"CREATE TRIGGER crash_cutover BEFORE {operation} ON {crash_table} {condition} BEGIN SELECT RAISE(ABORT,'injected crash'); END")
    conn.commit()
    with pytest.raises(sqlite3.IntegrityError, match='injected crash'):
        transition(i)
    assert not policy.receipt(conn)
    assert i['runtime'].state() == i['old_state']
    assert read_control(conn)['version'] == 1
    assert i['exchange'].writes == []


def test_late_evidence_halts_new_risk_without_mutating_old_unknown(flat_incident):
    i = flat_incident
    transition(i)
    broker = restarted(i)
    child = i['original']['children']
    i['exchange'].orders[child['link_id']] = dict(child['request'], orderId='late-old',
        orderStatus='Untriggered', cumExecQty='0', leavesQty=child['request']['qty'])
    policy.monitor(broker, refresh=True)
    assert policy.new_risk_blocked(broker)
    assert next(c for c in broker.children('s1') if c['link_id'] == child['link_id']) == child
    assert broker.conn.execute('SELECT count(*) FROM llm_scenario_settlements').fetchone()[0] == 0
    assert i['exchange'].writes == []


def new_runtime(i):
    from live import scenario_recovery as recovery
    from tests.test_scenario_runtime import proposal, wait_proposal
    from tests.test_scenario_entry_contract_runtime import definition
    broker = restarted(i)
    price = [100.]
    calls = []

    def model(snapshot, context):
        calls.append(copy.deepcopy(context))
        recovery.record_model_request(system_prompt='fixture', user_prompt=json.dumps(context), response_schema={})
        first = context['recovery']['first_observation']
        result = wait_proposal(snapshot, context) if first else proposal(snapshot, context)
        result.update(action_id='new-action-'+str(int(i['now'][0])), scenario_id=context['scenario_id'] or 'new-scenario')
        if not first:
            contract = definition('LONG')
            contract['explicit_failure']['price'] = 98.5
            result.update(chase=dict(max_bps=0, max_reprices=0), entry_contract=contract,
                          thesis_assessment=None, hard_stop=98,
                          entries=[dict(id='new-entry', price=100, quantity=.1)])
        else:
            result.update(entry_contract=None, thesis_assessment=None)
        result['recovery'] = dict(decision='OBSERVE' if first else 'PROBE', reason='New numerical evidence',
            changed_evidence=[] if first else ['timeframes.30m.closed.close'],
            counterevidence='A reversal remains possible.', invalidation='Original hard stop invalidates premise.')
        recovery.record_model_wire(json.dumps(result))
        return result

    runtime = ScenarioRuntime(broker.conn, broker, model,
        lambda: dict(valid=True, as_of_ms=i['now'][0]*1000,
            timeframes={'30m': {'closed': {'close': price[0], 'ma10': 99.}}}),
        clock=lambda: i['now'][0], entry_contract_enabled=True)
    return runtime, broker, price, calls


def open_new(i):
    transition(i)
    i['now'][0] += 300
    i['exchange'].now = i['now'][0]
    runtime, broker, price, calls = new_runtime(i)
    execute_errors = []
    execute = broker.execute
    def inspected_execute(*args, **kwargs):
        try:
            return execute(*args, **kwargs)
        except Exception as exc:
            execute_errors.append(repr(exc))
            raise
    broker.execute = inspected_execute
    assert runtime.tick()['status'] == 'wait'
    assert i['exchange'].writes == []
    assert runtime.state()['recovery']['phase'] == 'OBSERVING'
    i['now'][0] += 300
    i['exchange'].now = i['now'][0]
    price[0] += 1
    outcome = runtime.tick()
    assert outcome['status'] == 'intent_pending', outcome
    assert runtime.state()['active']['scenario_id'] == 'new-scenario'
    assert runtime.state()['recovery']['risk_fraction'] <= .005
    assert runtime.state()['recovery']['auto_promotion'] is False
    assert all(c['scenario_id'] == 'new-scenario' for c in broker.children())
    assert len([r for kind, r in i['exchange'].writes if kind == 'place']) == 1, (outcome, execute_errors)
    assert calls[0]['historical_unsettled'][0]['excluded_from_performance']
    assert list(broker.conn.execute('SELECT * FROM llm_scenario_intents WHERE scenario_id=?', ('s1',))) == i['old_intents']
    return runtime, broker


def test_new_runtime_requires_first_observation_then_actual_changed_evidence(flat_incident):
    open_new(flat_incident)


def fill_new(i, broker):
    exchange = i['exchange']
    exchange.orders['historical-native'] = copy.deepcopy(exchange.orders['native'])
    entry = next(c for c in broker.children() if c['kind'] == 'entry')
    exchange.fill(entry['link_id'], .1)
    exchange.orders['native']['orderId'] = 'new-native-id'
    broker.reconcile()


def test_new_filled_trade_settles_separately_and_preserves_old_unsettled(flat_incident):
    i = flat_incident
    runtime, broker = open_new(i)
    fill_new(i, broker)
    assert not policy.new_risk_blocked(broker), policy._events(broker.conn, policy.HALT)
    new_entry_fill = i['exchange'].fills[-1]['execId']
    i['exchange'].fill('native', .1)
    new_exit_fill = i['exchange'].fills[-1]['execId']
    i['now'][0] += 300
    i['exchange'].now = i['now'][0]
    outcome = runtime.tick()
    assert runtime.state()['active'] is None, (outcome, broker.reconcile())
    settlements = list(broker.conn.execute('SELECT scenario_id,evidence FROM llm_scenario_settlements'))
    assert len(settlements) == 1 and settlements[0][0] == 'new-scenario'
    proof = json.loads(settlements[0][1])
    assert set(proof['execution_ids']) == {new_entry_fill, new_exit_fill}
    assert proof['net_pnl'] == -.02
    assert runtime.state()['recovery']['auto_promotion'] is False
    assert list(broker.conn.execute('SELECT * FROM llm_scenario_intents WHERE scenario_id=?', ('s1',))) == i['old_intents']
    assert broker.children('s1') == i['old_children']


def test_late_old_order_blocks_new_risk_but_current_native_protection_remains(flat_incident):
    i = flat_incident
    runtime, broker = open_new(i)
    fill_new(i, broker)
    child = i['original']['children']
    i['exchange'].orders[child['link_id']] = dict(child['request'], orderId='late-original',
        orderStatus='Untriggered', cumExecQty='0', leavesQty=child['request']['qty'])
    # Simulate a missing Full SL: independent protection must run before the
    # historical monitor and remain allowed after the new-risk latch is set.
    i['exchange'].stop = '0'
    i['exchange'].orders['native']['triggerPrice'] = '0'
    i['now'][0] += 11
    i['exchange'].now = i['now'][0]
    result = broker.reconcile()
    assert result['protection_confirmed'] is True
    assert i['exchange'].stop == '98'
    assert policy.new_risk_blocked(broker)
    assert broker._verify_protection(broker.capture_account(), 98, 'LONG')
    before = len(i['exchange'].writes)
    with pytest.raises(ValueError, match='new_risk_halted'):
        broker._write('place_order', orderLinkId='forbidden-new-risk', reduceOnly=False)
    assert len(i['exchange'].writes) == before
    assert runtime.state()['active']['scenario_id'] == 'new-scenario'


def test_lower_existing_cap_is_preserved_and_never_promoted(flat_incident):
    from live import scenario_recovery as recovery
    i = flat_incident
    state = i['runtime'].state()
    state['recovery'] = dict(risk_fraction=.002)
    i['runtime']._save(state)
    transition(i)
    state = i['runtime'].state()
    assert recovery.offered_fraction(state) == .002
    assert recovery.risk_fraction(state, None) == .002
    evidence = state['recovery']['stage_evidence']
    evidence.update(window=[dict(scenario_id=str(n), settlement_hash='a'*64,
        net_pnl=10., settled_at=1800000000.+n) for n in (1, 2)])
    evidence.update(**recovery._window_metrics(evidence['window']))
    assert recovery.offered_stage(state['recovery']) == 0


@pytest.mark.parametrize('case', ['daily_halted', 'daily_missing', 'flows_missing', 'source_incomplete', 'source_order', 'source_position', 'extra_unknown', 'transport_lease'])
def test_account_and_risk_boundaries_fail_closed(flat_incident, case):
    i = flat_incident
    conn = i['broker'].conn
    from tests.test_scenario_broker import reply
    if case == 'daily_halted':
        raw = json.loads(conn.execute('SELECT body FROM llm_scenario_daily_accounting WHERE id=1').fetchone()[0])
        raw['halted'] = True
        conn.execute('UPDATE llm_scenario_daily_accounting SET body=?', (json.dumps(raw),))
    elif case == 'daily_missing':
        conn.execute('DELETE FROM llm_scenario_daily_accounting')
    elif case == 'flows_missing':
        i['exchange'].get_transaction_log = lambda **kw: dict(retCode=10001)
    elif case.startswith('source'):
        orders, positions = i['exchange'].get_open_orders, i['exchange'].get_positions
        if case == 'source_incomplete':
            i['exchange'].get_open_orders = lambda **kw: (dict(retCode=10001) if kw.get('category') == 'option' else orders(**kw))
        elif case == 'source_order':
            i['exchange'].get_open_orders = lambda **kw: (reply([dict(orderId='foreign', symbol='ETHUSDT')]) if kw.get('category') == 'spot' else orders(**kw))
        else:
            i['exchange'].get_positions = lambda **kw: (reply([dict(symbol='ETHUSDT', positionIdx=0, size='1')]) if kw.get('settleCoin') == 'USDC' else positions(**kw))
    elif case == 'extra_unknown':
        conn.execute("INSERT INTO llm_scenario_children VALUES('foreign','foreign','foreign','entry','e','{}','UNKNOWN',NULL,NULL,1)")
    else:
        policy._append(conn, policy.TRANSPORT, dict(call_id='crashed-worker', state='SENDING'), i['now'][0])
    conn.commit()
    with pytest.raises(Exception):
        transition(i)
    assert not policy.receipt(conn)
    assert read_control(conn)['version'] == 1
    assert i['exchange'].writes == []


def test_one_atomic_transaction_has_no_early_commit(flat_incident):
    i = flat_incident
    sql = []
    i['broker'].conn.set_trace_callback(sql.append)
    transition(i)
    start = next(n for n, statement in enumerate(sql) if statement == 'BEGIN IMMEDIATE')
    assert sum(statement == 'COMMIT' for statement in sql[start:]) == 1
    assert sql[start:].index('COMMIT') > next(n for n, statement in enumerate(sql[start:]) if 'UPDATE llm_scenario_control' in statement)


def test_legacy_normal_runner_control_reader_rejects_epoch_v2(flat_incident):
    from tests.test_incident_epoch_legacy_blocker import baseline_source
    i = flat_incident
    transition(i)
    source = baseline_source('scenario_control')
    namespace = {'json': json}
    exec(compile(source, '<frozen-control-v1>', 'exec'), namespace)
    with pytest.raises(ValueError, match='scenario_control_invalid'):
        namespace['read_control'](i['broker'].conn)
    assert not namespace['legacy_entries_allowed'](i['broker'].conn)
    assert not namespace['legacy_management_allowed'](i['broker'].conn)


def test_transport_and_final_epoch_check_hold_same_mutex(flat_incident, monkeypatch):
    from live import shared_entry_coordinator
    i = flat_incident
    runtime, broker = open_new(i)
    called = []
    original = i['exchange'].cancel_order
    lock_path = str(shared_entry_coordinator.database_path(broker.conn)) + '.btc-execution.lock'
    import os
    def cancel(**params):
        assert (os.getpid(), lock_path) in shared_entry_coordinator._held.keys
        assert broker._incident_transport_active is True
        called.append(True)
        return original(**params)
    monkeypatch.setattr(i['exchange'], 'cancel_order', cancel)
    entry = next(c for c in broker.children() if c['kind'] == 'entry')
    broker._cancel(entry)
    assert called == [True]
    assert runtime.state()['incident_epoch'] == i['approval']['epoch_id']


def test_old_link_rejected_even_by_current_epoch_broker(flat_incident):
    i = flat_incident
    _, broker = open_new(i)
    before = len(i['exchange'].writes)
    with pytest.raises(ValueError, match='sealed_order'):
        broker._write('cancel_order', orderLinkId=i['original']['children']['link_id'])
    assert len(i['exchange'].writes) == before


def test_fresh_current_pending_does_not_inherit_historical_unknown_age(flat_incident):
    from datetime import datetime, timezone
    from live import healthcheck
    i = flat_incident
    _, broker = open_new(i)
    now = datetime.fromtimestamp(i['now'][0]+1, timezone.utc)
    assert healthcheck._check_execution_stalled(broker.conn, 'demo', now) is None


@pytest.mark.parametrize('fill_race', [False, True])
def test_halt_cancels_existing_pending_entry_and_protects_racing_fill(flat_incident, fill_race):
    from live.scenario_recovery import authorize_pending
    i = flat_incident
    runtime, broker = open_new(i)
    entry = next(c for c in broker.children() if c['kind'] == 'entry')
    assert entry['status'] == 'LIVE'
    policy.record_late(broker, dict(status='known HALT before next protection cycle'))
    state = runtime.state()
    assert not authorize_pending(dict(state, incident_halted=True), state['active'], i['now'][0])
    original_cancel = i['exchange'].cancel_order
    if fill_race:
        def racing_cancel(**params):
            i['exchange'].orders['historical-native'] = copy.deepcopy(i['exchange'].orders['native'])
            i['exchange'].fill(entry['link_id'], .04)
            i['exchange'].orders['native']['orderId'] = 'racing-native-id'
            return original_cancel(**params)
        i['exchange'].cancel_order = racing_cancel
    before = len([r for kind, r in i['exchange'].writes if kind == 'place'])
    result = broker.reconcile()
    current = next(c for c in broker.children() if c['link_id'] == entry['link_id'])
    assert current['status'] == 'TERMINAL'
    assert current['evidence']['order']['orderStatus'] == 'Cancelled'
    assert float(i['exchange'].size) == (.04 if fill_race else 0)
    assert result['protection_confirmed'] is True
    if fill_race:
        assert broker._verify_protection(broker.capture_account(), 98, 'LONG')
    later = [r for kind, r in i['exchange'].writes if kind == 'place'][before:]
    assert all(r.get('reduceOnly') is True for r in later)


def test_postcutover_unknown_link_execution_halts_even_when_orders_remain_absent(flat_incident):
    i = flat_incident
    transition(i)
    broker = restarted(i)
    i['now'][0] += 11
    i['exchange'].fills.append(dict(execId='late-link-only', orderId='opaque-original-id',
        orderLinkId=policy.UNKNOWN, symbol='BTCUSDT', side='Sell', execType='Trade',
        execQty='.001', execPrice='100', execFee='.01', execTime=str(int(i['now'][0]*1000))))
    policy.monitor(broker, refresh=True)
    assert policy.halted(broker.conn)
    assert policy.new_risk_blocked(broker)
    assert next(c for c in broker.children('s1') if c['link_id'] == policy.UNKNOWN) == i['original']['children']
    assert not broker.conn.execute('SELECT 1 FROM llm_scenario_settlements').fetchone()
    assert any('late-link-only' in json.dumps(e) for e in policy._events(broker.conn, policy.HALT))


def test_final_guard_and_projection_reuse_explicit_observation_without_rest(flat_incident, monkeypatch):
    i = flat_incident
    _, broker = open_new(i)
    def no_rest(*args, **kwargs):
        raise AssertionError('Historical REST was repeated in a final guard/projection.')
    monkeypatch.setattr(broker, '_call', no_rest)
    assert policy.monitor(broker) is broker._incident_observation
    assert not policy.new_risk_blocked(broker)
    policy.guard_write(broker, 'place_order', dict(reduceOnly=False, orderLinkId='unsubmitted-current'))
    i['now'][0] += 11
    with pytest.raises(ValueError, match='new_risk_halted'):
        policy.guard_write(broker, 'place_order', dict(reduceOnly=False, orderLinkId='stale-current'))


def test_current_epoch_guard_rejects_different_broker_account_without_rest(flat_incident, monkeypatch):
    i = flat_incident
    _, broker = open_new(i)
    monkeypatch.setattr(broker, 'expected_main_uid', '999')
    with pytest.raises(ValueError, match='epoch_identity_changed'):
        policy.guard_write(broker, 'set_trading_stop', dict(stopLoss='98'))


def test_identity_get_delay_cannot_send_entry_after_final_observation_expires(flat_incident, monkeypatch):
    i = flat_incident
    _, broker = open_new(i)
    identity = broker._identity
    def delayed_identity():
        identity()
        i['now'][0] += 11
    monkeypatch.setattr(broker, '_identity', delayed_identity)
    before = len(i['exchange'].writes)
    with pytest.raises(ValueError):
        broker._write('place_order', category='linear', symbol='BTCUSDT', positionIdx=0,
            orderLinkId='stale-after-identity', reduceOnly=False, side='Buy', qty='.1', orderType='Limit', price='100')
    assert len(i['exchange'].writes) == before


def test_known_halt_cancellation_precedes_slow_historical_gets(flat_incident, monkeypatch):
    i = flat_incident
    _, broker = open_new(i)
    entry = next(c for c in broker.children() if c['kind'] == 'entry')
    policy.record_late(broker, dict(status='known historical HALT'))
    broker._incident_observation = None
    query = broker._query_child
    def delayed_historical(child):
        if child['scenario_id'] == 's1':
            assert i['exchange'].orders[entry['link_id']]['orderStatus'] == 'Cancelled'
            i['now'][0] += 80
        return query(child)
    monkeypatch.setattr(broker, '_query_child', delayed_historical)
    broker.reconcile()
    assert i['exchange'].orders[entry['link_id']]['orderStatus'] == 'Cancelled'
    assert broker._incident_observation is None
    assert policy.halted(broker.conn)


def test_current_protection_precedes_slow_historical_gets(flat_incident, monkeypatch):
    i = flat_incident
    _, broker = open_new(i)
    fill_new(i, broker)
    i['exchange'].stop = '0'
    i['exchange'].orders['native']['triggerPrice'] = '0'
    broker._incident_observation = None
    query = broker._query_child
    def delayed_historical(child):
        if child['scenario_id'] == 's1':
            assert i['exchange'].stop == '98'
            i['now'][0] += 80
        return query(child)
    monkeypatch.setattr(broker, '_query_child', delayed_historical)
    result = broker.reconcile()
    assert result['protection_confirmed'] is True
    assert policy.new_risk_blocked(broker)


def test_rolling_financial_coverage_passes_day_seven_without_resetting_incident(flat_incident):
    i = flat_incident
    transition(i)
    broker = restarted(i)
    for day in range(9):
        policy.monitor(broker, refresh=True)
        assert not policy.halted(broker.conn), (day, policy._events(broker.conn, policy.HALT))
        assert not policy.new_risk_blocked(broker)
        i['now'][0] += 86400
    assert next(c for c in broker.children('s1') if c['link_id'] == policy.UNKNOWN) == i['original']['children']
    assert len(policy._events(broker.conn, policy.FINANCE_CURSOR)) == 9


def test_unobserved_financial_gap_beyond_api_window_remains_fail_closed(flat_incident):
    i = flat_incident
    transition(i)
    broker = restarted(i)
    i['now'][0] += 8*86400
    policy.monitor(broker, refresh=True)
    assert policy.halted(broker.conn)
    assert policy.new_risk_blocked(broker)
    assert not policy._events(broker.conn, policy.FINANCE_CURSOR)


def test_runtime_overlong_historical_observation_skips_model_and_all_new_posts(flat_incident, monkeypatch):
    i = flat_incident
    transition(i)
    i['now'][0] += 300
    runtime, broker, _, calls = new_runtime(i)
    query = broker._query_child
    def delayed_history(child):
        if child['scenario_id'] == 's1':
            i['now'][0] += 80
        return query(child)
    monkeypatch.setattr(broker, '_query_child', delayed_history)
    outcome = runtime.tick()
    assert outcome['status'] == 'blocked', outcome
    assert calls == []
    assert i['exchange'].writes == []
    assert not policy.halted(broker.conn)  # this cycle only; the next tick re-observes


def test_owned_emergency_exit_fallback_precedes_delayed_historical_observation(flat_incident, monkeypatch):
    i = flat_incident
    _, broker = open_new(i)
    fill_new(i, broker)
    i['exchange'].stop = '0'
    i['exchange'].orders['native']['triggerPrice'] = '0'
    broker._incident_observation = None
    def unavailable_protection(*args):
        raise ValueError('Native update unavailable in controlled fixture.')
    monkeypatch.setattr(broker, '_ensure_protection', unavailable_protection)
    query = broker._query_child
    def delayed_history(child):
        if child['scenario_id'] == 's1':
            assert any(c['kind'] == 'exit' and c['local_id'].startswith('hard-stop-race:') for c in broker.children())
            i['now'][0] += 80
        return query(child)
    monkeypatch.setattr(broker, '_query_child', delayed_history)
    broker.reconcile()
    emergency = [c for c in broker.children() if c['kind'] == 'exit']
    assert len(emergency) == 1 and emergency[0]['request']['reduceOnly'] is True
    assert broker._incident_observation is None and not policy.halted(broker.conn)


def test_late_original_timestamp_fee_correction_is_watched_after_day_seven(flat_incident):
    i = flat_incident
    transition(i)
    broker = restarted(i)
    for _ in range(8):
        policy.monitor(broker, refresh=True)
        assert not policy.halted(broker.conn)
        i['now'][0] += 86400
    i['exchange'].fills[0]['execFee'] = '.02'
    policy.monitor(broker, refresh=True)
    assert policy.halted(broker.conn)
    assert any('archived_financial_sources' in e.get('evidence', {}) for e in policy._events(broker.conn, policy.HALT))
    assert not broker.conn.execute('SELECT 1 FROM llm_scenario_settlements').fetchone()


@pytest.mark.parametrize('gap_days', [6.5, 7.0])
def test_rolling_overlap_never_pushes_covered_gap_beyond_api_window(flat_incident, gap_days):
    i = flat_incident
    transition(i)
    broker = restarted(i)
    for _ in range(3):
        policy.monitor(broker, refresh=True)
        assert not policy.halted(broker.conn)
        i['now'][0] += 86400
    previous = policy._events(broker.conn, policy.FINANCE_CURSOR)[-1]['body']['end_ms']
    i['now'][0] = previous/1000 + gap_days*86400
    policy.monitor(broker, refresh=True)
    assert not policy.halted(broker.conn)
    latest = policy._events(broker.conn, policy.FINANCE_CURSOR)[-1]['body']
    assert latest['end_ms']-latest['start_ms'] <= 7*86400*1000
    assert latest['start_ms'] <= previous


@pytest.fixture
def inactive_history(tmp_path):
    from live.scenario_control import _write
    from tests.test_scenario_broker import Session
    conn = sqlite3.connect(tmp_path / 'inactive-history.db')
    broker = ScenarioDemoBroker(conn, session=Session(), expected_main_uid='123', execution_enabled=True)
    _write(conn, dict(version=1, state='active', main_uid='123'))
    conn.executemany('INSERT INTO llm_scenario_broker_evidence VALUES(?,?,?)',
        [('account', 1., json.dumps(dict(padding='x'*1024)))]*4000)
    conn.executemany('INSERT INTO llm_scenario_children VALUES(?,?,?,?,?,?,?,?,?,?)',
        [(str(n), str(n), 'old-'+str(n), 'entry', 'entry', '{}', 'TERMINAL', str(n), None, 1.)
         for n in range(200)])
    conn.commit()
    yield broker
    conn.close()


@pytest.mark.parametrize('indexed', [False, True])
def test_inactive_children_read_receipt_once_not_per_historical_child(inactive_history, indexed):
    broker = inactive_history
    conn = broker.conn
    if not indexed:
        conn.execute('DROP INDEX llm_scenario_broker_evidence_kind')
    statements = []
    steps = []
    conn.set_trace_callback(statements.append)
    conn.set_progress_handler(lambda: steps.append(1) or 0, 1000)
    try:
        children = broker.children()
    finally:
        conn.set_progress_handler(None, 0)
        conn.set_trace_callback(None)
    assert len(children) == 200
    assert {c['scenario_id'] for c in children} == {'old-'+str(n) for n in range(200)}
    lookups = [sql for sql in statements if sql.startswith('SELECT kind,body FROM llm_scenario_broker_evidence WHERE kind IN')]
    assert len(lookups) == 1
    # 200 child rows plus at most one 4,000-row evidence scan, never 200 scans.
    assert len(steps)*1000 < 40000
    if indexed:
        assert len(steps)*1000 < 10000
        plan = conn.execute('EXPLAIN QUERY PLAN SELECT body FROM llm_scenario_broker_evidence WHERE kind=? ORDER BY rowid', (policy.KIND,)).fetchall()
        assert any('SEARCH' in row[3] and 'llm_scenario_broker_evidence_kind' in row[3] for row in plan)
        assert not any('SCAN llm_scenario_broker_evidence' in row[3] for row in plan)


def test_evidence_index_is_initialized_under_lock_once_before_epoch_lookup(tmp_path):
    from live import shared_entry_coordinator
    from tests.test_scenario_broker import Session
    path = tmp_path / 'index-init.db'
    conn = sqlite3.connect(path)
    conn.execute('CREATE TABLE llm_scenario_broker_evidence(kind TEXT,captured_at REAL,body TEXT)')
    conn.execute("INSERT INTO llm_scenario_broker_evidence VALUES('account',1,'{}')")
    conn.commit()
    trace = []
    lock_checks = []
    def observe(sql):
        if sql.startswith('CREATE INDEX'):
            key = (os.getpid(), str(path.resolve())+'.btc-execution.lock')
            lock_checks.append(key in shared_entry_coordinator._held.keys)
        trace.append(sql)
    conn.set_trace_callback(observe)
    ScenarioDemoBroker(conn, session=Session(), expected_main_uid='123')
    conn.set_trace_callback(None)
    assert lock_checks == [True]
    index_at = next(n for n, sql in enumerate(trace) if sql.startswith('CREATE INDEX'))
    epoch_at = next(n for n, sql in enumerate(trace) if sql.startswith('SELECT kind,body FROM llm_scenario_broker_evidence WHERE kind IN'))
    assert index_at < epoch_at
    schema_version = conn.execute('PRAGMA schema_version').fetchone()[0]
    index_root = conn.execute("SELECT rootpage FROM sqlite_master WHERE name='llm_scenario_broker_evidence_kind'").fetchone()[0]
    ScenarioDemoBroker(conn, session=Session(), expected_main_uid='123')
    assert conn.execute('PRAGMA schema_version').fetchone()[0] == schema_version
    assert conn.execute("SELECT rootpage FROM sqlite_master WHERE name='llm_scenario_broker_evidence_kind'").fetchone()[0] == index_root
    assert conn.execute('SELECT * FROM llm_scenario_broker_evidence').fetchall() == [('account', 1., '{}')]
    conn.close()


def test_readonly_children_do_not_attempt_missing_index_migration(inactive_history):
    broker = inactive_history
    broker.conn.execute('DROP INDEX llm_scenario_broker_evidence_kind')
    broker.conn.commit()
    path = broker.conn.execute('PRAGMA database_list').fetchone()[2]
    from types import SimpleNamespace
    from live.scenario_execution import ScenarioExecution
    with sqlite3.connect('file:'+path+'?mode=ro', uri=True) as reader:
        statements = []
        reader.set_trace_callback(statements.append)
        assert len(ScenarioExecution.children(SimpleNamespace(conn=reader))) == 200
        assert reader.total_changes == 0
        assert not any(sql.startswith(('CREATE', 'UPDATE', 'INSERT', 'DELETE')) for sql in statements)
    assert not broker.conn.execute("SELECT 1 FROM sqlite_master WHERE name='llm_scenario_broker_evidence_kind'").fetchone()


def test_children_do_not_cache_absent_receipt_across_cutover(flat_incident):
    i = flat_incident
    before = i['broker'].children()
    assert before
    transition(i)
    assert i['broker'].children() == []
    assert i['broker'].children('s1') == before


def test_final_epoch_expiry_does_not_create_unsubmitted_unknown(flat_incident, monkeypatch):
    i = flat_incident
    _, broker = open_new(i)
    identity = broker._identity
    def delayed_identity():
        identity()
        i['now'][0] += 11
    monkeypatch.setattr(broker, '_identity', delayed_identity)
    before_children = copy.deepcopy(broker.children())
    before_writes = copy.deepcopy(i['exchange'].writes)
    with pytest.raises(ValueError):
        broker._submit(dict(scenario_id='new-scenario', action='OPEN'),
            'never-sent', 'entry', 'late-entry', dict(side='Buy', qty='.1',
                orderType='Limit', price='100', reduceOnly=False))
    assert broker.children() == before_children
    assert i['exchange'].writes == before_writes


@pytest.mark.parametrize('archive_failure', [False, True])
@pytest.mark.parametrize('batch_status', [None, 'COMPLETE'])
def test_entry_database_commit_delay_cannot_bypass_final_epoch_guard(flat_incident, archive_failure, batch_status):
    i = flat_incident
    _, broker = open_new(i)
    conn = broker.conn
    class DelayedCommit:
        def __getattr__(self, name):
            return getattr(conn, name)
        def commit(self):
            conn.commit()
            if conn.execute("SELECT 1 FROM llm_scenario_children WHERE intent_id='commit-delayed'").fetchone():
                i['now'][0] += 11
    broker.conn = DelayedCommit()
    if batch_status:
        conn.execute("INSERT INTO llm_scenario_execution_batches VALUES('commit-delayed',?)", (batch_status,))
        conn.commit()
    if archive_failure:
        conn.execute("CREATE TRIGGER block_abort_archive BEFORE INSERT ON llm_scenario_broker_evidence "
            "WHEN NEW.kind='entry_pretransport_abort' BEGIN SELECT RAISE(ABORT,'archive unavailable'); END")
        conn.commit()
    before = copy.deepcopy(i['exchange'].writes)
    with pytest.raises(ValueError):
        broker._submit(dict(scenario_id='new-scenario', action='OPEN'),
            'commit-delayed', 'entry', 'delayed', dict(side='Buy', qty='.1',
                orderType='Limit', price='100', reduceOnly=False))
    assert i['exchange'].writes == before
    if archive_failure:
        assert conn.execute("SELECT status FROM llm_scenario_children WHERE intent_id='commit-delayed'").fetchone()[0] == 'UNKNOWN'
        assert not conn.execute("SELECT 1 FROM llm_scenario_broker_evidence WHERE kind='entry_pretransport_abort'").fetchone()
        return
    assert not conn.execute("SELECT 1 FROM llm_scenario_children WHERE intent_id='commit-delayed'").fetchone()
    assert conn.execute("SELECT status FROM llm_scenario_execution_batches WHERE intent_id='commit-delayed'").fetchone()[0] == 'ABORTED'
    proof = json.loads(conn.execute("SELECT body FROM llm_scenario_broker_evidence WHERE kind='entry_pretransport_abort'").fetchone()[0])
    assert proof['child'][1] == 'commit-delayed'
    assert proof['child'][6] == 'UNKNOWN' and proof['transport_attempted'] is False
    with pytest.raises(ValueError):
        broker._submit(dict(scenario_id='new-scenario', action='OPEN'),
            'commit-delayed', 'entry', 'delayed', dict(side='Buy', qty='.1',
                orderType='Limit', price='100', reduceOnly=False))
    assert i['exchange'].writes == before


def test_monitor_gap_release_preserves_history_and_later_halts(flat_incident):
    i=flat_incident;transition(i);broker=restarted(i)
    policy.record_late(broker, dict(status='HISTORICAL_MONITOR_OR_COVERAGE_GAP'))
    before=copy.deepcopy(policy._events(broker.conn, policy.HALT))
    history=policy._snapshot(broker, 's1')
    policy.release_monitor_gap(broker, expected_halts_sha256=digest(before))
    assert not policy.halted(broker.conn) and not policy.new_risk_blocked(broker)
    from live.healthcheck import _check_execution_stalled
    from datetime import datetime,timezone
    notice=_check_execution_stalled(broker.conn,'demo',datetime.fromtimestamp(i['now'][0],timezone.utc))
    assert notice['code']=='historical_unsettled_excluded' and notice['level']=='info'
    assert policy._events(broker.conn, policy.HALT)==before
    assert policy._snapshot(broker, 's1')==history and i['exchange'].writes==[]
    policy.record_late(broker, dict(status='HISTORICAL_MONITOR_OR_COVERAGE_GAP'))
    assert policy.halted(broker.conn)


@pytest.mark.parametrize('fault', ['late_fill','fresh_late','bad_hash','expired','tampered_history','foreign_order','monitor_failure'])
def test_monitor_gap_release_fails_closed(flat_incident,monkeypatch,fault):
    i=flat_incident;transition(i);broker=restarted(i)
    policy.record_late(broker, dict(status='HISTORICAL_MONITOR_OR_COVERAGE_GAP'))
    expected=digest(policy._events(broker.conn,policy.HALT))
    if fault=='bad_hash':expected='bad'
    if fault=='late_fill':policy.record_late(broker, dict(financial_sources={'executions':[{'execId':'late'}]}))
    if fault=='fresh_late':
        i['now'][0]+=11
        i['exchange'].fills.append(dict(execId='release-late',orderId='opaque-original-id',orderLinkId=policy.UNKNOWN,symbol='BTCUSDT',side='Sell',execType='Trade',execQty='.001',execPrice='100',execFee='.01',execTime=str(int(i['now'][0]*1000))))
    if fault=='foreign_order':
        original=policy.account_sources
        def foreign(*a):
            rows=original(*a);next(r for r in rows if 'orders' in r)['orders']['result']['list'].append({'orderId':'foreign'});return rows
        monkeypatch.setattr(policy,'account_sources',foreign)
    if fault=='monitor_failure':
        monkeypatch.setattr(broker,'_query_child',lambda child: (_ for _ in ()).throw(TimeoutError('network unavailable')))
    if fault=='tampered_history':
        broker.conn.execute("UPDATE llm_scenario_children SET status='TERMINAL' WHERE scenario_id='s1'");broker.conn.commit()
    if fault=='expired':
        original=policy.monitor
        def delayed(*a,**kw):
            result=original(*a,**kw);i['now'][0]+=11;return result
        monkeypatch.setattr(policy,'monitor',delayed)
    with pytest.raises(ValueError):policy.release_monitor_gap(broker,expected_halts_sha256=expected)
    assert policy.halted(broker.conn) and i['exchange'].writes==[]


@pytest.mark.parametrize('extra,owned', [
    (dict(orderId='unsaved-sl', orderLinkId='', parentOrderLinkId='OWN', reduceOnly=True, stopOrderType='StopLoss'), True),
    (dict(orderId='unsaved-order', orderLinkId='OWN'), True),
    (dict(orderId='other-sl', orderLinkId='', parentOrderLinkId='someone-else', reduceOnly=True, stopOrderType='StopLoss'), False),
    (dict(orderId='opening', orderLinkId='', parentOrderLinkId='OWN', reduceOnly=False, stopOrderType='StopLoss'), False),
    (dict(orderId='blank', orderLinkId='', parentOrderLinkId=''), False),
])
def test_monitor_accepts_only_own_unsaved_orders(flat_incident, monkeypatch, extra, owned):
    # 2026-10-30 01:01: the entry's attached stop was live 3 minutes before its row was saved.
    i=flat_incident;transition(i);broker=restarted(i)
    link='sc_live_entry'
    broker.conn.execute('INSERT INTO llm_scenario_children VALUES(?,?,?,?,?,?,?,?,?,?)',
        (link,'live-intent','live-scenario','entry','e',json.dumps(dict(side='Buy',qty='.001')),'UNKNOWN',None,None,i['now'][0]))
    broker.conn.commit()
    order={k:(link if v=='OWN' else v) for k,v in extra.items()}
    original=policy.account_sources
    def with_order(*a):
        rows=original(*a);next(r for r in rows if 'orders' in r)['orders']['result']['list'].append(order);return rows
    monkeypatch.setattr(policy,'account_sources',with_order)
    assert (policy.monitor(broker, refresh=True) is not None) is owned
    assert policy.halted(broker.conn) is (not owned)
    if not owned:
        assert policy._events(broker.conn, policy.HALT)[-1]['evidence']['reason'] == 'incident_epoch_foreign_order'


def test_corrupt_monitor_release_cannot_hide_halt(flat_incident):
    i=flat_incident;transition(i);broker=restarted(i)
    policy.record_late(broker,dict(status='HISTORICAL_MONITOR_OR_COVERAGE_GAP'))
    policy.release_monitor_gap(broker,expected_halts_sha256=digest(policy._events(broker.conn,policy.HALT)))
    broker.conn.execute("UPDATE llm_scenario_broker_evidence SET body='{}' WHERE kind=?",(policy.MONITOR_RELEASE,));broker.conn.commit()
    with pytest.raises(ValueError):policy.halted(broker.conn)


@pytest.mark.parametrize('stage', ['child_lookup', 'finance', 'account'])
def test_monitor_deadline_blocks_only_this_cycle(flat_incident, monkeypatch, caplog, stage):
    i = flat_incident
    transition(i)
    broker = restarted(i)
    caplog.set_level(logging.WARNING, logger=policy.__name__)
    if stage == 'child_lookup':
        original = broker._query_child
        def delayed(*args, **kwargs):
            result = original(*args, **kwargs)
            i['now'][0] += 11
            return result
        monkeypatch.setattr(broker, '_query_child', delayed)
    else:
        name = 'historical_finance' if stage == 'finance' else 'account_sources'
        original = getattr(policy, name)
        def delayed(*args, **kwargs):
            result = original(*args, **kwargs)
            i['now'][0] += 11
            return result
        monkeypatch.setattr(policy, name, delayed)
    halts = copy.deepcopy(policy._events(broker.conn, policy.HALT))
    assert policy.monitor(broker, refresh=True) is None
    assert not policy.halted(broker.conn) and policy._events(broker.conn, policy.HALT) == halts
    assert policy.new_risk_blocked(broker)  # no fresh observation in this cycle
    messages = [r.getMessage() for r in caplog.records if r.name == policy.__name__
                and r.getMessage().startswith('incident_monitor_call_timing ')]
    assert len(messages) == 1
    assert f"started_ms={int((i['now'][0] - 11) * 1000)}" in messages[0]
    timing = json.loads(messages[0].split(' calls=', 1)[1])
    assert set(timing) == {'calls', 'dropped', 'measurement_failures'}
    assert timing['dropped'] == timing['measurement_failures'] == 0
    assert timing['calls'] and len(timing['calls']) <= 64
    for call in timing['calls']:
        assert set(call) == {'method', 'elapsed_ms', 'ok'}
        assert call['method'].startswith('get_') or call['method'] == 'other'
        assert isinstance(call['elapsed_ms'], (int, float)) and call['elapsed_ms'] >= 0
        assert type(call['ok']) is bool
    from live.scenario_broker import _MONITOR_CALL_TIMING
    assert _MONITOR_CALL_TIMING.get() is None
    assert i['exchange'].writes == []
    if stage == 'child_lookup':
        monkeypatch.setattr(broker, '_query_child', original)
    else:
        monkeypatch.setattr(policy, name, original)
    assert policy.monitor(broker, refresh=True) is not None
    assert not policy.new_risk_blocked(broker)
    assert policy._events(broker.conn, policy.HALT) == halts
    assert i['exchange'].writes == []


def test_monitor_timing_logging_failure_still_fails_closed(flat_incident, monkeypatch):
    i = flat_incident
    transition(i)
    broker = restarted(i)
    original = policy.account_sources

    def delayed(*args, **kwargs):
        result = original(*args, **kwargs)
        i['now'][0] += 11
        return result

    monkeypatch.setattr(policy, 'account_sources', delayed)
    monkeypatch.setattr(policy.logging.getLogger(policy.__name__), 'warning',
                        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError('logger failed')))
    assert policy.monitor(broker, refresh=True) is None
    assert policy.new_risk_blocked(broker) and not policy.halted(broker.conn)
    assert i['exchange'].writes == []
    from live.scenario_broker import _MONITOR_CALL_TIMING
    assert _MONITOR_CALL_TIMING.get() is None


def test_successful_monitor_restores_outer_trace_without_warning(flat_incident, caplog):
    i = flat_incident
    transition(i)
    broker = restarted(i)
    from live.scenario_broker import _MONITOR_CALL_TIMING
    outer = {'calls': [], 'dropped': 0}
    token = _MONITOR_CALL_TIMING.set(outer)
    caplog.clear()
    try:
        assert policy.monitor(broker, refresh=True) is not None
        assert _MONITOR_CALL_TIMING.get() is outer
        assert outer == {'calls': [], 'dropped': 0}
        assert not any('incident_monitor_call_timing' in r.getMessage() for r in caplog.records)
        assert i['exchange'].writes == []
    finally:
        _MONITOR_CALL_TIMING.reset(token)
