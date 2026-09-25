"""Actual KR assembly and agent contracts, with collection/model/order I/O replaced."""
import asyncio
import copy
import json

import pytest
from test_us_evidence_pipeline_contract import isolated_imports_and_effects  # noqa: F401

from cores.agents.report_agent import ReportAgent
from prism_core.kr_report_context import (
    apply_kr_report_context,
    market_cache_key,
    reference_context,
    render_flow_reference,
)


def inputs():
    return {
        'report_calculation_reference': '주가 20일 평균 12345.67원, 기준일 2026-09-22',
        'market_calculation_reference': '코스피 20일 평균 3000.00포인트',
        'flow_evidence': 'INTERNAL_FLOW_NOT_FOR_PUBLIC_SYNTHESIS',
        'flow_evidence_public': '수급 기준 2026-09-22: 외국인 987주 순매수입니다.',
        'report_calculations': {'facts': [{'id': 'index.1001.sma.20', 'value': 3000.0}]},
        'official_dart': {
            'section_contexts': {'company_status': '공시 재무표 123456천원; 2026년 반기; 연결',
                                 'company_overview': '공시 사업·지분 46.94%; 비교 전기말도 동일',
                                 'news_analysis': '공시 차입 약정: 2027년 6월, 조건부 면제'},
            'public_receipt': '공시 확인: 2026년 반기 연결, 출처 https://dart.fss.or.kr/'},
    }


def test_company_evidence_is_scoped_and_market_is_ticker_free():
    packet = inputs()
    agent = ReportAgent('test', 'BASE', ('dart',))
    status = apply_kr_report_context(agent, 'company_status', packet)
    market = apply_kr_report_context(agent, 'market_index_analysis', packet)
    assert '123456천원' in status.instruction and '12345.67원' in status.instruction
    assert '조건부 면제' not in status.instruction
    assert '3000.00포인트' in market.instruction
    assert '12345.67원' not in market.instruction and '46.94%' not in market.instruction
    assert market.server_names == agent.server_names
    assert agent.instruction == 'BASE'
    assert '비교 전기' in reference_context(packet)


def test_market_cache_changes_only_for_market_facts_date_language():
    packet = inputs()
    key = market_cache_key(packet, '20260923', 'ko')
    other = copy.deepcopy(packet)
    other['report_calculation_reference'] = '다른 종목 가격 999원'
    other['official_dart'] = {}
    other['market_calculation_reference'] += ' 조회시각만 변경'
    assert market_cache_key(other, '20260923', 'ko') == key
    other['report_calculations']['facts'][0]['value'] = 3100
    assert market_cache_key(other, '20260923', 'ko') != key
    assert market_cache_key(packet, '20260924', 'ko') != key
    assert market_cache_key(packet, '20260923', 'en') != key


def test_public_flow_preserves_partial_coverage_and_missing_ratio():
    evidence = {'asof_utc': '2026-09-23T10:00:00Z', 'windows': {
        '5': {'status': 'MISSING', 'start': '2026-09-18', 'end': '2026-09-22',
              'observed_sessions': 3, 'required_sessions': 5},
        '20': {'status': 'OK', 'start': '2026-08-20', 'end': '2026-09-22',
               'observed_sessions': 20, 'required_sessions': 20,
               'net_shares': {'foreign': -10, 'institution': 20, 'combined': 10},
               'positive_sessions': {'foreign': 0, 'institution': 20, 'combined': 19},
               'trailing_positive_sessions_within_window': {'foreign': 0, 'institution': 20, 'combined': 4},
               'combined_pct_of_traded_shares': None}}}
    public = render_flow_reference(evidence)
    assert '2026-09-18~2026-09-22, 3/5일 확인' in public
    assert '외국인 -10주, 기관 20주, 합계 10주' in public
    assert '0일, 20일, 4일' in public
    assert '최근 20관측일 구간은 거래량 자료' in public
    assert 'MISSING' not in public


def test_required_depth_stops_before_models_when_sources_are_unready(monkeypatch):
    import cores.data_prefetch as prefetch
    import prism_core.kr_official_report_inputs as official
    import prism_core.report_research_prefetch as research
    from cores import analysis

    monkeypatch.setattr(prefetch, 'prefetch_kr_analysis_data', lambda *args: {})
    async def unavailable(*args):
        return {'dart_chapter_inputs': {'ready': False}}
    async def no_research(*args):
        return None
    def forbidden(*args, **kwargs):
        raise AssertionError('Analysis models prepared before required source validation')
    monkeypatch.setattr(official, 'collect_kr_official_report_inputs', unavailable)
    monkeypatch.setattr(research, 'prefetch_report_research', no_research)
    monkeypatch.setattr(analysis, 'get_agent_directory', forbidden)
    with pytest.raises(ValueError, match='완성본'):
        asyncio.run(analysis.analyze_stock('017670', 'SK텔레콤', '20260923', require_dart_depth=True))


