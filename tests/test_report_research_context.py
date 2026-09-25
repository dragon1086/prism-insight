"""Report enrichment must be optional, bounded and never a new trade authority."""
from types import SimpleNamespace

import pytest

from cores.agents.report_agent import ReportAgent
from prism_core.report_research_context import apply_section_research, market_context_for_buy


def test_absent_packet_is_identity_and_preserves_tools():
    agent = ReportAgent('news', 'original prompt', ['perplexity', 'firecrawl'])
    assert apply_section_research(agent, 'news_analysis', {}, '20260918', 'ko') is agent


def test_usable_news_is_source_only_without_claiming_complete_competition():
    agent = ReportAgent('news', 'Query 1 is REQUIRED; call external tools', ['perplexity'])
    data = {'report_research': {'evidence_id': 'RE-test', 'news_usable': True,
                              'section_notes': {'news_analysis': 'Source S1, public facts; peer unknown'}}}
    out = apply_section_research(agent, 'news_analysis', data, '20260918', 'ko')
    assert out.server_names == ()
    assert 'Query 1 is REQUIRED' not in out.instruction
    assert 'RE-test' in out.instruction
    assert 'does not establish competitive superiority' in out.instruction


@pytest.mark.parametrize('market', ['KR', 'US'])
@pytest.mark.parametrize('language', ['ko', 'en'])
def test_tool_free_news_has_no_competitive_evidence_contract(market, language):
    agent = ReportAgent('news', 'Query 1 is REQUIRED; call external tools', ['perplexity'])
    data = {'report_research': {'evidence_id': 'RE-test', 'news_usable': True,
                              'receipt': {'market': market, 'usable_sources': 1},
                              'section_notes': {'news_analysis': 'Source S1, public facts; peer unknown'}}}
    out = apply_section_research(agent, 'news_analysis', data, '20260918', language)
    assert out.server_names == () and 'RE-test' in out.instruction
    for forbidden in ('Competitive Evidence', 'competitive-evidence', 'SOURCE_CHECKED', 'SEARCH_ONLY',
                      'INCOMPARABLE', 'peer_universe', 'sector_tailwind', 'price_leadership'):
        assert forbidden not in out.instruction
    assert 'separate code-computed competitor comparison table' in out.instruction


def test_unusable_news_leaves_original_fallback_unchanged():
    agent = ReportAgent('news', 'original', ['perplexity'])
    data = {'report_research': {'news_usable': False, 'section_notes': {'news_analysis': 'failed'}}}
    assert apply_section_research(agent, 'news_analysis', data, '20260918', 'en') is agent


def test_partial_prefetch_reused_without_waiving_required_peer_research():
    agent = ReportAgent('news', 'Query 1 is REQUIRED', ['perplexity', 'firecrawl'])
    data = {'report_research': {'news_usable': False, 'receipt': {'usable_sources': 1},
                               'section_notes': {'news_analysis': 'S1 exact source, peer UNKNOWN'}}}
    result = apply_section_research(agent, 'news_analysis', data, '20260918', 'en')
    assert result.instruction.startswith(agent.instruction)
    assert 'S1 exact source' in result.instruction
    assert result.server_names == agent.server_names


def test_other_section_keeps_existing_fact_prompt_and_tools():
    agent = ReportAgent('company', 'existing numeric prefetch', ['firecrawl'])
    data = {'report_research': {'evidence_id': 'RE-test', 'section_notes': {'company_overview': 'x' * 9000}}}
    out = apply_section_research(agent, 'company_overview', data, '20260918', 'en')
    assert out.instruction.startswith(agent.instruction)
    assert out.server_names == agent.server_names
    assert len(out.instruction) < 7500
    assert 'oversized_source_material_omitted' in out.instruction
    assert 'UNKNOWN' in out.instruction
    assert 'x' * 100 not in out.instruction


