"""One-round conflict recovery through actual KR report orchestration, offline."""
import asyncio
import copy

import pytest

from test_us_evidence_pipeline_contract import isolated_imports_and_effects  # noqa: F401


@pytest.fixture
def pipeline(monkeypatch, tmp_path):
    import cores.data_prefetch as prefetch
    import cores.report_generation as generation
    from cores import analysis, dart_deep_analysis
    from cores.llm import capabilities
    from cores.report_fact_editor import ReportFactConflictError
    import prism_core.kr_official_report_inputs as official
    import prism_core.report_research_prefetch as research

    calls = []
    snapshots = {}
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

    async def repair(reports, agents, prefetched, conflicts, *args):
        calls.append('repair')
        assert isinstance(conflicts, ReportFactConflictError)
        assert 'company_status' in conflicts.targets
        snapshots['targets'] = conflicts.targets
        snapshots['before_repair'] = copy.deepcopy(reports)
        snapshots['cache_before'] = copy.deepcopy(analysis._market_analysis_cache)
        result = dict(reports)
        for target in conflicts.targets:
            result[target] += '\nREPAIRED_FACTUAL_DRAFT'
        snapshots['repaired'] = result
        return result

    monkeypatch.setattr(generation, 'regenerate_conflicting_sections', repair)
    return analysis, ReportFactConflictError, calls, snapshots


@pytest.mark.parametrize('outcome', ['clean', 'repaired', 'repaired_five', 'other_error', 'second_conflict', 'strategy_error', 'strategy_failure_text'])
def test_actual_assembly_one_failure_only_repair_round(pipeline, monkeypatch, outcome):
    analysis, Conflict, calls, snapshots = pipeline

    async def strategy(reports, combined, *args):
        nth = calls.count('strategy')
        calls.append('strategy')
        if nth:
            assert 'investment_strategy' not in reports
            assert 'REPAIRED_FACTUAL_DRAFT' in combined
            assert 'INITIAL_STRATEGY' not in combined
            original = snapshots['before_repair']
            assert all(reports[key] == value for key, value in original.items()
                       if key not in {*snapshots['targets'], 'investment_strategy'})
            assert analysis._market_analysis_cache == snapshots['cache_before']
            if outcome == 'strategy_error': raise RuntimeError('repair strategy failed')
            if outcome == 'strategy_failure_text': return '투자 전략 분석 실패'
            return '### 5-1. 투자 전략\nRECOVERED_STRATEGY'
        return '### 5-1. 투자 전략\nINITIAL_STRATEGY'

    async def summary(reports, *args):
        nth = calls.count('summary')
        calls.append('summary')
        if outcome == 'other_error': raise ValueError('unrelated validation error')
        if outcome != 'clean' and (not nth or outcome == 'second_conflict'):
            targets = (('investor_trading_analysis', 'company_status', 'company_overview',
                        'news_analysis', 'market_index_analysis') if outcome == 'repaired_five' else ('company_status',))
            raise Conflict(tuple((target, 'period mismatch') for target in targets),
                           evidence_sections=('dart_deep_analysis',) * len(targets))
        if nth:
            assert 'REPAIRED_FACTUAL_DRAFT' in reports['company_status']
            assert 'RECOVERED_STRATEGY' in reports['investment_strategy']
            assert '### 6-1.' in reports['investment_strategy']
        return '# Example (017670) 분석 보고서\n**발행일:** 2026.09.24\n\n---\n\nRECOVERED_SUMMARY'

    monkeypatch.setattr(analysis, 'generate_investment_strategy', strategy)
    monkeypatch.setattr(analysis, 'generate_summary', summary)
    run = analysis.analyze_stock('017670', 'Example', '20260924', 'ko',
                                macro_context={'report_prose': 'FIXED_MACRO_BLOCK'}, require_dart_depth=True)
    if outcome in {'other_error', 'second_conflict', 'strategy_error', 'strategy_failure_text'}:
        with pytest.raises((ValueError, RuntimeError)):
            asyncio.run(run)
    else:
        report = asyncio.run(run)
        assert report.count('# Example (017670)') == 1
        assert report.count('**발행일:**') == 1
        assert 'FIXED_MACRO_BLOCK' in report and 'FIXED_DART_CHAPTER' in report
        if outcome in {'repaired', 'repaired_five'}:
            assert 'INITIAL_STRATEGY' not in report
            assert 'RECOVERED_STRATEGY' in report and 'REPAIRED_FACTUAL_DRAFT' in report
    repaired = outcome not in {'clean', 'other_error'}
    assert calls.count('repair') == int(repaired)
    assert calls.count('strategy') == (2 if repaired else 1)
    assert calls.count('summary') == (2 if outcome in {'repaired', 'repaired_five', 'second_conflict'} else 1)
    assert calls.count('prefetch') == calls.count('official') == calls.count('chapter') == 1
    assert all(calls.count(section) == 1 for section in ('company_status', 'company_overview', 'market_index_analysis'))
