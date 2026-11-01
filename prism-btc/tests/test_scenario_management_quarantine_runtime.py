"""Real runtime/broker quarantine integration; all exchange calls are in memory."""
import copy
import json
import sqlite3

import pytest

from live.scenario_broker import BrokerNotReady, ScenarioDemoBroker
from live.scenario_control import _write
from live.scenario_runtime import ScenarioRuntime
from tests.test_scenario_execution import Exchange, persist, record_exit_decision
from tests.test_scenario_entry_contract_runtime import definition
from tests.test_scenario_immediate_reduce_execution import auto_fill
from tests.test_scenario_runtime import proposal, wait_proposal


class ShortExchange(Exchange):
    """Mirror the shared fake's LONG-only fill bookkeeping for SHORT exposure."""
    def __init__(self):
        super().__init__()
        self.stop = '102'

    def get_positions(self, **kwargs):
        result = super().get_positions(**kwargs)
        for position in result['result']['list']:
            position['side'] = 'Sell' if float(position['size']) else ''
        return result

    def fill(self, link, amount):
        original_side = self.orders[link]['side']
        self.orders[link]['side'] = 'Buy' if original_side == 'Sell' else 'Sell'
        try:
            super().fill(link, amount)
        finally:
            self.orders[link]['side'] = original_side
        self.fills[-1]['side'] = original_side
        if 'native' in self.orders:
            self.orders['native']['side'] = 'Buy'


@pytest.fixture
def incident(tmp_path, request):
    from live import scenario_management_quarantine as quarantine
    conn = sqlite3.connect(tmp_path / 'quarantine.db')
    now = [1800000000.]
    side = getattr(request, 'param', 'LONG')
    exchange = ShortExchange() if side == 'SHORT' else Exchange()
    stop, partial_stop, failure_price = (102, 101, 101.5) if side == 'SHORT' else (98, 99, 98.5)
    broker = ScenarioDemoBroker(conn, session=exchange, expected_main_uid='123',
                                clock=lambda: now[0], execution_enabled=True)
    _write(conn, dict(version=1, state='active', main_uid='123'))
    calls = []

    def model(snapshot, context):
        calls.append(copy.deepcopy(context))
        if not context['scenario_id']:
            contract = definition(side)
            contract['explicit_failure']['price'] = failure_price
            return dict(proposal(snapshot, context), scenario_id='s1', action_id='a1',
                side=side, hard_stop=stop, entries=[dict(id='e1', price=100, quantity=.1)],
                take_profits=[], partial_stops=[dict(id='ps', price=partial_stop, fraction=.5)],
                entry_contract=contract, thesis_assessment=None)
        return dict(wait_proposal(snapshot, context, action_id='wait-' + str(int(now[0]))),
            entry_contract=None, thesis_assessment=dict(
                contract_id=context['registered_entry_contract']['contract_id'],
                status='INTACT', reason='Original premise remains intact.'))

    runtime = ScenarioRuntime(conn, broker, model,
        lambda: dict(valid=True, as_of_ms=now[0]*1000), clock=lambda: now[0],
        entry_contract_enabled=True)
    opening = runtime.tick()
    assert opening['status'] == 'intent_pending', opening
    exchange.fill(next(c['link_id'] for c in broker.children() if c['kind'] == 'entry'), .1)
    broker.reconcile()
    adjustment = persist(broker, action='ADJUST', action_id='adjust', revision=2,
        side=side, hard_stop=stop,
        entries=[], partial_stops=[dict(id='ps-adjusted', price=partial_stop, fraction=.5)])
    record_exit_decision(broker, adjustment)
    broker.execute(adjustment, 'adjust')
    reduced = persist(broker, action='REDUCE', action_id='r1', revision=3,
        side=side, hard_stop=stop, entries=[], reduce_fraction=.5, reduce_plan=dict(quantity_before=.1,
            requested_quantity=.05, target_remainder=.05))
    broker.prepare_reduce(reduced, 'r1')
    conn.commit()
    auto_fill(exchange)
    normal_place = exchange.place_order

    def lose_restore_response(**request):
        if request.get('triggerPrice'):
            exchange.writes.append(('place', request))
            raise TimeoutError('Response lost; acceptance genuinely unknown.')
        return normal_place(**request)

    exchange.place_order = lose_restore_response
    with pytest.raises(BrokerNotReady, match='submission_unknown'):
        broker.execute(reduced, 'r1')
    exchange.place_order = normal_place
    unknown = next(c for c in broker.children() if c['status'] == 'UNKNOWN')
    pending = [dict(id=i, action=(p := json.loads(raw))['action'], revision=p.get('revision'),
                    payload_sha256=quarantine.digest(p))
               for i, raw in conn.execute("SELECT id,payload FROM llm_scenario_intents WHERE status='PENDING'")]
    assert len(pending) == 3
    original = dict(children=copy.deepcopy(unknown),
        pending=list(conn.execute('SELECT * FROM llm_scenario_intents ORDER BY id')),
        contract=copy.deepcopy(runtime.state()['active']['entry_contract']))
    now[0] += 300
    exchange.now = now[0]
    calls.clear()
    exchange.writes.clear()

    def authorize():
        return quarantine.register(broker, approval_id='approved-incident',
            reason='Explicit user authorization for bounded existing-position management.',
            expires_at=now[0]+3600, scenario_id='s1', contract_id=original['contract']['contract_id'],
            unknown_link_id=unknown['link_id'], unknown_request_sha256=quarantine.digest(unknown['request']),
            pending_intents=pending, maximum_quantity='.05')

    yield dict(runtime=runtime, broker=broker, exchange=exchange, now=now,
               calls=calls, original=original, authorize=authorize, quarantine=quarantine,
               model=model)
    conn.close()


