"""Deterministic fact/units representation, not free-text correctness guarantees."""
from copy import deepcopy
import json
from types import SimpleNamespace

import pytest

from engine.scenario_ma_context import build_ma_structure_context
from live.scenario_calculated_review import _model_payload, calculate_candidate, receipt_hash, research_review
from live.scenario_model_input import restore_model_input
from live.scenario_preview import response_contract
from tests.test_scenario_calculated_review import context, run, wire
from tests.test_scenario_ma_context import NOW, point, snapshot
from tests.test_scenario_review_guard import model_receipt_hash


@pytest.mark.parametrize('close,fast,slow,expected', [
    (82103.8, 81954.37999999999, 82300.00285714284, ('ABOVE', 'BELOW')),
    (82014.4, 82042.17, 81976.9657142857, ('BELOW', 'ABOVE')),
    (82103.8, 81996.03, 81979.05999999998, ('ABOVE', 'ABOVE')),
    (100, 110, 120, ('BELOW', 'BELOW')),
    (100, 100, 95, ('EQUAL', 'ABOVE')),
    (100, 105, 100, ('BELOW', 'EQUAL')),
    (100, 100, 100, ('EQUAL', 'EQUAL')),
])
@pytest.mark.parametrize('forming', [False, True])
def test_own_time_per_ma_relations_and_phase(close, fast, slow, expected, forming):
    snap = snapshot(before=point('30m', 2, close=close, fast=fast, slow=slow),
                    after=point('30m', 1, close=close, fast=fast, slow=slow, confirmed=False))
    original = deepcopy(snap)
    facts = build_ma_structure_context(snap, dict(now=NOW / 1000, mark_price=close + 1000))
    p = facts['primary']['30m']['points'][int(forming)]
    assert (p['close_vs_ma10'], p['close_vs_ma35']) == expected
    assert p['phase'] == ('FORMING' if forming else 'CONFIRMED')
    assert p['is_confirmed'] is not forming
    assert snap == original


def test_incident_confirmed_between_does_not_become_forming_above():
    snap = snapshot('1h', before=point('1h', 2, close=82014.4, fast=82042.17, slow=81976.9657142857),
        after=point('1h', 1, close=82103.8, fast=81996.03, slow=81979.05999999998, confirmed=False))
    data = restore_model_input(_model_payload(snap, dict(context(), now=NOW / 1000), {}))
    confirmed, forming = data['contract_context']['ma_structure']['primary']['1h']['points']
    assert (confirmed['phase'], confirmed['price_position']) == ('CONFIRMED', 'BETWEEN')
    assert (confirmed['close_vs_ma10'], confirmed['close_vs_ma35']) == ('BELOW', 'ABOVE')
    assert (forming['phase'], forming['price_position']) == ('FORMING', 'ABOVE')


@pytest.mark.parametrize('fault', ['future', 'missing', 'nan'])
def test_invalid_points_do_not_gain_relation_or_phase_facts(fault):
    snap = snapshot()
    p = snap['timeframes']['30m']['ma_path']['forming_point']
    if fault == 'future':
        p['observed_at_ms'] = NOW + 1
    elif fault == 'missing':
        p.pop('ma10')
    else:
        p['ma35'] = float('nan')
    facts = build_ma_structure_context(snap, dict(now=NOW / 1000, mark_price=100))
    assert len(facts['primary']['30m']['points']) == 1
    assert facts['primary']['30m']['points'][0]['phase'] == 'CONFIRMED'
    assert facts['primary']['1h']['points'] == []


def test_incident_total_target_usdt_is_not_per_btc_and_receipt_is_untouched():
    ctx = context()
    candidate = wire(ctx, 'LONG')
    candidate.update(entries=[dict(id='e', price=82200, quantity=.015)], hard_stop=81900,
                     take_profits=[dict(id='tp', price=82252.7, fraction=1)])
    receipt = calculate_candidate(candidate, ctx, candidate_id='c0')
    original, original_hash = deepcopy(receipt), receipt_hash(receipt)
    payload = _model_payload(dict(valid=True, as_of_ms=1000000), ctx, {},
                             calculated_candidates=dict(candidates=[receipt]))
    restored = restore_model_input(payload)
    units = restored['calculation_field_units']['projection.targets']
    assert units['quantity'] == 'BTC'
    assert units['gross_pnl'] == 'USDT_FOR_THIS_TARGET_QUANTITY_BEFORE_COSTS'
    assert units['gross_reward_per_btc'] == 'USDT_PER_BTC_NOT_TOTAL_PNL'
    assert units['gross_reward_to_reserved_risk_ratio'] == 'DIMENSIONLESS_NOT_USDT'
    target = restored['calculated_candidates']['candidates'][0]['projection']['targets'][0]
    assert target['quantity'] == .015
    assert target['gross_reward_per_btc'] == 52.7
    assert target['gross_pnl'] == .7905
    assert restored['calculated_candidates']['candidates'][0]['projection'] == original['projection']
    assert restored['calculated_candidates']['candidates'][0]['host_receipt_sha256'] == original['receipt_sha256']
    assert receipt == original and receipt_hash(receipt) == original_hash
    assert 'calculation_field_units' not in receipt


