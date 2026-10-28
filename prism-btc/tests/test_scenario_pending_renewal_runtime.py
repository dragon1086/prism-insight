from live.scenario_review_storage import review_text
"""Full fake-exchange runtime path; captured requests never contact a model."""
from copy import deepcopy
import json
import sqlite3
from types import SimpleNamespace

import pytest

from live.scenario_broker import ScenarioDemoBroker
from live.scenario_control import _write
from live.scenario_pending_renewal import load, receipt
from live.scenario_review_adapter import make_calculated_review_proposer
from utils.btc_review_references import read_tape
from live.scenario_runtime import ScenarioRuntime
from live.scenario_model_input import restore_model_input
from tests.test_scenario_execution import Exchange
from tests.test_scenario_review_runtime import wire


def renewal_runtime(tmp_path, *, reapprove=True, assessment='INTACT', before_select=None,
                    select_renewal=True, side='LONG', trade_alternatives=0, initial_risk_fraction=None,
                    mixed_initial=False, goal_enabled=False, selection_reason='Explicit selected approval'):
    conn = sqlite3.connect(tmp_path / 'renewal.db')
    exchange = Exchange()
    original_place = exchange.place_order

    def place(**request):
        result = original_place(**request)
        if request.get('triggerPrice'):
            exchange.orders[request['orderLinkId']]['orderStatus'] = 'Untriggered'
        return result

    exchange.place_order = place
    broker = ScenarioDemoBroker(conn, session=exchange, expected_main_uid='123',
                               clock=lambda: exchange.now, execution_enabled=True)
    if initial_risk_fraction is not None:
        # Synthetic previously approved stage budget, not recovery migration proof.
        account_context = broker.context
        def stage_budget_context():
            context = account_context()
            if broker._active() is None:
                context['scenario_risk_fraction'] = initial_risk_fraction
            return context
        broker.context = stage_budget_context
    _write(conn, dict(version=1, state='active', main_uid='123'))
    calls, contexts = [], []

    def raw(ctx, action):
        result = wire(ctx, action)
        if action == 'OPEN':
            result.update(side=side, confidence=.1, hard_stop=98 if side == 'LONG' else 102,
                entries=[dict(id='conditional', price=102 if side == 'LONG' else 98,
                trigger_price=101 if side == 'LONG' else 99, allocation_fraction=1)],
                take_profits=[dict(id='tp', price=105 if side == 'LONG' else 95, fraction=1)], entry_contract=dict(
                    version=1, regime='BREAKOUT', premise='Original conditional breakout',
                    expected_progress='Progress after activation, not a new thesis',
                    explicit_failure=dict(source='MARK_PRICE', operator='le' if side == 'LONG' else 'ge',
                                          price=99 if side == 'LONG' else 101, minimum_observations=2)))
            if mixed_initial:
                result['entries'][0]['allocation_fraction'] = .5
                result['entries'].append(dict(id='ordinary', price=100, trigger_price=None, allocation_fraction=.5))
            if ctx.get('entry_contract_version') == 2:
                result['entry_contract'].pop('expected_progress')
                result['entry_contract'].update(version=2, goal=dict(mode='BOUNDED_BEFORE',
                    reference=dict(frame='4h', phase='confirmed', ma='ma35')))
        else:
            result['entry_contract'] = None
        record = ctx.get('registered_entry_contract')
        result['thesis_assessment'] = dict(contract_id=record['contract_id'], status=assessment,
            reason='Current original structure remains valid') if record else None
        if ctx.get('pending_renewal_version') == 1:
            result['pending_renewal'] = {'offer_id': ctx['pending_renewal_offer']['offer_id']} if reapprove and action == 'WAIT' else None
        return result

    def generate(**request):
        calls.append(deepcopy(request))
        ctx = contexts[-1]
        data = restore_model_input(json.loads(request['user_prompt']))
        if 'wait_proposal' in request['response_schema']['properties']:
            result = dict(wait_proposal=raw(ctx, 'WAIT'),
                candidates=[dict(raw(ctx, 'EXIT'), rationale=f'alternative {i}') for i in range(trade_alternatives)]
                    if ctx['scenario_id'] else [raw(ctx, 'OPEN')],
                          no_candidate_reason='Keep original reservation only' if ctx['scenario_id'] else '')
        else:
            bundle = data['calculated_candidates']
            chosen = (next((row for row in bundle['candidates'] if row['id'] == 'WAIT_RENEWAL'), bundle['wait'])
                      if ctx['scenario_id'] and select_renewal else
                      bundle['wait'] if ctx['scenario_id'] else bundle['candidates'][0])
            result = dict(selection_token=f"{chosen['id']}:{chosen['receipt_sha256']}", rationale=selection_reason)
            if before_select and ctx['scenario_id']:
                before_select(broker, exchange)
        return SimpleNamespace(text=json.dumps(result))

    adapter = make_calculated_review_proposer(generate=generate, clock=lambda: exchange.now,
                                              research_mode='economic-reject-calculated-v1')

    def propose(snapshot, context, *, record):
        contexts.append(deepcopy(context))
        return adapter(snapshot, context, record=record)

    runtime = ScenarioRuntime(conn, broker, lambda *_: pytest.fail('single proposer'),
        lambda: dict(valid=True, as_of_ms=exchange.now * 1000), clock=lambda: exchange.now,
        calculated_review_enabled=True, calculated_review_propose=propose,
        research_review_mode='economic-reject-calculated-v1', entry_contract_enabled=True,
        pending_renewal_enabled=True, entry_goal_enabled=goal_enabled)
    if goal_enabled:
        from engine.scenario_snapshot import TIMEFRAME_MS, candle_start
        def market():
            at = exchange.now * 1000
            start = candle_start(at, TIMEFRAME_MS['4h'])
            p = dict(open_time_ms=start - TIMEFRAME_MS['4h'], open=100, high=101,
                     low=99, close=100, ma10=100, ma35=106 if side == 'LONG' else 94)
            return dict(valid=True, as_of_ms=at, timeframes={'4h': {'ma_path': dict(
                version=1, confirmed_points=[p], forming_point=None)}})
        runtime.snapshot = market
    return runtime, broker, exchange, calls, contexts


