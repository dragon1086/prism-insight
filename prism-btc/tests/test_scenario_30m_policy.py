from copy import deepcopy

from live.scenario_analysis_frames import analysis_model_view, allowed_evidence_path
from tests.test_scenario_analysis_frames import source


def test_15m_and_5m_are_excluded_without_mutating_history():
    raw=source()
    raw['market_snapshot']['timeframes']['5m']=deepcopy(raw['market_snapshot']['timeframes']['15m'])
    before=deepcopy(raw)
    view=analysis_model_view(raw)
    assert raw==before
    assert '15m' not in view['market_snapshot']['timeframes']
    assert '5m' not in view['market_snapshot']['timeframes']
    assert '15m' not in view['contract_context']['ma_structure']['primary']
    assert view['contract_context']['ma_structure']['decision_priority']['timing_only']==[]


def test_timing_view_does_not_grant_new_recovery_or_goal_authority():
    from live.scenario_entry_goal import goal_schema
    assert not allowed_evidence_path('timeframes.15m.forming.close')
    assert not allowed_evidence_path('timeframes.5m.forming.close')
    assert allowed_evidence_path('timeframes.30m.forming.close')
    assert '15m' not in goal_schema()['anyOf'][0]['properties']['reference']['properties']['frame']['enum']


def test_15m_reference_points_do_not_change_prepared_entry_prices():
    from live.scenario_entry_plans import prepare
    from live.scenario_explanation import source_from_structure
    from tests.test_scenario_entry_plans import sample
    s,c,t=sample()
    before=prepare(s,c,t)
    t['primary']['15m']=deepcopy(t['primary']['30m'])
    for p in t['primary']['15m']['points']:
        p.update(high=100.4,low=99.8,ma10=100.2,ma35=100.3)
    assert not any(p[0].startswith('P:15m:') for p in source_from_structure(t,c)['points'])
    assert prepare(s,c,t)==before


def test_15m_structure_cannot_reach_flat_and_holding_model_requests():
    from live.scenario_calculated_review import _model_payload
    from live.scenario_model_input import restore_model_input
    from tests.test_scenario_ma_context import snapshot, NOW
    from tests.test_scenario_prompt_logic import context
    for holding in (False,True):
        market=snapshot('15m')
        for tf in ('30m','1h'):
            market['timeframes'].update(snapshot(tf)['timeframes'])
        ctx=dict(context(holding),now=NOW/1000,mark_price=100.)
        payload=restore_model_input(_model_payload(market,ctx,{}))
        assert '15m' not in payload['market_snapshot']['timeframes']
        assert '15m' not in payload['contract_context']['ma_structure']['primary']


def test_15m_explanation_references_are_not_current_evidence():
    from engine.scenario_ma_context import build_ma_structure_context
    from live.scenario_explanation import source_from_structure, compile_explanation
    from tests.test_scenario_explanation import fixture
    ctx,_=fixture()
    snap=ctx['_snapshot']
    snap['timeframes']['15m']=deepcopy(snap['timeframes']['30m'])
    snap['timeframes']['15m']['ma_path']['confirmed_points'][0]['open_time_ms']=900000
    ctx['explanation_source']=source_from_structure(build_ma_structure_context(snap,ctx),ctx)
    raw=dict(interpretation='15분봉 저점과 현재 목표의 관계입니다.',
             evidence_refs=['P:15m:0:low'],comparisons=[])
    receipt=compile_explanation(raw,ctx,dict(action='WAIT'),scope='WAIT',snapshot=snap)
    assert receipt['source_status']=='VERIFIED'
    assert receipt['evidence']==[None]