def test_without_authorization_keeps_original_pending_gate(incident):
    assert incident['runtime'].tick()['status'] == 'intent_pending'
    assert incident['calls'] == []


def test_authorized_wait_uses_fresh_input_without_changing_exchange_facts(incident):
    receipt = incident['authorize']()
    runtime = incident['runtime']
    assert runtime.tick()['status'] == 'wait'
    assert incident['calls'][0]['management_quarantine'] == receipt
    assert incident['calls'][0]['input_captured_at'] == incident['now'][0]
    assert incident['exchange'].writes == []
    assert list(runtime.conn.execute('SELECT * FROM llm_scenario_intents ORDER BY id')) == incident['original']['pending']


def test_authorized_wait_preserves_unknown_accounting_and_unsettled_contract(incident):
    incident['authorize']()
    assert incident['runtime'].tick()['status'] == 'wait'
    assert incident['broker'].context()['accounting_status'] == 'pending'
    target = next(c for c in incident['broker'].children() if c['status'] == 'UNKNOWN')
    assert target == incident['original']['children']
    assert not incident['runtime'].conn.execute('SELECT 1 FROM llm_scenario_settlements').fetchone()
    assert incident['runtime'].state()['active']['entry_contract'] == incident['original']['contract']


def test_revocation_during_model_prevents_wait_commit(incident):
    incident['authorize']()
    runtime = incident['runtime']
    def revoke_then_propose(snapshot, context):
        result = incident['model'](snapshot, context)
        incident['quarantine'].revoke(incident['broker'], approval_id='approved-incident', reason='User revoked.')
        return result
    runtime.propose = revoke_then_propose
    assert runtime.tick()['status'] in {'blocked', 'stale_proposal', 'intent_pending'}
    assert len(incident['calls']) == 1
    assert incident['exchange'].writes == []
    assert 'last_thesis_assessment' not in runtime.state()['active']


def test_fourth_pending_intent_revokes_management_exception(incident):
    incident['authorize']()
    persist(incident['broker'], action='EXIT', action_id='unrelated-exit', entries=[])
    assert incident['runtime'].tick()['status'] in {'blocked', 'intent_pending'}
    assert incident['calls'] == []
    assert incident['exchange'].writes == []


@pytest.mark.parametrize('action', ['OPEN', 'ADJUST', 'REDUCE'])
def test_authorized_context_rejects_risk_changing_action(incident, action):
    incident['authorize']()
    runtime = incident['runtime']
    runtime.propose = lambda s, c: dict(incident['model'](s, c), action=action)
    assert runtime.tick()['status'] == 'blocked'
    assert len(incident['calls']) == 1
    assert incident['exchange'].writes == []


def test_authorized_context_rejects_model_requested_cancellation(incident):
    incident['authorize']()
    runtime = incident['runtime']
    runtime.propose = lambda s, c: dict(incident['model'](s, c), cancel_entry_ids=['arbitrary-order'])
    assert runtime.tick()['status'] == 'blocked'
    assert incident['exchange'].writes == []


