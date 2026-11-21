"""Read-only phase-separated prices, not an entry classifier or trade gate."""
from copy import deepcopy

import pytest

from engine.scenario_ma_context import build_ma_structure_context
from live.scenario_calculated_review import receipt_hash
from live.scenario_entry_display import flat_entry_display
from tests.test_scenario_ma_context import NOW, point, snapshot


def progress_snapshot():
    result={'valid':True,'as_of_ms':NOW,'timeframes':{}}
    for frame,duration,closes in [('30m',1800000,[100.,102.,104.,106.]),('1h',3600000,[106.,104.,102.,100.])]:
        boundary=NOW//duration*duration
        bars=[{'open_time_ms':boundary-(4-i)*duration,'close':close} for i,close in enumerate(closes)]
        result['timeframes'][frame]={'status':'ok','confirmed':{'is_confirmed':True,'open_time_ms':bars[-1]['open_time_ms'],'atr14_risk_only':2.},'recent_confirmed_bars':bars}
    return result


def test_confirmed_progress_is_signed_per_frame_and_does_not_mutate_source():
    from live.scenario_entry_display import confirmed_three_bar_progress
    source=progress_snapshot();before=deepcopy(source);result=confirmed_three_bar_progress(source)
    assert result['30m']['signed_up_positive']==3.0 and result['1h']['signed_up_positive']==-3.0
    assert result['30m']['last_close_ms']<=source['as_of_ms']
    assert source==before


def test_confirmed_progress_exposes_recent_rapid_rise_without_blocking():
    from live.scenario_entry_display import confirmed_three_bar_progress
    source = progress_snapshot()
    for frame, first, last, atr in [('30m', 77072.7, 77632.9, 269.22344462705956),
                                    ('1h', 77039.9, 77395.6, 385.05579132416176)]:
        item = source['timeframes'][frame]
        item['recent_confirmed_bars'][0]['close'] = first
        item['recent_confirmed_bars'][-1]['close'] = last
        item['confirmed']['atr14_risk_only'] = atr
    result = confirmed_three_bar_progress(source)
    assert result['30m']['signed_up_positive'] == pytest.approx(2.081, abs=.001)
    assert result['1h']['signed_up_positive'] == pytest.approx(.924, abs=.001)


@pytest.mark.parametrize('fault',['atr_zero','atr_nan','gap','future','stale','wrong_confirmed','bad_close','bad_status'])
def test_confirmed_progress_keeps_invalid_evidence_unknown(fault):
    from live.scenario_entry_display import confirmed_three_bar_progress
    source=progress_snapshot();frame=source['timeframes']['30m'];bars=frame['recent_confirmed_bars']
    if fault=='atr_zero':frame['confirmed']['atr14_risk_only']=0
    elif fault=='atr_nan':frame['confirmed']['atr14_risk_only']=float('nan')
    elif fault=='gap':bars[-2]['open_time_ms']-=1800000
    elif fault=='future':bars[-1]['open_time_ms']+=1800000
    elif fault=='stale':
        for row in bars:row['open_time_ms']-=1800000
        frame['confirmed']['open_time_ms']=bars[-1]['open_time_ms']
    elif fault=='wrong_confirmed':frame['confirmed']['open_time_ms']-=1800000
    elif fault=='bad_close':bars[0]['close']=float('nan')
    else:frame['status']='unavailable'
    result=confirmed_three_bar_progress(source)
    assert result['30m'] is None and result['1h']['signed_up_positive']==-3.0


def structure():
    market = snapshot(before=point('30m', 2, close=100), after=point('30m', 1, close=110, confirmed=False))
    return build_ma_structure_context(market, {'now': NOW/1000, 'mark_price': 110})


def candidate(side='LONG'):
    c = dict(id='c0', eligible=True, effective_plan=dict(action='OPEN', side=side,
        entries=[dict(price=100)], take_profits=[dict(price=120), dict(price=130)], partial_stops=[]))
    c['receipt_sha256'] = receipt_hash(c)
    return c


def test_confirmed_tail_is_not_forming_close_and_source_is_unchanged():
    facts = structure()
    before = deepcopy(facts)
    result = flat_entry_display(facts)
    assert result['confirmed_close_tail']['30m'] == [[facts['primary']['30m']['points'][0]['as_of_ms'], 100]]
    assert result['confirmed_close_tail']['1h'] is None
    assert facts == before