@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
def test_initial_lease_and_explicit_selected_wait_keep_exact_order(tmp_path, side):
    r, b, e, calls, contexts = renewal_runtime(tmp_path, side=side)
    outcome = r.tick()
    assert outcome['status'] == 'intent_pending', outcome
    active = r.state()['active']
    first = load(r.conn, active)
    assert first and first['until'] == e.now + 420
    parent = r.conn.execute('SELECT payload FROM llm_scenario_intents').fetchone()[0]
    children = b.children()
    original_request = deepcopy(children[0]['request'])
    original_order = children[0]['order_id']
    writes = deepcopy(e.writes)
    e.now += 301
    outcome = r.tick()
    assert outcome['status'] == 'intent_pending', outcome
    latest = load(r.conn, r.state()['active'])
    assert latest['until'] == 1800000720
    assert latest['revision'] == 1
    proof = receipt(r.conn, latest['last_intent_id'])
    assert proof['status'] == 'APPLIED', proof
    assert r.conn.execute('SELECT payload FROM llm_scenario_intents ORDER BY rowid LIMIT 1').fetchone()[0] == parent
    assert b.children()[0]['order_id'] == original_order
    assert b.children()[0]['request'] == original_request
    assert e.writes == writes
    assert contexts[-1]['pending_renewal_offer']['from_expires_at'] == 1800000420
    assert all(len(call['user_prompt'].encode()) <= 100000 for call in calls)
    assert len(calls) == 4  # OPEN DRAFT+SELECT, renewal DRAFT+SELECT even without trade alternatives.
    selected_input = restore_model_input(json.loads(calls[-1]['user_prompt']))
    fallback = selected_input['calculated_candidates']['wait']
    assert fallback['proposal_source'] == 'HOST_NO_RENEWAL_FALLBACK'
    assert fallback['raw_proposal']['pending_renewal'] is None
    # Repeated executor call returns durable proof, never another renewal/write.
    payload = json.loads(r.conn.execute('SELECT payload FROM llm_scenario_intents ORDER BY rowid DESC LIMIT 1').fetchone()[0])
    b.execute(payload, payload['action_id'])
    assert load(r.conn, r.state()['active']) == latest and e.writes == writes
    state = r.state()
    r._reconcile(state)
    r._context(state)  # Exact latest pending risk equality must survive lease apply.


def test_plain_wait_does_not_renew_then_original_lease_cancels(tmp_path):
    r, b, e, _, _ = renewal_runtime(tmp_path, reapprove=False)
    assert r.tick()['status'] == 'intent_pending'
    e.now += 301
    assert r.tick()['status'] == 'wait'
    assert load(r.conn, r.state()['active'])['revision'] == 0
    e.now = 1800000420
    b.reconcile()
    assert b.children()[0]['status'] == 'TERMINAL'
    assert not e.fills


