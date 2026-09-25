"""US report assembly contracts with all external effects replaced by test doubles."""
import asyncio
import builtins
import importlib.util
import io
import logging
import os
import socket
import subprocess
import sys
from contextlib import asynccontextmanager, contextmanager
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pandas as pd
import pytest
import yfinance as yf
from test_us_report_public_inputs import company_packet, macro_packet

ROOT = Path(__file__).resolve().parents[1]


def font_discovery_only(original):
    """Allow only Matplotlib's read-only system font probes, never model CLIs."""
    allowed = {('fc-list', '--help'), ('fc-list', '--format=%{file}\\n'),
               ('system_profiler', '-xml', 'SPFontsDataType')}

    def guarded(process, args, *positional, **kwargs):
        # Positional Popen options can hide shell/executable overrides.
        if (not isinstance(args, (list, tuple)) or tuple(args) not in allowed
                or positional or kwargs.get('shell') or kwargs.get('executable') is not None):
            raise AssertionError('Subprocess access forbidden except exact system font discovery')
        return original(process, args, **kwargs)
    return guarded


@contextmanager
def preserved_import_graph():
    """Restore both import caches: sys.modules and parent package attributes."""
    prefixes = ('cores', 'trading', 'tracking', 'kis_auth', 'domestic_stock_trading',
                'overseas_stock_trading')

    def affected(name):
        return any(name == prefix or name.startswith(prefix + '.') for prefix in prefixes)

    original_path = sys.path[:]
    original_modules = {name: module for name, module in sys.modules.items() if affected(name)}
    original_attributes = {
        name: {key: value for key, value in vars(module).items()
               if isinstance(value, ModuleType)}
        for name, module in original_modules.items() if module is not None
    }
    try:
        yield
    finally:
        sys.path[:] = original_path
        for name in list(sys.modules):
            if affected(name):
                del sys.modules[name]
        sys.modules.update(original_modules)
        for name, attributes in original_attributes.items():
            module = original_modules[name]
            for key, value in list(vars(module).items()):
                if isinstance(value, ModuleType) and affected(value.__name__) and key not in attributes:
                    delattr(module, key)
            vars(module).update(attributes)


@pytest.fixture(autouse=True)
def isolated_imports_and_effects(monkeypatch, tmp_path):
    """US/KR file loaders must not change later imports or invoke real backends."""
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
    monkeypatch.setattr(subprocess.Popen, '__init__', font_discovery_only(subprocess.Popen.__init__))
    # yfinance's curl transport bypasses Python socket.connect.
    import curl_cffi.requests
    monkeypatch.setattr(curl_cffi.requests.Session, 'request', no_network)
    with preserved_import_graph():
        import cores.report_generation as report_generation
        monkeypatch.setattr(report_generation, '_get_report_backend', no_network)
        yield


@pytest.mark.parametrize('args', [
    ['fc-list', '--help'], ['fc-list', '--format=%{file}\\n'],
    ['system_profiler', '-xml', 'SPFontsDataType'],
])
def test_subprocess_guard_allows_only_exact_read_only_font_discovery(args):
    calls = []
    guard = font_discovery_only(lambda process, command, **kwargs: calls.append(command))
    guard(object(), args, stdout=subprocess.PIPE)
    assert calls == [args]


@pytest.mark.parametrize('args,kwargs', [
    (['codex', 'exec', 'prompt'], {}), (['curl', 'https://example.test'], {}),
    (['fc-list', '--help', 'extra'], {}), ('fc-list --help', {}),
    (['fc-list', '--help'], {'shell': True}),
    (['fc-list', '--help'], {'executable': '/bin/sh'}),
])
def test_subprocess_guard_rejects_model_network_and_overrides(args, kwargs):
    def forbidden(*args, **kwargs):
        raise AssertionError('original Popen must not be reached')
    with pytest.raises(AssertionError, match='except exact system font discovery'):
        font_discovery_only(forbidden)(object(), args, **kwargs)


