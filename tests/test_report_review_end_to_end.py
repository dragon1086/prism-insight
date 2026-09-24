"""Real review protocol, repair, synthesis and publication boundary; only I/O faked."""
import asyncio
import copy
import json

import pytest

from test_us_evidence_pipeline_contract import isolated_imports_and_effects  # noqa: F401


@pytest.fixture
def actual_pipeline(monkeypatch, tmp_path):
    from cores import analysis, report_generation, report_fact_editor
    from cores.llm import capabilities
    from cores.llm.ports import LLMResult
    from prism_core.dart_source_tree_catalog import build_catalog
    from prism_core.dart_source_table_evidence import pack_readable_units
    import cores.data_prefetch as prefetch
    import prism_core.kr_official_report_inputs as official
    import prism_core.report_research_prefetch as research

    url = 'https://dart.fss.or.kr/report/viewer.do?rcpNo=20260814001644'
    context = json.dumps({'sources': [{'source': {
        'url': url, 'source_id': 'fixed-official', 'filing': {
            'role': 'primary', 'scope': 'standalone',
            'period_start': '2026-01-01', 'period_end': '2026-06-30'}},
        'catalog': pack_readable_units(build_catalog('<p>같은 원문 금액 123원입니다.</p>')['units'])}]})
    packet = {'dart_chapter_inputs': {'ready': True,
        'contexts': {role: context for role in ('finance', 'business', 'risks')},
        'receipt': {'core_conserved': True, 'capacity_ok': True}},
        'public_receipt': '별도 원문 123원 ' + url}
    prefetch_data = {'report_calculation_reference': '같은 기간 기준 금액 123원입니다.',
                     'market_calculation_reference': '시장 기준값 123입니다.'}
    events = []
    control = {'scenario': 'base_conflict', 'assessments': 0, 'strategies': 0, 'finals': 0}

    def collect(*args):
        events.append('collect')
        return copy.deepcopy(prefetch_data)
    async def collect_official(*args):
        events.append('official')
        return copy.deepcopy(packet)
    async def no_research(*args): return {}
    monkeypatch.setattr(prefetch, 'prefetch_kr_analysis_data', collect)
    monkeypatch.setattr(official, 'collect_kr_official_report_inputs', collect_official)
    monkeypatch.setattr(research, 'prefetch_report_research', no_research)
    monkeypatch.setattr(analysis, '_report_stock_names', lambda: {})
    monkeypatch.setattr(analysis, 'get_chart_as_base64_html', lambda *a, **k: '')
    monkeypatch.setattr(capabilities, 'vision_available', lambda: False)
    monkeypatch.setattr(capabilities, 'vision_buy_quality_active', lambda: False)
    monkeypatch.setenv('PRISM_PARALLEL_REPORT', 'false')
    monkeypatch.setenv('PRISM_REPORT_DIAGNOSTICS_DIR', str(tmp_path / 'diagnostics'))
    monkeypatch.setattr(report_fact_editor, '_calendar_context', lambda day: {
        'reference_date': day, 'calendar': 'XKRX', 'is_session': False})
    work = tmp_path / 'work'; work.mkdir(); monkeypatch.chdir(work)
    analysis._market_analysis_cache.clear()

    def prose(marker):
        return '### 사실 분석\n\n' + marker + '\n\n' + (
            '제공된 원문에 있는 금액은 123원이며 기준 기간과 별도 범위를 구분합니다. ' * 12)

    class Backend:
        async def run(self, spec, message):
            name = spec.name
            events.append(name)
            if name.startswith('dart_depth_'):
                marker = '공시 자료입니다.'
                if control['scenario'] == 'dart_conflict':
                    marker = 'stalefiling' if events.count(name) == 1 else 'correctedfiling'
                    source = message.split('<filing_source_data>')[1].split('</filing_source_data>')[0]
                    control.setdefault('filing_sources', {}).setdefault(name, []).append(source)
                return LLMResult(text=prose(marker) + '\n\n' + prose('조건을 확인합니다.')
                                 + '\n\n출처 ' + url)
            if name.endswith('_fact_recovery'):
                if control['scenario'] == 'repair_timeout': raise TimeoutError('fixture timeout')
                if control['scenario'] == 'repair_cancel': raise asyncio.CancelledError()
                return LLMResult(text=prose('correctedfact'))
            if name == 'investment_strategy_agent':
                control['strategies'] += 1
                if control['scenario'] == 'base_conflict':
                    assert 'correctedfact' in message
                    assert 'stalecompanyfact' not in message
                if control['scenario'] == 'dart_conflict':
                    assert 'correctedfiling' in message and 'stalefiling' not in message
                if control['scenario'] == 'strategy_failure':
                    return LLMResult(text='Investment strategy analysis failed')
                return LLMResult(text='### 5-1. 투자 전략\n\n' + prose('기존 매매 정책을 유지합니다.'))
            if name in ('report_fact_assessor', 'report_final_fact_editor'):
                assert spec.output_schema is not None
                assert spec.mcp_servers == ()
                request = json.loads(message)
                sections = request['sections']
                payload = {'status': 'READY', 'summary': None, 'edits': [], 'unresolved': []}
                if name == 'report_fact_assessor':
                    control['assessments'] += 1
                    assert 'investment_strategy' not in sections
                    scenario = control['scenario']
                    conflict = (scenario in ('base_conflict', 'repair_timeout', 'repair_cancel', 'unsupported_claim',
                                             'price_conflict', 'dart_conflict') and control['assessments'] == 1
                                or scenario in ('repeated_conflict', 'source_conflict'))
                    if conflict:
                        target = {'source_conflict': 'shared_reference', 'price_conflict': 'price_volume_analysis',
                                  'dart_conflict': 'dart_deep_analysis'}.get(scenario, 'company_status')
                        payload.update(status='CONFLICTS', unresolved=[{
                            'section': target, 'issue': '기간 설명이 기준 원문과 다릅니다.',
                            'kind': 'unsupported_claim' if scenario == 'unsupported_claim' else 'contradiction',
                            'evidence_section': None if scenario == 'unsupported_claim' else 'dart_deep_analysis',
                            'source_roles': [] if scenario == 'unsupported_claim' else ['finance']}])
                else:
                    control['finals'] += 1
                    if control['scenario'] == 'repeated_strategy' or (
                            control['scenario'] == 'strategy_only' and control['finals'] == 1):
                        payload.update(status='CONFLICTS', unresolved=[{
                            'section': 'investment_strategy', 'issue': '종합 문장의 기간 설명을 다시 대조해야 합니다.',
                            'kind': 'contradiction', 'evidence_section': 'shared_reference', 'source_roles': []}])
                    else:
                        payload['summary'] = '## 종합 요약\n\n' + (
                            '제공한 공시의 기간과 별도 범위, 현금흐름과 원금 이행 조건을 구분합니다. '
                            '이미 발생한 사건과 남은 위험을 나누고 기존 매매 정책을 유지합니다. ' * 5)
                return LLMResult(structured=spec.output_schema.model_validate(payload))
            return LLMResult(text=prose('stalecompanyfact' if name == 'company_status_agent' else '기본 자료입니다.'))

    monkeypatch.setattr(report_generation, '_get_report_backend', lambda: Backend())
    return analysis, events, control