@pytest.mark.parametrize('assessment', ['WEAKENING', 'UNKNOWN', 'FAILED', None])
def test_non_intact_assessment_cannot_approve(tmp_path, assessment):
    r, _, e, _, _ = renewal_runtime(tmp_path, assessment=assessment)
    assert r.tick()['status'] == 'intent_pending'
    e.now += 301
    r.tick()
    assert load(r.conn, r.state()['active'])['revision'] == 0


def test_draft_approval_declined_by_selected_host_wait_has_no_authority(tmp_path):
    r, b, e, calls, _ = renewal_runtime(tmp_path, select_renewal=False)
    assert r.tick()['status'] == 'intent_pending'
    before = deepcopy(e.writes)
    e.now += 301
    assert r.tick()['status'] == 'wait'
    assert len(calls) == 4
    assert load(r.conn, r.state()['active'])['revision'] == 0
    assert not r.conn.execute('SELECT 1 FROM llm_scenario_pending_renewals').fetchone()
    assert e.writes == before


@pytest.mark.parametrize('fault', ['triggered', 'partial', 'qty', 'trigger_price', 'control', 'deadline', 'model_error'])
def test_selected_approval_cannot_survive_new_adverse_evidence(tmp_path, fault):
    def change(b, e):
        child = b.children()[0]
        if fault == 'triggered': e.orders[child['link_id']]['orderStatus'] = 'New'
        if fault == 'partial': e.fill(child['link_id'], .001)
        if fault == 'qty': e.orders[child['link_id']]['qty'] = '999'
        if fault == 'trigger_price': e.orders[child['link_id']]['triggerPrice'] = '101.1'
        if fault == 'control': _write(b.conn, dict(version=1, state='paused', main_uid='123'))
        if fault == 'deadline': e.now = 1800000420
        if fault == 'model_error': raise TimeoutError('fixture')
    r, b, e, _, _ = renewal_runtime(tmp_path, before_select=change)
    assert r.tick()['status'] == 'intent_pending'
    e.now += 301
    r.tick()
    lease = json.loads(r.conn.execute('SELECT body FROM llm_scenario_pending_leases').fetchone()[0])
    assert lease['revision'] == 0 and lease['until'] == 1800000420
    assert not r.conn.execute("SELECT 1 FROM llm_scenario_pending_renewals WHERE json_extract(body,'$.status')='APPLIED'").fetchone()
    if fault == 'partial':
        assert b._verify_protection(b.capture_account(), 98, 'LONG') is True


@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
def test_same_slot_replay_and_absolute_hour_cap(tmp_path, side):
    r, b, e, _, _ = renewal_runtime(tmp_path, side=side)
    assert r.tick()['status'] == 'intent_pending'
    initial = load(r.conn, r.state()['active'])
    for slot in range(1, 12):
        e.now = 1800000000 + slot * 300 + 1
        assert r.tick()['status'] == 'intent_pending'
        lease = load(r.conn, r.state()['active'])
        assert lease['until'] == min(1800000000 + (slot + 1) * 300 + 120, 1800003600)
        assert lease['anchor'] == initial['anchor']
        r.tick()
        assert load(r.conn, r.state()['active']) == lease
    assert len([w for w in e.writes if w[0] == 'place']) == 1
    e.now = 1800003600
    b.reconcile()
    assert b.children()[0]['status'] == 'TERMINAL'
    assert len([w for w in e.writes if w[0] == 'cancel']) == 1


def test_crash_after_intent_before_apply_is_explicitly_rejected_not_silently_terminal(tmp_path):
    r, b, e, _, _ = renewal_runtime(tmp_path)
    assert r.tick()['status'] == 'intent_pending'
    execute = b.execute
    b.execute = lambda *_: (_ for _ in ()).throw(RuntimeError('crash before local apply'))
    e.now += 301
    assert r.tick()['status'] == 'intent_pending'
    ident = r.conn.execute('SELECT id FROM llm_scenario_intents ORDER BY rowid DESC LIMIT 1').fetchone()[0]
    assert receipt(r.conn, ident) is None
    b.execute = execute
    b.reconcile()
    assert receipt(r.conn, ident)['status'] == 'REJECTED'
    assert load(r.conn, r.state()['active'])['revision'] == 0
    payload = json.loads(r.conn.execute('SELECT payload FROM llm_scenario_intents WHERE id=?', (ident,)).fetchone()[0])
    b.execute(payload, ident)
    assert load(r.conn, r.state()['active'])['revision'] == 0