def test_explicit_exit_is_reduce_only_and_never_cancels_unknown_or_native_stop(incident):
    incident['authorize']()
    runtime = incident['runtime']
    runtime.propose = lambda s, c: dict(incident['model'](s, c), action='EXIT')
    assert runtime.tick()['status'] == 'intent_pending'
    posts = [r for kind, r in incident['exchange'].writes if kind == 'place']
    assert len(posts) == 1
    assert posts[0]['reduceOnly'] is True and float(posts[0]['qty']) <= .05
    assert not [r for kind, r in incident['exchange'].writes if kind == 'cancel']


def test_exit_receipt_and_intent_are_durable_before_exchange_post(incident):
    incident['authorize']()
    runtime, exchange = incident['runtime'], incident['exchange']
    runtime.propose = lambda s, c: dict(incident['model'](s, c), action='EXIT')
    original_place = exchange.place_order
    inspected = []
    def check_commit(**request):
        path = runtime.conn.execute('PRAGMA database_list').fetchone()[2]
        with sqlite3.connect(path) as reader:
            rows = reader.execute('SELECT body FROM llm_scenario_broker_evidence WHERE kind=?',
                                  (incident['quarantine'].EXIT_KIND,)).fetchall()
            assert len(rows) == 1
            receipt = json.loads(rows[0][0])
            payload, status = reader.execute('SELECT payload,status FROM llm_scenario_intents WHERE id=?',
                                            (receipt['intent_id'],)).fetchone()
            assert status == 'PENDING'
            assert incident['quarantine'].digest(json.loads(payload)) == receipt['payload_sha256']
            inspected.append(receipt['intent_id'])
        return original_place(**request)
    exchange.place_order = check_commit
    runtime.tick()
    assert len(inspected) == 1


@pytest.mark.parametrize('accepted_before_timeout', [False, True])
def test_exit_ack_loss_restart_never_posts_again_or_runs_another_model(incident, accepted_before_timeout):
    incident['authorize']()
    runtime, exchange = incident['runtime'], incident['exchange']
    runtime.propose = lambda s, c: dict(incident['model'](s, c), action='EXIT')
    original_place = exchange.place_order
    attempts = []
    def timeout(**request):
        attempts.append(copy.deepcopy(request))
        if accepted_before_timeout:
            original_place(**request)
        raise TimeoutError('Uncertain EXIT response.')
    exchange.place_order = timeout
    assert runtime.tick()['status'] == 'intent_pending'
    assert len(attempts) == 1
    incident['now'][0] += 300
    exchange.now = incident['now'][0]
    restarted_broker = ScenarioDemoBroker(runtime.conn, session=exchange,
        expected_main_uid='123', clock=lambda: incident['now'][0], execution_enabled=True)
    model_calls = []
    restarted = ScenarioRuntime(runtime.conn, restarted_broker,
        lambda s, c: model_calls.append(c), lambda: dict(valid=True, as_of_ms=incident['now'][0]*1000),
        clock=lambda: incident['now'][0], entry_contract_enabled=True)
    restarted.tick()
    assert attempts and len(attempts) == 1
    assert model_calls == []
    assert next(c for c in restarted_broker.children()
                if c['link_id'] == incident['original']['children']['link_id']) == incident['original']['children']


def test_confirmed_original_contract_failure_exits_without_second_model_call(incident):
    from tests.test_scenario_broker import reply
    incident['authorize']()
    runtime, exchange = incident['runtime'], incident['exchange']
    exchange.get_tickers = lambda **kw: reply([dict(symbol='BTCUSDT', markPrice='98.4',
        lastPrice='98.4', nextFundingTime='1800003600000')])
    assert runtime.tick()['status'] == 'wait'
    assert len(incident['calls']) == 1
    incident['now'][0] += 300
    exchange.now = incident['now'][0]
    result = runtime.tick()
    assert result['status'] == 'intent_pending' and result['model_called'] is False
    assert len(incident['calls']) == 1
    posts = [r for kind, r in exchange.writes if kind == 'place']
    assert len(posts) == 1 and posts[0]['reduceOnly'] is True
    assert runtime.conn.execute('SELECT 1 FROM llm_scenario_entry_failures').fetchone()