def test_units_are_supplied_only_with_calculations_in_real_two_phase_requests():
    _, _, calls = run()
    draft, selection = [restore_model_input(json.loads(call['user_prompt'])) for call in calls]
    assert 'calculation_field_units' not in draft
    assert 'projection.targets' in selection['calculation_field_units']
    assert all(len(call['user_prompt'].encode()) <= 100000 for call in calls)


@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
def test_nested_holding_target_units_preserve_separate_assumptions_and_hashes(side):
    from live.scenario_calculated_review import SELECT_PROMPT
    from tests.test_scenario_calculated_review import holding, holding_wire
    ctx = holding(side)
    ctx.update(positions=[dict(price=83000, quantity=.04)],
               pending_entries=[dict(id='pending', price=83000, quantity=.06)])
    receipt = calculate_candidate(holding_wire(ctx), ctx, candidate_id='c0')
    original, digest = deepcopy(receipt), receipt_hash(receipt)
    restored = restore_model_input(_model_payload(dict(valid=True, as_of_ms=1000000), ctx, {},
        calculated_candidates=dict(candidates=[receipt])))
    result = restored['calculated_candidates']['candidates'][0]['projection']
    assert result['confirmed_remaining']['targets'][0]['quantity'] == .02
    assert result['confirmed_remaining']['targets'][0]['gross_pnl'] == 20
    assert result['hypothetical_all_fills']['targets'][0]['quantity'] == .05
    assert result['hypothetical_all_fills']['targets'][0]['gross_pnl'] == 50
    assert 'confirmed_remaining covers held lots' in SELECT_PROMPT
    assert 'hypothetical_all_fills additionally assumes pending/new fills' in SELECT_PROMPT
    assert 'Never add them' in SELECT_PROMPT
    assert restored['calculated_candidates']['candidates'][0]['projection'] == original['projection']
    assert restored['calculated_candidates']['candidates'][0]['host_receipt_sha256'] == original['receipt_sha256']
    assert receipt == original and receipt_hash(receipt) == digest


def test_both_phases_deliver_new_relations_and_keep_snapshot_and_receipts_exact():
    ctx = context()
    ctx.update(now=NOW / 1000, input_captured_at=NOW / 1000, account_captured_at=NOW / 1000)
    snap = dict(snapshot(), valid=True)
    original = deepcopy((snap, ctx))
    calls = []

    def generate(**request):
        calls.append(request)
        payload = restore_model_input(json.loads(request['user_prompt']))
        assert payload['market_snapshot'] == snap
        points = payload['contract_context']['ma_structure']['primary']['30m']['points']
        assert [p['phase'] for p in points] == ['CONFIRMED', 'FORMING']
        assert all((p['close_vs_ma10'], p['close_vs_ma35']) == ('BELOW', 'ABOVE') for p in points)
        if len(calls) == 1:
            candidate, wait = wire(ctx, 'LONG'), wire(ctx)
            candidate['expires_at'] = wait['expires_at'] = ctx['now'] + 100
            reply = dict(wait_proposal=wait, candidates=[candidate], no_candidate_reason='')
        else:
            assert payload['calculation_field_units']['projection.targets']['quantity'] == 'BTC'
            chosen = payload['calculated_candidates']['wait']
            reply = dict(selection_token=f"{chosen['id']}:{model_receipt_hash(chosen)}", rationale='fixture')
        return SimpleNamespace(text=json.dumps(reply))

    result = research_review(snap, ctx, response_contract(ctx), generate=generate,
                             record=lambda _: None, clock=lambda: ctx['now'])
    assert len(calls) == 2 and result['orders_submitted'] == 0
    assert (snap, ctx) == original