def test_apply_receipt_failure_rolls_back_lease_and_latest_evidence(tmp_path):
    r, b, e, _, _ = renewal_runtime(tmp_path)
    assert r.tick()['status'] == 'intent_pending'
    r.conn.execute("CREATE TRIGGER refuse_apply BEFORE INSERT ON llm_scenario_pending_renewals "
        "WHEN json_extract(NEW.body,'$.status')='APPLIED' BEGIN SELECT RAISE(ABORT,'fixture'); END")
    r.conn.commit()
    e.now += 301
    assert r.tick()['status'] == 'intent_pending'
    lease = load(r.conn, r.state()['active'])
    assert lease['revision'] == 0 and lease['until'] == 1800000420
    original = json.loads(r.conn.execute('SELECT evidence FROM llm_scenario_intents ORDER BY rowid LIMIT 1').fetchone()[0])
    assert original['open_entries'][0]['expires_at'] == 1800000420
    ident = r.conn.execute('SELECT id FROM llm_scenario_intents ORDER BY rowid DESC LIMIT 1').fetchone()[0]
    assert receipt(r.conn, ident)['status'] == 'REJECTED'


@pytest.mark.parametrize('stage', [0, 1])
def test_soft_recovery_latch_can_reapprove_but_never_reauthorizes_original_submit(tmp_path, stage):
    from live import scenario_recovery as recovery
    fraction = .005 if stage == 0 else .01
    r, b, e, _, _ = renewal_runtime(tmp_path, initial_risk_fraction=fraction)
    assert r.tick()['status'] == 'intent_pending'
    state = r.state()
    original = json.loads(r.conn.execute('SELECT payload FROM llm_scenario_intents').fetchone()[0])
    state['breaker'] = dict(blocked=True, reasons=['three_losses'], consecutive_losses=3)
    state['recovery'] = dict(phase='CONSUMED', scenario_id=state['active']['scenario_id'],
        action_id=original['action_id'], side='LONG', initial_equity=10000, risk_fraction=fraction,
        budget=10000 * fraction, entry_deadline=original['expires_at'], initial_entries=original['entries'],
        permit_id='fixture', policy_version=2, stage=stage, stage_evidence={})
    recovery.ensure_schema(r.conn)
    r._save(state)
    r.recovery_enabled = True
    permit = deepcopy(state['recovery'])
    e.now += 301
    result = r.tick()
    assert result['status'] == 'intent_pending', result
    assert load(r.conn, r.state()['active'])['revision'] == 1
    assert r.state()['recovery'] == permit
    assert recovery.authorize_entry(r.state(), original, b.context(), e.now) is False


def test_notices_show_real_lease_and_only_applied_renewal(tmp_path):
    r, _, e, _, _ = renewal_runtime(tmp_path)
    assert r.tick()['status'] == 'intent_pending'
    plan = r.conn.execute("SELECT body FROM llm_scenario_outbox WHERE kind='PLAN'").fetchone()[0]
    from live.scenario_notice import _time
    assert _time(1800000420) in plan and '최초 제출 권한 마감' in plan
    e.now += 301
    r.tick()
    messages = r.conn.execute("SELECT body FROM llm_scenario_outbox WHERE kind='RESERVATION_RENEWED'").fetchall()
    assert len(messages) == 1
    assert _time(1800000720) in messages[0][0]
    assert '새 주문·추가 진입·체결 확인이 아닙니다' in messages[0][0]
    assert '취소 확인 전 늦은 체결' in messages[0][0]


def test_two_trade_alternatives_plus_renewal_are_lossless_and_decline_is_available(tmp_path):
    r, _, e, calls, _ = renewal_runtime(tmp_path, trade_alternatives=2)
    assert r.tick()['status'] == 'intent_pending'
    e.now += 301
    outcome = r.tick()
    assert outcome['status'] == 'intent_pending', (outcome, [json.loads(read_tape(row[1], r.conn, row[0], decode_legacy=review_text))['outcome']
        for row in r.conn.execute('SELECT slot,body FROM llm_scenario_review_tapes')])
    projected = json.loads(calls[-1]['user_prompt'])
    restored = restore_model_input(projected)
    choices = restored['calculated_candidates']
    assert [row['id'] for row in choices['candidates']] == ['c0', 'c1', 'WAIT_RENEWAL']
    renewal = choices['candidates'][-1]
    assert renewal['raw_model_wrapper']['wait_proposal']['pending_renewal'] == renewal['raw_proposal']['pending_renewal']
    from live.scenario_calculated_review import receipt_hash
    assert choices['wait']['derived_from_model_proposal_sha256'] == receipt_hash(renewal['raw_proposal'])
    assert choices['wait']['raw_proposal']['pending_renewal'] is None
    assert len(calls[-1]['user_prompt'].encode()) < 100000