def test_another_unknown_child_removes_exception_before_model(incident):
    incident['authorize']()
    broker = incident['broker']
    child = incident['original']['children']
    row = broker.conn.execute('SELECT * FROM llm_scenario_children WHERE link_id=?',
                              (child['link_id'],)).fetchone()
    columns = [r[1] for r in broker.conn.execute('PRAGMA table_info(llm_scenario_children)')]
    clone = dict(zip(columns, row))
    clone['link_id'] = 'another-unknown'
    clone['local_id'] = 'another-restore'
    broker.conn.execute('INSERT INTO llm_scenario_children (' + ','.join(columns) + ') VALUES (' +
                        ','.join('?' for _ in columns) + ')', tuple(clone[c] for c in columns))
    broker.conn.commit()
    assert incident['runtime'].tick()['status'] in {'blocked', 'intent_pending'}
    assert incident['calls'] == []


def test_model_schema_allows_only_wait_and_exit_during_quarantine(incident):
    from live.scenario_contract import response_schema
    incident['authorize']()
    context = incident['runtime']._context(incident['runtime'].state())
    context['input_id'] = 'schema-only'
    contract = response_schema(context)
    assert contract['properties']['action']['enum'] == ['WAIT', 'EXIT']


@pytest.mark.parametrize('action', ['WAIT', 'EXIT'])
def test_revocation_after_fresh_validation_prevents_commit(incident, monkeypatch, action):
    import live.scenario_runtime as runtime_module
    incident['authorize']()
    runtime = incident['runtime']
    runtime.propose = lambda s, c: dict(incident['model'](s, c), action=action)
    original_validate = runtime_module.validate_execution_prices
    def revoke_after_validation(payload, context):
        value = original_validate(payload, context)
        incident['quarantine'].revoke(incident['broker'], approval_id='approved-incident', reason='Revoked before commit.')
        return value
    monkeypatch.setattr(runtime_module, 'validate_execution_prices', revoke_after_validation)
    assert runtime.tick()['status'] == 'blocked'
    assert incident['exchange'].writes == []
    assert list(runtime.conn.execute('SELECT * FROM llm_scenario_intents ORDER BY id')) == incident['original']['pending']


def test_exit_receipt_storage_failure_rolls_back_unsubmitted_intent(incident):
    incident['authorize']()
    runtime = incident['runtime']
    runtime.conn.execute("""CREATE TRIGGER reject_exit_receipt BEFORE INSERT ON llm_scenario_broker_evidence
        WHEN NEW.kind='management_quarantine_exit_v1'
        BEGIN SELECT RAISE(ABORT, 'Injected receipt storage failure'); END""")
    runtime.conn.commit()
    runtime.propose = lambda s, c: dict(incident['model'](s, c), action='EXIT')
    assert runtime.tick()['status'] == 'blocked'
    assert incident['exchange'].writes == []
    assert list(runtime.conn.execute('SELECT * FROM llm_scenario_intents ORDER BY id')) == incident['original']['pending']
    assert not runtime.conn.execute('SELECT 1 FROM llm_scenario_broker_evidence WHERE kind=?',
                                   (incident['quarantine'].EXIT_KIND,)).fetchone()


def test_predecessor_payload_change_revokes_exception_before_model(incident):
    incident['authorize']()
    conn = incident['runtime'].conn
    raw = json.loads(conn.execute("SELECT payload FROM llm_scenario_intents WHERE id='adjust'").fetchone()[0])
    raw['hard_stop'] = 97
    conn.execute("UPDATE llm_scenario_intents SET payload=? WHERE id='adjust'", (json.dumps(raw),))
    conn.commit()
    assert incident['runtime'].tick()['status'] in {'blocked', 'intent_pending'}
    assert incident['calls'] == []
    assert incident['exchange'].writes == []


def test_expired_approval_does_not_reopen_model_lane(incident):
    incident['authorize']()
    incident['now'][0] += 3601
    incident['exchange'].now = incident['now'][0]
    assert incident['runtime'].tick()['status'] in {'blocked', 'intent_pending'}
    assert incident['calls'] == []
    assert incident['exchange'].writes == []


