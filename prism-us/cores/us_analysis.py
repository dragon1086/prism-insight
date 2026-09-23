"""
US Stock Analysis Module

Generate comprehensive stock analysis reports for US stocks.
Uses yfinance MCP server for market data and US-specific agents.
"""
import os
import asyncio
from datetime import datetime, timezone
from pathlib import Path
from prism_core.competitive_evidence import attach_competitive_evidence
from prism_core.report_technical_facts import extract_report_technical_facts
from prism_core.us_report_consistency import add_shared_context, reference_context, evidence_appendix

from dotenv import load_dotenv
load_dotenv()

from mcp_agent.app import MCPApp

# Set up import paths
import sys
import importlib.util
_prism_us_dir = Path(__file__).parent.parent
_project_root = Path(__file__).parent.parent.parent

# Import from main project's cores using direct file import to avoid namespace collision
# This is necessary because prism-us/cores/ shadows the main project's cores/
def _import_from_project_root(module_name: str, file_path: Path):
    """Import a module directly from a specific file path."""
    spec = importlib.util.spec_from_file_location(module_name, file_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _import_root_module_with_root_cores(module_name: str, file_path: Path):
    """Import a ROOT-project module by file path while temporarily making the
    ROOT ``cores`` package authoritative for the duration of ``exec_module``.

    Why this is needed:
    prism-us runs as ``python prism-us/us_stock_analysis_orchestrator.py``, so
    ``sys.path[0]`` is the ``prism-us/`` dir and ``import cores`` binds to
    ``prism-us/cores`` — which SHADOWS the root ``cores`` package. Modules like
    ``cores/report_generation.py`` contain TOP-LEVEL absolute imports
    (``from cores.agents.report_agent import ReportAgent``, ``from cores.llm...``,
    ``from cores.openai_error_logging import ...``) that must resolve against the
    ROOT ``cores`` package. ``prism-us/cores/agents/`` has no ``report_agent``, so
    a plain file-path exec raises
    ``ModuleNotFoundError: No module named 'cores.agents.report_agent'``.

    Fix: for the exec only, (a) put the project root at the front of ``sys.path``
    and (b) evict the prism-us ``cores``/``cores.*`` entries from ``sys.modules``
    so the exec re-imports ``cores`` and its submodules from ROOT. Everything is
    restored in ``finally`` so the rest of the prism-us process keeps seeing
    ``cores`` == ``prism-us/cores``. report_generation performs its ``cores.*``
    imports only at module top level, so the swap only needs to be active while
    exec_module runs; the returned module keeps its ROOT-backed bindings.
    """
    root_str = str(_project_root)

    # Snapshot & evict the prism-us `cores` (and submodules) so the exec below
    # re-imports `cores`/`cores.agents`/`cores.llm`/... from ROOT.
    saved_modules = {
        name: mod
        for name, mod in list(sys.modules.items())
        if name == "cores" or name.startswith("cores.")
    }
    for name in saved_modules:
        del sys.modules[name]

    path_inserted = False
    if not sys.path or sys.path[0] != root_str:
        sys.path.insert(0, root_str)
        path_inserted = True
    try:
        spec = importlib.util.spec_from_file_location(module_name, file_path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        if path_inserted:
            try:
                sys.path.remove(root_str)
            except ValueError:
                pass
        # Drop any ROOT `cores.*` entries created during the exec, then restore
        # the original prism-us `cores` view so downstream prism-us imports keep
        # resolving against prism-us/cores.
        for name in [
            n for n in list(sys.modules) if n == "cores" or n.startswith("cores.")
        ]:
            del sys.modules[name]
        sys.modules.update(saved_modules)


# Import report_generation from main project's cores.
# report_generation.py has top-level `from cores.X import ...` statements that
# must bind to the ROOT cores package, so use the cores-swapping loader.
_report_gen_module = _import_root_module_with_root_cores(
    "main_report_generation",
    _project_root / "cores" / "report_generation.py"
)
generate_report = _report_gen_module.generate_report
generate_summary = _report_gen_module.generate_summary
generate_investment_strategy = _report_gen_module.generate_investment_strategy
get_disclaimer = _report_gen_module.get_disclaimer
generate_market_report = _report_gen_module.generate_market_report

# Import utils from main project's cores
_utils_module = _import_from_project_root(
    "main_utils",
    _project_root / "cores" / "utils.py"
)
clean_markdown = _utils_module.clean_markdown

# Add prism-us directory for local imports
sys.path.insert(0, str(_prism_us_dir))

# Import from prism-us local cores.agents using relative import path
# We need to import the local agents module directly
_agents_path = _prism_us_dir / "cores" / "agents" / "__init__.py"
_spec = importlib.util.spec_from_file_location("us_agents", _agents_path)
_us_agents_module = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_us_agents_module)
get_us_agent_directory = _us_agents_module.get_us_agent_directory

from prism_core.market_report_singleflight import MarketReportCache

# Import chart functions from us_stock_chart module
_chart_module = _import_from_project_root(
    "us_stock_chart",
    _prism_us_dir / "cores" / "us_stock_chart.py"
)
get_us_price_chart_html = _chart_module.get_us_price_chart_html
get_us_institutional_chart_html = _chart_module.get_us_institutional_chart_html
get_us_technical_chart_html = _chart_module.get_us_technical_chart_html

_social_client_module = _import_from_project_root(
    "us_social_sentiment_client",
    _prism_us_dir / "cores" / "us_social_sentiment_client.py"
)
USSocialSentimentClient = _social_client_module.USSocialSentimentClient


def render_us_analyst_receipt(status, language="ko"):
    """Public, allowlisted collection metadata; never issuer or forecast evidence."""
    data = status if isinstance(status, dict) else {}
    ko = language == 'ko'
    unknown = '미확인' if ko else 'unverified'
    labels = ({'complete': '전체 항목 일부 확보', 'partial': '일부 항목 확보',
               'missing': '사용 가능한 값 미확보', 'error': '수집 오류'} if ko else
              {'complete': 'some values in all components', 'partial': 'partial collection',
               'missing': 'no usable values collected', 'error': 'collection error'})
    state = data.get('status')
    state_text = labels.get(state, unknown) if isinstance(state, str) else unknown
    source = 'Yahoo Finance / yfinance' if data.get('source') == 'Yahoo Finance / yfinance' else unknown
    captured = unknown
    raw = data.get('captured_at')
    if isinstance(raw, str) and len(raw) <= 64:
        try:
            parsed = datetime.fromisoformat(raw.replace('Z', '+00:00'))
            if parsed.tzinfo is not None:
                captured = parsed.astimezone(timezone.utc).isoformat()
        except (ValueError, OverflowError):
            pass
    names = (
        ('earnings_estimate', 'EPS 추정', 'EPS estimates'),
        ('revenue_estimate', '매출 추정', 'revenue estimates'),
        ('eps_trend', 'EPS 추이', 'EPS trend'), ('eps_revisions', 'EPS 수정', 'EPS revisions'),
        ('growth_estimates', '성장 추정', 'growth estimates'),
        ('analyst_price_targets', '목표가', 'price targets'),
        ('recommendations_summary', '추천 분포', 'recommendations'),
    )
    states = {'available': '확보', 'missing': '미확보', 'error': '오류'} if ko else {
        'available': 'available', 'missing': 'not collected', 'error': 'error'}
    components = data.get('components')
    components = components if isinstance(components, dict) else {}
    details = []
    for key, korean, english in names:
        value = components.get(key)
        if isinstance(value, str) and value in states:
            details.append(f'{korean if ko else english}: {states[value]}')
    detail = '; '.join(details) or unknown
    if ko:
        return (f'### 애널리스트 자료 수집 현황\n\n수집 상태: {state_text}. 출처: {source}. '
                f'수집 시각(UTC): {captured}. 전망 발표 시각: 미확인.\n항목별 수집: {detail}.\n'
                '전체 항목 확보는 항목마다 일부 값이 있다는 뜻이며 모든 필드·기간 검증을 뜻하지 않습니다. '
                '미확보·오류는 발행사의 자료 부재나 전망의 긍정·부정을 뜻하지 않습니다. '
                '선택적 자료 누락만으로 별도 매수·매도 조건을 만들지 않습니다.')
    return (f'### Analyst Data Collection Status\n\nCollection status: {state_text}. Source: {source}. '
            f'Capture time (UTC): {captured}. Estimate publication time: unverified.\nComponents: {detail}.\n'
            'Complete means some usable values per component, not every field or period verified. '
            'Missing/error is not issuer-wide absence or positive/negative forecast evidence. '
            'Missing optional data alone is not a separate buy/sell gate.')


async def analyze_us_stock(
    ticker: str = "AAPL",
    company_name: str = "Apple Inc.",
    reference_date: str = None,
    language: str = "ko",
    include_news: bool = True,
    macro_context: dict = None,
    market_report_cache: MarketReportCache | None = None,
) -> str:
    """
    Generate comprehensive stock analysis report for US stock.

    Args:
        ticker: Stock ticker symbol (e.g., "AAPL", "MSFT")
        company_name: Company name (e.g., "Apple Inc.")
        reference_date: Analysis reference date (YYYYMMDD format)
        language: Language code (default: "ko")
        include_news: Whether to include news analysis (requires Perplexity API)
        market_report_cache: Optional batch-owned market report singleflight.

    Returns:
        str: Generated final report markdown text
    """
    # 1. Initial setup and preprocessing
    app = MCPApp(name="us_stock_analysis")
    market_report_cache = market_report_cache if market_report_cache is not None else MarketReportCache()

    # Use today's date if reference_date is not provided
    if reference_date is None:
        reference_date = datetime.now().strftime("%Y%m%d")

    async with app.run() as parallel_app:
        logger = parallel_app.logger
        logger.info(f"Starting: {company_name}({ticker}) US analysis - reference date: {reference_date}")

        # 2. Create dictionary to store data as shared resource
        section_reports = {}

        # 3. Define sections to analyze (US-specific)
        # yfinance sections: run sequentially to avoid rate limits
        yfinance_sections = [
            "price_volume_analysis",           # Technical analysis (yfinance OHLCV)
            "institutional_holdings_analysis",  # yfinance holders
            "company_status",                  # yfinance financials
            "company_overview",                # yfinance info
            "market_index_analysis"            # yfinance indices
        ]
        # Non-yfinance sections: can run in parallel
        parallel_sections = []
        if include_news:
            parallel_sections.append("news_analysis")  # perplexity (requires API key)
        else:
            # Add placeholder for skipped news section
            if language == "ko":
                section_reports["news_analysis"] = "_뉴스 분석은 Perplexity API 키가 필요합니다. 기술적/재무 분석은 정상적으로 제공됩니다._"
            else:
                section_reports["news_analysis"] = "_News analysis requires Perplexity API key. Technical and fundamental analysis are provided normally._"
            logger.info("Skipping news_analysis (Perplexity API not configured)")
        # Always include news_analysis in base_sections for report structure
        base_sections = yfinance_sections + ["news_analysis"]

        # 4. Prefetch data to reduce MCP tool call overhead
        try:
            _prefetch_path = Path(__file__).parent / "data_prefetch.py"
            _prefetch_spec = importlib.util.spec_from_file_location("us_data_prefetch", _prefetch_path)
            _prefetch_module = importlib.util.module_from_spec(_prefetch_spec)
            _prefetch_spec.loader.exec_module(_prefetch_module)
            prefetched = _prefetch_module.prefetch_us_analysis_data(ticker)
            logger.info(f"Prefetched US data for {ticker}: {list(prefetched.keys()) if prefetched else 'none'}")
        except Exception as e:
            logger.warning(f"US data prefetch failed, falling back to MCP: {e}")
            prefetched = {}

        try:
            from prism_core.report_research_prefetch import prefetch_report_research
            research = await prefetch_report_research("US", ticker, reference_date, company_name)
            if research:
                prefetched["report_research"] = research
                if research.get("news_usable") is True and "news_analysis" not in parallel_sections:
                    parallel_sections.append("news_analysis")
                    section_reports.pop("news_analysis", None)
        except Exception:
            logger.warning("Optional report research unavailable; retaining existing sources")

        if include_news and os.getenv("ADANOS_API_KEY"):
            try:
                social_client = USSocialSentimentClient()
                social_snapshot = await asyncio.to_thread(
                    social_client.get_social_sentiment_markdown,
                    ticker,
                    7,
                )
                if social_snapshot:
                    prefetched["social_sentiment"] = social_snapshot
                    logger.info(f"Prefetched social sentiment for {ticker}")
            except Exception as e:
                logger.warning(f"US social sentiment prefetch failed, continuing without it: {e}")

        # 5. Get US-specific agents (with prefetched data)
        prefetched["shared_macro_available"] = bool(macro_context)
        agents = get_us_agent_directory(company_name, ticker, reference_date, base_sections, language, prefetched_data=prefetched)
        prefetched['report_technical_reference'] = extract_report_technical_facts(prefetched.get('stock_ohlcv', ''))
        shared_reference = reference_context(prefetched, language)
        agents = {key: (agent if key == 'market_index_analysis' else
                        add_shared_context(agent, shared_reference, '', language))
                  for key, agent in agents.items()}

        # 6. Execute base analysis using HYBRID mode
        # - yfinance sections: sequential with 2 sec delay (rate limit friendly)
        # - news_analysis: parallel with yfinance sections (uses perplexity, not yfinance)
        logger.info(f"Running US analysis in HYBRID mode for {company_name}...")
        logger.info(f"  - yfinance sections (sequential): {yfinance_sections}")
        logger.info(f"  - parallel sections: {parallel_sections}")

        async def process_yfinance_sections():
            """Process yfinance-dependent sections sequentially"""
            results = {}
            for section in yfinance_sections:
                if section in agents:
                    logger.info(f"Processing {section} for {company_name}...")
                    try:
                        agent = agents[section]
                        if section in ('company_status', 'company_overview'):
                            # Reuse the existing news call; do not regenerate sections or add a model call.
                            news_task = parallel_tasks.get('news_analysis')
                            news_draft = (await news_task)[1] if news_task is not None else ''
                            if section == 'company_overview':
                                news_draft = (news_draft or '') + '\n\n' + results.get('company_status', '')
                            agent = add_shared_context(agent, '', news_draft or '', language)
                        if section == "market_index_analysis":
                            async def generate_market():
                                logger.info("Generating new US market analysis")
                                return await generate_market_report(
                                    agent, section, reference_date, logger, language
                                )
                            report = await market_report_cache.get(reference_date, language, generate_market)
                        else:
                            report = await generate_report(
                                agent, section, company_name, ticker, reference_date, logger, language
                            )
                        results[section] = report
                        # Add delay between yfinance calls to avoid rate limits
                        # 3 seconds gives yfinance time to reset rate limits
                        await asyncio.sleep(3)
                    except Exception as e:
                        logger.error(f"Error processing {section}: {e}")
                        results[section] = f"Analysis failed: {section}"
            return results

        async def process_parallel_section(section):
            """Process a non-yfinance section with its own MCPApp context"""
            if section not in agents:
                return section, None

            section_app = MCPApp(name=f"us_stock_analysis_{section}")
            async with section_app.run() as section_context:
                section_logger = section_context.logger
                section_logger.info(f"Processing {section} for {company_name}...")
                try:
                    agent = agents[section]
                    report = await generate_report(
                        agent, section, company_name, ticker, reference_date, section_logger, language
                    )
                    return section, report
                except Exception as e:
                    section_logger.error(f"Error processing {section}: {e}")
                    return section, f"Analysis failed: {section}"

        # Execute hybrid: yfinance sequential + parallel sections concurrently
        parallel_tasks = {s: asyncio.create_task(process_parallel_section(s)) for s in parallel_sections}
        yfinance_task = process_yfinance_sections()

        # Run both concurrently
        all_results = await asyncio.gather(yfinance_task, *parallel_tasks.values())

        # Collect results
        # First result is yfinance sections dict
        yfinance_results = all_results[0]
        section_reports.update(yfinance_results)

        # Remaining results are (section, report) tuples from parallel sections
        for result in all_results[1:]:
            if result and result[1] is not None:
                section_reports[result[0]] = result[1]

        # Both hybrid branches are complete: reuse news without another model call.
        section_reports, evidence_receipt = attach_competitive_evidence(
            section_reports, "US", ticker, reference_date, language
        )
        section_reports['company_status'] = section_reports.get('company_status', '') + '\n\n' + render_us_analyst_receipt(
            prefetched.get('analysis_estimates_status'), language)
        if prefetched['report_technical_reference']:
            section_reports['price_volume_analysis'] = section_reports.get('price_volume_analysis', '') + '\n\n' + prefetched['report_technical_reference']
        section_reports['shared_reference'] = shared_reference
        logger.info(
            f"[COMPETITIVE_EVIDENCE] market=US symbol={ticker} date={reference_date} "
            f"status={evidence_receipt['status']} evidence_id={evidence_receipt['evidence_id']} "
            f"record_chars={evidence_receipt['record_chars']}"
        )

        from prism_core.market_report_context import market_report_context, public_market_analysis
        section_reports["market_index_analysis"] = public_market_analysis(
            section_reports.get("market_index_analysis", ""), macro_context, language
        )
        shared_market = market_report_context(macro_context, language)
        if shared_market:
            section_reports["market_index_analysis"] = section_reports.get("market_index_analysis", "") + shared_market

        # 6. Integrate content from other reports
        combined_reports = shared_reference
        for section in base_sections:
            if section in section_reports:
                combined_reports += f"\n\n--- {section.upper()} ---\n\n"
                combined_reports += section_reports[section]

        # 7. Generate investment strategy
        try:
            logger.info(f"Processing investment_strategy for {company_name}...")

            investment_strategy = await generate_investment_strategy(
                section_reports, combined_reports, company_name, ticker, reference_date, logger, language
            )
            section_reports["investment_strategy"] = investment_strategy.lstrip('\n')
            logger.info(f"Completed investment_strategy - {len(investment_strategy)} characters")
        except Exception as e:
            logger.error(f"Error processing investment_strategy: {e}")
            section_reports["investment_strategy"] = "Investment strategy analysis failed"

        # 8. Generate executive summary
        try:
            logger.info(f"Processing summary for {company_name}...")
            summary = await generate_summary(
                section_reports, company_name, ticker, reference_date, logger, language
            )
            # Remove duplicate title/date if the agent added them
            # Pattern: "# Company Name (TICKER) Analysis Report\n**Publication Date:** ..."
            import re
            summary = summary.lstrip('\n')
            # Remove any leading H1 title that matches the report title pattern
            summary = re.sub(
                r'^#\s*' + re.escape(company_name) + r'\s*\(' + re.escape(ticker) + r'\)[^\n]*\n+',
                '',
                summary,
                flags=re.IGNORECASE
            )
            # Remove any publication date line right after title removal
            summary = re.sub(
                r'^\*{0,2}(Publication Date|발행일)\*{0,2}\s*:\s*[^\n]+\n+',
                '',
                summary,
                flags=re.IGNORECASE
            )
            # Remove leading separators (---)
            summary = re.sub(r'^-{3,}\s*\n+', '', summary)
            section_reports["summary"] = summary.lstrip('\n')
            logger.info(f"Completed summary - {len(summary)} characters")
        except Exception as e:
            logger.error(f"Error processing summary: {e}")
            section_reports["summary"] = "Summary generation failed"

        # 9. Generate charts (optional - may fail if yfinance data unavailable)
        # Charts are inserted into specific sections:
        # - Price chart → 1-1. Price and Volume Analysis
        # - Institutional chart → 1-2. Institutional Ownership Analysis
        # - Technical indicators chart → 4. Market Analysis
        price_chart_html = ""
        institutional_chart_html = ""
        technical_chart_html = ""

        try:
            import yfinance as yf

            # Get stock data for charts
            stock = yf.Ticker(ticker)
            hist = stock.history(period="1y")

            if not hist.empty:
                # 1. Price Chart (Candlestick with MA and Volume)
                price_chart_html = get_us_price_chart_html(
                    ticker, company_name, hist, width=900, dpi=80
                )
                if price_chart_html:
                    logger.info(f"Generated price chart for {ticker}")
                else:
                    logger.warning(f"Failed to generate price chart for {ticker}")

                # 2. Institutional Holdings Chart
                major_holders = stock.major_holders
                institutional_holders = stock.institutional_holders
                institutional_chart_html = get_us_institutional_chart_html(
                    ticker, company_name, major_holders, institutional_holders, width=900, dpi=80
                )
                if institutional_chart_html:
                    logger.info(f"Generated institutional chart for {ticker}")
                else:
                    logger.warning(f"Failed to generate institutional chart for {ticker}")

                # 3. Technical Indicators Chart (RSI + MACD)
                technical_chart_html = get_us_technical_chart_html(
                    ticker, company_name, hist, width=900, dpi=80
                )
                if technical_chart_html:
                    logger.info(f"Generated technical indicators chart for {ticker}")
                else:
                    logger.warning(f"Failed to generate technical indicators chart for {ticker}")

        except Exception as e:
            logger.warning(f"Chart generation skipped: {e}")

        # 10. Compile final report
        # Header
        formatted_date = f"{reference_date[:4]}.{reference_date[4:6]}.{reference_date[6:]}"

        # Build chart sections with fallback messages
        price_chart_section = ""
        if price_chart_html:
            price_chart_section = f"\n\n#### Price Chart\n\n{price_chart_html}\n"

        institutional_chart_section = ""
        if institutional_chart_html:
            institutional_chart_section = f"\n\n#### Institutional Holdings Chart\n\n{institutional_chart_html}\n"

        technical_chart_section = ""
        if technical_chart_html:
            technical_chart_section = f"\n\n#### Technical Indicators (RSI & MACD)\n\n{technical_chart_html}\n"

        # Build macro section before final report composition
        macro_section = ""
        if macro_context:
            from prism_core.market_report_context import public_macro_prose
            report_prose = public_macro_prose(macro_context, language)
            if report_prose:
                macro_header = "### 거시경제 환경\n\n" if language == "ko" else "### Macroeconomic Environment\n\n"
                macro_section = macro_header + report_prose + "\n\n"
            else:
                # Fallback: build from structured fields if report_prose is empty
                regime = macro_context.get("market_regime", "sideways")
                regime_rationale = macro_context.get("regime_rationale", "")
                leading = macro_context.get("leading_sectors", [])
                lagging = macro_context.get("lagging_sectors", [])
                risks = macro_context.get("risk_events", [])

                if language == "ko":
                    regime_labels = {
                        "parabolic": "폭주 강세장",
                        "strong_bull": "강한 강세장", "moderate_bull": "보통 강세장",
                        "sideways": "횡보장", "moderate_bear": "보통 약세장", "strong_bear": "강한 약세장"
                    }
                    macro_section += "### 거시경제 환경\n\n"
                    macro_section += f"**시장 체제**: {regime_labels.get(regime, regime)}\n\n"
                    if regime_rationale:
                        macro_section += f"**판단 근거**: {regime_rationale}\n\n"
                    if leading:
                        sectors_str = ", ".join([s.get("sector", "") for s in leading[:3]])
                        macro_section += f"**주도 섹터**: {sectors_str}\n\n"
                    if lagging:
                        sectors_str = ", ".join([s.get("sector", "") for s in lagging[:3]])
                        macro_section += f"**소외 섹터**: {sectors_str}\n\n"
                    if risks:
                        for r in risks[:3]:
                            macro_section += f"- ⚠️ {r.get('event', '')} (영향: {r.get('severity', 'medium')})\n"
                        macro_section += "\n"
                else:
                    macro_section += "### Macroeconomic Environment\n\n"
                    macro_section += f"**Market Regime**: {regime.replace('_', ' ').title()}\n\n"
                    if regime_rationale:
                        macro_section += f"**Rationale**: {regime_rationale}\n\n"
                    if leading:
                        sectors_str = ", ".join([s.get("sector", "") for s in leading[:3]])
                        macro_section += f"**Leading Sectors**: {sectors_str}\n\n"
                    if lagging:
                        sectors_str = ", ".join([s.get("sector", "") for s in lagging[:3]])
                        macro_section += f"**Lagging Sectors**: {sectors_str}\n\n"
                    if risks:
                        for r in risks[:3]:
                            macro_section += f"- ⚠️ {r.get('event', '')} (Severity: {r.get('severity', 'medium')})\n"
                        macro_section += "\n"

        # Language-specific headers
        if language == "ko":
            headers = {
                "title": f"# {company_name} ({ticker}) 분석 보고서",
                "pub_date": "발행일",
                "exec_summary": "## 핵심 요약",
                "tech_analysis": "## 1. 기술적 분석",
                "fundamental": "## 2. 펀더멘털 분석",
                "news": "## 3. 최근 주요 뉴스 요약",
                "market": "## 4. 시장 분석",
                "strategy": "## 5. 투자 전략 및 의견",
            }
        else:
            headers = {
                "title": f"# {company_name} ({ticker}) Analysis Report",
                "pub_date": "Publication Date",
                "exec_summary": "## Executive Summary",
                "tech_analysis": "## 1. Technical Analysis",
                "fundamental": "## 2. Fundamental Analysis",
                "news": "## 3. Recent Major News Summary",
                "market": "## 4. Market Analysis",
                "strategy": "## 5. Investment Strategy and Opinion",
            }

        section_reports, source_appendix = evidence_appendix(
            section_reports, language, prefetched['report_technical_reference'])
        final_report = f"""{headers["title"]}

**{headers["pub_date"]}:** {formatted_date}

---

{section_reports.get("summary", headers["exec_summary"] + " - Summary not available")}

---

{headers["tech_analysis"]}

{section_reports.get("price_volume_analysis", "Analysis not available")}
{price_chart_section}
{section_reports.get("institutional_holdings_analysis", "Analysis not available")}
{institutional_chart_section}
---

{headers["fundamental"]}

{section_reports.get("company_status", "Analysis not available")}

{section_reports.get("company_overview", "Analysis not available")}

---

{headers["news"]}

{section_reports.get("news_analysis", "Analysis not available")}

---

{headers["market"]}

{section_reports.get("market_index_analysis", "Analysis not available")}
{technical_chart_section}
{macro_section}
---

{headers["strategy"]}

{section_reports.get("investment_strategy", "Strategy not available")}

---

{get_disclaimer(language)}
"""

        # 11. Clean up markdown formatting
        final_report = clean_markdown(final_report) + source_appendix

        logger.info(f"Final report generated: {company_name}({ticker}) - {len(final_report)} characters")

        return final_report


def clear_us_market_cache():
    """Clear the US market analysis cache"""
    global _us_market_analysis_cache
    _us_market_analysis_cache = {}


if __name__ == "__main__":
    import time
    import threading
    import signal

    # Timeout after 60 minutes
    def exit_after_timeout():
        time.sleep(3600)
        print("60-minute timeout reached: forcefully terminating process")
        os.kill(os.getpid(), signal.SIGTERM)

    # Start timer as background thread
    timer_thread = threading.Thread(target=exit_after_timeout, daemon=True)
    timer_thread.start()

    start = time.time()

    # Run analysis for Apple Inc.
    result = asyncio.run(analyze_us_stock(
        ticker="AAPL",
        company_name="Apple Inc.",
        reference_date=datetime.now().strftime("%Y%m%d"),
        language="en"
    ))

    # Save result
    from report_model_config import REPORT_MODEL, report_model_slug

    output_path = (
        f"AAPL_Apple Inc_{datetime.now().strftime('%Y%m%d')}_"
        f"{report_model_slug(REPORT_MODEL)}.md"
    )
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(result)

    end = time.time()
    print(f"Total execution time: {end - start:.2f} seconds")
    print(f"Final report length: {len(result):,} characters")
    print(f"Report saved to: {output_path}")
