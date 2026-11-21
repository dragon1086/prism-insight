"""Flow is captured once for judgment; never queried by the model or fresh guard."""
import json

from live.scenario_model_input import restore_model_input
from tests.test_scenario_entry_contract_wire import context, taped
from tests.test_scenario_runtime import setup, terminal, wait_proposal  # noqa: F401


def test_runtime_saves_same_flow_passed_to_model_without_fresh_guard_requery(setup, monkeypatch):  # noqa: F811
    from live import scenario_position_flow
    runtime, broker, now, _ = setup
    calls = []
    def build(conn, active, ctx, at, *, snapshot=None):
        assert isinstance(snapshot, dict)
        calls.append((active['scenario_id'] if active else None, at))
        return dict(version=1, status='AVAILABLE', sentinel='same-scenario-history')
    monkeypatch.setattr(scenario_position_flow, 'build_position_flow', build)
    assert runtime.tick()['status'] == 'intent_pending'
    broker.evidence = dict(intents=[terminal()])
    broker.ctx['positions'] = [dict(price=100, quantity=1)]
    now[0] += 300
    def model(snap, ctx):
        assert ctx['position_flow']['sentinel'] == 'same-scenario-history'
        return wait_proposal(snap, ctx, action_id='wait')
    runtime.propose = model
    assert runtime.tick()['status'] == 'wait'
    assert len(calls) == 2  # one per decision; not each account/fresh guard capture
    stored = json.loads(runtime.conn.execute(
        'SELECT context FROM llm_scenario_decisions WHERE slot=?', (int(now[0]//300),)).fetchone()[0])
    assert stored['position_flow']['sentinel'] == 'same-scenario-history'


def test_flow_and_priority_survive_real_two_phase_requests():
    ctx = context()
    ctx['position_flow'] = dict(version=1, status='PARTIAL', scenario_id=ctx['scenario_id'],
        scope='HISTORICAL', coverage=dict(omitted_items=3),
        thesis_transitions=[dict(status='WEAKENING', reason='Original concern')])
    args, calls = taped(ctx, status='WEAKENING', choose_wait=True)
    assert args[3]['outcome']['status'] == 'COMPLETE'
    assert len(calls) == 2
    for call in calls:
        restored = restore_model_input(json.loads(call['user_prompt']))
        assert restored['contract_context']['position_flow'] == ctx['position_flow']
        assert restored['contract_context']['ma_structure']['decision_priority']['descending'][0] == '1h'


def test_flow_helper_is_part_of_policy_provenance():
    from live.scenario_provenance import review_policy_binding, _manifest
    assert 'live/scenario_position_flow.py' in review_policy_binding()['source_sha256']
    assert 'live/scenario_position_flow.py' in json.dumps(_manifest())


def test_both_holding_review_phases_receive_mark_pace():
    ctx = context()
    pace = dict(status='AVAILABLE', source='MARK_PAIR_LAST_OHLC_NO_PATH',
                interval_seconds=300, favorable_usdt_per_btc=-67.24,
                favorable_atr30_per_5m=-0.342, favorable_atr1h_per_5m=-0.168,
                last30_favorable_atr=-0.357, last30_range_atr=0.726)
    ctx['position_flow'] = dict(version=1, status='AVAILABLE', coverage={}, mark_pace=pace)
    args, calls = taped(ctx, status='WEAKENING', choose_wait=True)
    assert args[3]['outcome']['status'] == 'COMPLETE'
    assert len(calls) == 2
    for call in calls:
        restored = restore_model_input(json.loads(call['user_prompt']))
        assert restored['contract_context']['position_flow']['mark_pace'] == pace


def test_selection_only_history_budget_replays_through_final_guard(monkeypatch):
    from live import scenario_position_flow
    from live.scenario_review_guard import guard_review
    ctx = context()
    ctx['position_flow'] = dict(version=1, status='AVAILABLE', coverage={},
        historical_observation_changes=[dict(slot=i, note='x' * 1000) for i in range(3)])
    _, unrestricted = taped(ctx, status='WEAKENING', choose_wait=True)
    draft_bytes, selection_bytes = [len(call['user_prompt'].encode()) for call in unrestricted]
    assert selection_bytes > draft_bytes
    monkeypatch.setattr(scenario_position_flow, '_MODEL_INPUT_LIMIT', selection_bytes - 1)
    args, calls = taped(ctx, status='WEAKENING', choose_wait=True)
    assert args[3]['outcome']['status'] == 'COMPLETE'
    flows = [restore_model_input(json.loads(call['user_prompt']))['contract_context']['position_flow'] for call in calls]
    assert flows[0] == ctx['position_flow']
    assert flows[1]['coverage']['model_budget_omitted_items'] == 1
    fresh = dict(ctx, now=1001, account_captured_at=1001)
    result = guard_review(*args, fresh, bindings={'test': 'entry-contract'}, research_mode='economic-reject-calculated-v1')
    assert result['payload']['action'] == 'WAIT'