def test_import_graph_restores_parent_attributes_as_well_as_modules():
    import cores
    import cores.report_generation as original

    with preserved_import_graph():
        replacement = ModuleType('cores.report_generation')
        sys.modules['cores.report_generation'] = replacement
        cores.report_generation = replacement
        newly_imported = ModuleType('cores._isolation_probe')
        sys.modules[newly_imported.__name__] = newly_imported
        cores._isolation_probe = newly_imported
    assert sys.modules['cores.report_generation'] is original
    assert cores.report_generation is original
    assert 'cores._isolation_probe' not in sys.modules
    assert not hasattr(cores, '_isolation_probe')


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
    import prism_core.us_peer_comparison as peer_module

    async def no_peers(*args, **kwargs):
        return {'ready': False, 'skip_reason': 'test_stub', 'elapsed': 0.0}

    monkeypatch.setattr(peer_module, 'collect_us_peer_comparison', no_peers)
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
    from prism_core.report_technical_facts import (
        TECHNICAL_FACTS_END,
        TECHNICAL_FACTS_START,
    )
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
    # US publishes a deterministic peer table: no model CE record is copied or moved to the appendix.
    appendix = report.split('## Appendix: source and calculation records')[1]
    assert 'Competitive Evidence' not in appendix and 'Competitive Evidence Handoff' not in report


def test_shared_market_cache_never_receives_ticker_specific_reference(analysis, monkeypatch):
    from prism_core.market_report_singleflight import MarketReportCache
    from prism_core.report_technical_facts import (
        TECHNICAL_FACTS_END,
        TECHNICAL_FACTS_START,
    )
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


def test_code_financial_math_reaches_real_agents_synthesis_and_appendix_without_extra_calls(analysis, monkeypatch):
    from prism_core.report_financial_math import (
        END,
        START,
        render_annual_leverage_calculations,
        render_target_upside_calculations,
    )

    period = pd.Timestamp('2025-12-31')
    income = pd.DataFrame({period: {'EBITDA': 2_000_000_000}})
    balance = pd.DataFrame({period: {'Total Debt': 6_585_000_000, 'Stockholders Equity': 7_170_000_000,
                                    'Cash And Cash Equivalents': 420_000_000}})
    target = render_target_upside_calculations({'target_mean': 247.4}, 234.76,
                                             'regularMarketPrice', '2026-09-23T16:00:00Z', 'dated_recent')
    leverage = render_annual_leverage_calculations(income, balance)
    assert START in target and END in leverage
    original = analysis.importlib.util.spec_from_file_location

    class Loader:
        def create_module(self, spec):
            return None

        def exec_module(self, module):
            module.prefetch_us_analysis_data = lambda ticker: {
                'stock_info': target + '\n| Current Price | $234.76 |',
                'financial_statements': leverage + '\nRAW_FINANCIALS_NOT_A_SHARED_RECORD',
                'company_profile': 'PROFILE', 'analysis_estimates': 'ESTIMATES',
                'stock_ohlcv': 'OHLCV', 'market_indices': {'SPY': 'INDEX_ONLY'}}

    def spec_for(name, path, *args, **kwargs):
        if name == 'us_data_prefetch':
            return importlib.util.spec_from_loader(name, Loader())
        return original(name, path, *args, **kwargs)

    monkeypatch.setattr(analysis.importlib.util, 'spec_from_file_location', spec_for)
    monkeypatch.setattr(analysis, 'get_us_agent_directory', analysis._us_agents_module.get_us_agent_directory)
    calls = []

    def assert_math(text):
        assert '5.38%' in text and '47.8735%' in text
        assert '247.40 USD / 234.76 USD' in text and '2025-12-31' in text

    async def section(agent, name, *args, **kwargs):
        calls.append(name)
        if name in ('company_status', 'company_overview'):
            assert_math(agent.instruction)
        if name == 'market_index_analysis':
            assert '5.38%' not in agent.instruction  # Shared market cache is not ticker-specific.
        return 'MODEL PROSE DELIBERATELY OMITS CALCULATION RECORDS'

    async def strategy(sections, combined, *args, **kwargs):
        calls.append('strategy')
        assert_math(sections['shared_reference'])
        assert_math(combined)
        assert 'RAW_FINANCIALS_NOT_A_SHARED_RECORD' not in sections['shared_reference']
        return 'STRATEGY WITHOUT CALCULATIONS'

    async def summary(sections, *args, **kwargs):
        calls.append('summary')
        assert_math(sections['shared_reference'])
        return 'SUMMARY WITHOUT CALCULATIONS'

    monkeypatch.setattr(analysis, 'generate_report', section)
    monkeypatch.setattr(analysis, 'generate_market_report', section)
    monkeypatch.setattr(analysis, 'generate_investment_strategy', strategy)
    monkeypatch.setattr(analysis, 'generate_summary', summary)
    report = asyncio.run(analysis.analyze_us_stock('TEST', 'Example', '20260923', 'en'))
    assert len(calls) == 8 and len(set(calls)) == 8
    appendix = report.split('## Appendix:', 1)[1]
    assert_math(appendix)
    assert 'RAW_FINANCIALS_NOT_A_SHARED_RECORD' not in appendix
    assert START not in appendix and END not in appendix


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