def test_oversized_news_does_not_disable_original_discovery():
    agent = ReportAgent('news', 'Query 1 is REQUIRED', ['perplexity', 'firecrawl'])
    packet = {'news_usable': True, 'section_notes': {'news_analysis': 'x' * 6001}}
    out = apply_section_research(agent, 'news_analysis', {'report_research': packet}, '20260918', 'en')
    assert out.server_names == agent.server_names
    assert out.instruction.startswith(agent.instruction)
    assert 'oversized_source_material_omitted' in out.instruction


def test_malformed_receipt_preserves_original_agent():
    agent = ReportAgent('news', 'original', ['perplexity'])
    for receipt in ('unexpected', ['unexpected'], {'usable_sources': '1'}):
        packet = {'receipt': receipt, 'section_notes': {'news_analysis': 'unverified'}}
        assert apply_section_research(agent, 'news_analysis', {'report_research': packet},
                                      '20260918', 'en') is agent


def test_source_json_and_table_survive_injection_whole():
    import json
    agent = ReportAgent('news', 'Query 1 is REQUIRED', ['perplexity'])
    source = json.dumps({'sources': [{'source_id': 'S1', 'excerpt':
        '| Supplier | Q2 2026 |\n| --- | --- |\n| Micron | 24% |\n| Samsung | 38% |'}]})
    packet = {'evidence_id': 'RE-table', 'receipt': {'usable_sources': 1},
              'section_notes': {'news_analysis': source}}
    out = apply_section_research(agent, 'news_analysis', {'report_research': packet}, '20260918', 'en')
    envelope = json.loads(out.instruction[out.instruction.index('{'):])
    assert envelope['source_material'] == source
    assert json.loads(envelope['source_material'])['sources'][0]['source_id'] == 'S1'
    assert out.server_names == agent.server_names
    for term in ('SOURCE_METADATA_UNVERIFIED', 'rounded share total', 'table transcription',
                 'not merely a search snippet', 'independently verifying its claims'):
        assert term in out.instruction


def test_market_context_off_empty_and_industry_scope_preserved():
    assert market_context_for_buy(None) == ''
    assert market_context_for_buy({'market_regime': 'sideways'}) == ''
    context = {'market_regime': 'sideways', 'market_intelligence': {'packet_id': 'MI-1'},
               'leading_sectors': [{'sector': 'Technology', 'industry': 'Semiconductors'}]}
    text = market_context_for_buy(context)
    assert 'Semiconductors' in text and 'Technology' in text and 'MI-1' in text
    assert 'not an additional score' in text


def test_malformed_values_fail_open():
    agent = ReportAgent('news', 'legacy', ['perplexity'])
    for packet in (None, [], {'section_notes': []}, {'section_notes': {'news_analysis': None}}):
        assert apply_section_research(agent, 'news_analysis', {'report_research': packet}, '20260918', 'en') is agent
    assert market_context_for_buy(SimpleNamespace()) == ''


def test_real_kr_us_factories_reuse_source_packet_and_off_is_identical():
    import importlib.util
    from pathlib import Path
    from cores.agents import get_agent_directory

    path = Path(__file__).resolve().parents[1] / 'prism-us/cores/agents/__init__.py'
    spec = importlib.util.spec_from_file_location('research_us_factory', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    for factory, symbol in ((get_agent_directory, '005930'),
                            (module.get_us_agent_directory, 'MSFT')):
        for language in ('ko', 'en'):
            sections = ['news_analysis', 'company_overview', 'company_status']
            args = ('Example', symbol, '20260918', sections, language)
            original = factory(*args)
            disabled = factory(*args, prefetched_data={})
            for section in sections:
                assert original[section].instruction == disabled[section].instruction
                assert original[section].server_names == disabled[section].server_names
            packet = {'evidence_id': 'RE-shared', 'news_usable': True,
                      'section_notes': {section: 'S1: evidence UNKNOWN peer comparison'
                                        for section in sections}}
            enabled = factory(*args, prefetched_data={'report_research': packet})
            assert not enabled['news_analysis'].server_names
            for section in sections:
                assert 'RE-shared' in enabled[section].instruction
                assert 'UNKNOWN' in enabled[section].instruction
                if section != 'news_analysis':
                    assert enabled[section].server_names == original[section].server_names
