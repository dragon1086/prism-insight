"""Retired lower-frame facts never enter new model judgment inputs."""
from copy import deepcopy
import json

import pytest

from live.scenario_analysis_frames import analysis_model_view, allowed_evidence_path


def test_entry_extension_is_optional_signed_and_not_stored():
    frame = {'status': 'ok', 'confirmed': {'is_confirmed': True, 'ma10': 100., 'atr14_risk_only': 10.},
             'forming': {'ohlcv': {'close': 130.}}}
    raw = {'market_snapshot': {'valid': True, 'timeframes': {'30m': frame}}, 'contract_context': {}}
    before = deepcopy(raw)
    assert analysis_model_view(raw)['market_snapshot']['timeframes']['30m']['forming_last_vs_confirmed_ma10_atr'] == 3.
    assert raw == before
    raw['market_snapshot']['timeframes']['30m']['forming']['observation_kind'] = 'synthetic_boundary'
    assert 'forming_last_vs_confirmed_ma10_atr' not in analysis_model_view(raw)['market_snapshot']['timeframes']['30m']
    del raw['market_snapshot']['timeframes']['30m']['forming']['observation_kind']
    raw['market_snapshot']['timeframes']['30m']['forming']['ohlcv']['close'] = 80.
    assert analysis_model_view(raw)['market_snapshot']['timeframes']['30m']['forming_last_vs_confirmed_ma10_atr'] == -2.
    raw['contract_context']['positions'] = [{'size': 1}]
    assert 'forming_last_vs_confirmed_ma10_atr' not in analysis_model_view(raw)['market_snapshot']['timeframes']['30m']
    raw['contract_context']['positions'] = []
    raw['market_snapshot']['timeframes']['30m']['confirmed']['atr14_risk_only'] = 0.
    assert 'forming_last_vs_confirmed_ma10_atr' not in analysis_model_view(raw)['market_snapshot']['timeframes']['30m']


def source():
    return dict(market_snapshot={'timeframes': {
        frame: {'forming': {'ohlcv': {'close': 100}, 'volume_acceleration': {'recent_15m_volume': 99},
            'volume_projection': {'projected_final': 10, 'same_progress_profile': {'current_volume': 99}}}}
        for frame in ('15m', '30m', '1h')}},
        contract_context={'ma_structure': {'primary': {'15m': {'points': [123]}, '30m': {'points': [456]}},
            'decision_priority': {'descending': ['1h','30m','4h','12h','1d','1w'], 'timing_only': '15m'}},
            'current_primary_frame_facts': {'15m': {'close': 777}, '30m': {'close': 100}},
            'recovery': {key: {'timeframes.15m.forming.close': 777, 'timeframes.30m.forming.close': 100}
                         for key in ('baseline','current','changed_evidence')}},
        response_contract={'properties': {'frame': {'enum':['15m','30m','1h']},
            'recovery':{'anyOf':[{'properties':{'changed_evidence':{'items':{
                'enum':['timeframes.15m.forming.close','timeframes.30m.forming.close']}}}}]}}})


def test_model_view_removes_direct_and_derived_15m_without_source_mutation():
    raw=source()
    before=deepcopy(raw)
    view=analysis_model_view(raw)
    assert raw==before
    assert set(view['market_snapshot']['timeframes'])=={'30m','1h'}
    for frame in view['market_snapshot']['timeframes'].values():
        assert 'volume_acceleration' not in frame['forming']
        assert 'same_progress_profile' not in frame['forming']['volume_projection']
        assert frame['forming']['volume_projection']['projected_final']==10
    ctx=view['contract_context']
    assert '15m' not in ctx['ma_structure']['primary']
    assert ctx['ma_structure']['decision_priority']['timing_only']==[]
    assert '15m' not in ctx['current_primary_frame_facts']
    assert all('timeframes.15m.forming.close' not in ctx['recovery'][key] for key in ('baseline','current','changed_evidence'))
    assert view['response_contract']['properties']['frame']['enum']==['30m','1h']
    assert view['response_contract']['properties']['recovery']['anyOf'][0]['properties']['changed_evidence']['items']['enum']==['timeframes.30m.forming.close']


