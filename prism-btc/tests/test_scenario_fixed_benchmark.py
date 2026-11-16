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
