"""US report assembly contracts with all external effects replaced by test doubles."""
import asyncio
import builtins
import importlib.util
import io
import logging
import os
import socket
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest
import yfinance as yf

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def isolated_imports_and_effects(monkeypatch, tmp_path):
    """US file loaders must not change a later test's root-package resolution."""
    prefixes = ('cores', 'trading', 'tracking', 'kis_auth', 'domestic_stock_trading',
                'overseas_stock_trading')

    def affected(name):
        return any(name == prefix or name.startswith(prefix + '.') for prefix in prefixes)

    original_path = sys.path[:]
    original_modules = {name: module for name, module in sys.modules.items() if affected(name)}
    monkeypatch.setenv('PYTHON_DOTENV_DISABLED', '1')
    monkeypatch.setenv('KIS_CONFIG_ROOT', str(tmp_path))
    forbidden = {'.env', '.env.mcp-cloud', 'kis_devlp.yaml', 'mcp_agent.secrets.yaml',
                 'mcp-agent.secrets.yaml', 'mcp_agent.config.yaml', 'mcp-agent.config.yaml'}

    def guarded_open(original):
        def open_without_credentials(file, *args, **kwargs):
            if isinstance(file, (str, bytes, os.PathLike)) and Path(os.fsdecode(file)).name in forbidden:
                raise AssertionError('Credential/config access forbidden in report contract tests')
            return original(file, *args, **kwargs)
        return open_without_credentials

    def no_network(*args, **kwargs):
        raise AssertionError('Network access forbidden in report contract tests')

    monkeypatch.setattr(builtins, 'open', guarded_open(builtins.open))
    monkeypatch.setattr(io, 'open', guarded_open(io.open))
    monkeypatch.setattr(socket.socket, 'connect', no_network)
    monkeypatch.setattr(socket.socket, 'connect_ex', no_network)
    monkeypatch.setattr(socket, 'create_connection', no_network)
    try:
        yield
    finally:
        sys.path[:] = original_path
        for name in list(sys.modules):
            if affected(name):
                del sys.modules[name]
        sys.modules.update(original_modules)


def load(relative):
    spec = importlib.util.spec_from_file_location('us_evidence_' + Path(relative).stem, ROOT / relative)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def analysis(monkeypatch):
    import dotenv
    monkeypatch.setattr(dotenv, 'load_dotenv', lambda *a, **k: None)
    module = load('prism-us/cores/us_analysis.py')

    class App:
        def __init__(self, **kwargs):
            pass

        @asynccontextmanager
        async def run(self):
            yield SimpleNamespace(logger=logging.getLogger('test-us-evidence'))

    monkeypatch.setattr(module, 'MCPApp', App)
    monkeypatch.setattr(module, 'get_us_agent_directory', lambda *a, **k: {
        name: module._report_gen_module.ReportAgent(name, 'BASE')
        for name in ('company_status', 'price_volume_analysis', 'market_index_analysis')})

    async def model(*args, **kwargs):
        return 'MODEL BODY WITHOUT COLLECTION METADATA'

    async def no_sleep(*args):
        return None

    monkeypatch.setattr(module, 'generate_report', model)
    monkeypatch.setattr(module, 'generate_market_report', model)
    monkeypatch.setattr(module.asyncio, 'sleep', no_sleep)
    monkeypatch.setattr(yf, 'Ticker', lambda ticker: SimpleNamespace(history=lambda **kwargs: pd.DataFrame()))
    import prism_core.report_research_prefetch as research

    async def no_research(*args, **kwargs):
        return {}

    monkeypatch.setattr(research, 'prefetch_report_research', no_research)
    monkeypatch.setattr(module, 'collect_us_public_report_inputs', no_research)
    return module


@pytest.mark.parametrize('language', ['ko', 'en'])
@pytest.mark.parametrize('state', ['complete', 'partial', 'missing', 'error', None])
def test_actual_report_assembly_keeps_receipt_before_both_synthesis_calls(analysis, monkeypatch, language, state):
    receipt = {'status': state, 'source': 'Yahoo Finance / yfinance',
               'captured_at': '2026-09-23T18:00:00+00:00', 'estimate_published_at': None,
               'components': {'earnings_estimate': 'available', 'revenue_estimate': 'error'}} if state else {}
    original = analysis.importlib.util.spec_from_file_location

    class PrefetchLoader:
        def create_module(self, spec):
            return None

        def exec_module(self, module):
            module.prefetch_us_analysis_data = lambda ticker: {'analysis_estimates_status': receipt}

    def spec_for(name, path, *args, **kwargs):
        if name == 'us_data_prefetch':
            return importlib.util.spec_from_loader(name, PrefetchLoader())
        return original(name, path, *args, **kwargs)

    monkeypatch.setattr(analysis.importlib.util, 'spec_from_file_location', spec_for)
    seen = []

    async def synthesis(sections, *args, **kwargs):
        seen.append(sections['company_status'])
        return 'SYNTHESIS OMITTED ALL RECEIPTS'

    monkeypatch.setattr(analysis, 'generate_investment_strategy', synthesis)
    monkeypatch.setattr(analysis, 'generate_summary', synthesis)
    report = asyncio.run(analysis.analyze_us_stock('TEST', 'Example', '20260923', language, include_news=False))
    text = analysis.render_us_analyst_receipt(receipt, language)
    assert text in report and len(seen) == 2 and all(text in s for s in seen)
    trading = load('prism-us/cores/agents/trading_agents.py')
    assert 'forecast EPS' in trading.create_us_trading_scenario_agent('en').instruction


