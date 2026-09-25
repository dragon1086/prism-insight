"""Real KR pipeline preflight on both generated and cached market drafts."""
import asyncio
import copy

import pytest

from test_us_evidence_pipeline_contract import isolated_imports_and_effects  # noqa: F401


@pytest.fixture
def pipeline(monkeypatch, tmp_path):
    import cores.data_prefetch as prefetch
    from cores import analysis, dart_deep_analysis
    from cores.llm import capabilities
    import prism_core.kr_official_report_inputs as official
    import prism_core.report_research_prefetch as research

    calls = []
    source = {'report_calculation_reference': 'FIXED_CALCULATED_SOURCE',
              'market_calculation_reference': 'FIXED_MARKET_SOURCE'}
    official_packet = {'dart_chapter_inputs': {'ready': True},
                       'public_receipt': 'FIXED_OFFICIAL_RECEIPT'}

    def collect(*args):
        calls.append('prefetch')
        return copy.deepcopy(source)
    async def official_collect(*args):
        calls.append('official')
        return copy.deepcopy(official_packet)
    async def research_collect(*args): return {}
    async def chapter(*args, **kwargs):
        calls.append('chapter')
        return '## 5. DART\nFIXED_DART_CHAPTER', {'status': 'ok', 'calls': 0}
    async def draft(agent, section, *args):
        calls.append(section)
        return 'DRAFT_' + section

    monkeypatch.setattr(prefetch, 'prefetch_kr_analysis_data', collect)
    monkeypatch.setattr(official, 'collect_kr_official_report_inputs', official_collect)
    monkeypatch.setattr(research, 'prefetch_report_research', research_collect)
    monkeypatch.setattr(dart_deep_analysis, 'generate_dart_chapter', chapter)
    monkeypatch.setattr(analysis, '_report_stock_names', lambda: {})
    monkeypatch.setattr(analysis, 'generate_report', draft)
    monkeypatch.setattr(analysis, 'generate_market_report', draft)
    monkeypatch.setattr(analysis, 'get_chart_as_base64_html', lambda *args, **kwargs: '')
    monkeypatch.setattr(capabilities, 'vision_available', lambda: False)
    monkeypatch.setattr(capabilities, 'vision_buy_quality_active', lambda: False)
    monkeypatch.setenv('PRISM_PARALLEL_REPORT', 'false')
    analysis._market_analysis_cache.clear()
    work = tmp_path / 'work'
    work.mkdir()
    monkeypatch.chdir(work)
    return analysis, calls


def test_standalone_kr_market_claim_removed_before_strategy_and_summary(pipeline, monkeypatch):
    analysis, calls = pipeline
    bad = 'Fed raised rates by 0.25 to 3.75–4.00 [5][4][7][1]'
    stages = []

    async def market(*args, **kwargs):
        calls.append('unsafe_market_generation')
        return bad

    def check(reports):
        assert '0.25' not in reports['market_index_analysis']
        assert '3.75' not in reports['market_index_analysis']
        assert '[5]' not in reports['market_index_analysis']
        assert '제외했습니다' in reports['market_index_analysis']
        assert '공통 시장 근거' not in reports['market_index_analysis']
        assert 'FIXED_MARKET_SOURCE' in reports['shared_reference']
        assert 'FIXED_CALCULATED_SOURCE' in reports['shared_reference']

    async def strategy(reports, combined, *args, **kwargs):
        check(reports)
        assert bad not in combined and 'FIXED_MARKET_SOURCE' in combined
        stages.append('strategy')
        return '### 5-1. 투자 전략\nSource-grounded strategy'

    async def summary(reports, *args, **kwargs):
        check(reports)
        stages.append('summary')
        return 'Source-grounded summary'

    monkeypatch.setattr(analysis, 'generate_market_report', market)
    monkeypatch.setattr(analysis, 'generate_investment_strategy', strategy)
    monkeypatch.setattr(analysis, 'generate_summary', summary)
    for _ in range(2):
        report = asyncio.run(analysis.analyze_stock('017670', 'Example', '20260925', require_dart_depth=True))
        assert bad not in report and '0.25' not in report and '제외했습니다' in report
    assert calls.count('unsafe_market_generation') == 1  # second draft came from market cache
    assert stages == ['strategy', 'summary'] * 2
    assert bad in analysis._market_analysis_cache.values()  # raw cache is not silently rewritten


@pytest.mark.parametrize('outcome', ['clean', 'summary_error', 'strategy_error', 'strategy_failure_text'])
def test_deep_report_single_synthesis_pass(pipeline, monkeypatch, outcome):
    analysis, calls = pipeline

    async def strategy(reports, combined, *args):
        calls.append('strategy')
        assert 'FIXED_DART_CHAPTER' in combined
        if outcome == 'strategy_error':
            raise RuntimeError('strategy failed')
        if outcome == 'strategy_failure_text':
            return '투자 전략 분석 실패'
        return '### 5-1. 투자 전략\nINITIAL_STRATEGY'

    async def summary(reports, *args, **kwargs):
        calls.append('summary')
        if outcome == 'summary_error':
            raise ValueError('summary failed')
        return '# Example (017670) 분석 보고서\n**발행일:** 2026.09.24\n\n---\n\nFINAL_SUMMARY'

    monkeypatch.setattr(analysis, 'generate_investment_strategy', strategy)
    monkeypatch.setattr(analysis, 'generate_summary', summary)
    run = analysis.analyze_stock('017670', 'Example', '20260924', 'ko',
                                 macro_context={'report_prose': 'FIXED_MACRO_BLOCK'}, require_dart_depth=True)
    if outcome.startswith('strategy'):
        with pytest.raises(RuntimeError, match='(?i)strategy'):
            asyncio.run(run)
        assert calls.count('summary') == 0
    else:
        report = asyncio.run(run)
        assert report.count('# Example (017670)') == 1
        assert report.count('**발행일:**') == 1
        assert 'FIXED_MACRO_BLOCK' in report and 'FIXED_DART_CHAPTER' in report
        assert '### 6-1. 투자 전략' in report and 'INITIAL_STRATEGY' in report
        if outcome == 'summary_error':
            assert '요약 생성 중 오류가 발생했습니다.' in report
        else:
            assert 'FINAL_SUMMARY' in report
        assert calls.count('summary') == 1
    assert calls.count('strategy') == 1
    assert calls.count('prefetch') == calls.count('official') == calls.count('chapter') == 1
    assert all(calls.count(section) == 1 for section in ('company_status', 'company_overview', 'market_index_analysis'))
