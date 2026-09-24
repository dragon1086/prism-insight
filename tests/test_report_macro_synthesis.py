"""Public macro rendering is identical in synthesis inputs and the final report."""
import asyncio
import copy
import importlib.util

import pytest

from prism_core.report_macro_section import render_macro_section
from test_us_evidence_pipeline_contract import analysis, isolated_imports_and_effects  # noqa: F401


CONTEXT = {"market_regime": "moderate_bull", "regime_rationale": "MACRO_SENTINEL rationale",
           "leading_sectors": [{"sector": "Technology"}],
           "lagging_sectors": [{"sector": "Utilities"}],
           "risk_events": [{"event": "Inflation", "severity": "high"}]}


@pytest.mark.parametrize("market", ["KR", "US"])
@pytest.mark.parametrize("language", ["ko", "en"])
def test_structured_renderer_preserves_existing_exact_format(market, language):
    expected = (
        "### 거시경제 환경\n\n**시장 체제**: 보통 강세장\n\n"
        "**판단 근거**: MACRO_SENTINEL rationale\n\n**주도 섹터**: Technology\n\n"
        "**소외 섹터**: Utilities\n\n- ⚠️ Inflation (영향: high)\n\n"
        if language == "ko" else
        "### Macroeconomic Environment\n\n**Market Regime**: Moderate Bull\n\n"
        "**Rationale**: MACRO_SENTINEL rationale\n\n**Leading Sectors**: Technology\n\n"
        "**Lagging Sectors**: Utilities\n\n- ⚠️ Inflation (Severity: high)\n\n"
    )
    original = copy.deepcopy(CONTEXT)
    assert render_macro_section(CONTEXT, language, market) == expected
    assert CONTEXT == original
    assert render_macro_section(None, language, market) == ""


@pytest.mark.parametrize("language", ["ko", "en"])
def test_existing_market_specific_prose_heading_and_public_guard(language):
    context = {"report_prose": "### Existing prose\nMACRO_SENTINEL"}
    assert render_macro_section(context, language, "KR") == context["report_prose"] + "\n\n"
    heading = "### 거시경제 환경\n\n" if language == "ko" else "### Macroeconomic Environment\n\n"
    assert render_macro_section(context, language, "US") == heading + context["report_prose"] + "\n\n"
    context["market_intelligence"] = {}
    assert "MACRO_SENTINEL" not in render_macro_section(context, language, "US")


def observe_synthesis(monkeypatch, module, market, language):
    from prism_core import report_macro_section
    expected = render_macro_section(CONTEXT, language, market)
    renders, seen = [], []

    def render(*args):
        renders.append(args)
        return render_macro_section(*args)

    async def strategy(sections, combined, *args, **kwargs):
        assert sections["macro_context"] == expected
        assert expected in combined
        assert "--- MACRO_CONTEXT ---" in combined
        seen.append("strategy")
        return "Strategy without repeating macro"

    async def summary(sections, *args, **kwargs):
        assert sections["macro_context"] == expected
        seen.append("summary")
        return "Summary without repeating macro"

    monkeypatch.setattr(report_macro_section, "render_macro_section", render)
    monkeypatch.setattr(module, "generate_investment_strategy", strategy)
    monkeypatch.setattr(module, "generate_summary", summary)
    return expected, renders, seen


@pytest.mark.parametrize("language", ["ko", "en"])
def test_actual_us_orchestration_shares_one_macro_block(analysis, monkeypatch, language):  # noqa: F811
    original = analysis.importlib.util.spec_from_file_location

    class Loader:
        def create_module(self, spec): return None
        def exec_module(self, module): module.prefetch_us_analysis_data = lambda ticker: {}

    def spec_for(name, path, *args, **kwargs):
        return importlib.util.spec_from_loader(name, Loader()) if name == "us_data_prefetch" else original(name, path, *args, **kwargs)

    monkeypatch.setattr(analysis.importlib.util, "spec_from_file_location", spec_for)
    expected, renders, seen = observe_synthesis(monkeypatch, analysis, "US", language)
    report = asyncio.run(analysis.analyze_us_stock("TEST", "Example", "20260923", language,
                                                 include_news=False, macro_context=copy.deepcopy(CONTEXT)))
    assert seen == ["strategy", "summary"] and len(renders) == 1
    assert report.count(analysis.clean_markdown(expected).strip()) == 1


@pytest.mark.parametrize("language", ["ko", "en"])
def test_actual_kr_orchestration_shares_one_macro_block(monkeypatch, tmp_path, language):
    import cores.data_prefetch as prefetch
    import prism_core.kr_official_report_inputs as official
    import prism_core.report_research_prefetch as research
    from cores import analysis as kr_analysis
    from cores.llm import capabilities

    monkeypatch.setattr(prefetch, "prefetch_kr_analysis_data", lambda *args: {})
    async def empty(*args, **kwargs): return {}
    async def base(*args, **kwargs): return "Base section without macro"
    monkeypatch.setattr(official, "collect_kr_official_report_inputs", empty)
    monkeypatch.setattr(research, "prefetch_report_research", empty)
    monkeypatch.setattr(kr_analysis, "generate_report", base)
    monkeypatch.setattr(kr_analysis, "generate_market_report", base)
    monkeypatch.setattr(kr_analysis, "get_chart_as_base64_html", lambda *args, **kwargs: "")
    monkeypatch.setattr(capabilities, "vision_available", lambda: False)
    monkeypatch.setattr(capabilities, "vision_buy_quality_active", lambda: False)
    monkeypatch.setenv("PRISM_PARALLEL_REPORT", "false")
    work = tmp_path / "work"
    work.mkdir()
    monkeypatch.chdir(work)
    kr_analysis._market_analysis_cache.clear()
    expected, renders, seen = observe_synthesis(monkeypatch, kr_analysis, "KR", language)
    report = asyncio.run(kr_analysis.analyze_stock("017670", "Example", "20260923", language,
                                                 macro_context=copy.deepcopy(CONTEXT)))
    assert seen == ["strategy", "summary"] and len(renders) == 1
    assert report.count(kr_analysis.clean_markdown(expected).strip()) == 1
    assert all("MACRO_SENTINEL" not in value for value in kr_analysis._market_analysis_cache.values())
