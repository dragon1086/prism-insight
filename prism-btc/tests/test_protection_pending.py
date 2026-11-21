"""A pending tightening must never certify the new stop or continue execution."""
import pytest

from live.scenario_broker import BrokerNotReady
from tests.test_scenario_execution import live as live_fixture, persist
from tests.test_scenario_review_guard import model_receipt_hash


@pytest.fixture
def held(tmp_path):
    broker, exchange = live_fixture.__wrapped__(tmp_path)
    broker.execute(persist(broker), 'a1')
    exchange.fill(broker.children()[0]['link_id'], .1)
    assert broker.reconcile()['protection_confirmed'] is True
    return broker, exchange


@pytest.mark.parametrize('damage', ['none', 'timeout', 'rejected', 'missing_before', 'missing_after', 'undersized_after', 'wrong_trigger', 'regressed', 'before_regressed', 'increased_exposure'])
def test_pending_tightening_requires_verified_old_native_protection(held, damage):
    broker, exchange = held
    if damage == 'missing_before':
        exchange.orders.pop('native')
    if damage == 'before_regressed':
        exchange.stop = '97'
        exchange.orders['native']['triggerPrice'] = '97'
    observed = broker.capture_account()
    def delayed(**params):
        exchange.writes.append(('stop', params))
        if damage == 'timeout':
            raise TimeoutError('ambiguous transport')
        if damage == 'rejected':
            return dict(retCode=10001, result={})
        if damage == 'wrong_trigger':
            exchange.orders['native']['triggerBy'] = 'LastPrice'
        if damage == 'regressed':
            exchange.stop = '97'
            exchange.orders['native']['triggerPrice'] = '97'
        if damage == 'increased_exposure':
            exchange.size = '.2'
            exchange.orders['native'].update(qty='.2', leavesQty='.2')
        if damage == 'missing_after':
            exchange.orders.pop('native')
        if damage == 'undersized_after':
            exchange.orders['native']['qty'] = '.01'
        return dict(retCode=0, result={})
    exchange.set_trading_stop = delayed
    before = len(exchange.writes)
    with pytest.raises(BrokerNotReady) as caught:
        broker._ensure_protection(observed, 99, 'LONG')
    assert (type(caught.value).__name__ == 'BrokerProtectionPending') is (damage == 'none')
    assert exchange.stop == ('97' if damage in ('regressed', 'before_regressed') else '98')
    assert len(exchange.writes) == before + 1


@pytest.mark.parametrize('adds', [False, True])
@pytest.mark.parametrize('verified_old_protection', [False, True])
def test_runtime_pending_label_never_confirms_intent_or_allows_added_risk(tmp_path, adds, verified_old_protection):
    from live.scenario_broker import BrokerProtectionPending
    from tests.test_scenario_runtime import setup, proposal, terminal
    runtime, broker, now, _ = setup.__wrapped__(tmp_path)
    runtime.tick()
    broker.ctx['positions'] = [dict(price=100, quantity=1)]
    broker.evidence = dict(intents=[terminal()])
    now[0] += 300
    def adjust(snapshot, context):
        plan = proposal(snapshot, context)
        plan.update(action='ADJUST', action_id='adjust-2', hard_stop=99,
                    entries=[dict(id='add', price=100, quantity=.1)] if adds else [])
        return plan
    runtime.propose = adjust
    def execute(payload, ident):
        raise (BrokerProtectionPending if verified_old_protection else BrokerNotReady)('pending')
    broker.execute = execute
    result = runtime.tick()
    assert result['status'] == 'intent_pending'
    expected = 'awaiting_protection' if verified_old_protection and not adds else 'submission_unknown'
    assert result['reason'] == expected
    assert runtime.conn.execute("SELECT status FROM llm_scenario_intents WHERE id='adjust-2'").fetchone() == ('PENDING',)