def test_lower_frame_perturbation_cannot_change_current_judgment_facts():
    raw=source()
    altered=deepcopy(raw)
    altered['market_snapshot']['timeframes']['15m']['forming']['ohlcv']['close']=999999
    for frame in altered['market_snapshot']['timeframes'].values():
        frame['forming']['volume_acceleration']['recent_15m_volume']=999999
        frame['forming']['volume_projection']['same_progress_profile']['current_volume']=999999
    a,b=analysis_model_view(raw),analysis_model_view(altered)
    assert a==b


def test_current_recovery_paths_deny_15m_but_keep_non_candle_market_facts():
    assert not allowed_evidence_path('timeframes.15m.forming.close')
    assert allowed_evidence_path('timeframes.30m.forming.close')
    assert allowed_evidence_path('market.spread')


def test_new_goal_rejects_15m_but_original_goal_resolution_stays_supported():
    from live.scenario_entry_goal import goal_schema, valid_goal, prepare_goal_source, resolve_goal, plan_goal
    from live.scenario_contract import _matches
    from tests.test_scenario_ma_context import NOW, point, snapshot
    goal=dict(mode='BOUNDED_BEFORE',reference=dict(frame='15m',phase='confirmed',ma='ma35'))
    assert valid_goal(goal)  # Immutable old contracts remain valid definitions.
    assert not _matches(goal,goal_schema())
    market=snapshot('15m',before=point('15m',2))
    market['valid']=True
    ctx=dict(now=NOW/1000,input_id='old-original',entry_contract_version=2,entry_goal_decision_slot=1)
    prepare_goal_source(market,ctx)
    original=resolve_goal(goal,ctx)
    assert original['goal']['reference']['frame']=='15m'
    with pytest.raises(ValueError,match='frame_not_allowed_for_new_open'):
        plan_goal(dict(action='OPEN',entry_contract={'goal':goal}),ctx)
    active=dict(ctx,scenario_id='old-active',registered_entry_contract={'version':2,'resolved_goal':original})
    assert plan_goal(dict(action='EXIT'),active)==original


def test_original_numeric_codec_keeps_15m_but_new_wire_evidence_does_not():
    from live.scenario_recovery import numeric_market
    from live.scenario_contract import response_schema
    from tests.test_scenario_calculated_review import context
    market={'timeframes':{'15m':{'forming':{'close':10}},'30m':{'forming':{'close':20}}}}
    numeric=numeric_market(market)
    assert 'timeframes.15m.forming.close' in numeric
    ctx=context()
    ctx.update(recovery_contract_version=1,recovery={'changed_evidence':numeric})
    schema=response_schema(ctx)['properties']['recovery']['anyOf'][0]['properties']['changed_evidence']
    assert schema['items']['enum']==['timeframes.30m.forming.close']


def test_calculated_flat_and_active_requests_have_no_live_15m():
    from live.scenario_calculated_review import _model_payload
    from live.scenario_model_input import restore_model_input
    from tests.test_scenario_calculated_review import context
    raw=source()
    for scenario in (None,'active'):
        ctx=context()
        ctx.update(scenario_id=scenario,recovery=raw['contract_context']['recovery'])
        result=restore_model_input(_model_payload(raw['market_snapshot'],ctx,{}))
        assert '15m' not in result['market_snapshot']['timeframes']
        assert '15m' not in result['contract_context']['ma_structure']['primary']
        assert '15m' not in result['contract_context'].get('entry_display',{}).get('confirmed_close_tail',{})
        assert '15m' not in json.dumps(result['contract_context']['recovery'])


def test_new_open_wire_rejects_15m_goal_with_valid_30m_control():
    from live.scenario_contract import validate_wire_proposal
    from tests.test_scenario_calculated_review import context, wire
    ctx=context()
    ctx['entry_contract_version']=2
    raw=wire(ctx,'LONG')
    raw.update(entry_contract=dict(version=2,regime='TREND',premise='structural entry',
        goal=dict(mode='BOUNDED_BEFORE',reference=dict(frame='30m',phase='confirmed',ma='ma35')),
        explicit_failure=dict(source='MARK_PRICE',operator='le',price=83300,minimum_observations=2)),thesis_assessment=None)
    assert validate_wire_proposal(raw,ctx)['entry_contract']['goal']['reference']['frame']=='30m'
    raw['entry_contract']['goal']['reference']['frame']='15m'
    with pytest.raises(ValueError):
        validate_wire_proposal(raw,ctx)


