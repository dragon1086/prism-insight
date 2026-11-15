"""Incident level facts and reference separation; not proof of model comprehension."""
from copy import deepcopy
import json
from types import SimpleNamespace

import pytest

from engine.scenario_ma_context import build_ma_structure_context
from live.scenario_calculated_review import research_review
from live.scenario_llm import MA_STRUCTURE_PROMPT
from live.scenario_model_input import project_model_input, restore_model_input
from live.scenario_preview import response_contract
from tests.test_scenario_calculated_review import context, wire
from tests.test_scenario_ma_context import point, snapshot, NOW
from tests.test_scenario_review_guard import model_receipt_hash,model_intent


MARK = 81911.6
INCIDENT_LEVELS = {'4h.ma10.confirmed': 81857.85, '4h.ma10.forming': 81904.7,
                   '12h.ma10.confirmed': 81836.77, '12h.ma10.forming': 81864.07}


def incident_snapshot():
    # Price/time/MA subset from slot5974730, not reconstructed market history.
    frames = {}
    for frame, start, high, low, slow, forming_slow, observed in (
        ('4h', 1792396800000, 83069.4, 82081.0, 81998.70857142856, 81960.56, 1792419005568),
        ('12h', 1792368000000, 83155.0, 81962.4, 83388.21999999999, 83260.49714285714, 1792419005659),
    ):
        confirmed = dict(open_time_ms=start, close_time_ms=1792411200000,
            open=82684.2 if frame == '4h' else 82065.9,
            close=82320.7, high=high, low=low, ma10=INCIDENT_LEVELS[frame + '.ma10.confirmed'],
            ma35=slow, is_confirmed=True)
        forming = dict(open_time_ms=1792411200000, observed_at_ms=observed,
            open=82320.7, close=81915.6, high=82735.2, low=81715.8,
            ma10=INCIDENT_LEVELS[frame + '.ma10.forming'], ma35=forming_slow, is_confirmed=False)
        frames[frame] = dict(ma_path=dict(version=1, confirmed_points=[confirmed], forming_point=forming))
    return dict(valid=True, as_of_ms=1792419005844, timeframes=frames)


def test_incident_wire_preserves_four_below_current_but_above_planned_entry_levels():
    snap = incident_snapshot()
    facts = build_ma_structure_context(snap, dict(now=1792419006.002721, mark_price=MARK))
    payload = dict(market_snapshot=snap, contract_context=dict(ma_structure=facts))
    projected, _ = project_model_input(payload)
    assert restore_model_input(projected) == payload
    table = projected['contract_context']['ma_structure']['levels']['$row_table']
    rows = {key: dict(zip(table['columns'], row)) for key, row in zip(table['keys'], table['rows'])}
    assert facts['reference']['price'] == MARK
    for key, price in INCIDENT_LEVELS.items():
        assert rows[key]['price'] == price
        assert rows[key]['relative_to_mark'] == 'BELOW'
        assert rows[key]['distance_price'] == pytest.approx(price - MARK)
        assert key in facts['short_downward_obstacles']
        assert 81690 < 81700 < price < MARK < 81915.6


@pytest.mark.parametrize('mark,entry,expected,bucket', [
    (100, 90, 'BELOW', 'short_downward_obstacles'),
    (90, 100, 'ABOVE', 'long_upward_obstacles'),
    (95, 100, 'EQUAL', 'equal_reference_levels'),
])
def test_level_side_is_current_mark_not_hypothetical_entry(mark, entry, expected, bucket):
    snap = snapshot('4h', before=point('4h', 2, fast=95),
                    after=point('4h', 1, fast=95, confirmed=False))
    facts = build_ma_structure_context(snap, dict(now=NOW / 1000, mark_price=mark))
    for phase in ('confirmed', 'forming'):
        key = '4h.ma10.' + phase
        assert facts['levels'][key]['relative_to_mark'] == expected
        assert facts['levels'][key]['distance_price'] == 95 - mark
        assert key in facts[bucket]
    assert (95 - mark) != (95 - entry)


@pytest.mark.parametrize('mark', [None, 0, float('nan')])
def test_missing_current_mark_does_not_use_proposed_entry_as_reference(mark):
    facts = build_ma_structure_context(snapshot('4h'), dict(now=NOW / 1000,
        mark_price=mark, pending_entries=[dict(price=90, quantity=.001)]))
    assert facts['reference'] is None
    assert all('relative_to_mark' not in row and 'distance_price' not in row
               for row in facts['levels'].values())


def test_missing_levels_and_confirmed_forming_straddle_are_not_collapsed():
    snap = snapshot('4h', before=point('4h', 2, fast=105),
                    after=point('4h', 1, fast=95, confirmed=False))
    facts = build_ma_structure_context(snap, dict(now=NOW / 1000, mark_price=100))
    assert facts['levels']['4h.ma10.confirmed']['relative_to_mark'] == 'ABOVE'
    assert facts['levels']['4h.ma10.forming']['relative_to_mark'] == 'BELOW'
    assert facts['higher_frames']['12h']['status'] == 'unavailable'
    assert not any(key.startswith('12h.') for key in facts['levels'])


def test_both_review_requests_keep_current_facts_and_unmodified_bad_candidate_prose():
    ctx = context()
    ctx.update(now=1792419006.002721, input_captured_at=1792419005.271,
               account_captured_at=1792419004.966, mark_price=MARK, conditional_entry_version=1)
    snap = incident_snapshot()
    original = deepcopy((snap, ctx))
    calls = []
    bad_reason = '4h/12h MA10 are above current price and already passed'  # Incident error, not a fact.

    def generate(**request):
        calls.append(request)
        assert MA_STRUCTURE_PROMPT in request['system_prompt']
        assert len(request['user_prompt'].encode()) <= 100000
        data = restore_model_input(json.loads(request['user_prompt']))
        facts = data['contract_context']['ma_structure']
        assert facts['reference']['price'] == MARK
        for key, price in INCIDENT_LEVELS.items():
            assert facts['levels'][key]['price'] == price
            assert facts['levels'][key]['relative_to_mark'] == 'BELOW'
        if len(calls) == 1:
            candidate, wait = wire(ctx, 'SHORT'), wire(ctx)
            candidate.update(rationale=bad_reason, hard_stop=82750,
                entries=[dict(id='e', price=81690, quantity=.001, trigger_price=81700)],
                take_profits=[dict(id='tp', price=81500, fraction=1)])
            candidate['expires_at'] = wait['expires_at'] = ctx['now'] + 100
            reply = dict(wait_proposal=wait, candidates=[candidate], no_candidate_reason='')
        else:
            bundle = data['calculated_candidates']
            assert model_intent(bundle['candidates'][0])['rationale'] == bad_reason
            chosen = bundle['wait']
            reply = dict(selection_token=f"{chosen['id']}:{model_receipt_hash(chosen)}", rationale='fixture WAIT')
        return SimpleNamespace(text=json.dumps(reply))

    result = research_review(snap, ctx, response_contract(ctx), generate=generate,
                             record=lambda _: None, clock=lambda: ctx['now'])
    assert len(calls) == 2 and result['selected']['raw_proposal']['action'] == 'WAIT'
    assert result['orders_submitted'] == 0 and result['executable'] is False
    assert (snap, ctx) == original
