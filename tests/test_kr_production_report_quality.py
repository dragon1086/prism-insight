"""Actual KR assembly and agent contracts, with collection/model/order I/O replaced."""
import asyncio
import copy

import pytest

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

    async def summary(reports, *args):
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
