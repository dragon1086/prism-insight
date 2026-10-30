"""Current goal/contract behavior must be opt-in and exactly replayable."""
import json
import sqlite3
from types import SimpleNamespace

import pytest

from analysis.scenario_replay import contract_for, network_boundary, run_replay
from live.scenario_model_input import restore_model_input
from live.scenario_review_storage import review_text
from utils.btc_review_event_references import read_event
from tests.test_scenario_calculated_replay import wire
from tests.test_scenario_replay import inputs

PROFILE = 'economic-reject-goal-v2'


def test_goal_profile_is_explicit_without_changing_legacy_flags():
    bundle, _ = inputs()
    old = contract_for(bundle, 'OHLC', 1, 10000, policy_profile='economic-reject-calculated-v1')
    new = contract_for(bundle, 'OHLC', 1, 10000, policy_profile=PROFILE)
    assert old['runtime_flags'] == dict(recovery_enabled=True, automatic_normalization_enabled=True)
    assert all(new['runtime_flags'][k] is True for k in (
        'entry_contract_enabled', 'entry_goal_enabled', 'pending_renewal_enabled'))
    assert new['context_contracts']['entry_contract_version'] == 2
    assert new['phase_contracts']['research_mode'] == 'economic-reject-calculated-v1'


@pytest.mark.parametrize('goal_case', ['unbounded', 'bounded', 'crossed'])
def test_goal_profile_registers_holds_and_replays_or_rejects_crossed_bound(tmp_path, goal_case):
    bundle, market = inputs()
    if goal_case == 'bounded':
        from backtest.scenario_tape import digest
        for key in ('open', 'high', 'low', 'close'):
            market.warmup['4h'][key] += 10
        bundle['data_hash'] = digest(market.manifest())
    contexts = []

    def generate(**request):
        payload = restore_model_input(json.loads(request['user_prompt']))
        ctx = payload['contract_context']
        assert ctx['entry_contract_version'] == 2
        if 'selection_token' in request['response_schema']['properties']:
            choices = payload['calculated_candidates']
            selected = next((r for r in choices['candidates'] if r['eligible']), choices['wait'])
            response = dict(selection_token=f"{selected['id']}:{selected['receipt_sha256']}", rationale='fixture')
        else:
            contexts.append(ctx)
            wait = wire(ctx, 'WAIT')
            wait.update(entry_contract=None, thesis_assessment=None)
            if ctx.get('pending_renewal_version') == 1:
                wait['pending_renewal'] = None
            if ctx.get('scenario_id'):
                wait['thesis_assessment'] = dict(contract_id=ctx['registered_entry_contract']['contract_id'],
                    status='INTACT', reason='Synthetic original premise remains intact')
                candidates = []
            else:
                plan = wire(ctx, 'OPEN')
                plan['entries'][0].pop('quantity')
                plan['entries'][0]['allocation_fraction'] = .01
                plan.update(entry_contract=dict(version=2, regime='TREND', premise='Synthetic goal test',
                    goal=dict(mode='NO_MA_BOUND' if goal_case == 'unbounded' else 'BOUNDED_BEFORE',
                              reference=None if goal_case == 'unbounded' else dict(
                                  frame='4h' if goal_case == 'bounded' else '1h', phase='confirmed', ma='ma35')),
                    explicit_failure=dict(source='MARK_PRICE',operator='le',price=99,minimum_observations=2)),
                    thesis_assessment=None)
                if ctx.get('pending_renewal_version') == 1:
                    plan['pending_renewal'] = None
                if goal_case == 'bounded':
                    plan['take_profits'][0]['price'] = 103
                candidates = [plan]
            response = dict(wait_proposal=wait, candidates=candidates, no_candidate_reason='' if candidates else 'Hold fixture')
        return SimpleNamespace(text=json.dumps(response))

    with network_boundary('frozen') as attempts:
        fresh = run_replay(bundle, market, tmp_path/'fresh', mode='fixture', tape_path=tmp_path/'tape',
            policy_profile=PROFILE, model_generator=generate, clock_monotonic=lambda: 0.)
        frozen = run_replay(bundle, market, tmp_path/'frozen', mode='frozen', tape_path=tmp_path/'tape',
            policy_profile=PROFILE)
    assert attempts == []
    assert fresh['execution_status'] == 'COMPLETE', fresh['failure_evidence']
    assert fresh['result_hash'] == frozen['result_hash']
    assert frozen['execution_metadata']['actual_model_calls'] == 0
    if goal_case == 'unbounded':
        from backtest.scenario_tape import TapeMismatch
        with pytest.raises(TapeMismatch, match='economic_tape_contract'):
            run_replay(bundle, market, tmp_path/'wrong-profile', mode='frozen',
                tape_path=tmp_path/'tape', policy_profile='economic-reject-calculated-v1')
    with sqlite3.connect(tmp_path/'fresh/replay.sqlite') as conn:
        contracts = conn.execute('SELECT body FROM llm_scenario_entry_contracts').fetchall()
        events = [json.loads(read_event(body, conn, slot, sequence, decode_legacy=review_text))
                  for slot, sequence, body in conn.execute('SELECT slot,sequence,body FROM llm_scenario_review_events')]
    from tools.compare_btc_scenario_replays import verify_economic_rejections
    verify_economic_rejections(tmp_path/'fresh', fresh)
    if goal_case == 'crossed':
        assert not contracts
        assert fresh['decision_outcomes'] == {'wait': 2}
        bundles = [e['body'] for e in events if e['kind'] == 'CALCULATED_CANDIDATES']
        assert len(bundles) == 2
        assert all(b['candidates'][0]['rejection'] == 'entry_goal_bound_exceeded' for b in bundles)
    else:
        assert len(contracts) == 1
        contract = json.loads(contracts[0][0])
        assert contract['version'] == 2
        assert contexts[-1]['registered_entry_contract']['contract_id'] == contract['contract_id']
        assert contexts[-1]['positions']
        if goal_case == 'bounded':
            assert contract['resolved_goal']['price'] > 103
