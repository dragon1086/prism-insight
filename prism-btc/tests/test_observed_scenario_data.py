"""Observed-input release and account-isolation contract; offline fixtures only."""
from copy import deepcopy
import hashlib
import json

import pytest

from backtest.observed_scenario_data import ObservedScenarioData, ObservedInputGap
from tests.test_scenario_snapshot import snapshot as market_snapshot, NOW


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def packet(count=2):
    start = NOW // 300000 * 300000
    rows = []
    for index in range(count):
        stamp = start + index*300000
        market = market_snapshot(now=stamp+6000)
        rows.append(dict(slot=stamp//300000, status='AVAILABLE', available_at_ms=stamp+10000,
            source_context_now=(stamp+7000)/1000, snapshot=market, snapshot_hash=digest(market),
            source_evidence=dict(audit_event_ref='1'*64, audit_body_sha256='2'*64,
                context_hash='3'*64, manifest_ref='4'*64, source_git_revision='a'*40,
                availability_basis='decision_input_audit_observed_at', request_market_verified=True),
            quote_evidence=dict(mark_price=100., observed_at_ms=stamp+6500, source='retained_context_mark')))
    return dict(schema_version=1, kind='OBSERVED_MARKET_SNAPSHOTS', symbol='BTCUSDT', start_ms=start,
                end_ms=start+count*300000, source={'read_only':True, 'metadata_hashes':{
                    'source_identity':'5'*64,'exporter_code':'6'*64,'manifest_set':'7'*64}},
                rows=rows, rows_sha256=digest(rows))


def write_packet(tmp_path, data):
    path = tmp_path / 'observed.json'
    path.write_text(json.dumps(data, allow_nan=False))
    return path, hashlib.sha256(path.read_bytes()).hexdigest()


def reseal(data):
    for row in data['rows']:
        if 'snapshot' in row:
            row['snapshot_hash'] = digest(row['snapshot'])
    data['rows_sha256'] = digest(data['rows'])
    return data


def test_exact_release_copy_and_hybrid_manifest(tmp_path):
    data=packet()
    path,pin=write_packet(tmp_path,data)
    provider=ObservedScenarioData(path,pin)
    schedule=provider.schedule(data['start_ms'],data['end_ms'])
    assert schedule == [{'slot':r['slot'],'available_at_ms':r['available_at_ms'],'status':'AVAILABLE'} for r in data['rows']]
    at=schedule[0]['available_at_ms']
    with pytest.raises(ValueError):
        provider.snapshot(at-1)
    returned=provider.snapshot(at)
    assert returned == data['rows'][0]['snapshot']
    returned['timeframes'].clear()
    assert provider.snapshot(at) == data['rows'][0]['snapshot']
    assert provider.manifest()['execution_basis'] == 'HYBRID_OBSERVED_INPUT_ASSUMED_EXECUTION'
    assert provider.manifest()['input_coverage_eligible'] is True
    assert provider.manifest()['profitability_proven'] is False
    assert provider.manifest()['market_truth_verified'] is False


@pytest.mark.parametrize('fault', ['file_hash','rows_hash','snapshot_hash','duplicate','missing',
    'out_of_order','early_release','wrong_slot','future_forming','stale_primary','future_confirmed',
    'account','proposal','nested_secret','unknown_row','bool_slot','nan_context','invalid_snapshot'])
def test_mutants_rejected_before_simulation(tmp_path,fault):
    data=packet()
    row=data['rows'][0]
    if fault=='rows_hash': data['rows_sha256']='0'*64
    elif fault=='snapshot_hash': row['snapshot_hash']='0'*64
    elif fault=='duplicate': data['rows'][1]=deepcopy(row)
    elif fault=='missing': data['rows'].pop()
    elif fault=='out_of_order': data['rows'].reverse()
    elif fault=='early_release': row['available_at_ms']-=4000
    elif fault=='wrong_slot': row['slot']+=1
    elif fault=='future_forming': row['snapshot']['timeframes']['4h']['forming']['observed_at_ms']=row['available_at_ms']+1
    elif fault=='stale_primary': row['snapshot']['timeframes']['15m']['forming']['observed_at_ms']=row['available_at_ms']-120001
    elif fault=='future_confirmed': row['snapshot']['timeframes']['30m']['confirmed']['close_time_ms']=row['available_at_ms']+1
    elif fault in ('account','proposal'): row[fault]={'equity':10000}
    elif fault=='nested_secret': row['snapshot']['timeframes']['15m']['forming']['api_key']='private'
    elif fault=='unknown_row': row['unknown']='not allowed'
    elif fault=='bool_slot': row['slot']=True
    elif fault=='nan_context': row['source_context_now']=None
    elif fault=='invalid_snapshot': row['snapshot']['valid']=False
    if fault not in ('rows_hash','snapshot_hash'):
        reseal(data)
    else:
        if fault=='snapshot_hash': data['rows_sha256']=digest(data['rows'])
    path,pin=write_packet(tmp_path,data)
    with pytest.raises(ValueError):
        ObservedScenarioData(path,'0'*64 if fault=='file_hash' else pin)


