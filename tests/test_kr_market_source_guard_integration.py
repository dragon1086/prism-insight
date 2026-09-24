"""Real KR pipeline preflight on both generated and cached market drafts."""
import asyncio

from test_report_conflict_recovery import pipeline  # noqa: F401
from test_us_evidence_pipeline_contract import isolated_imports_and_effects  # noqa: F401


def test_standalone_kr_market_claim_removed_before_assessor_strategy_and_summary(pipeline, monkeypatch):  # noqa: F811
    analysis, _, calls, _ = pipeline
    import cores.report_fact_editor as editor
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

    async def assess(reports, *args):
        check(reports)
        stages.append('assess')

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
    monkeypatch.setattr(editor, 'assess_report_facts', assess)
    monkeypatch.setattr(analysis, 'generate_investment_strategy', strategy)
    monkeypatch.setattr(analysis, 'generate_summary', summary)
    for _ in range(2):
        report = asyncio.run(analysis.analyze_stock('017670', 'Example', '20260925', require_dart_depth=True))
        assert bad not in report and '0.25' not in report and '제외했습니다' in report
    assert calls.count('unsafe_market_generation') == 1  # second draft came from market cache
    assert stages == ['assess', 'strategy', 'summary'] * 2
    assert bad in analysis._market_analysis_cache.values()  # raw cache is not silently rewritten
