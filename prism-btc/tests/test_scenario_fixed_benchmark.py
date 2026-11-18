"""The retained sampled benchmark must not change the default replay contract."""
from copy import deepcopy
import gzip

import pytest

from analysis.scenario_calculated_replay import read_tape_text
from analysis.scenario_replay import contract_for, run_replay, network_boundary
from tests.test_scenario_replay import inputs, wait_policy


@pytest.mark.parametrize('damage', ['empty', 'duplicate', 'unsorted', 'unaligned', 'outside', 'boolean'])
def test_invalid_sample_schedule_is_rejected(damage):
    bundle, _ = inputs()
    start = bundle['start_ms']
    slots = {'empty': [], 'duplicate': [start, start],
             'unsorted': [start+300000, start], 'unaligned': [start+1],
             'outside': [bundle['end_ms']], 'boolean': [True]}[damage]
    with pytest.raises(ValueError, match='invalid_sampled_decision_schedule'):
        contract_for(bundle, 'OHLC', 1, 10000, decision_slots_ms=slots)


def test_sample_schedule_is_frozen_and_does_not_change_default_contract():
    bundle, _ = inputs()
    before = deepcopy(bundle)
    slots = [bundle['start_ms']]
    default = contract_for(bundle, 'OHLC', 1, 10000)
    sampled = contract_for(bundle, 'OHLC', 1, 10000, decision_slots_ms=slots)
    assert 'sampled_model_decision_slots_ms' not in default
    slots.append(bundle['start_ms']+300000)
    assert sampled['sampled_model_decision_slots_ms'] == [bundle['start_ms']]
    assert sampled['between_model_samples'].startswith('NATIVE_PROTECTION')
    assert bundle == before


@pytest.mark.parametrize('filename', ['sample.jsonl', 'plain-name.gz'])
def test_sampled_runtime_preserves_full_native_period_and_exact_replay(tmp_path,filename):
    bundle, market = inputs(900000)
    slots = [bundle['start_ms'], bundle['start_ms']+600000]
    tape = tmp_path/filename
    with network_boundary('frozen') as denied:
        recorded = run_replay(bundle, market, tmp_path/'recorded', mode='fixture',
            tape_path=tape, policy=wait_policy, decision_slots_ms=slots)
        replayed = run_replay(bundle, market, tmp_path/'replayed', mode='frozen',
            tape_path=tape, decision_slots_ms=slots)
    assert not denied
    assert recorded['model_decisions'] == 2
    assert recorded['contract']['end_ms'] == bundle['end_ms']
    assert recorded['result_hash'] == replayed['result_hash']


def test_compressed_and_plain_tapes_preserve_exact_text(tmp_path):
    text = '{"kind":"header"}\n{"kind":"tick"}\n'
    plain, packed = tmp_path/'tape', tmp_path/'tape.gz'
    plain.write_text(text)
    with gzip.open(packed, 'wt', encoding='utf-8') as stream:
        stream.write(text)
    assert read_tape_text(plain) == read_tape_text(packed) == text


def test_window_schedule_rechecks_the_entire_window_and_keeps_warmup():
    from analysis.scenario_replay import window_decision_slots
    bundle = dict(start_ms=0, end_ms=10800000)
    windows = [dict(start_ms=1800000,end_ms=5400000),
               dict(start_ms=7200000,end_ms=9000000)]
    before = deepcopy(windows)
    assert window_decision_slots(bundle,windows) == [1500000,1800000,3600000,5400000,6900000,7200000,9000000]
    assert windows == before


@pytest.mark.parametrize('windows', [[],[dict(start_ms=True,end_ms=1800000)],
    [dict(start_ms=0,end_ms=1800000)],[dict(start_ms=1800000,end_ms=1800000)],
    [dict(start_ms=1800001,end_ms=3600001)],[dict(start_ms=1800000,end_ms=3900000)],
    [dict(start_ms=1800000,end_ms=10800000)],None])
def test_invalid_window_schedule_is_rejected(windows):
    from analysis.scenario_replay import window_decision_slots
    with pytest.raises(ValueError,match='invalid_review_windows'):
        window_decision_slots(dict(start_ms=0,end_ms=10800000),windows)


def test_sampled_economic_coverage_never_claims_full_period_model_evaluation(tmp_path):
    bundle, market = inputs(900000)
    slots = [bundle['start_ms'], bundle['start_ms']+600000]
    tape=tmp_path/'economic.tape'
    with network_boundary('frozen')as denied:
        recorded=run_replay(bundle,market,tmp_path/'recorded',mode='fixture',tape_path=tape,
            policy_profile='single-pass-economic-v4',policy=wait_policy,
            decision_slots_ms=slots,clock_monotonic=lambda:0.)
        frozen=run_replay(bundle,market,tmp_path/'frozen',mode='frozen',tape_path=tape,
            policy_profile='single-pass-economic-v4',decision_slots_ms=slots)
    assert not denied
    c=recorded['coverage']
    assert c['review_scope']=='REGISTERED_SAMPLED_SCHEDULE'
    assert c['full_period_5m_slots']==3 and c['model_review_records']==2
    assert c['slots_without_model_review']==[]
    assert not c['full_period_model_record_coverage']
    assert recorded['result_hash']==frozen['result_hash']