@pytest.mark.parametrize('invalid', [None, [], 'SECRET', {'status': 'SECRET', 'source': 'SECRET',
                                                       'captured_at': 'SECRET', 'components': {'x': 'SECRET'}}])
def test_invalid_receipt_is_safe_and_cannot_inject_text(analysis, invalid):
    text = analysis.render_us_analyst_receipt(invalid, 'en')
    assert 'SECRET' not in text and 'unverified' in text.lower()


@pytest.mark.parametrize('captured', ['2026-09-23T18:00:00', '9999-12-31T23:59:59-01:00'])
def test_invalid_capture_time_stays_unverified(analysis, captured):
    text = analysis.render_us_analyst_receipt({'captured_at': captured}, 'en')
    assert 'Capture time (UTC): unverified' in text


def test_news_is_reused_before_financials_and_canonical_facts_reach_pdf_body(analysis, monkeypatch):
    from prism_core.report_technical_facts import TECHNICAL_FACTS_START, TECHNICAL_FACTS_END
    original = analysis.importlib.util.spec_from_file_location
    canonical = 'CANONICAL SMA50 233.3818 ASOF 2026-09-21'

    class PrefetchLoader:
        def create_module(self, spec):
            return None

        def exec_module(self, module):
            module.prefetch_us_analysis_data = lambda ticker: {
                'stock_ohlcv': TECHNICAL_FACTS_START + canonical + TECHNICAL_FACTS_END,
                'stock_info': '| Current Price | $234.76 |'}

    def spec_for(name, path, *args, **kwargs):
        if name == 'us_data_prefetch':
            return importlib.util.spec_from_loader(name, PrefetchLoader())
        return original(name, path, *args, **kwargs)

    monkeypatch.setattr(analysis.importlib.util, 'spec_from_file_location', spec_for)
    names = ('price_volume_analysis', 'company_status', 'company_overview', 'news_analysis')
    monkeypatch.setattr(analysis, 'get_us_agent_directory', lambda *a, **k: {
        name: analysis._report_gen_module.ReportAgent(name, 'BASE') for name in names})
    calls = []

    raw_record = '#### Competitive Evidence\n```json\n{"name":"fact","arguments":{"value":100}}\n```\n'

    async def generate(agent, section, *args, **kwargs):
        calls.append(section)
        assert canonical in agent.instruction
        if section == 'news_analysis':
            return 'GUIDANCE_SOURCE_SENTINEL https://example.test/official\n' + raw_record
        if section in ('company_status', 'company_overview'):
            assert 'GUIDANCE_SOURCE_SENTINEL' in agent.instruction
        if section == 'company_overview':
            assert 'FINANCIAL_PERIOD_SENTINEL' in agent.instruction
        return 'FINANCIAL_PERIOD_SENTINEL'

    seen = []

    async def synthesis(sections, *args, **kwargs):
        assert canonical in sections['price_volume_analysis']
        assert '$234.76' in sections['shared_reference']
        seen.append(True)
        return 'SHORT SYNTHESIS'

    monkeypatch.setattr(analysis, 'generate_report', generate)
    monkeypatch.setattr(analysis, 'generate_investment_strategy', synthesis)
    monkeypatch.setattr(analysis, 'generate_summary', synthesis)
    report = asyncio.run(analysis.analyze_us_stock('TEST', 'Example', '20260923', 'en'))
    assert calls.count('news_analysis') == 1 and len(calls) == 4
    assert len(seen) == 2 and canonical in report
    assert '```json\n{"name":"fact","arguments":{"value":100}}\n```' in report.split('## Appendix:')[1]