def test_gap_is_explicit_and_never_falls_back(tmp_path):
    data=packet()
    slot=data['rows'][1]['slot']
    data['rows'][1]={'slot':slot,'status':'MISSING','reason':'NO_RETAINED_MARKET_INPUT'}
    reseal(data)
    path,pin=write_packet(tmp_path,data)
    with pytest.raises(ObservedInputGap): ObservedScenarioData(path,pin)
    provider=ObservedScenarioData(path,pin,allow_gaps=True)
    assert provider.manifest()['input_coverage_eligible'] is False
    assert provider.schedule()[1] == {'slot':slot,'status':'MISSING','available_at_ms':slot*300000}
    with pytest.raises(ObservedInputGap): provider.snapshot(slot*300000+10000)
    with pytest.raises(ValueError): provider.snapshot(data['end_ms'])


def test_schedule_requires_aligned_contained_range_and_preserves_provider(tmp_path):
    data=packet()
    provider=ObservedScenarioData(*write_packet(tmp_path,data))
    for start,end in [(data['start_ms']+1,data['end_ms']), (data['start_ms']-300000,data['end_ms']),
                      (data['start_ms'],data['end_ms']+300000), (True,data['end_ms'])]:
        with pytest.raises(ValueError): provider.schedule(start,end)
    rows=provider.schedule(); rows[0]['available_at_ms']=0
    assert provider.schedule()[0]['available_at_ms']>0


@pytest.mark.parametrize('fault', ['bool_schema','extra_top','extra_source','source_not_ro',
    'bad_source_hash','bad_audit_ref','unknown_revision','false_request_verified',
    'quote_bool','quote_future','quote_wrong_source','available_bool','context_bool',
    'snapshot_future','missing_primary','primary_bool_time','unknown_timeframe','balance'])
def test_strict_packet_provenance_and_privacy_shapes(tmp_path,fault):
    data=packet()
    row=data['rows'][0]
    if fault=='bool_schema': data['schema_version']=True
    elif fault=='extra_top': data['context']={}
    elif fault=='extra_source': data['source']['account_id']='private'
    elif fault=='source_not_ro': data['source']['read_only']=False
    elif fault=='bad_source_hash': data['source']['metadata_hashes']['source_identity']='unknown'
    elif fault=='bad_audit_ref': row['source_evidence']['audit_event_ref']='uuid-not-hash'
    elif fault=='unknown_revision': row['source_evidence']['source_git_revision']='not-a-revision'
    elif fault=='false_request_verified': row['source_evidence']['request_market_verified']=False
    elif fault=='quote_bool': row['quote_evidence']['mark_price']=True
    elif fault=='quote_future': row['quote_evidence']['observed_at_ms']=row['available_at_ms']+1
    elif fault=='quote_wrong_source': row['quote_evidence']['source']='current_exchange_quote'
    elif fault=='available_bool': row['available_at_ms']=True
    elif fault=='context_bool': row['source_context_now']=True
    elif fault=='snapshot_future': row['snapshot']['as_of_ms']=row['available_at_ms']+1
    elif fault=='missing_primary': row['snapshot']['timeframes']['15m']['forming']=None
    elif fault=='primary_bool_time': row['snapshot']['timeframes']['15m']['forming']['observed_at_ms']=True
    elif fault=='unknown_timeframe': row['snapshot']['timeframes']['5m']={}
    else: row['snapshot']['timeframes']['1h']['forming']['balance']=1
    path,pin=write_packet(tmp_path,reseal(data))
    with pytest.raises(ValueError): ObservedScenarioData(path,pin)


@pytest.mark.parametrize('offset,accepted', [(120000,True),(120001,False)])
def test_primary_staleness_at_release_boundary(tmp_path,offset,accepted):
    data=packet()
    row=data['rows'][0]
    # Keep a genuine forming candle and its MA point in the same observation
    # period; shifting only observed_at across the candle boundary is invalid.
    row['available_at_ms']=row['slot']*300000+130000
    row['source_context_now']=(row['available_at_ms']-500)/1000
    row['snapshot']=market_snapshot(now=row['available_at_ms']-1000,
                                    observed=row['available_at_ms']-offset)
    args=write_packet(tmp_path,reseal(data))
    if accepted:
        provider=ObservedScenarioData(*args)
        assert provider.snapshot(row['available_at_ms'])==row['snapshot']
        with pytest.raises(ValueError,match='stale_at_access'):
            provider.snapshot(row['available_at_ms']+1)
    else:
        with pytest.raises(ValueError,match='primary_input_stale'): ObservedScenarioData(*args)


def test_duplicate_json_keys_and_oversized_slot_range_rejected(tmp_path):
    data=packet()
    path,pin=write_packet(tmp_path,data)
    raw=path.read_text().replace('"schema_version": 1', '"schema_version": 1, "schema_version": 1')
    path.write_text(raw)
    with pytest.raises(ValueError,match='duplicate_json_key'):
        ObservedScenarioData(path,hashlib.sha256(path.read_bytes()).hexdigest())
    data['end_ms']=data['start_ms']+289*300000
    with pytest.raises(ValueError,match='packet_range'):
        ObservedScenarioData(*write_packet(tmp_path,data))


