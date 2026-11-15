"""Timeframe emphasis is explicit, never a fabricated probability or risk gate."""
from copy import deepcopy

from engine.scenario_ma_context import build_ma_structure_context
from live.scenario_calculated_review import _model_payload
from live.scenario_llm import MA_STRUCTURE_PROMPT, SYSTEM_PROMPT
from live.scenario_model_input import restore_model_input
from tests.test_scenario_ma_context import NOW, snapshot
from tests.test_scenario_prompt_logic import context


def test_priority_and_missing_frames_are_explicit_in_model_payload():
    snap = snapshot()
    for frame in ('1h', '4h', '12h', '1d', '1w'):
        snap['timeframes'].update(snapshot(frame)['timeframes'])
    original = deepcopy(snap)
    ctx = dict(context(), now=NOW/1000, mark_price=100)
    data = restore_model_input(_model_payload(snap, ctx, {}))['contract_context']['ma_structure']
    assert data['decision_priority'] == dict(
        descending=['1h', '30m', '4h', '12h', '1d', '1w'],
        timing_only=[], interpretation='QUALITATIVE_EMPHASIS_NOT_PROBABILITY_OR_VETO')
    assert '15m' not in data['primary']
    for frame in ('4h', '12h', '1d', '1w'):
        higher = data['higher_frames'][frame]
        assert higher['status'] == 'available'
        assert higher['latest_structure']['ma_order'] == 'BULLISH'
        assert higher['latest_structure']['price_position'] == 'BETWEEN'
        assert higher['latest_structure']['is_confirmed'] is False
    assert snap == original


def test_missing_higher_frames_not_invented_from_priority():
    data = build_ma_structure_context(snapshot(), dict(now=NOW/1000, mark_price=100))
    for frame in ('4h', '12h', '1d', '1w'):
        assert data['higher_frames'][frame]['latest_structure'] is None
        assert data['higher_frames'][frame]['status'] == 'unavailable'




def test_missing_hourly_frame_remains_unknown_despite_highest_priority():
    data = build_ma_structure_context(snapshot(), dict(now=NOW/1000, mark_price=100))
    assert data['decision_priority']['descending'][0] == '1h'
    assert data['primary']['1h']['status'] == 'unavailable'
    assert data['primary']['1h']['points'] == []
    assert data['primary']['30m']['status'] == 'available'