@pytest.mark.parametrize('summary_fails', [False, True])
def test_deep_chapter_and_peer_facts_survive_real_assembly_and_both_syntheses(monkeypatch, tmp_path, summary_fails):
    import cores.data_prefetch as prefetch
    import prism_core.kr_official_report_inputs as official
    import prism_core.kr_peer_comparison as peers
    import prism_core.report_research_prefetch as research
    from cores import analysis, dart_deep_analysis
    from cores.llm import capabilities
    from prism_core.dart_source_table_evidence import pack_readable_units
    from prism_core.dart_source_tree_catalog import build_catalog

    data = inputs()
    dart = data.pop('official_dart')
    dart['dart_chapter_inputs'] = {'ready': True,
        'contexts': {key: json.dumps({'sources': [{'source': {
            'url': 'https://dart.fss.or.kr/report/viewer.do?rcpNo=20260813001728', 'source_id': 'fixture-' + key,
            'filing': {'role': 'primary', 'scope': 'consolidated',
                       'period_start': '2026-01-01', 'period_end': '2026-06-30'}},
            'catalog': pack_readable_units(build_catalog('<p>WHOLE_SOURCE_' + key + '</p>')['units'])}]})
                     for key in dart_deep_analysis.ROLES},
        'receipt': {'core_conserved': True, 'capacity_ok': True}}
    monkeypatch.setattr(prefetch, 'prefetch_kr_analysis_data', lambda *args: data)
    async def collect(*args):
        return dart
    async def no_research(*args):
        return None
    monkeypatch.setattr(official, 'collect_kr_official_report_inputs', collect)
    monkeypatch.setattr(research, 'prefetch_report_research', no_research)
    monkeypatch.setattr(analysis, 'get_chart_as_base64_html', lambda *a, **k: '')
    monkeypatch.setattr(capabilities, 'vision_available', lambda: False)
    monkeypatch.setattr(capabilities, 'vision_buy_quality_active', lambda: False)
    monkeypatch.setenv('PRISM_PARALLEL_REPORT', 'false')
    work = tmp_path / 'work'
    work.mkdir()
    monkeypatch.chdir(work)
    analysis._market_analysis_cache.clear()
    model_calls, peer_calls = [], []
    evidence = ('\n\n#### Competitive Evidence\n'
                '| field | type | entity | peer_universe | metric·value·period | source | publication_date | status |\n'
                '|---|---|---|---|---|---|---|---|\n'
                '| 수요 | sector_tailwind | SK텔레콤 | KT | 증가 | https://example.com/a | 2026-09-01 | SEARCH_ONLY |\n\n'
                '뉴스 종합 결론 문장입니다.')
    async def base(agent, section, *args):
        model_calls.append(section)
        # The Industry Analyst receives the pre-collected peer table; others do not.
        assert ('PEER 9.23배' in agent.instruction) is (section == 'company_overview')
        return '### 기본 분석\n기본 사실' + (evidence if section == 'news_analysis' else '')
    async def peer_collect(ticker, company, reference_date):
        peer_calls.append((ticker, company, reference_date))
        return {'ready': True, 'peers': [{}, {}], 'period': '2025/12', 'skip_reason': None,
                'public_markdown': '#### 경쟁사 비교 분석\n\nPEER 9.23배', 'model_context': 'PEER 9.23배'}
    monkeypatch.setattr(peers, 'collect_wisereport_peers', peer_collect)
    authored = {}
    async def write(agent, message):
        role = agent.name.removeprefix('dart_depth_')
        model_calls.append(role)
        assert 'WHOLE_SOURCE_' + role in message
        assert ('PEER 9.23배' in message) is (role == 'business')
        text = f'### 상세 분석 {role}\n\n' + (f'{role}의 금액 987.65와 이행 요청 조건 및 남은 약정 한도와 기간을 설명합니다.\n\n' * 180)
        text += '\n\n출처: https://dart.fss.or.kr/report/viewer.do?rcpNo=20260813001728'
        authored[role] = text.strip()
        return text, {'input_tokens': 100, 'output_tokens': 50, 'total_tokens': 150}
    monkeypatch.setattr(dart_deep_analysis, '_write', write)
    async def strategy(reports, combined, *args):
        model_calls.append('strategy')
        assert all(text in combined for text in authored.values())
        assert 'PEER 9.23배' in combined
        # Synthesis still reads the model evidence record and its overview handoff.
        assert 'SEARCH_ONLY' in combined and 'Competitive Evidence Handoff' in combined
        assert reports['dart_deep_analysis'] in combined
        assert '코스피 20일 평균 3000.00포인트' in combined
        assert '수급 기준 2026-09-22: 외국인 987주' in combined
        assert 'INTERNAL_FLOW_NOT_FOR_PUBLIC_SYNTHESIS' not in combined
        return '\\n\\n### 5-1. 투자 전략\n조건부 의무를 고려한 전략'
    async def summary(reports, *args, **kwargs):
        model_calls.append('summary')
        if summary_fails:
            raise ValueError('unresolved_fact_conflict')
        assert all(text in reports['dart_deep_analysis'] for text in authored.values())
        assert reports['peer_comparison'].endswith('9.23배')
        assert '코스피 20일 평균 3000.00포인트' in reports['shared_reference']
        assert '수급 기준 2026-09-22: 외국인 987주' in reports['shared_reference']
        return '## 핵심 요약\n심층 분석의 조건을 유지합니다.'
    monkeypatch.setattr(analysis, 'generate_report', base)
    monkeypatch.setattr(analysis, 'generate_market_report', base)
    monkeypatch.setattr(analysis, 'generate_investment_strategy', strategy)
    monkeypatch.setattr(analysis, 'generate_summary', summary)
    report = asyncio.run(analysis.analyze_stock('017670', 'SK텔레콤', '20260923', require_dart_depth=True))
    # Summary failure uses the ordinary fallback instead of aborting the report.
    assert ('요약 생성 중 오류가 발생했습니다.' in report) is summary_fails
    assert len(model_calls) == 11 and peer_calls == [('017670', 'SK텔레콤', '20260923')]
    assert report.index('## 2. 펀더멘털 분석') < report.index('#### 경쟁사 비교 분석\n\nPEER 9.23배') < report.index('## 3. 뉴스 분석')
    # No competitive evidence record, handoff or appendix in the public KR report.
    for token in ('Competitive Evidence', '경쟁력 비교 근거', 'SEARCH_ONLY', 'sector_tailwind', 'peer_universe',
                  'Evidence ID', '근거 식별자', '부록: 출처와 비교 근거'):
        assert token not in report
    assert report.index('## 3. 뉴스 분석') < report.index('뉴스 종합 결론 문장입니다.') < report.index('## 4. 시장 분석')
    assert all(text in report for text in authored.values())
    assert report.index('## 5. DART') < report.index('## 6. 투자 전략')
    assert '### 6-1. 투자 전략' in report and '### 5-1. 투자 전략' not in report
    assert dart_deep_analysis.CHAPTER_START in report and dart_deep_analysis.CHAPTER_END in report