def test_restart_retains_applied_lease_without_new_submission(tmp_path):
    r, b, e, _, _ = renewal_runtime(tmp_path)
    assert r.tick()['status'] == 'intent_pending'
    e.now += 301
    assert r.tick()['status'] == 'intent_pending'
    before, writes = load(r.conn, r.state()['active']), deepcopy(e.writes)
    restarted = ScenarioRuntime(r.conn, b, r.propose, r.snapshot, clock=r.clock,
        calculated_review_enabled=True, calculated_review_propose=r.calculated_review_propose,
        research_review_mode=r.research_review_mode, entry_contract_enabled=True, pending_renewal_enabled=True)
    restarted.tick()
    assert load(r.conn, restarted.state()['active']) == before and e.writes == writes
    e.now += 300
    assert restarted.tick()['status'] == 'intent_pending'
    assert load(r.conn, restarted.state()['active'])['revision'] == 2
    assert e.writes == writes


@pytest.mark.parametrize('field,value', [
    ('orderStatus', 'New'), ('slTriggerBy', 'LastPrice'), ('slTriggerBy', None),
    ('triggerBy', 'LastPrice'), ('orderLinkId', 'different'), ('side', 'Sell'),
    ('orderType', 'Market'), ('reduceOnly', True), ('triggerDirection', 2),
    ('positionIdx', 1), ('timeInForce', 'IOC'), ('slOrderType', 'Limit'),
])
def test_executor_rejects_trigger_race_after_exact_child_query(tmp_path, monkeypatch, field, value):
    r, b, e, _, _ = renewal_runtime(tmp_path)
    assert r.tick()['status'] == 'intent_pending'
    original = b._renew_pending

    def race(payload, ident):
        capture = b.capture_account
        changed = [False]
        def capture_after_query():
            if not changed[0]:
                e.orders[b.children()[0]['link_id']][field] = value
                changed[0] = True
            return capture()
        with monkeypatch.context() as m:
            m.setattr(b, 'capture_account', capture_after_query)
            return original(payload, ident)
    monkeypatch.setattr(b, '_renew_pending', race)
    e.now += 301
    assert r.tick()['status'] == 'intent_pending'
    assert load(r.conn, r.state()['active'])['revision'] == 0
    assert json.loads(r.conn.execute('SELECT body FROM llm_scenario_pending_renewals').fetchone()[0])['status'] == 'REJECTED'


def test_sql_notice_latency_past_old_expiry_rolls_back_entire_apply(tmp_path, monkeypatch):
    from live import scenario_outbox
    r, _, e, _, _ = renewal_runtime(tmp_path)
    assert r.tick()['status'] == 'intent_pending'
    enqueue = scenario_outbox.enqueue
    def delayed(conn, ident, event):
        enqueue(conn, ident, event)
        if event['kind'] == 'RESERVATION_RENEWED': e.now = 1800000420
    monkeypatch.setattr(scenario_outbox, 'enqueue', delayed)
    e.now += 301
    assert r.tick()['status'] == 'intent_pending'
    lease = load(r.conn, r.state()['active'])
    assert lease['revision'] == 0
    assert not r.conn.execute("SELECT 1 FROM llm_scenario_outbox WHERE kind='RESERVATION_RENEWED'").fetchone()
    latest = json.loads(r.conn.execute('SELECT evidence FROM llm_scenario_intents ORDER BY rowid LIMIT 1').fetchone()[0])
    assert latest['open_entries'][0]['expires_at'] == 1800000420


def test_cancel_pending_is_not_an_untriggered_renewal_opportunity(tmp_path):
    r, b, e, _, contexts = renewal_runtime(tmp_path)
    assert r.tick()['status'] == 'intent_pending'
    child = b.children()[0]
    b._save('cancel_ack_pending', dict(order_id=child['order_id'], link_id=child['link_id'], terminal_confirmed=False))
    e.now += 301
    assert r.tick()['status'] == 'wait'
    assert 'pending_renewal_offer' not in contexts[-1]
    assert load(r.conn, r.state()['active'])['revision'] == 0