def invalidate_approval(incident, mode):
    if mode == 'expired':
        incident['now'][0] += 3601
        incident['exchange'].now = incident['now'][0]
    elif mode == 'revoked':
        incident['quarantine'].revoke(incident['broker'], approval_id='approved-incident', reason='Revoked.')
    else:
        conn = incident['runtime'].conn
        rowid, body = conn.execute('SELECT rowid,body FROM llm_scenario_broker_evidence WHERE kind=?',
                                  (incident['quarantine'].KIND,)).fetchone()
        record = json.loads(body)
        record['sha256'] = 'corrupt'
        conn.execute('UPDATE llm_scenario_broker_evidence SET body=? WHERE rowid=?', (json.dumps(record), rowid))
        conn.commit()


@pytest.mark.parametrize('mode', ['expired', 'revoked', 'corrupt'])
def test_invalid_authorization_never_interrupts_independent_native_protection(incident, mode):
    incident['authorize']()
    invalidate_approval(incident, mode)
    result = incident['broker'].reconcile()
    assert result['protection_confirmed'] is True
    assert incident['exchange'].orders['native']['orderStatus'] == 'Untriggered'
    assert incident['exchange'].writes == []


@pytest.mark.parametrize('mode', ['expired', 'revoked', 'corrupt'])
def test_invalid_authorization_still_queries_existing_unknown_exit_without_repost(incident, mode):
    incident['authorize']()
    runtime, exchange = incident['runtime'], incident['exchange']
    exchange.place_order = lambda **kw: Exchange.place_order(exchange, **kw)
    exchange.lose_ack = True
    runtime.propose = lambda s, c: dict(incident['model'](s, c), action='EXIT')
    assert runtime.tick()['status'] == 'intent_pending'
    exit_child = next(c for c in incident['broker'].children() if c['kind'] == 'exit')
    assert exit_child['status'] == 'UNKNOWN'
    invalidate_approval(incident, mode)
    observed_links = []
    original_query = exchange.get_open_orders
    def query(**request):
        observed_links.append(request.get('orderLinkId'))
        return original_query(**request)
    exchange.get_open_orders = query
    writes_before = copy.deepcopy(exchange.writes)
    result = incident['broker'].reconcile()
    assert result['protection_confirmed'] is True
    assert exit_child['link_id'] in observed_links
    assert exchange.writes == writes_before


def calculated_runtime(incident, action, *, after_review=None):
    from types import SimpleNamespace
    from live.scenario_review_adapter import make_calculated_review_proposer
    from tests.test_scenario_review_runtime import wire
    captured, calls = {}, []
    rationale = dict(interpretation='기존 진입 근거와 잔여 보유분을 검토합니다.', evidence_refs=[], comparisons=[])
    def plan(action):
        return dict(wire(captured, action), entry_contract=None,
            thesis_assessment=dict(contract_id=captured['registered_entry_contract']['contract_id'],
                                   status='INTACT', reason='Original premise remains intact.'),
            rationale=rationale)
    def generate(**request):
        calls.append(copy.deepcopy(request))
        if 'wait_proposal' in request['response_schema']['properties']:
            result = dict(wait_proposal=plan('WAIT'), candidates=[plan('EXIT')], no_candidate_reason='')
        else:
            data = json.loads(request['user_prompt'])['calculated_candidates']
            chosen = data['wait'] if action == 'WAIT' else data['candidates'][0]
            result = dict(selection_token=f"{chosen['id']}:{chosen['receipt_sha256']}", rationale=rationale)
        return SimpleNamespace(text=json.dumps(result))
    adapter = make_calculated_review_proposer(generate=generate, clock=lambda: incident['now'][0])
    def propose(snapshot, context, *, record):
        captured.update(copy.deepcopy(context))
        result = adapter(snapshot, context, record=record)
        if after_review:
            after_review()
        return result
    runtime = ScenarioRuntime(incident['runtime'].conn, incident['broker'],
        lambda *_: pytest.fail('Legacy proposer must not be called.'),
        lambda: dict(valid=True, as_of_ms=incident['now'][0]*1000),
        clock=lambda: incident['now'][0], entry_contract_enabled=True,
        calculated_review_enabled=True, calculated_review_propose=propose)
    return runtime, calls


@pytest.mark.parametrize('action', ['WAIT', 'EXIT'])
def test_two_phase_review_reaches_real_broker_under_quarantine(incident, action):
    incident['authorize']()
    runtime, calls = calculated_runtime(incident, action)
    outcome = runtime.tick()
    assert outcome['status'] == ('wait' if action == 'WAIT' else 'intent_pending'), outcome
    assert len(calls) == 2
    posts = [r for kind, r in incident['exchange'].writes if kind == 'place']
    assert len(posts) == (0 if action == 'WAIT' else 1)
    assert incident['broker'].context()['accounting_status'] == 'pending'
    assert not runtime.conn.execute('SELECT 1 FROM llm_scenario_settlements').fetchone()


