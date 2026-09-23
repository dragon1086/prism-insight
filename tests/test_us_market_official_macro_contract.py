"""Actual market factory and directory select one noncontradictory contract."""
import importlib.util
from pathlib import Path

import pytest

from prism_core import us_official_macro_sources as macro
from prism_core.us_report_public_inputs import (
    OFFICIAL_MACRO_MARKER,
    apply_public_report_inputs,
)

ROOT = Path(__file__).resolve().parents[1]


def load(name):
    spec = importlib.util.spec_from_file_location('macro_contract_' + name, ROOT / 'prism-us/cores/agents' / f'{name}.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize('language', ['ko', 'en'])
def test_factory_supplied_macro_overrides_price_only_contract(language):
    factory = load('market_index_agents').create_us_market_index_analysis_agent
    agent = factory('20260923', '20250923', 1, language, 'S&P 500: 6500 points', True,
                    official_macro_data='CPI August: 3.4% YoY NSA; PCE July 3.7% YoY')
    assert agent.server_names == []
    assert 'CPI August: 3.4%' in agent.instruction and 'NSA' in agent.instruction
    assert OFFICIAL_MACRO_MARKER in agent.instruction
    assert '데이터만 분석하십시오' not in agent.instruction
    assert 'using only the provided index prices' not in agent.instruction
    assert 'separate macro agent owns' not in agent.instruction
    assert '미수집·미해독은 미발표가 아닙니다' in agent.instruction if language == 'ko' else 'Collection or decoding gaps do not mean unreleased' in agent.instruction


@pytest.mark.parametrize('language', ['ko', 'en'])
def test_directory_renders_once_and_apply_is_idempotent(monkeypatch, language):
    directory = load('__init__')
    original = macro.render_us_official_macro_sources
    calls = []

    def render(*args, **kwargs):
        calls.append(args)
        return original(*args, **kwargs)

    monkeypatch.setattr(macro, 'render_us_official_macro_sources', render)
    packet = {'as_of': '2026-09-23', 'sources': {
        'cpi': {'status': 'released', 'observed_period': 'August 2026', 'excerpt': '3.4% YoY NSA', 'url': 'https://www.bls.gov/news.release/cpi.nr0.htm'},
        'pce': {'status': 'not_found'},
    }}
    inputs = {'market_indices': {'sp500': '6500 points'}, 'shared_macro_available': True, 'official_macro': packet}
    agent = directory.get_us_agent_directory('Example', 'TEST', '20260923', ['market_index_analysis'], language, inputs)['market_index_analysis']
    assert len(calls) == 1 and agent.instruction.count('3.4% YoY NSA') == 1
    assert agent.server_names == []
    assert apply_public_report_inputs(agent, 'market_index_analysis', inputs, language) is agent
    assert len(calls) == 1


@pytest.mark.parametrize('sources', [{}, {'cpi': {'status': 'not_found'}}])
def test_no_official_facts_preserves_shared_price_only_legacy(sources):
    directory = load('__init__')
    agent = directory.get_us_agent_directory('Example', 'TEST', '20260923', ['market_index_analysis'], 'en', {
        'market_indices': {'sp500': '6500 points'}, 'shared_macro_available': True,
        'official_macro': {'sources': sources}})['market_index_analysis']
    assert 'using only the provided index prices' in agent.instruction
    assert OFFICIAL_MACRO_MARKER not in agent.instruction
    assert agent.server_names == []
