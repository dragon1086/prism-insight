"""Opt-in observed clocks/market inputs through the unchanged simulated runtime."""
from copy import deepcopy
import json
from types import SimpleNamespace

import pytest

from analysis.scenario_replay import contract_for, network_boundary, run_replay
from backtest.scenario_tape import TapeMismatch
from tests.test_observed_scenario_data import packet, reseal, write_packet
from tests.test_scenario_replay import inputs, wait_policy


def lane(tmp_path, data=None):
    data = packet() if data is None else data
    path, pin = write_packet(tmp_path, data)
    bundle, market = inputs(data['end_ms']-data['start_ms'], start_ms=data['start_ms'])
    return data, bundle, market, dict(observed_packet=path, observed_packet_sha256=pin)


@pytest.mark.parametrize('input_mode', ['observed', 'reconstructed_at_observed_schedule'])
def test_observed_clock_precedes_context_and_raw_account_is_not_imported(tmp_path, input_mode):
    data = packet()
    # This earlier retained account quote is evidence, not the simulated mark.
    for row in data['rows']:
        row['quote_evidence']['mark_price'] = 987654.0
    data, bundle, market, options = lane(tmp_path, reseal(data))
    captured = []
    def policy(snapshot, context):
        captured.append((deepcopy(snapshot), deepcopy(context)))
        return wait_policy(snapshot, context)
    with network_boundary('frozen') as blocked:
        report = run_replay(bundle, market, tmp_path/'result', mode='fixture', tape_path=tmp_path/'tape',
            policy=policy, initial_equity=12345., market_input_mode=input_mode, **options)
    assert not blocked
    assert len(captured) == len(data['rows'])
    assert report['contract']['schema'] == 3
    assert report['replay_scope'] == 'HYBRID_OBSERVED_INPUT_ASSUMED_EXECUTION'
    for (snapshot, context), row in zip(captured, data['rows']):
        assert context['now'] == row['available_at_ms']/1000
        assert context['account_captured_at'] == context['now']
        assert context['now'] > row['source_context_now']
        assert context['initial_equity'] == 12345.
        assert context['positions'] == []
        assert context['mark_price'] != row['quote_evidence']['mark_price']
        if input_mode == 'observed':
            assert snapshot == row['snapshot']
        else:
            assert snapshot == market.snapshot(row['slot']*300000)
            assert snapshot['as_of_ms'] < row['snapshot']['as_of_ms']
            assert snapshot['historical_replay']['boundary_convention'] == 'after_previous_close_before_next_trade'


@pytest.mark.parametrize('path', ['OHLC', 'OLHC'])
@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
@pytest.mark.parametrize('input_mode', ['observed', 'reconstructed_at_observed_schedule'])
def test_schema3_trade_and_frozen_keep_same_physics_and_result(tmp_path, path, side, input_mode):
    _, bundle, market, options = lane(tmp_path)
    tape = tmp_path/'tape'
    def policy(snapshot, context):
        base = dict(wait_policy(snapshot, context), scenario_id=context['scenario_id'] or 'observed-fixture',
                    action_id='a-'+str(context['now']), expires_at=context['now']+600)
        if context['scenario_id']:
            return dict(base, action='EXIT')
        return dict(base, action='OPEN', side=side, leverage=10, confidence=.5,
                    hard_stop=98 if side == 'LONG' else 102,
                    entries=[dict(id='e1', price=100, quantity=.1)], take_profits=[], partial_stops=[],
                    chase=dict(max_bps=0, max_reprices=0))
    with network_boundary('frozen') as blocked:
        fresh = run_replay(bundle, market, tmp_path/'fresh', mode='fixture', tape_path=tape,
            policy=policy, path=path, market_input_mode=input_mode, **options)
        frozen = run_replay(bundle, market, tmp_path/'frozen', mode='frozen', tape_path=tape,
            path=path, market_input_mode=input_mode, **options)
    assert not blocked
    assert fresh['result_hash'] == frozen['result_hash']
    assert fresh['economic']['completed_scenarios'] == 1
    assert fresh['economic']['fees'] > 0
    assert fresh['economic']['open_quantity'] == 0
    assert frozen['execution_metadata']['actual_model_calls'] == 0
    for name in ('exchange.json', 'equity.json'):
        assert json.loads((tmp_path/'fresh'/name).read_text()) == json.loads((tmp_path/'frozen'/name).read_text())


def test_schema3_actual_request_builder_fresh_frozen_without_real_model(tmp_path):
    from live.scenario_contract import identity_fields
    from live.scenario_model_input import restore_model_input
    data, bundle, market, options = lane(tmp_path)
    calls = []
    def generate(**kwargs):
        payload = restore_model_input(json.loads(kwargs['user_prompt']))
        calls.append(payload)
        context = payload['contract_context']
        proposal = dict(identity_fields(context), action='WAIT', confidence=.2, expires_at=context['now']+60,
            leverage=10, side=None, hard_stop=None, chase=None, entries=[], take_profits=[],
            partial_stops=[], cancel_entry_ids=[], rationale='synthetic wire, no model')
        return SimpleNamespace(text=json.dumps(proposal))
    with network_boundary('frozen') as blocked:
        fresh = run_replay(bundle, market, tmp_path/'fresh', mode='fresh', tape_path=tmp_path/'tape',
            model_generator=generate, market_input_mode='observed', **options)
        frozen = run_replay(bundle, market, tmp_path/'frozen', mode='frozen', tape_path=tmp_path/'tape',
            market_input_mode='observed', **options)
    assert not blocked
    assert fresh['result_hash'] == frozen['result_hash']
    assert [call['market_snapshot'] for call in calls] == [row['snapshot'] for row in data['rows']]
    assert fresh['model_failures'] == fresh['input_preparation_failures'] == 0