@pytest.mark.parametrize('action', ['WAIT', 'EXIT'])
def test_two_phase_selection_revocation_blocks_selected_action(incident, action):
    incident['authorize']()
    def revoke():
        incident['quarantine'].revoke(incident['broker'], approval_id='approved-incident', reason='Revoked after SELECT.')
    runtime, calls = calculated_runtime(incident, action, after_review=revoke)
    assert runtime.tick()['status'] in {'blocked', 'stale_proposal'}
    assert len(calls) == 2
    assert incident['exchange'].writes == []
    assert list(runtime.conn.execute('SELECT * FROM llm_scenario_intents ORDER BY id')) == incident['original']['pending']


@pytest.mark.parametrize('action', ['WAIT', 'EXIT'])
def test_final_real_context_revocation_blocks_calculated_commit(incident, action):
    incident['authorize']()
    broker = incident['broker']
    context_calls = []
    def install_revocation_at_final_capture():
        original_context = broker.context
        def context():
            context_calls.append(True)
            if len(context_calls) == 2:
                incident['quarantine'].revoke(broker, approval_id='approved-incident', reason='Revoked before final capture.')
            return original_context()
        broker.context = context
    runtime, calls = calculated_runtime(incident, action, after_review=install_revocation_at_final_capture)
    assert runtime.tick()['status'] in {'blocked', 'stale_proposal'}
    assert len(calls) == 2 and len(context_calls) == 2
    assert incident['exchange'].writes == []
    assert list(runtime.conn.execute('SELECT * FROM llm_scenario_intents ORDER BY id')) == incident['original']['pending']


def test_calculated_exit_pending_blocks_subsequent_review_and_second_post(incident):
    incident['authorize']()
    runtime, calls = calculated_runtime(incident, 'EXIT')
    exchange = incident['exchange']
    exchange.place_order = lambda **kw: Exchange.place_order(exchange, **kw)
    exchange.lose_ack = True
    assert runtime.tick()['status'] == 'intent_pending'
    assert len(calls) == 2
    incident['now'][0] += 300
    exchange.now = incident['now'][0]
    assert runtime.tick()['status'] in {'blocked', 'intent_pending'}
    assert len(calls) == 2
    assert len([r for kind, r in exchange.writes if kind == 'place']) == 1


def test_flat_position_never_authorizes_new_entry_or_synthetic_settlement(incident):
    incident['authorize']()
    incident['exchange'].fill('native', .05)
    outcome = incident['runtime'].tick()
    assert outcome['status'] == 'blocked'
    assert outcome['reason'] == 'flat_quarantined_unsettled'
    assert incident['calls'] == []
    assert not incident['runtime'].conn.execute('SELECT 1 FROM llm_scenario_settlements').fetchone()
    assert any(c['status'] == 'UNKNOWN' for c in incident['broker'].children())


@pytest.mark.parametrize('incident,exit_side', [('LONG', 'Sell'), ('SHORT', 'Buy')], indirect=['incident'])
def test_calculated_exit_side_symmetry_preserves_native_stop_and_unknown(incident, exit_side):
    incident['authorize']()
    runtime, calls = calculated_runtime(incident, 'EXIT')
    exchange = incident['exchange']
    # Leave the EXIT live so independent native protection remains observable.
    exchange.place_order = lambda **kw: Exchange.place_order(exchange, **kw)
    assert runtime.tick()['status'] == 'intent_pending'
    assert len(calls) == 2
    posts = [request for kind, request in exchange.writes if kind == 'place']
    assert len(posts) == 1
    assert posts[0]['side'] == exit_side
    assert posts[0]['reduceOnly'] is True and posts[0]['closeOnTrigger'] is True
    assert float(posts[0]['qty']) == .05
    assert exchange.orders['native']['orderStatus'] == 'Untriggered'
    assert not [request for kind, request in exchange.writes if kind == 'cancel']
    assert next(c for c in incident['broker'].children()
                if c['link_id'] == incident['original']['children']['link_id']) == incident['original']['children']