@pytest.mark.parametrize('language', ['ko', 'en'])
def test_peer_table_follows_overview_and_reaches_overview_agent_and_synthesis(analysis, monkeypatch, language):
    import prism_core.us_peer_comparison as peer_module
    table = '#### 경쟁사 비교 분석\n\n| 구분 | TEST | PEER | 피어 중앙값 |\n|---|---:|---:|---:|\n| 시가총액($B) | 1.0 | 2.0 | 2.0 |'

    async def peers(ticker, company, date, report_language):
        assert (ticker, date, report_language) == ('TEST', '20260923', language)
        return {'ready': True, 'peers': [{}, {}], 'source': 'perplexity', 'misaligned': [],
                'public_markdown': table, 'model_context': table + '\nPEER_CONTEXT_SENTINEL', 'elapsed': 1.0}

    monkeypatch.setattr(peer_module, 'collect_us_peer_comparison', peers)
    seen = {}

    def directory(*args, prefetched_data=None, **kwargs):
        seen['prefetched'] = prefetched_data
        return {name: analysis._report_gen_module.ReportAgent(name, 'BASE')
                for name in ('company_status', 'company_overview')}

    monkeypatch.setattr(analysis, 'get_us_agent_directory', directory)

    async def section(agent, name, *args, **kwargs):
        return f'### {name} BODY'

    async def strategy(sections, combined, *args, **kwargs):
        seen['combined'] = combined
        return 'STRATEGY'

    async def summary(sections, *args, **kwargs):
        seen['summary'] = sections
        return 'SUMMARY'

    monkeypatch.setattr(analysis, 'generate_report', section)
    monkeypatch.setattr(analysis, 'generate_investment_strategy', strategy)
    monkeypatch.setattr(analysis, 'generate_summary', summary)
    report = asyncio.run(analysis.analyze_us_stock('TEST', 'Example', '20260923', language, include_news=False))
    assert seen['prefetched']['peer_comparison']['model_context'].endswith('PEER_CONTEXT_SENTINEL')
    assert table in seen['combined'] and seen['summary']['peer_comparison'] == table
    assert seen['combined'].index('company_overview BODY') < seen['combined'].index(table)
    assert report.index('company_overview BODY') < report.index('#### 경쟁사 비교 분석') < report.index('## 3.')
    assert 'PEER_CONTEXT_SENTINEL' not in report and report.count('#### 경쟁사 비교 분석') == 1
