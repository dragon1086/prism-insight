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



def test_holding_reviews_run_between_entry_samples_only_while_filled(tmp_path):
    from analysis.scenario_replay import run_replay,network_boundary
    from tests.test_scenario_replay import inputs,wait_policy
    bundle,market=inputs(1200000);start=bundle['start_ms'];seen=[]
    entries=[start,start+900000];management=[start+300000,start+600000]
    def policy(snapshot,context):
        seen.append(int(context['now']*1000)//300000*300000)
        base=dict(wait_policy(snapshot,context),scenario_id=context['scenario_id']or'held-fixture',
                  action_id='a-'+str(context['now']),expires_at=context['now']+600)
        if context['scenario_id']:return dict(base,action='EXIT')
        if len(seen)>1:return base
        return dict(base,action='OPEN',side='LONG',leverage=10,confidence=.5,hard_stop=98,
                    entries=[dict(id='e1',price=100,quantity=.1)],take_profits=[],partial_stops=[],
                    chase=dict(max_bps=0,max_reprices=0))
    kwargs=dict(decision_slots_ms=entries,holding_review_slots_ms=management)
    with network_boundary('frozen')as denied:
        first=run_replay(bundle,market,tmp_path/'record',mode='fixture',tape_path=tmp_path/'tape',policy=policy,**kwargs)
        frozen=run_replay(bundle,market,tmp_path/'frozen',mode='frozen',tape_path=tmp_path/'tape',**kwargs)
    assert not denied and seen==[start,start+300000,start+900000]
    schedule=first['conditional_holding_schedule']
    assert schedule['executed_holding_slots_ms']==[start+300000]
    assert schedule['skipped_flat_holding_slots_ms']==[start+600000]
    assert first['economic']['completed_scenarios']==1 and first['economic']['open_quantity']==0
    assert first['result_hash']==frozen['result_hash']


@pytest.mark.parametrize('holding',[[],[True],[1],[0,0]])
def test_holding_schedule_requires_valid_registered_entry_schedule(holding):
    from analysis.scenario_replay import contract_for
    from tests.test_scenario_replay import inputs
    bundle,_=inputs()
    with pytest.raises(ValueError):
        contract_for(bundle,'OHLC',1,10000,holding_review_slots_ms=holding)



def test_flat_conditional_slots_are_not_missing_model_reviews(tmp_path):
    bundle,market=inputs(900000);start=bundle['start_ms'];tape=tmp_path/'tape'
    kwargs=dict(policy_profile='single-pass-economic-v4',decision_slots_ms=[start,start+600000],
                holding_review_slots_ms=[start+300000])
    with network_boundary('frozen')as denied:
        recorded=run_replay(bundle,market,tmp_path/'record',mode='fixture',tape_path=tape,
                            policy=wait_policy,clock_monotonic=lambda:0.,**kwargs)
        frozen=run_replay(bundle,market,tmp_path/'frozen',mode='frozen',tape_path=tape,**kwargs)
    assert not denied and recorded['result_hash']==frozen['result_hash']
    assert recorded['coverage']['processed_slots']==2
    assert recorded['coverage']['slots_without_model_review']==[]
    assert recorded['coverage']['eligible'] is True
    schedule=recorded['conditional_holding_schedule']
    assert schedule['skipped_flat_holding_slots_ms']==[start+300000]
    assert schedule['unprocessed_candidate_slots_ms']==[]
    assert schedule['full_live_review_cadence'] is False


@pytest.mark.parametrize('damage',['empty','boolean','unaligned','duplicate','outside'])
def test_invalid_holding_slots_with_valid_entries_are_rejected(damage):
    bundle,_=inputs();start=bundle['start_ms']
    bad={'empty':[],'boolean':[True],'unaligned':[start+1],
         'duplicate':[start,start],'outside':[bundle['end_ms']]}[damage]
    with pytest.raises(ValueError,match='invalid_conditional_holding_schedule'):
        contract_for(bundle,'OHLC',1,10000,decision_slots_ms=[start],holding_review_slots_ms=bad)