def test_legacy_no_anchor_keeps_original_expiry(tmp_path):
    r, b, e, _, _ = renewal_runtime(tmp_path)
    r.pending_renewal_enabled = False
    assert r.tick()['status'] == 'intent_pending'
    active = r.state()['active']
    assert 'pending_wait_anchor' not in active and load(r.conn, active) is None
    e.now = active['expires_at']
    b.reconcile()
    assert b.children()[0]['status'] == 'TERMINAL'


def test_first_version_rejects_single_pass_enablement(tmp_path):
    r, b, _, _, _ = renewal_runtime(tmp_path)
    with pytest.raises(ValueError, match='two_phase'):
        ScenarioRuntime(r.conn, b, r.propose, r.snapshot, calculated_review_enabled=True,
            calculated_review_propose=r.calculated_review_propose, research_review_mode='economic-reject-single-v1',
            entry_contract_enabled=True, pending_renewal_enabled=True)


@pytest.mark.parametrize('calculated', [False, True])
def test_disabled_restart_observes_old_lease_but_cannot_offer_or_commit_renewal(tmp_path, calculated):
    r, b, e, _, _ = renewal_runtime(tmp_path)
    assert r.tick()['status'] == 'intent_pending'
    before = load(r.conn, r.state()['active'])
    restarted = ScenarioRuntime(r.conn, b, r.propose, r.snapshot, clock=r.clock,
        calculated_review_enabled=calculated,
        calculated_review_propose=r.calculated_review_propose if calculated else None,
        research_review_mode=r.research_review_mode if calculated else 'legacy',
        entry_contract_enabled=True, pending_renewal_enabled=False)
    e.now += 301
    state = restarted.state()
    restarted._reconcile(state)
    context = restarted._context(state)
    assert context['pending_wait']['lease_expires_at'] == before['until']
    assert 'pending_renewal_offer' not in context and 'pending_renewal_version' not in context
    assert b.children()[0]['status'] == 'LIVE'
    e.now = before['until']
    b.reconcile()
    assert b.children()[0]['status'] == 'TERMINAL'


def test_mixed_initial_batch_has_no_version_one_anchor(tmp_path):
    r, b, _, _, _ = renewal_runtime(tmp_path, mixed_initial=True)
    assert r.tick()['status'] == 'intent_pending'
    assert len(b.children()) == 2
    assert load(r.conn, r.state()['active']) is None
    assert 'pending_wait_anchor' not in r.state()['active']


def test_other_adjust_child_does_not_inherit_original_lease(tmp_path):
    r, b, e, _, _ = renewal_runtime(tmp_path)
    assert r.tick()['status'] == 'intent_pending'
    state = r.state()
    original = json.loads(r.conn.execute('SELECT payload FROM llm_scenario_intents').fetchone()[0])
    adjust = dict(original, action='ADJUST', action_id='extra', revision=2,
        entries=[dict(id='extra', price=100, quantity=.1)], expires_at=e.now + 50)
    state['active']['revision'] = 2
    state['active']['expires_at'] = adjust['expires_at']
    r._save(state)
    r.conn.execute("INSERT INTO llm_scenario_intents VALUES(?,?,?,'PENDING',NULL)",
                   ('extra', state['active']['scenario_id'], json.dumps(adjust)))
    r.conn.commit()
    b.execute(adjust, 'extra')
    e.now += 100  # Old submission and new ADJUST expiry passed; original lease still alive.
    b.reconcile()
    children = {child['local_id']: child for child in b.children()}
    assert children['conditional']['status'] == 'LIVE'
    assert children['extra']['status'] == 'TERMINAL'
    assert b._pending_entry(children['conditional'])['expires_at'] == 1800000420


def test_executor_rechecks_input_age_after_slow_preflight(tmp_path, monkeypatch):
    r, b, e, _, _ = renewal_runtime(tmp_path)
    assert r.tick()['status'] == 'intent_pending'
    original = b._renew_pending
    def near_stale(payload, ident):
        row = r.conn.execute('SELECT slot,context FROM llm_scenario_decisions WHERE proposal IS NOT NULL ORDER BY slot DESC LIMIT 1').fetchone()
        context = json.loads(row[1])
        # Isolated executor boundary: captured input was 119s old on entry.
        context['input_captured_at'] = e.now - 119
        r.conn.execute('UPDATE llm_scenario_decisions SET context=? WHERE slot=?', (json.dumps(context), row[0]))
        r.conn.commit()
        preflight = b._preflight
        def slow(*args, **kwargs):
            value = preflight(*args, **kwargs)
            e.now += 2
            return value
        with monkeypatch.context() as m:
            m.setattr(b, '_preflight', slow)
            return original(payload, ident)
    monkeypatch.setattr(b, '_renew_pending', near_stale)
    e.now += 301
    assert r.tick()['status'] == 'intent_pending'
    assert e.now < 1800000420
    assert load(r.conn, r.state()['active'])['revision'] == 0
    assert json.loads(r.conn.execute('SELECT body FROM llm_scenario_pending_renewals').fetchone()[0])['status'] == 'REJECTED'