@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
def test_candidate_price_order_ties_all_targets_refs_and_receipt_binding(side):
    facts = structure()
    facts['levels'] = {'1d.ma35.confirmed': {'price': 120}, '12h.ma35.forming': {'price': 125}}
    c = candidate(side)
    before = deepcopy((facts, c))
    result = flat_entry_display(facts, {'candidates': [c]})
    assert result['candidate_price_order'] == [['c0', c['receipt_sha256'], 'AVAILABLE', [
        [100, ['E0']], [120, ['T0', 'M:1d.ma35.confirmed']],
        [125, ['M:12h.ma35.forming']], [130, ['T1']]]]]
    assert (facts, c) == before


@pytest.mark.parametrize('fault', ['receipt', 'value', 'id'])
def test_tampered_receipt_does_not_create_false_order_or_block(fault):
    c = candidate()
    if fault == 'receipt':
        c['receipt_sha256'] = '0'*64
    elif fault == 'value':
        c['effective_plan']['entries'][0]['price'] = 1
    else:
        c['id'] = 'c1'
    display = flat_entry_display(structure(), {'candidates': [c]})
    assert display['candidate_price_order'][0][2:] == ['UNAVAILABLE', None]
    assert display['candidate_price_order_status'] == 'PARTIAL_UNAVAILABLE'


def test_bounds_and_no_tp_are_explicit_not_silent_omissions():
    c = candidate()
    c['effective_plan']['take_profits'] = []
    c['receipt_sha256'] = receipt_hash({k:v for k,v in c.items() if k != 'receipt_sha256'})
    assert flat_entry_display(structure(), {'candidates':[c]})['candidate_price_order'][0][3] == [[100,['E0']]]
    assert flat_entry_display(structure(), {'candidates':[c,c,c]})['candidate_price_order_status'] == 'UNAVAILABLE_BOUND'


@pytest.mark.parametrize('fault', ['future', 'stale', 'nonconsecutive'])
def test_invalid_time_source_is_not_relabelled_confirmed(fault):
    market = snapshot()
    path = market['timeframes']['30m']['ma_path']
    if fault == 'future':
        path['confirmed_points'][0]['open_time_ms'] = NOW + 10000000
    elif fault == 'stale':
        path['confirmed_points'] = [point('30m', 4)]
    else:
        path['confirmed_points'] = [point('30m', 4), point('30m', 2)]
    facts = build_ma_structure_context(market, {'now': NOW/1000, 'mark_price':100})
    assert flat_entry_display(facts)['confirmed_close_tail']['30m'] is None


def test_display_helper_is_in_policy_binding_and_loaded_source_parity():
    from live.scenario_provenance import review_policy_binding, _manifest
    assert 'live/scenario_entry_display.py' in review_policy_binding()['source_sha256']
    assert 'live/scenario_entry_display.py' in str(_manifest())


def test_multiple_entries_and_tp_quantities_do_not_modify_price_order():
    c = candidate('SHORT')
    c['effective_plan']['entries'] = [dict(price=120, quantity=.001), dict(price=100, quantity=.009)]
    c['effective_plan']['take_profits'] = [dict(price=100, fraction=0), dict(price=90, fraction=.3)]
    c['receipt_sha256'] = receipt_hash({k:v for k,v in c.items() if k != 'receipt_sha256'})
    before = deepcopy(c)
    order = flat_entry_display(structure(), {'candidates':[c]})['candidate_price_order'][0][3]
    assert order == [[90,['T1']], [100,['E1','T0']], [120,['E0']]]
    assert c == before


def test_bounded_missing_or_nonfinite_candidate_data_is_unavailable():
    c = candidate()
    c['effective_plan']['entries'] *= 21
    c['receipt_sha256'] = receipt_hash({k:v for k,v in c.items() if k != 'receipt_sha256'})
    assert flat_entry_display(structure(), {'candidates':[c]})['candidate_price_order'][0][2] == 'UNAVAILABLE'
    c['effective_plan']['entries'][0]['price'] = float('nan')
    assert flat_entry_display(structure(), {'candidates':[c]})['candidate_price_order'][0][2] == 'UNAVAILABLE'