def test_shared_market_cache_never_receives_ticker_specific_reference(analysis, monkeypatch):
    from prism_core.market_report_singleflight import MarketReportCache
    from prism_core.report_technical_facts import TECHNICAL_FACTS_START, TECHNICAL_FACTS_END
    original = analysis.importlib.util.spec_from_file_location

    class Loader:
        def create_module(self, spec):
            return None

        def exec_module(self, module):
            module.prefetch_us_analysis_data = lambda ticker: {
                'stock_ohlcv': TECHNICAL_FACTS_START + 'PRIVATE_STOCK_' + ticker + TECHNICAL_FACTS_END,
                'stock_info': '| Current Price | $234.76 |'}

    def spec_for(name, path, *args, **kwargs):
        if name == 'us_data_prefetch':
            return importlib.util.spec_from_loader(name, Loader())
        return original(name, path, *args, **kwargs)

    monkeypatch.setattr(analysis.importlib.util, 'spec_from_file_location', spec_for)
    market_calls = []

    async def market(agent, *args, **kwargs):
        market_calls.append(agent.instruction)
        assert 'PRIVATE_STOCK' not in agent.instruction and '$234.76' not in agent.instruction
        return 'SHARED MARKET ONLY'

    async def synthesis(*args, **kwargs):
        return 'SYNTHESIS'

    monkeypatch.setattr(analysis, 'generate_market_report', market)
    monkeypatch.setattr(analysis, 'generate_investment_strategy', synthesis)
    monkeypatch.setattr(analysis, 'generate_summary', synthesis)

    async def run():
        cache = MarketReportCache()
        for ticker in ('FIRST', 'SECOND'):
            await analysis.analyze_us_stock(ticker, ticker, '20260923', 'en',
                                           include_news=False, market_report_cache=cache)

    asyncio.run(run())
    assert len(market_calls) == 1


def test_official_inputs_reach_real_factories_synthesis_and_public_report(analysis, monkeypatch):
    from test_us_report_public_inputs import company_packet, macro_packet
    original = analysis.importlib.util.spec_from_file_location

    class Loader:
        def create_module(self, spec):
            return None

        def exec_module(self, module):
            module.prefetch_us_analysis_data = lambda ticker: {
                'stock_info': '| Current Price | $234.76 |', 'company_profile': 'PROFILE',
                'financial_statements': 'FINANCIALS', 'analysis_estimates': 'ESTIMATES',
                'stock_ohlcv': 'OHLCV', 'market_indices': {'SPY': 'INDEX_ONLY'}}

    def spec_for(name, path, *args, **kwargs):
        if name == 'us_data_prefetch':
            return importlib.util.spec_from_loader(name, Loader())
        return original(name, path, *args, **kwargs)

    async def public_inputs(*args, **kwargs):
        return {'official_company': company_packet(), 'official_macro': macro_packet()}

    monkeypatch.setattr(analysis.importlib.util, 'spec_from_file_location', spec_for)
    monkeypatch.setattr(analysis, 'collect_us_public_report_inputs', public_inputs)
    monkeypatch.setattr(analysis, 'get_us_agent_directory', analysis._us_agents_module.get_us_agent_directory)
    calls = []

    async def section(agent, name, *args, **kwargs):
        calls.append(name)
        if name in ('company_status', 'news_analysis'):
            assert 'GUIDANCE_SOURCE' in agent.instruction
        if name == 'company_overview':
            assert 'SEGMENT_SOURCE' in agent.instruction
        if name == 'market_index_analysis':
            assert 'CPI_SOURCE_SENTINEL' in agent.instruction
            assert agent.server_names == []
            assert 'GUIDANCE_SOURCE' not in agent.instruction
        return 'REPORT SECTION'

    async def strategy(sections, combined, *args, **kwargs):
        calls.append('strategy')
        assert '2026-07-23' in sections['company_status']
        assert '2026-09-30' in sections['market_index_analysis']
        assert '4.76%' in combined and '+0.20%p' in combined
        return 'STRATEGY'

    async def summary(sections, *args, **kwargs):
        calls.append('summary')
        assert '2026-07-23' in sections['company_status']
        return 'SUMMARY'

    monkeypatch.setattr(analysis, 'generate_report', section)
    monkeypatch.setattr(analysis, 'generate_market_report', section)
    monkeypatch.setattr(analysis, 'generate_investment_strategy', strategy)
    monkeypatch.setattr(analysis, 'generate_summary', summary)
    report = asyncio.run(analysis.analyze_us_stock('TEST', 'Example', '20260923', 'en'))
    assert len(calls) == 8 and len(set(calls)) == 8
    assert 'Official company source coverage' in report and 'Official macro source coverage' in report
    assert 'GUIDANCE_SOURCE' not in report and 'CPI_SOURCE_SENTINEL' not in report
    assert '2026-09-30' in report and '4.76%' in report


@pytest.mark.parametrize('language', ['ko', 'en'])
def test_trading_prompt_finality_never_promotes_last_hour_or_cached_preclose(language):
    trading = load('prism-us/cores/agents/trading_agents.py')
    buy = trading.create_us_trading_scenario_agent(language).instruction
    sell = trading.create_us_sell_decision_agent(language).instruction
    for text in (buy, sell):
        assert '사실상 확정' not in text and "today's data is settled" not in text
        assert ('조기폐장' if language == 'ko' else 'early close') in text
        assert ('마감 전 수집' if language == 'ko' else 'captured before close') in text
        assert ('선택적' if language == 'ko' else 'optional') in text
        assert ('회사 가이던스' if language == 'ko' else 'Company guidance') in text
        assert ('유기적 성장' if language == 'ko' else 'organic') in text
    assert ('확정 종가' if language == 'ko' else 'confirmed close') in sell
