"""The30m judgment boundary also applies to optional tick-speed summaries."""
from copy import deepcopy

import pytest

from live.scenario_analysis_frames import analysis_model_view


@pytest.mark.parametrize('side', ['LONG', 'SHORT', None])
def test_sub30_mark_speed_cannot_change_model_view_or_mutate_raw(side):
    raw = {'market_snapshot': {'timeframes': {}}, 'contract_context': {
        'side': side, 'mark_price': 100., 'position_flow': {
            'mark_pace': {'interval_seconds': 300., 'favorable_atr30_per_5m': -99.},
            'status': 'AVAILABLE', 'requested_changes': [{'action': 'REDUCE'}]},
        'positions': [{'quantity': 1.}] if side else [], 'protection_ok': True}}
    before = deepcopy(raw)
    altered = deepcopy(raw)
    altered['contract_context']['position_flow']['mark_pace'] = {
        'interval_seconds': 60., 'favorable_atr30_per_5m': 999999.}
    view = analysis_model_view(raw)
    assert view == analysis_model_view(altered)
    assert raw == before
    ctx = view['contract_context']
    assert 'mark_pace' not in ctx['position_flow']
    assert ctx['position_flow']['requested_changes'] == [{'action': 'REDUCE'}]
    assert ctx['mark_price'] == 100. and ctx['protection_ok'] is True
    del altered['contract_context']['position_flow']['mark_pace']
    assert view == analysis_model_view(altered)


@pytest.mark.parametrize('flow', [None, {}, {'status': 'UNAVAILABLE'}])
def test_missing_optional_speed_is_not_fabricated(flow):
    raw = {'market_snapshot': {}, 'contract_context': {'position_flow': flow}}
    assert analysis_model_view(raw)['contract_context']['position_flow'] == flow
