"""Cancellation-only WAIT must retain risk until exact terminal evidence."""
import json
import sqlite3
from types import SimpleNamespace

import pytest

from analysis.scenario_replay import network_boundary, run_replay
from backtest.scenario_exchange import ScenarioExchange
from tests.test_scenario_calculated_replay import wire
from tests.test_scenario_replay import inputs
from tests.test_scenario_review_guard import model_receipt_hash


@pytest.mark.parametrize('profile', ['single-pass-economic-v4', 'calculated-economic-v4'])
@pytest.mark.parametrize('ack', ['exact', 'timeout', 'missing_identity', 'wrong_identity'])
def test_wait_cancel_ack_pending_retains_intent_until_terminal(tmp_path, monkeypatch, profile, ack):
    original = ScenarioExchange.cancel_order
    cancels = []

    def cancel(self, **params):
        cancels.append(params)
        response = original(self, **params)
        if ack == 'timeout':
            raise TimeoutError('cancel transport unknown')
        if ack == 'missing_identity':
            return dict(retCode=0, result={})
        if ack == 'wrong_identity':
            return dict(response, result=dict(response['result'], orderLinkId='foreign-order'))
        return response

    monkeypatch.setattr(ScenarioExchange, 'cancel_order', cancel)
    bundle, market = inputs(1200000)
    wall = [0.]

    def generate(**request):
        wall[0] += 1
        data = json.loads(request['user_prompt'])
        ctx = data['contract_context']
        if 'selection_token' in request['response_schema']['properties']:
            selected = data['calculated_candidates']['candidates'][0]
            result = dict(selection_token=f"{selected['id']}:{model_receipt_hash(selected)}", rationale='fixture')
        else:
            action = 'OPEN' if ctx['now'] < bundle['start_ms']/1000+300 else 'WAIT'
            plan = wire(ctx, action)
            plan['expires_at'] = ctx['now']+1200
            if action == 'OPEN':
                plan['entries'][0]['price'] = 95
                plan['entries'][0]['quantity'] = .2
                plan['hard_stop'] = 94
            else:
                plan['cancel_entry_ids'] = [row['id'] for row in ctx['pending_entries']]
            result = plan
            if 'wait_proposal' in request['response_schema']['properties']:
                result = dict(wait_proposal=plan if action == 'WAIT' else wire(ctx, 'WAIT'),
                    candidates=[plan] if action == 'OPEN' else [], no_candidate_reason='fixture')
        return SimpleNamespace(text=json.dumps(result))

    with network_boundary('frozen') as blocked:
        fresh = run_replay(bundle, market, tmp_path/'fresh', mode='fixture', tape_path=tmp_path/'tape',
            policy_profile=profile, model_generator=generate, clock_monotonic=lambda: wall[0])
        frozen = run_replay(bundle, market, tmp_path/'frozen', mode='frozen', tape_path=tmp_path/'tape',
            policy_profile=profile)
    assert not blocked
    assert len(cancels) == 2  # One request in each fresh/frozen execution, never a retry.
    outcomes = [json.loads(line).get('outcome', {}) for line in (tmp_path/'tape').read_text().splitlines()]
    expected = 'awaiting_cancellation' if ack == 'exact' else 'submission_unknown'
    assert any(row.get('reason') == expected for row in outcomes), outcomes
    assert fresh['execution_status'] == ('COMPLETE' if ack == 'exact' else 'STOPPED')
    assert fresh['result_hash'] == frozen['result_hash']
    assert fresh['economic']['completed_scenarios'] == 0
    assert fresh['economic']['open_quantity'] == 0
    with sqlite3.connect(tmp_path/'fresh/replay.sqlite') as conn:
        assert conn.execute("SELECT count(*) FROM llm_scenario_children WHERE kind!='entry'").fetchone()[0] == 0
        assert conn.execute("SELECT status FROM llm_scenario_children").fetchall() == [('TERMINAL',)]
        if ack == 'exact':
            receipt = json.loads(conn.execute("SELECT body FROM llm_scenario_broker_evidence WHERE kind='cancel_ack_pending'").fetchone()[0])
            assert receipt['terminal_confirmed'] is False
            assert conn.execute("SELECT count(*) FROM llm_scenario_intents WHERE status='PENDING'").fetchone()[0] == 0
    assert frozen['execution_metadata']['actual_model_calls'] == 0