def test_display_preserves_baseline_history_selection_and_all_other_logical_fields():
    from live.scenario_entry_display import project_entry_display_input
    from live.scenario_model_input import project_model_input, restore_model_input
    from live.scenario_position_flow import project_position_flow_input
    from tests.test_scenario_model_input import encoded
    from tests.test_scenario_model_input_gaps import gap_payload
    source = gap_payload()
    source['synthetic_size_padding'] = ''
    source['contract_context']['position_flow'] = {'version': 1, 'status': 'AVAILABLE', 'coverage': {},
        'historical_observation_changes': [{'note': 'x' * 2000}]}
    initial, _ = project_model_input(source)
    source['synthetic_size_padding'] = 'x' * (100500 - len(encoded(initial)))
    before = deepcopy(source)
    baseline, _ = project_position_flow_input(source)
    current, _ = project_entry_display_input(source)
    restored = restore_model_input(current)
    assert restored['contract_context'].pop('entry_display')['confirmed_close_tail']['1h'] is None
    assert restored == restore_model_input(baseline)
    assert source == before


def test_nonflat_wrapper_returns_identical_wire_and_audit():
    from live.scenario_entry_display import project_entry_display_input
    from live.scenario_position_flow import project_position_flow_input
    source = {'market_snapshot': {}, 'contract_context': {'scenario_id': 'active'}}
    assert project_entry_display_input(source) == project_position_flow_input(source)


def test_progress_does_not_evict_existing_entry_display_at_budget(monkeypatch):
    from live import scenario_position_flow
    from live.scenario_entry_display import project_entry_display_input
    from live.scenario_model_input import project_model_input, restore_model_input
    from live.scenario_position_flow import project_position_flow_input

    source = {'market_snapshot': progress_snapshot(),
              'contract_context': {'scenario_id': None, 'ma_structure': structure()}}
    projected, _ = project_position_flow_input(source)
    logical = restore_model_input(projected)
    logical['contract_context']['entry_display'] = flat_entry_display(logical['contract_context']['ma_structure'])
    baseline, _ = project_model_input(logical, allow_gap_rescue=True)
    assert scenario_position_flow._size(baseline) > scenario_position_flow._size(projected)
    monkeypatch.setattr(scenario_position_flow, '_MODEL_INPUT_LIMIT', scenario_position_flow._size(baseline))

    result, _ = project_entry_display_input(source)
    assert restore_model_input(result) == restore_model_input(baseline)


def test_optional_display_never_blocks_exact_cap_already_v7_baseline():
    from live.scenario_entry_display import project_entry_display_input
    from live.scenario_position_flow import project_position_flow_input
    from tests.test_scenario_model_input import encoded
    from tests.test_scenario_model_input_gaps import gap_payload
    source = gap_payload()
    baseline, _ = project_position_flow_input(source)
    source['synthetic_size_padding'] = 'x' * (len(source['synthetic_size_padding']) + 100000 - len(encoded(baseline)))
    baseline, audit = project_position_flow_input(source)
    assert len(encoded(baseline)) == 100000
    assert baseline['model_input_projection']['version'] == 7
    result, result_audit = project_entry_display_input(source)
    assert encoded(result) == encoded(baseline)
    assert result_audit == audit


def test_display_does_not_raise_existing_cap_or_hide_oversized_baseline():
    from live.scenario_entry_display import project_entry_display_input
    from live.scenario_position_flow import project_position_flow_input
    from tests.test_scenario_model_input import encoded
    from tests.test_scenario_model_input_gaps import gap_payload
    source = gap_payload()
    source['synthetic_size_padding'] = 'x' * 110000
    assert len(encoded(project_position_flow_input(source)[0])) > 100000
    assert len(encoded(project_entry_display_input(source)[0])) > 100000


def test_progress_extreme_numeric_input_is_unknown_not_crash():
    from live.scenario_entry_display import confirmed_three_bar_progress
    source=progress_snapshot();source['timeframes']['30m']['recent_confirmed_bars'][0]['close']=10**400
    values=confirmed_three_bar_progress(source)
    assert values['30m'] is None and values['1h']['signed_up_positive']==-3.0
