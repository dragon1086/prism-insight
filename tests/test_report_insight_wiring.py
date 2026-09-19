"""Actual factory and report-port input boundary checks, no live tools/models."""
import asyncio
from types import SimpleNamespace

import pytest

from cores.agents import get_agent_directory
from prism_core.report_insight_manifest import build_insight_manifest


def test_actual_kr_factory_only_adds_section_owned_manifest():
    pf = {'stock_ohlcv': 'prices', 'trading_volume': 'flows'}
    pf['report_insight_manifest'] = build_insight_manifest('KR', 'EXM', '20260918', pf, {})
    agents = get_agent_directory('Example', '123456', '20260918', ['news_analysis', 'investor_trading_analysis'], prefetched_data=pf)
    assert 'direct_peers_competitive_position' in agents['news_analysis'].instruction
    assert 'flows_positioning' in agents['investor_trading_analysis'].instruction
    assert 'direct_peers_competitive_position' not in agents['investor_trading_analysis'].instruction
    assert tuple(agents['investor_trading_analysis'].server_names) == ()


@pytest.mark.parametrize('flag,budget', [('1', (6000, 18000)), ('0', None)])
def test_actual_report_port_sets_guard_only_when_opted_in(monkeypatch, flag, budget):
    from cores import report_generation as generation
    from prism_core.report_insight_prefetch import PROFILE
    seen = []
    class Backend:
        async def run(self, spec, message):
            seen.append(spec)
            return SimpleNamespace(text='report')
    monkeypatch.setenv('PRISM_REPORT_INSIGHT_PREFETCH', flag)
    monkeypatch.setattr(generation, '_get_report_backend', lambda: Backend())
    agent = SimpleNamespace(name='news', instruction='source review', server_names=('perplexity', 'firecrawl'),
                            report_research_profile=PROFILE if flag == '1' else None)
    text = asyncio.run(generation._generate_agent_text(agent, 'question', max_tokens=1000, max_iterations=2))
    assert text == 'report'
    assert seen[0].params.report_research_tool_budget == budget
    assert seen[0].params.max_tokens == 1000


def test_process_flag_alone_cannot_guard_unapproved_report(monkeypatch):
    from cores import report_generation as generation
    seen = []
    class Backend:
        async def run(self, spec, message):
            seen.append(spec)
            return SimpleNamespace(text='report')
    monkeypatch.setenv('PRISM_REPORT_INSIGHT_PREFETCH', '1')
    monkeypatch.setattr(generation, '_get_report_backend', lambda: Backend())
    agent = SimpleNamespace(name='excluded', instruction='legacy', server_names=('firecrawl',))
    asyncio.run(generation._generate_agent_text(agent, 'question', max_tokens=1000, max_iterations=2))
    assert seen[0].params.report_research_tool_budget is None


def test_actual_us_factory_preserves_per_report_policy_marker():
    import importlib.util
    from pathlib import Path

    from prism_core.report_insight_prefetch import PROFILE

    path = Path(__file__).resolve().parents[1] / 'prism-us/cores/agents/__init__.py'
    spec = importlib.util.spec_from_file_location('us_insight_policy_agents', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    pf = {'report_research': {'receipt': {'collector_version': PROFILE}, 'section_notes': {}}}
    pf['report_insight_manifest'] = build_insight_manifest('US', 'MU', '20260918', pf, {})
    agent = module.get_us_agent_directory('Micron', 'MU', '20260918', ['news_analysis'], prefetched_data=pf)['news_analysis']
    assert agent.report_research_profile == PROFILE