def test_unknown_request_revision_are_not_invented_and_gap_mode_never_profit_eligible(tmp_path):
    data=packet()
    data['rows'][0]['source_evidence']['source_git_revision']=None
    data['rows'][0]['source_evidence']['request_market_verified']=None
    args=write_packet(tmp_path,reseal(data))
    provider=ObservedScenarioData(*args,allow_gaps=True)
    assert provider.manifest()['input_coverage_eligible'] is False
    assert provider.manifest()['market_truth_verified'] is False
    assert set(provider.snapshot(data['rows'][0]['available_at_ms']))=={'as_of_ms','valid','issues','timeframes'}


@pytest.mark.parametrize('field', ['context','equity','positions','current_plan'])
def test_hidden_private_context_never_leaves_provider(tmp_path,field):
    data=packet()
    data['rows'][0]['snapshot']['timeframes']['30m']['forming'][field]={'private':123}
    with pytest.raises(ValueError,match='private_snapshot_field'):
        ObservedScenarioData(*write_packet(tmp_path,reseal(data)))


def test_future_embedded_ma_path_observation_is_rejected(tmp_path):
    data=packet()
    row=data['rows'][0]
    row['snapshot']['timeframes']['30m']['ma_path']['forming_point']['observed_at_ms']=row['available_at_ms']+1
    with pytest.raises(ValueError,match='forming_candle_time_mismatch|future_embedded_observation'):
        ObservedScenarioData(*write_packet(tmp_path,reseal(data)))


@pytest.mark.parametrize('location', ['confirmed','recent_confirmed_bars','ma_confirmed'])
@pytest.mark.parametrize('fault', ['future','open_bool','off_grid','wrong_close','false_confirmed'])
def test_actual_confirmed_schema_derives_close_and_rejects_time_mutants(tmp_path,location,fault):
    from engine.scenario_snapshot import TIMEFRAME_MS, candle_start
    data=packet()
    frame=data['rows'][0]['snapshot']['timeframes']['30m']
    bar=(frame['confirmed'] if location=='confirmed' else frame['recent_confirmed_bars'][-1]
         if location=='recent_confirmed_bars' else frame['ma_path']['confirmed_points'][-1])
    if fault=='future':
        bar['open_time_ms']=candle_start(data['rows'][0]['snapshot']['as_of_ms'],TIMEFRAME_MS['30m'])
        bar.pop('close_time_ms',None)
    elif fault=='open_bool': bar['open_time_ms']=True
    elif fault=='off_grid': bar['open_time_ms']+=1
    elif fault=='wrong_close': bar['close_time_ms']=bar['open_time_ms']+TIMEFRAME_MS['30m']-1
    else: bar['is_confirmed']=False
    with pytest.raises(ValueError):ObservedScenarioData(*write_packet(tmp_path,reseal(data)))


@pytest.mark.parametrize('location', ['forming','ma_forming'])
@pytest.mark.parametrize('fault', ['previous_candle','open_bool','off_grid','confirmed'])
def test_forming_bar_belongs_to_observed_candle_and_is_unconfirmed(tmp_path,location,fault):
    from engine.scenario_snapshot import TIMEFRAME_MS
    data=packet()
    frame=data['rows'][0]['snapshot']['timeframes']['1h']
    bar=frame['forming'] if location=='forming' else frame['ma_path']['forming_point']
    if fault=='previous_candle': bar['open_time_ms']-=TIMEFRAME_MS['1h']
    elif fault=='open_bool': bar['open_time_ms']=False
    elif fault=='off_grid': bar['open_time_ms']+=1
    else: bar['is_confirmed']=True
    with pytest.raises(ValueError):ObservedScenarioData(*write_packet(tmp_path,reseal(data)))


def test_weekly_bar_requires_monday_alignment_not_unix_week_grid(tmp_path):
    data=packet()
    frame=data['rows'][0]['snapshot']['timeframes']['1w']
    original=frame['confirmed']['open_time_ms']
    assert ObservedScenarioData(*write_packet(tmp_path,data)).manifest()['available_slots']==2
    frame['confirmed']['open_time_ms']=original//604800000*604800000
    assert frame['confirmed']['open_time_ms']!=original
    with pytest.raises(ValueError,match='unaligned_bar_open'):
        ObservedScenarioData(*write_packet(tmp_path,reseal(data)))


def test_confirmed_bar_not_available_at_earlier_frame_observation(tmp_path):
    from engine.scenario_snapshot import TIMEFRAME_MS
    data=packet()
    row=data['rows'][0]
    frame=row['snapshot']['timeframes']['30m']
    # As-of is after the boundary, but this frame was observed before it.
    observed=frame['confirmed']['open_time_ms']+TIMEFRAME_MS['30m']-1
    for bar in (frame['forming'],frame['ma_path']['forming_point']):
        bar['observed_at_ms']=observed
        bar['open_time_ms']=frame['confirmed']['open_time_ms']
    with pytest.raises(ValueError,match='future_confirmed_bar'):
        ObservedScenarioData(*write_packet(tmp_path,reseal(data)))