@pytest.mark.parametrize('fault', ['duplicate', 'future', 'stale', 'invalid', 'account', 'file_pin', 'coverage'])
def test_bad_observation_fails_before_outputs_and_model(tmp_path, fault):
    data = packet()
    if fault == 'duplicate': data['rows'][1] = deepcopy(data['rows'][0])
    elif fault == 'future': data['rows'][0]['snapshot']['as_of_ms'] = data['rows'][0]['available_at_ms']+1
    elif fault == 'stale': data['rows'][0]['snapshot']['timeframes']['15m']['forming']['observed_at_ms'] -= 180000
    elif fault == 'invalid': data['rows'][0]['snapshot']['valid'] = False
    elif fault == 'account': data['rows'][0]['context'] = {'positions':[{'quantity':999}], 'initial_equity':999999}
    elif fault == 'coverage': data['rows'].pop()
    _, bundle, market, options = lane(tmp_path, reseal(data))
    if fault == 'file_pin': options['observed_packet_sha256'] = '0'*64
    def forbidden(**kwargs):
        pytest.fail('invalid input reached model')
    with pytest.raises(ValueError):
        run_replay(bundle, market, tmp_path/'result', mode='fresh', tape_path=tmp_path/'tape',
            model_generator=forbidden, market_input_mode='observed', **options)
    assert not (tmp_path/'result').exists()
    assert not (tmp_path/'tape').exists()


def test_explicit_gap_keeps_protection_without_tick_or_fake_wait(tmp_path):
    data = packet()
    data['rows'][1] = dict(slot=data['rows'][1]['slot'], status='MISSING', reason='source_snapshot_missing')
    _, bundle, market, options = lane(tmp_path, reseal(data))
    calls = []
    def policy(snapshot, context):
        calls.append(context['now'])
        return wait_policy(snapshot, context)
    with pytest.raises(ValueError):
        run_replay(bundle, market, tmp_path/'denied', mode='fixture', tape_path=tmp_path/'denied-tape',
            policy=policy, market_input_mode='observed', **options)
    assert calls == [] and not (tmp_path/'denied').exists()
    with network_boundary('frozen'):
        report = run_replay(bundle, market, tmp_path/'gap', mode='fixture', tape_path=tmp_path/'tape',
            policy=policy, market_input_mode='observed', allow_input_gaps=True, **options)
        frozen = run_replay(bundle, market, tmp_path/'copy', mode='frozen', tape_path=tmp_path/'tape',
            market_input_mode='observed', allow_input_gaps=True, **options)
    assert len(calls) == 1
    assert report['result_hash'] == frozen['result_hash']
    assert report['decision_outcomes'] == {'wait':1, 'INPUT_GAP':1}
    assert report['model_decisions'] == 1
    assert sum(report['protection_outcomes'].values()) > 1
    assert 'INPUT_GAP' in report['readiness']['insufficiency_reasons']
    assert report['readiness']['profitability_proven'] is False


def test_default_contract_has_no_observed_fields_and_modes_cannot_cross_tapes(tmp_path):
    _, bundle, market, options = lane(tmp_path)
    contract = contract_for(bundle, 'OHLC', 1., 10000.)
    assert contract['schema'] == 2
    assert 'market_input' not in contract and 'replay_scope' not in contract
    with network_boundary('frozen'):
        run_replay(bundle, market, tmp_path/'old', mode='fixture', tape_path=tmp_path/'old-tape', policy=wait_policy)
        with pytest.raises(TapeMismatch):
            run_replay(bundle, market, tmp_path/'wrong', mode='frozen', tape_path=tmp_path/'old-tape',
                market_input_mode='observed', **options)
        run_replay(bundle, market, tmp_path/'new', mode='fixture', tape_path=tmp_path/'new-tape', policy=wait_policy,
            market_input_mode='observed', **options)
        with pytest.raises(TapeMismatch):
            run_replay(bundle, market, tmp_path/'wrong2', mode='frozen', tape_path=tmp_path/'new-tape')
    assert not (tmp_path/'wrong').exists() and not (tmp_path/'wrong2').exists()


def test_reconstructed_comparator_never_retimes_old_boundary_to_hide_staleness(tmp_path):
    from tests.test_scenario_snapshot import snapshot as observed_snapshot
    data = packet()
    for row in data['rows']:
        start = row['slot']*300000
        row.update(snapshot=observed_snapshot(now=start+130000), available_at_ms=start+140000,
                   source_context_now=(start+131000)/1000)
        row['quote_evidence']['observed_at_ms'] = start+130500
    _, bundle, market, options = lane(tmp_path, reseal(data))
    with pytest.raises(ValueError, match='reconstructed_snapshot_stale_at_observed_schedule'):
        run_replay(bundle, market, tmp_path/'result', mode='fixture', tape_path=tmp_path/'tape',
            policy=wait_policy, market_input_mode='reconstructed_at_observed_schedule', **options)
    assert not (tmp_path/'result').exists()