@pytest.mark.parametrize('parallel', [False, True])
@pytest.mark.parametrize('dart_fails', [False, True])
def test_real_kr_assembly_reaches_synthesis_and_publication_without_extra_models(
        monkeypatch, tmp_path, parallel, dart_fails):
    import cores.data_prefetch as prefetch
    import prism_core.kr_official_report_inputs as official
    import prism_core.report_research_prefetch as research
    from cores import analysis
    from cores.llm import capabilities

    packet = inputs()
    dart = packet.pop('official_dart')
    monkeypatch.setattr(prefetch, 'prefetch_kr_analysis_data', lambda *a: copy.deepcopy(packet))
    collected = []

    async def collect(ticker, company, day):
        collected.append((ticker, company, day))
        if dart_fails:
            raise ValueError('private transport error')
        return dart

    async def no_research(*args):
        return None

    monkeypatch.setattr(official, 'collect_kr_official_report_inputs', collect)
    monkeypatch.setattr(research, 'prefetch_report_research', no_research)
    monkeypatch.setattr(analysis, 'get_chart_as_base64_html', lambda *a, **k: '')
    monkeypatch.setattr(capabilities, 'vision_available', lambda: False)
    monkeypatch.setattr(capabilities, 'vision_buy_quality_active', lambda: False)
    monkeypatch.setenv('PRISM_PARALLEL_REPORT', str(parallel).lower())
    monkeypatch.setenv('PRISM_PARALLEL_REPORT_MAX_CONCURRENCY', '3')
    work = tmp_path / 'work'
    work.mkdir()
    monkeypatch.chdir(work)
    analysis._market_analysis_cache.clear()
    calls = []

    async def base(agent, section, *args):
        calls.append(section)
        if section == 'market_index_analysis':
            assert '3000.00포인트' in agent.instruction and '12345.67원' not in agent.instruction
        else:
            assert '12345.67원' in agent.instruction
        if section in dart['section_contexts']:
            assert (dart['section_contexts'][section] in agent.instruction) is (not dart_fails)
        return f'### {section}\n관측 수치를 분석했습니다. BAR_FINALITY_UNKNOWN'

    async def strategy(reports, combined, *args):
        calls.append('strategy')
        assert '12345.67원' in combined and '12345.67원' in reports['shared_reference']
        assert ('공시 확인' in combined) is (not dart_fails)
        return '기존 위험 한도를 유지하는 전략'

    async def summary(reports, *args, **kwargs):
        calls.append('summary')
        assert '12345.67원' in reports['shared_reference']
        assert 'investment_strategy' in reports
        return '## 핵심 요약\n숫자와 공시 기간을 함께 검토했습니다.'

    monkeypatch.setattr(analysis, 'generate_report', base)
    monkeypatch.setattr(analysis, 'generate_market_report', base)
    monkeypatch.setattr(analysis, 'generate_investment_strategy', strategy)
    monkeypatch.setattr(analysis, 'generate_summary', summary)
    report = asyncio.run(analysis.analyze_stock('017670', 'SK텔레콤', '20260923'))
    assert len(calls) == 8 and len(set(calls)) == 8
    assert collected == [('017670', 'SK텔레콤', '20260923')]
    assert '12345.67원' in report and '3000.00포인트' in report
    assert ('공시 확인' in report) is (not dart_fails)
    assert 'BAR_FINALITY_UNKNOWN' not in report and 'private transport error' not in report