def run(pipeline):
    analysis, _, _ = pipeline
    return asyncio.run(analysis.analyze_stock('252990', 'Example', '20260924', require_dart_depth=True))


def test_actual_typed_review_repairs_before_first_strategy(actual_pipeline):
    _, events, control = actual_pipeline
    result = run(actual_pipeline)
    assert control == {'scenario': 'base_conflict', 'assessments': 2, 'strategies': 1, 'finals': 1}
    assert events.index('company_status_agent_fact_recovery') < events.index('investment_strategy_agent')
    assert events.count('collect') == events.count('official') == 1
    assert 'correctedfact' in result and 'stalecompanyfact' not in result


def test_actual_strategy_only_conflict_rebuilds_without_recollecting(actual_pipeline):
    _, events, control = actual_pipeline
    control['scenario'] = 'strategy_only'
    assert '종합 요약' in run(actual_pipeline)
    assert control['assessments'] == 1 and control['strategies'] == control['finals'] == 2
    assert not any(event.endswith('_fact_recovery') for event in events)
    assert events.count('collect') == events.count('official') == 1


@pytest.mark.parametrize('scenario', ['repeated_conflict', 'source_conflict', 'repair_timeout'])
def test_actual_failed_assessment_or_repair_never_reaches_synthesis(actual_pipeline, scenario):
    _, events, control = actual_pipeline
    control['scenario'] = scenario
    with pytest.raises((ValueError, TimeoutError)):
        run(actual_pipeline)
    assert control['strategies'] == control['finals'] == 0
    assert sum(event.endswith('_fact_recovery') for event in events) <= 1


def test_price_facts_are_repairable_without_changing_source_packet(actual_pipeline):
    _, events, control = actual_pipeline
    control['scenario'] = 'price_conflict'
    assert '종합 요약' in run(actual_pipeline)
    assert events.count('price_volume_analysis_agent_fact_recovery') == 1
    assert control['assessments'] == 2 and control['strategies'] == 1
    assert events.count('official') == 1


def test_unsupported_model_claim_is_qualified_without_inventing_evidence(actual_pipeline):
    _, events, control = actual_pipeline
    control['scenario'] = 'unsupported_claim'
    assert '종합 요약' in run(actual_pipeline)
    assert control['assessments'] == 2 and events.count('company_status_agent_fact_recovery') == 1


def test_dart_model_chapter_rebuilds_from_identical_official_source_once(actual_pipeline):
    _, events, control = actual_pipeline
    control['scenario'] = 'dart_conflict'
    report = run(actual_pipeline)
    assert 'correctedfiling' in report and 'stalefiling' not in report
    assert not any(name.endswith('_fact_recovery') for name in events)
    for name, sources in control['filing_sources'].items():
        assert events.count(name) == 2 and sources[0] == sources[1]
    assert len(control['filing_sources']) == 3 and events.count('official') == 1


def test_repeated_strategy_conflict_has_one_retry_not_an_infinite_loop(actual_pipeline):
    _, _, control = actual_pipeline
    control['scenario'] = 'repeated_strategy'
    with pytest.raises(ValueError): run(actual_pipeline)
    assert control['strategies'] == control['finals'] == 2


def test_failed_strategy_marker_cannot_become_a_successful_deep_report(actual_pipeline):
    _, _, control = actual_pipeline
    control['scenario'] = 'strategy_failure'
    with pytest.raises(ValueError): run(actual_pipeline)
    assert control['strategies'] == 1 and control['finals'] == 0


def test_cancellation_during_repair_propagates_without_synthesis(actual_pipeline):
    _, _, control = actual_pipeline
    control['scenario'] = 'repair_cancel'
    with pytest.raises(asyncio.CancelledError): run(actual_pipeline)
    assert control['strategies'] == control['finals'] == 0