@pytest.mark.parametrize('fill_all', [False, True])
def test_holding_has_no_waiting_policy_metadata_but_remaining_order_keeps_lease(tmp_path, fill_all):
    r, b, e, _, _ = renewal_runtime(tmp_path)
    assert r.tick()['status'] == 'intent_pending'
    before = load(r.conn, r.state()['active'])
    child = b.children()[0]
    e.now += 1
    e.fill(child['link_id'], float(child['request']['qty']) if fill_all else .001)
    state = r.state()
    r._reconcile(state)
    context = r._context(state)
    assert context['positions']
    for field in ('pending_wait_policy_version', 'pending_wait', 'pending_renewal_version', 'pending_renewal_offer'):
        assert field not in context
    assert load(r.conn, r.state()['active']) == before
    if not fill_all:
        assert context['pending_entries'][0]['expires_at'] == before['until']
    assert b._verify_protection(b.capture_account(), 98, 'LONG') is True


def test_zero_fill_metadata_retains_exact_host_offer(tmp_path):
    from live.scenario_pending_renewal import offer
    r, b, e, _, _ = renewal_runtime(tmp_path)
    assert r.tick()['status'] == 'intent_pending'
    e.now += 301
    state = r.state()
    r._reconcile(state)
    context = r._context(state)
    lease = load(r.conn, state['active'])
    assert context['positions'] == [] and context['pending_entries']
    assert context['pending_wait_policy_version'] == context['pending_renewal_version'] == 1
    assert context['pending_wait'] == dict(lease_expires_at=lease['until'],
        absolute_expires_at=lease['anchor']['hard_deadline'])
    assert context['pending_renewal_offer'] == offer(r.conn, state, context,
                                                   b.children(state['active']['scenario_id']), e.now)


@pytest.mark.parametrize('corrupt', [False, True])
def test_pending_renewal_reads_compressed_selected_tape(tmp_path, monkeypatch, corrupt):
    import gzip
    from live.scenario_review_storage import MAGIC
    r, b, e, _, _ = renewal_runtime(tmp_path)
    assert r.tick()['status'] == 'intent_pending'
    original = b._renew_pending

    def compressed(payload, ident):
        slot = r.conn.execute('SELECT slot FROM llm_scenario_decisions WHERE json_extract(proposal, "$.action_id")=?', (ident,)).fetchone()[0]
        body = r.conn.execute('SELECT body FROM llm_scenario_review_tapes WHERE slot=?', (slot,)).fetchone()[0]
        value = MAGIC + (b'broken' if corrupt else gzip.compress(read_tape(body, r.conn, slot, decode_legacy=review_text).encode(), mtime=0))
        r.conn.execute('UPDATE llm_scenario_review_tapes SET body=? WHERE slot=?', (value, slot))
        r.conn.commit()
        return original(payload, ident)

    monkeypatch.setattr(b, '_renew_pending', compressed)
    e.now += 301
    r.tick()
    lease = load(r.conn, r.state()['active'])
    assert lease['revision'] == (0 if corrupt else 1)
    status = json.loads(r.conn.execute('SELECT body FROM llm_scenario_pending_renewals').fetchone()[0])['status']
    assert status == ('REJECTED' if corrupt else 'APPLIED')


def test_runtime_durable_event_comparison_reads_compressed_events(tmp_path, monkeypatch):
    import gzip
    from live.scenario_review_storage import MAGIC
    r, b, e, _, _ = renewal_runtime(tmp_path)
    original = r._record_calculated_review

    def compressed(slot, event, *, commit=True):
        original(slot, event, commit=commit)
        seq, body = r.conn.execute('SELECT sequence,body FROM llm_scenario_review_events WHERE slot=? ORDER BY sequence DESC LIMIT 1', (slot,)).fetchone()
        r.conn.execute('UPDATE llm_scenario_review_events SET body=? WHERE slot=? AND sequence=?',
                       (MAGIC + gzip.compress(review_text(body).encode(), mtime=0), slot, seq))
        if commit:
            r.conn.commit()

    monkeypatch.setattr(r, '_record_calculated_review', compressed)
    result = r.tick()
    assert result['status'] == 'intent_pending', result
    assert r.conn.execute("SELECT COUNT(*) FROM llm_scenario_review_events WHERE typeof(body)='blob'").fetchone()[0] > 0