def test_15m_only_change_cannot_open_new_recovery_permission(tmp_path):
    import sqlite3
    from live import scenario_recovery as recovery
    from live.scenario_runtime import ScenarioRuntime
    from tests.test_scenario_runtime import Broker, proposal, wait_proposal
    now=[1800000000.]
    lower=[100.]
    conn=sqlite3.connect(tmp_path/'policy.db')
    broker=Broker()
    broker.ctx['accounting_status']='confirmed'
    def market():
        return dict(valid=True,as_of_ms=now[0]*1000,timeframes={
            '15m':{'closed':{'close':lower[0]}},'30m':{'closed':{'close':100.}}})
    runtime=ScenarioRuntime(conn,broker,lambda *_:None,market,clock=lambda:now[0],recovery_enabled=True)
    state=runtime.state()
    state['breaker']=dict(blocked=True,reasons=['three_losses'],consecutive_losses=3,completed_ids=['a','b','c'])
    runtime._save(state)
    def decide(s,c):
        recovery.record_model_request(system_prompt='fixture',user_prompt=json.dumps(c),response_schema={})
        first=c['recovery']['first_observation']
        raw=wait_proposal(s,c) if first else proposal(s,c)
        if not first:
            raw['chase']={'max_bps':0,'max_reprices':0}
        raw['recovery']=dict(decision='OBSERVE' if first else 'PROBE',reason='fixture',
            changed_evidence=[] if first else ['timeframes.15m.closed.close'],counterevidence='fixture',invalidation='fixture')
        recovery.record_model_wire(json.dumps(raw))
        return raw
    runtime.propose=decide
    assert runtime.tick()['status']=='wait'
    now[0]+=300
    lower[0]+=10
    assert runtime.tick()['status']!='intent_pending'
    assert broker.executed==[]
    latest=json.loads(conn.execute('SELECT context FROM llm_scenario_decisions ORDER BY slot DESC').fetchone()[0])
    assert latest['recovery']['changed_evidence']=={}
    assert latest['recovery']['eligible'] is False
    assert conn.execute("SELECT COUNT(*) FROM llm_scenario_recovery_events WHERE kind='PERMIT_CONSUMED'").fetchone()[0]==0


def test_retired_historical_opinions_are_omitted_without_mutating_registered_contract():
    raw=source()
    contract={'contract_id':'unchanged','definition':{'premise':'15m original premise',
        'goal':{'reference':{'frame':'15m'}},'explicit_failure':{'price':123}}}
    ctx=raw['contract_context']
    ctx['registered_entry_contract']=deepcopy(contract)
    ctx['previous_thesis_assessment']={'status':'WEAKENING','reason':'15분봉 반등 2.99x'}
    ctx['position_flow']={'thesis_transitions':[{'reason':'15m drop 88','price':123}]}
    ctx['current_plan']={'action':'WAIT','rationale':'15-minute weakness','entry_contract':deepcopy(contract)}
    ctx['review_memory']={'items':[{'id':'keep','reason':'30m support 15000'}]}
    before=deepcopy(raw)
    result=analysis_model_view(raw)['contract_context']
    assert result['previous_thesis_assessment']=={'status':'WEAKENING','reason':'OMITTED_RETIRED_TIMEFRAME_OPINION'}
    assert result['position_flow']['thesis_transitions']==[{'reason':'OMITTED_RETIRED_TIMEFRAME_OPINION','price':123}]
    assert result['current_plan']['rationale']=='OMITTED_RETIRED_TIMEFRAME_OPINION'
    assert result['registered_entry_contract']==contract
    assert result['current_plan']['entry_contract']==contract
    assert result['review_memory']==ctx['review_memory']
    assert raw==before