@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
@pytest.mark.parametrize('lose_old_stop', [False, True])
def test_delayed_tightening_runtime_and_frozen_replay_keep_protection_boundary(tmp_path, monkeypatch, side, lose_old_stop):
    import json
    import sqlite3
    from types import SimpleNamespace
    from analysis.scenario_replay import run_replay, network_boundary
    from backtest.scenario_exchange import ScenarioExchange
    from live.scenario_model_input import restore_model_input
    from tests.test_scenario_calculated_replay import wire
    from tests.test_scenario_replay import inputs
    bundle, market = inputs(1200000)
    original = ScenarioExchange.set_trading_stop
    def stop(self, **params):
        response = original(self, **params)
        if lose_old_stop:
            self.orders[self._native_id]['orderStatus'] = 'Cancelled'
        return response
    monkeypatch.setattr(ScenarioExchange, 'set_trading_stop', stop)
    def generate(**request):
        data = restore_model_input(json.loads(request['user_prompt']))
        ctx = data['contract_context']
        if 'selection_token' in request['response_schema']['properties']:
            choices = data['calculated_candidates']
            selected = next((r for r in choices['candidates'] if r['eligible']), choices['wait'])
            return SimpleNamespace(text=json.dumps(dict(selection_token=f"{selected['id']}:{model_receipt_hash(selected)}", rationale='fixture')))
        elapsed = ctx['now'] - bundle['start_ms']/1000
        action = 'OPEN' if not ctx.get('scenario_id') and elapsed < 300 else 'ADJUST' if ctx.get('scenario_id') and elapsed < 600 else 'WAIT'
        plan = wire(ctx, action)
        if action != 'WAIT':
            plan.update(side=side, chase=dict(max_bps=0, max_reprices=0), hard_stop=(98 if action == 'OPEN' else 98.5) if side == 'LONG' else (102 if action == 'OPEN' else 101.5),
                take_profits=[dict(id='initial' if action == 'OPEN' else 'replacement', price=120 if side == 'LONG' else 80, fraction=.5 if action == 'OPEN' else 1)])
        response = dict(wait_proposal=wire(ctx,'WAIT'),candidates=[plan] if action!='WAIT' else [],no_candidate_reason='fixture')
        return SimpleNamespace(text=json.dumps(response))
    with network_boundary('frozen') as attempts:
        fresh = run_replay(bundle,market,tmp_path/'fresh',mode='fixture',tape_path=tmp_path/'tape',
            policy_profile='calculated-economic-v4',model_generator=generate,clock_monotonic=lambda:0.)
        frozen = run_replay(bundle,market,tmp_path/'frozen',mode='frozen',tape_path=tmp_path/'tape',policy_profile='calculated-economic-v4')
    assert not attempts and fresh['result_hash']==frozen['result_hash']
    with sqlite3.connect(tmp_path/'fresh/replay.sqlite') as conn:
        outcomes=[json.loads(r[0]) for r in conn.execute('SELECT outcome FROM llm_scenario_decisions')]
        assert conn.execute("SELECT count(*) FROM llm_scenario_children WHERE kind='entry'").fetchone()[0]==1
        receipts=conn.execute("SELECT count(*) FROM llm_scenario_broker_evidence WHERE kind='native_tightening_pending'").fetchone()[0]
        if not lose_old_stop:
            pending_at=conn.execute("SELECT min(captured_at) FROM llm_scenario_broker_evidence WHERE kind='native_tightening_pending'").fetchone()[0]
            replacements=conn.execute("SELECT created_at FROM llm_scenario_children WHERE kind='tp' AND local_id LIKE '%:replacement'").fetchall()
            assert len(replacements)==1 and replacements[0][0]>pending_at
    assert fresh['execution_status']==('STOPPED' if lose_old_stop else 'COMPLETE'),fresh['failure_evidence']
    assert any(r.get('reason')==('submission_unknown' if lose_old_stop else 'awaiting_protection') for r in outcomes)
    assert (receipts>0) is (not lose_old_stop)