def test_new_review_tape_is_compressed_and_preserves_complete_envelope(tmp_path):
    from backtest.scenario_tape import digest
    from live.scenario_review_storage import review_text
    from utils.btc_review_references import MAGIC
    r, _, _, _, _ = renewal_runtime(tmp_path)
    assert r.tick()['status'] == 'intent_pending'
    stored = r.conn.execute('SELECT body FROM llm_scenario_review_tapes').fetchone()[0]
    assert isinstance(stored, bytes) and stored.startswith(MAGIC)
    slot = r.conn.execute('SELECT slot FROM llm_scenario_review_tapes').fetchone()[0]
    envelope = json.loads(read_tape(stored, r.conn, slot, decode_legacy=review_text))
    assert envelope['outcome']['status'] == 'COMPLETE' and envelope['executable'] is False
    assert envelope['sha256'] == digest({k: v for k, v in envelope.items() if k != 'sha256'})


def test_runtime_compressed_tape_exports_with_matching_original_seal(tmp_path):
    import sys
    from pathlib import Path
    sys.path.append(str(Path(__file__).resolve().parents[2]))
    from observability import btc_scenario_export
    from live.scenario_review_storage import review_text
    r, _, _, _, _ = renewal_runtime(tmp_path)
    assert r.tick()['status'] == 'intent_pending'
    db = Path(r.conn.execute('PRAGMA database_list').fetchone()[2])
    stored = r.conn.execute('SELECT body FROM llm_scenario_review_tapes').fetchone()[0]
    slot = r.conn.execute('SELECT slot FROM llm_scenario_review_tapes').fetchone()[0]
    original = read_tape(stored, r.conn, slot, decode_legacy=review_text)
    state, spool = tmp_path / 'export-state.sqlite', tmp_path / 'export.jsonl'
    btc_scenario_export.export_once(db, state, spool)
    btc_scenario_export.export_once(db, state, spool)
    rows = [json.loads(line) for line in spool.read_text().splitlines() if line.strip()]
    exported = [row for row in rows if row.get('event_type') == 'btc.research.review_tape']
    assert len(exported) == 1
    assert exported[0]['attributes']['tape_hash_status'] == 'MATCH'
    assert exported[0]['attributes']['body_sha256'] == btc_scenario_export._hash(original)
    r.conn.close()


@pytest.mark.parametrize('corrupt', [False, True])
def test_pending_renewal_reads_referenced_tape_and_rejects_changed_source(tmp_path, monkeypatch, corrupt):
    from utils.btc_review_references import compact, pack

    r, b, e, _, _ = renewal_runtime(tmp_path)
    assert r.tick()['status'] == 'intent_pending'
    original = b._renew_pending

    def referenced(payload, ident):
        slot = r.conn.execute('SELECT slot FROM llm_scenario_decisions WHERE json_extract(proposal, "$.action_id")=?', (ident,)).fetchone()[0]
        stored = r.conn.execute('SELECT body FROM llm_scenario_review_tapes WHERE slot=?', (slot,)).fetchone()[0]
        snapshot, context = r.conn.execute('SELECT snapshot,context FROM llm_scenario_decisions WHERE slot=?', (slot,)).fetchone()
        events = [review_text(row[0]) for row in r.conn.execute('SELECT body FROM llm_scenario_review_events WHERE slot=? ORDER BY sequence', (slot,))]
        value = pack(compact(read_tape(stored, r.conn, slot, decode_legacy=review_text), snapshot, context, events))
        r.conn.execute('UPDATE llm_scenario_review_tapes SET body=? WHERE slot=?', (value, slot))
        if corrupt:
            r.conn.execute('UPDATE llm_scenario_review_events SET body=? WHERE slot=? AND sequence=0', ('{}', slot))
        r.conn.commit()
        return original(payload, ident)

    monkeypatch.setattr(b, '_renew_pending', referenced)
    e.now += 301
    r.tick()
    lease = load(r.conn, r.state()['active'])
    assert lease['revision'] == (0 if corrupt else 1)
    status = json.loads(r.conn.execute('SELECT body FROM llm_scenario_pending_renewals').fetchone()[0])['status']
    assert status == ('REJECTED' if corrupt else 'APPLIED')