def test_stored_15m_goal_revalidates_only_original_source_after_policy_change():
    import sqlite3
    from live.scenario_entry_goal import prepare_goal_source, resolve_goal, stored_goal
    from live.scenario_entry_contract import bind_contract
    from tests.test_scenario_ma_context import NOW, point, snapshot
    market=snapshot('15m',before=point('15m',2))
    market['valid']=True
    ctx=dict(now=NOW/1000,input_id='old',entry_contract_version=2,entry_goal_decision_slot=1)
    prepare_goal_source(market,ctx)
    definition=dict(version=2,regime='TREND',premise='15m historical original',
        goal=dict(mode='BOUNDED_BEFORE',reference=dict(frame='15m',phase='confirmed',ma='ma35')),
        explicit_failure=dict(source='MARK_PRICE',operator='le',price=85,minimum_observations=2))
    proposal=dict(action='OPEN',action_id='original',input_id='old',scenario_id='active',side='LONG',
        entries=[dict(price=90)],take_profits=[dict(price=94,fraction=1)],hard_stop=80,entry_contract=definition)
    resolved=resolve_goal(definition['goal'],ctx)
    record=bind_contract(definition,proposal,NOW/1000,resolved_goal=resolved)
    conn=sqlite3.connect(':memory:')
    conn.execute('CREATE TABLE llm_scenario_decisions(slot INTEGER,snapshot TEXT,context TEXT,proposal TEXT)')
    conn.execute('INSERT INTO llm_scenario_decisions VALUES(?,?,?,?)',
        (1,json.dumps(market),json.dumps(ctx),json.dumps(proposal)))
    assert stored_goal(conn,record)==resolved


def test_legacy_single_call_uses_same_current_frame_boundary():
    from types import SimpleNamespace
    from live.scenario_llm import propose
    from live.scenario_model_input import restore_model_input
    from tests.test_scenario_llm import context, wire
    market=source()['market_snapshot']
    market.update(valid=True,as_of_ms=1000000)
    for frame in market['timeframes'].values():
        frame['forming']['observed_at_ms']=1000000
    before=deepcopy(market)
    ctx=context()
    calls=[]
    def generate(**request):
        calls.append(request)
        return SimpleNamespace(text=json.dumps(wire(ctx)))
    propose(market,ctx,{},generate=generate,clock=lambda:1000)
    actual=restore_model_input(json.loads(calls[0]['user_prompt']))
    assert '15m' not in actual['market_snapshot']['timeframes']
    assert '15m' not in actual['contract_context']['current_primary_frame_facts']
    assert market==before


def test_only_15m_availability_cannot_make_analysis_structure_available():
    raw=source()
    raw['contract_context']['ma_structure'].update(status='available',higher_frames={})
    raw['contract_context']['ma_structure']['primary']={'15m':{'status':'available'},'30m':{'status':'unavailable'}}
    assert analysis_model_view(raw)['contract_context']['ma_structure']['status']=='unavailable'


def test_collection_issue_details_are_not_signals_and_invalid_source_stays_invalid():
    raw=source()
    raw['market_snapshot'].update(valid=False,as_of_ms=123,issues=['15m:stale_confirmed_history','30m:missing_forming'])
    before=deepcopy(raw)
    view=analysis_model_view(raw)['market_snapshot']
    assert view['valid'] is False and view['as_of_ms']==123
    assert view['issues']==['NON_ANALYSIS_COLLECTION_ISSUE_DETAIL_OMITTED','30m:missing_forming']
    assert raw==before


def test_frame_policy_never_filters_opaque_identity_enums():
    raw=source()
    identities={'input_id':{'enum':['15m']},'action_id':{'enum':['timeframes.15m.forming.close']},
        'scenario_id':{'enum':['same_progress_profile']}}
    raw['response_contract']={'properties':dict(identities,candidates={'items':{'properties':deepcopy(identities)}})}
    before=deepcopy(raw)
    result=analysis_model_view(raw)['response_contract']['properties']
    for key, value in identities.items():
        assert result[key]==value
        assert result['candidates']['items']['properties'][key]==value
    assert raw==before


def test_only_recovery_changed_evidence_enum_is_filtered():
    item={'items':{'enum':['timeframes.15m.forming.close','timeframes.30m.forming.close']}}
    recovery={'anyOf':[{'properties':{'changed_evidence':deepcopy(item)}}]}
    raw={'response_contract':{'properties':{'changed_evidence':deepcopy(item),'recovery':recovery}}}
    result=analysis_model_view(raw)['response_contract']['properties']
    assert result['changed_evidence']==item
    assert result['recovery']['anyOf'][0]['properties']['changed_evidence']['items']['enum']==['timeframes.30m.forming.close']
