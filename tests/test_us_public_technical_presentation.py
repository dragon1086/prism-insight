"""Real US assembly preserves internal evidence but publishes readable facts."""
import asyncio
import importlib.util

import pandas as pd
from test_us_evidence_pipeline_contract import (  # noqa: F401
    analysis,
    isolated_imports_and_effects,
)

from prism_core.report_technical_facts import render_report_technical_facts


def test_real_assembly_humanizes_only_published_technical_packet(analysis, monkeypatch):  # noqa: F811
    frame = pd.DataFrame({'Close': range(100, 320)}, index=pd.date_range('2026-01-01', periods=220))
    original = analysis.importlib.util.spec_from_file_location

    class PrefetchLoader:
        def create_module(self, spec):
            return None

        def exec_module(self, module):
            module.prefetch_us_analysis_data = lambda ticker: {
                'stock_ohlcv': render_report_technical_facts(frame), '_report_ohlcv_frame': frame,
            }

    def spec_for(name, path, *args, **kwargs):
        if name == 'us_data_prefetch':
            return importlib.util.spec_from_loader(name, PrefetchLoader())
        return original(name, path, *args, **kwargs)

    monkeypatch.setattr(analysis.importlib.util, 'spec_from_file_location', spec_for)
    seen = []

    async def synthesis(sections, *args, **kwargs):
        seen.append(sections['price_volume_analysis'])
        return '관측값 319 USD: BAR_FINALITY_UNKNOWN 상태입니다.'

    monkeypatch.setattr(analysis, 'generate_summary', synthesis)
    monkeypatch.setattr(analysis, 'generate_investment_strategy', synthesis)
    # Rendering itself has separate real-renderer tests; no provider fallback.
    monkeypatch.setattr(analysis, 'get_us_price_chart_html', lambda *a, **k: '')
    monkeypatch.setattr(analysis, 'get_us_technical_chart_html', lambda *a, **k: '')
    report = asyncio.run(analysis.analyze_us_stock('TEST', 'Example', '20260923', 'ko', include_news=False))
    assert all('BAR_FINALITY_UNKNOWN' in text for text in seen)
    assert 'BAR_FINALITY_UNKNOWN' not in report
    assert '### 기술지표 계산 기준' in report
    assert '319.0000 USD' in report
    assert '2026-08-08' in report
    assert 'status=complete' not in report
