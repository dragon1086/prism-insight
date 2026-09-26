import os
import asyncio
import re
from collections.abc import Mapping
from datetime import datetime
from dotenv import load_dotenv

# Standard logger for the buy-quality SHADOW hook. The function-local `logger`
import logging as _logging
_BQ_LOG = _logging.getLogger("prism.buy_quality")


class _ReportRunContext:
    """Keep the existing async scope while using a standard process logger."""

    def __init__(self, name: str):
        self.logger = _logging.getLogger(name)

    def run(self):
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

from cores.agents import get_agent_directory
from cores.report_generation import generate_report, generate_summary, generate_investment_strategy, get_disclaimer, generate_market_report

# Load environment variables
load_dotenv()
from cores.stock_chart import (
    create_price_chart,
    create_trading_volume_chart,
    create_market_cap_chart,
    create_fundamentals_chart,
    create_annual_earnings_chart,
    create_annual_fundamentals_chart,
    create_dart_balance_sheet_chart,
    get_chart_as_base64_html
)
from cores.utils import clean_markdown
from prism_core.kr_report_context import reference_context, market_cache_key
from prism_core.report_presentation import humanize_report_status


# Market analysis cache storage (global variable)
_market_analysis_cache = {}


def _report_parallel_limit(
    section_count: int,
    environ: Mapping[str, str] | None = None,
) -> int:
    """Resolve an optional bound while preserving legacy full parallelism."""

    values = os.environ if environ is None else environ
    raw_limit = values.get("PRISM_PARALLEL_REPORT_MAX_CONCURRENCY")
    if raw_limit is None:
        return section_count
    try:
        return min(section_count, max(1, int(raw_limit)))
    except ValueError:
        return section_count

def _with_balance_chart(chapter_text, chart_html, language):
    """Close subsection 5-1 (finance) with the balance-sheet chart, inside the chapter."""
    if not chart_html:
        return chapter_text
    from cores.dart_deep_analysis import CHAPTER_END
    block = ('#### 재무구조 추이 (DART 재무상태표)\n\n' if language == 'ko'
             else '#### Balance Sheet Structure (DART)\n\n') + chart_html + '\n\n'
    match = re.search(r'(?m)^### 5-2\b', chapter_text)
    cut = match.start() if match else chapter_text.rindex(CHAPTER_END)
    return chapter_text[:cut] + block + chapter_text[cut:]


async def _alert_dart_depth_missing(company_code, company_name, reference_date, stage, detail):
    from prism_core.ops_alert import send_ops_alert
    await send_ops_alert(
        f'[PRISM] DART 심층분석 누락 ({stage})\n'
        f'{company_name}({company_code}) 기준일 {reference_date}\n'
        f'보고서는 5장(DART) 없이 발행됩니다.\n{detail}\n'
        f'로그: logs/subprocess/report_{company_code}_*.log')


async def analyze_stock(company_code: str = "000660", company_name: str = "SK하이닉스", reference_date: str = None, language: str = "ko", macro_context: dict = None, *, require_dart_depth=False):
    """
    Generate comprehensive stock analysis report

    Args:
        company_code: Stock code
        company_name: Company name
        reference_date: Analysis reference date (YYYYMMDD format)
        language: Language code ("ko" or "en")

    Returns:
        str: Generated final report markdown text
    """
    # 1. Initial setup and preprocessing
    app = _ReportRunContext(name="stock_analysis")

    # Use today's date if reference_date is not provided
    if reference_date is None:
        reference_date = datetime.now().strftime("%Y%m%d")


    async with app.run() as parallel_app:
        logger = parallel_app.logger
        logger.info(f"Starting: {company_name}({company_code}) analysis - reference date: {reference_date}")

        # 2. Create dictionary to store data as shared resource
        section_reports = {}

        # 3. Define sections to analyze
        base_sections = ["price_volume_analysis", "investor_trading_analysis", "company_status", "company_overview", "news_analysis", "market_index_analysis"]

        # 4. Prefetch data to reduce MCP tool call overhead
        from cores.data_prefetch import prefetch_kr_analysis_data
        try:
            from datetime import timedelta
            ref_date_obj = datetime.strptime(reference_date, "%Y%m%d")
            max_years_calc = 1
            max_years_ago_calc = (ref_date_obj - timedelta(days=365*max_years_calc)).strftime("%Y%m%d")
            prefetched = await asyncio.to_thread(
                prefetch_kr_analysis_data, company_code, reference_date, max_years_ago_calc)
        except Exception as e:
            logger.warning(f"Data prefetch failed, falling back to MCP: {e}")
            prefetched = {}

        # Writers and synthesis share the same local calendar fact.
        from cores.report_calendar import calendar_context
        prefetched['report_calendar_context'] = await asyncio.to_thread(calendar_context, reference_date)

        # Optional research is gathered once before section/model retries.
        try:
            from prism_core.report_research_prefetch import prefetch_report_research
            research = await prefetch_report_research("KR", company_code, reference_date, company_name)
            if research:
                prefetched["report_research"] = research
        except Exception:
            logger.warning("Optional report research unavailable; retaining existing sources")

        # Model-free official filing inputs are on the shared bot/batch path.
        try:
            from prism_core.kr_official_report_inputs import collect_kr_official_report_inputs
            prefetched['official_dart'] = await collect_kr_official_report_inputs(
                company_code, company_name, reference_date)
        except Exception:
            logger.warning('Official filing inputs unavailable; retaining existing report sources')

        shared_reference = reference_context(prefetched, language)
        if require_dart_depth and not prefetched.get('official_dart', {}).get('dart_chapter_inputs', {}).get('ready'):
            dart_packet = prefetched.get('official_dart', {})
            diagnostics = dart_packet.get('diagnostics', {})
            receipt = dart_packet.get('dart_chapter_inputs', {}).get('receipt', {})
            logger.warning('DART depth unavailable: sources=%s capacity_ok=%s failure_type=%s gaps=%s',
                           receipt.get('source_count'), receipt.get('capacity_ok'),
                           receipt.get('failure_type'), diagnostics.get('gaps', []))
            # The rest of the report still ships; maintainers are told why the chapter is missing.
            await _alert_dart_depth_missing(company_code, company_name, reference_date, 'inputs_not_ready',
                                            f"sources={receipt.get('source_count')} "
                                            f"capacity_ok={receipt.get('capacity_ok')} "
                                            f"writer_bytes={receipt.get('writer_bytes')} "
                                            f"gaps={sorted(set(diagnostics.get('gaps', [])))[:6]}")
        # WiseFn-selected competitor table, collected before the Industry Analyst
        # so it interprets real peer numbers. Optional data, never a trading gate.
        try:
            from prism_core.kr_peer_comparison import collect_wisereport_peers
            peer_packet = await collect_wisereport_peers(company_code, company_name, reference_date)
        except Exception as e:
            peer_packet = {'ready': False, 'skip_reason': f'error_{type(e).__name__}'}
        logger.info(
            f"[PEER_COMPARISON] symbol={company_code} status={'ready' if peer_packet.get('ready') else 'skipped'} "
            f"peers={max(0, len(peer_packet.get('peers') or []) - 1)} period={peer_packet.get('period') or '-'} "
            f"reason={peer_packet.get('skip_reason') or '-'}")
        if peer_packet.get('ready'):
            prefetched['peer_comparison'] = peer_packet
        cache_key = market_cache_key(prefetched, reference_date, language)
        # 5. Get agents (with prefetched data)
        agents = get_agent_directory(company_name, company_code, reference_date, base_sections, language, prefetched_data=prefetched)

        # 6. Execute base analysis
        # Parallel processing option: Activated when PRISM_PARALLEL_REPORT=true is set in .env file
        # ⚠️ Warning: Parallel processing greatly improves speed but may hit OpenAI API rate limits.
        # When using advanced models like GPT-5.2, rate limits may be stricter, so be careful.
        parallel_enabled = os.getenv("PRISM_PARALLEL_REPORT", "false").lower() == "true"

        if parallel_enabled:
            # Parallel execution mode
            # Keep independent section loggers; the backend owns MCP server lifecycles.
            parallel_limit = _report_parallel_limit(len(base_sections))
            parallel_semaphore = asyncio.Semaphore(parallel_limit)
            logger.info(
                "Running analysis in PARALLEL mode for %s (max concurrency=%d)...",
                company_name,
                parallel_limit,
            )

            async def process_section_unbounded(section):
                """Process a single section with its own logger context."""
                if section not in agents:
                    return section, None

                section_app = _ReportRunContext(name=f"stock_analysis_{section}")

                async with section_app.run() as section_context:
                    section_logger = section_context.logger
                    section_logger.info(f"Processing {section} for {company_name}...")
                    try:
                        agent = agents[section]
                        if section == "market_index_analysis":
                            if cache_key in _market_analysis_cache:
                                section_logger.info("Using cached market analysis")
                                return section, _market_analysis_cache[cache_key]
                            else:
                                section_logger.info("Generating new market analysis")
                                report = await generate_market_report(agent, section, reference_date, section_logger, language)
                                _market_analysis_cache.clear()
                                _market_analysis_cache[cache_key] = report
                                return section, report
                        else:
                            report = await generate_report(agent, section, company_name, company_code, reference_date, section_logger, language)
                            return section, report
                    except Exception as e:
                        section_logger.error(f"Final failure processing {section}: {e}")
                        return section, f"Analysis failed: {section}"

            async def process_section(section):
                async with parallel_semaphore:
                    return await process_section_unbounded(section)

            # Execute all sections in parallel (each with its own logger context).
            results = await asyncio.gather(*[process_section(section) for section in base_sections])
            for section, report in results:
                if report is not None:
                    section_reports[section] = report
        else:
            # Sequential execution mode (default - rate limit friendly)
            logger.info(f"Running analysis in SEQUENTIAL mode for {company_name}...")
            for section in base_sections:
                if section in agents:
                    logger.info(f"Processing {section} for {company_name}...")

                    try:
                        agent = agents[section]
                        if section == "market_index_analysis":
                            # Check if data exists in cache
                            if cache_key in _market_analysis_cache:
                                logger.info("Using cached market analysis")
                                report = _market_analysis_cache[cache_key]
                            else:
                                logger.info("Generating new market analysis")
                                report = await generate_market_report(agent, section, reference_date, logger, language)
                                # Save to cache
                                _market_analysis_cache.clear()
                                _market_analysis_cache[cache_key] = report
                        else:
                            report = await generate_report(agent, section, company_name, company_code, reference_date, logger, language)
                        section_reports[section] = report
                    except Exception as e:
                        logger.error(f"Final failure processing {section}: {e}")
                        section_reports[section] = f"Analysis failed: {section}"

        if require_dart_depth:
            failed_sections = [section for section in base_sections
                               if not isinstance(section_reports.get(section), str)
                               or not section_reports[section].strip()
                               or section_reports[section].startswith('Analysis failed:')]
            if failed_sections:
                raise RuntimeError('Required report section failed before depth generation')

        from prism_core.market_report_context import market_report_context, public_market_analysis
        section_reports['market_index_analysis'] = public_market_analysis(
            section_reports.get('market_index_analysis', ''), macro_context, language,
            require_citation_integrity=True)
        shared_market = market_report_context(macro_context, language)
        if shared_market:
            section_reports["market_index_analysis"] = section_reports.get("market_index_analysis", "") + shared_market

        # Pre-collected competitor table: published after 2-2 and fed to synthesis/DART.
        peer_context = ''
        if peer_packet.get('ready'):
            peer_context = peer_packet.get('model_context', '')
            section_reports['peer_comparison'] = peer_packet['public_markdown']

        # This chapter bypasses the legacy 3,000-character summary contract and
        # is preserved as written, before strategy and executive synthesis.
        from cores.dart_deep_analysis import CHAPTER_INCOMPLETE, generate_dart_chapter
        dart_packet = prefetched.get('official_dart', {})
        chapter_inputs = dart_packet.get('dart_chapter_inputs', {}) if isinstance(dart_packet, dict) else {}
        # Completed WiseReport-based sections tell writers what the report already covers.
        covered_sections = '\n\n'.join(
            section_reports[key] for key in ('company_status', 'company_overview')
            if isinstance(section_reports.get(key), str) and section_reports[key].strip()
            and not section_reports[key].startswith('Analysis failed:'))
        try:
            dart_chapter, dart_receipt = await generate_dart_chapter(
                chapter_inputs, company_name=company_name, company_code=company_code,
                reference_date=reference_date, language=language, shared_reference=shared_reference,
                peer_context=peer_context, report_context=covered_sections,
                concurrency=min(3, _report_parallel_limit(3)) if parallel_enabled else 1,
                sector=dart_packet.get('sector_profile') if isinstance(dart_packet, dict) else None)
        except Exception as e:
            logger.warning('DART depth generation incomplete; preserving existing basic analysis without a new BUY gate',
                           exc_info=require_dart_depth)
            if require_dart_depth:
                await _alert_dart_depth_missing(company_code, company_name, reference_date, 'generation_failed',
                                                f'{type(e).__name__}: {str(e)[:300]}')
            dart_chapter, dart_receipt = '', {'status': 'not_generated', 'calls': None}
        logger.info('DART chapter status=%s calls=%s', dart_receipt['status'], dart_receipt['calls'])
        if dart_chapter and dart_receipt.get('budget_exceeded'):
            # Delivered anyway; the cost overrun is a maintenance signal, not a failure.
            source_receipt = chapter_inputs.get('receipt', {}) if isinstance(chapter_inputs, dict) else {}
            from prism_core.ops_alert import send_ops_alert
            await send_ops_alert(
                f'[PRISM] DART 심층분석 비용 상한 초과 (발행됨)\n{company_name}({company_code}) 기준일 {reference_date}\n'
                f"원문 {source_receipt.get('total_bytes', 0) // 1000}KB, 모델 메시지 "
                f"{dart_receipt.get('message_bytes', 0) // 1000}KB, 호출 {dart_receipt['calls']}회")
        if dart_chapter:
            section_reports['dart_deep_analysis'] = dart_chapter
        else:
            section_reports['dart_depth_limit'] = (
                CHAPTER_INCOMPLETE + '\n공시 심층 장은 이번 보고서에 반영하지 못했습니다. '
                '아래 내용은 기존 기본 분석이며 공시 위험을 전부 점검한 결과가 아닙니다. '
                '이 자료 누락 자체를 별도의 매수·매도 조건으로 해석하지 않습니다.'
                if language == 'ko' else CHAPTER_INCOMPLETE + '\nThe filing-depth chapter is not included. '
                'This is a basic report, not a complete filing-risk review. Missing optional evidence is not a separate trading condition.')

        # Render once before synthesis; reuse the identical block in the PDF.
        from prism_core.report_macro_section import render_macro_section
        macro_section = render_macro_section(macro_context, language, "KR")
        if macro_section:
            section_reports['macro_context'] = macro_section

        # 6. Integrate content from other reports
        from prism_core.kr_report_context import synthesis_reference_context
        shared_reference = synthesis_reference_context(prefetched, language)
        section_reports['shared_reference'] = shared_reference
        synthesis_sections = base_sections + ['peer_comparison', 'dart_deep_analysis', 'dart_depth_limit', 'macro_context']

        def combine_synthesis_reports(reports):
            combined = shared_reference
            for section in synthesis_sections:
                if section in reports:
                    combined += f"\n\n--- {section.upper()} ---\n\n" + reports[section]
            return combined

        async def write_strategy(reports):
            reports.pop('investment_strategy', None)
            combined = combine_synthesis_reports(reports)
            strategy = await generate_investment_strategy(
                reports, combined, company_name, company_code, reference_date, logger, language)
            if (not isinstance(strategy, str) or not strategy.strip()
                    or strategy.strip() in {'Investment strategy analysis failed', '투자 전략 분석 실패'}):
                raise RuntimeError('Investment strategy generation failed')
            strategy = strategy.lstrip('\n')
            if dart_chapter:
                strategy = re.sub(r'(?m)^((?:\\n)*)(#{2,4})[ \t]+5(?=[.-])', r'\1\2 6', strategy)
            reports['investment_strategy'] = strategy

        try:
            await write_strategy(section_reports)
        except Exception as e:
            if dart_chapter:
                raise
            logger.error(f"Error processing investment_strategy: {e}")
            section_reports['investment_strategy'] = 'Investment strategy analysis failed'

        try:
            executive_summary = await generate_summary(
                section_reports, company_name, company_code, reference_date, logger, language
            )
            # Remove duplicate title/date if the agent added them
            executive_summary = executive_summary.lstrip('\n')
            # Remove any leading H1 title that matches the report title pattern
            executive_summary = re.sub(
                r'^#\s*' + re.escape(company_name) + r'\s*\(' + re.escape(company_code) + r'\)[^\n]*\n+',
                '',
                executive_summary,
                flags=re.IGNORECASE
            )
            # Remove any publication date line right after title removal
            executive_summary = re.sub(
                r'^\*{0,2}(Publication Date|발행일)\*{0,2}\s*:\s*[^\n]+\n+',
                '',
                executive_summary,
                flags=re.IGNORECASE
            )
            # Remove leading separators (---)
            executive_summary = re.sub(r'^-{3,}\s*\n+', '', executive_summary)
            executive_summary = executive_summary.lstrip('\n')
        except Exception as e:
            logger.error(f"Error generating executive summary: {e}")
            executive_summary = "## 핵심 요약\n\n요약 생성 중 오류가 발생했습니다." if language == "ko" else "## Executive Summary\n\nProblem occurred while generating analysis summary."

        # 10. Generate charts
        charts_dir = os.path.join("../charts", f"{company_code}_{reference_date}")
        os.makedirs(charts_dir, exist_ok=True)

        # KIS has no market-cap/valuation history (today's snapshot only), so the
        # daily cap/fundamental charts are omitted; fall back to WiseReport's
        # reported fiscal-year figures instead of leaving the section empty.
        financial_summary = None
        try:
            from prism_core.kr_financial_summary import collect_wisereport_financial_summary
            financial_summary = await collect_wisereport_financial_summary(company_code, reference_date)
        except Exception as e:  # noqa: BLE001 - optional chart data
            logger.warning(f"Annual financial summary unavailable: {type(e).__name__}")
        market_cap_chart_is_earnings = False

        try:
            # Generate chart images
            price_chart_html = get_chart_as_base64_html(
                company_code, company_name, create_price_chart, 'Price Chart', width=900, dpi=80, image_format='jpg', compress=True,
                days=730, adjusted=True
            )

            volume_chart_html = get_chart_as_base64_html(
                company_code, company_name, create_trading_volume_chart, 'Trading Volume Chart', width=900, dpi=80, image_format='jpg', compress=True,
                days=30  # Supply/demand analysis based on 1 month
            )

            market_cap_chart_html = get_chart_as_base64_html(
                company_code, company_name, create_market_cap_chart, 'Market Cap Trend', width=900, dpi=80, image_format='jpg', compress=True,
                days=730
            )

            fundamentals_chart_html = get_chart_as_base64_html(
                company_code, company_name, create_fundamentals_chart, 'Fundamental Indicators', width=900, dpi=80, image_format='jpg', compress=True,
                days=730
            )

            if market_cap_chart_html is None and financial_summary is not None:
                market_cap_chart_html = get_chart_as_base64_html(
                    company_code, company_name, create_annual_earnings_chart, 'Annual Earnings Trend', width=900, dpi=80, image_format='jpg', compress=True,
                    financial_summary=financial_summary
                )
                market_cap_chart_is_earnings = market_cap_chart_html is not None

            if fundamentals_chart_html is None and financial_summary is not None:
                fundamentals_chart_html = get_chart_as_base64_html(
                    company_code, company_name, create_annual_fundamentals_chart, 'Fundamental Indicators', width=900, dpi=80, image_format='jpg', compress=True,
                    financial_summary=financial_summary
                )
        except Exception as e:
            logger.error(f"Error occurred while generating charts: {str(e)}")
            price_chart_html = None
            volume_chart_html = None
            market_cap_chart_html = None
            fundamentals_chart_html = None

        # Filing-chapter chart: only balance-sheet totals that pass exact checks;
        # anything unverifiable is omitted rather than approximated.
        dart_balance_chart_html = None
        if dart_chapter:
            try:
                from prism_core.dart_balance_sheet import balance_sheet_series
                balance_series = balance_sheet_series(chapter_inputs)
                if balance_series:
                    sector = dart_packet.get('sector_profile') if isinstance(dart_packet, dict) else None
                    financial = isinstance(sector, dict) and sector.get('kind') == 'financial'
                    dart_balance_chart_html = get_chart_as_base64_html(
                        company_code, company_name, create_dart_balance_sheet_chart, 'Balance Sheet Structure',
                        width=900, dpi=80, image_format='jpg', compress=True, series=balance_series,
                        ratio='equity_to_assets' if financial else 'debt_to_equity')
                else:
                    logger.info('DART balance-sheet chart omitted: totals not verifiable')
            except Exception as e:  # noqa: BLE001 - optional chart
                logger.warning(f"DART balance-sheet chart unavailable: {type(e).__name__}")

        # 10b. Render QA (Phase 6 S2) — OFF by default, non-blocking
        from cores.llm.capabilities import (
            vision_available,
            vision_buy_quality_active,
        )
        _vision_buy_quality_active = vision_buy_quality_active()
        if vision_available() and _vision_buy_quality_active:
            try:
                import base64
                import tempfile
                from cores.llm.features.render_qa import qa_and_log
                _qa_html = price_chart_html or volume_chart_html
                if _qa_html:
                    _m = re.search(r'base64,([^"]+)"', _qa_html)
                    if _m:
                        _img_bytes = base64.b64decode(_m.group(1))
                        with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as _tf:
                            _tf.write(_img_bytes)
                            _tf_path = _tf.name
                        await qa_and_log(_tf_path, context_label=f"{company_code}_{company_name}")
                        import os as _os
                        _os.unlink(_tf_path)
            except Exception:
                pass  # render QA must never affect the pipeline

        # 10c. Buy-quality vision gate (Phase 6 S3) — SHADOW by default, log-only.
        # Computes a CAN SLIM base analysis + per-regime verdict and LOGS it.
        # In shadow mode it has ZERO effect on the report or any buy decision.
        # When PRISM_FEATURE_VISION_IN_REPORT=on, the descriptive base analysis is
        # ALSO surfaced as a markdown subsection in the technical section below —
        # a SOFT input the buy agent reads, never a buy gate (that stays S5/TODO).
        vision_pattern_md = ""
        if vision_available() and _vision_buy_quality_active:
            try:
                import base64 as _bq_base64
                import re as _bq_re
                from cores.llm.capabilities import vision_shadow, vision_in_report
                from cores.llm.features.buy_quality import (
                    analyze_base,
                    analyze_base_oneil,
                    format_vision_pattern_md,
                    gate_verdict,
                )
                _bq_html = price_chart_html
                _bq_regime = (macro_context or {}).get("market_regime", "sideways")
                _BQ_LOG.info("[BUY_QUALITY][SHADOW] hook reached: code=%s regime=%s",
                             company_code, _bq_regime)
                # Phase 6 S3.5: prefer the two-timeframe O'Neil path (daily +
                # weekly with RS line), which grounds rs_line_new_high and the
                # weekly base reading. Falls back to the single daily report
                # image when the two-timeframe generation returns None (e.g.
                # pykrx/index data unavailable). Still SHADOW/log-only.
                _bq_analysis = await analyze_base_oneil(
                    company_code, company_name, regime=_bq_regime
                )
                if _bq_analysis is None and _bq_html:
                    _bq_m = _bq_re.search(r'base64,([^"]+)"', _bq_html)
                    if _bq_m:
                        _bq_img = _bq_base64.b64decode(_bq_m.group(1))
                        _bq_analysis = await analyze_base(_bq_img)
                if _bq_analysis is not None:
                    _bq_verdict = gate_verdict(_bq_analysis, _bq_regime)
                    _BQ_LOG.info(
                        "[BUY_QUALITY][SHADOW] code=%s regime=%s would_buy=%s "
                        "qscore=%s thr=%s base=%s",
                        company_code,
                        _bq_regime,
                        _bq_verdict["would_buy"],
                        _bq_verdict["quality_score"],
                        _bq_verdict["threshold"],
                        _bq_analysis.base_type,
                    )
                    # Opt-in (PRISM_FEATURE_VISION_IN_REPORT=on): surface the
                    # descriptive base analysis into the report's technical
                    # section. SOFT report content the buy agent reads — this is
                    # NOT the buy gate (that remains the S5/TODO below).
                    if vision_in_report():
                        vision_pattern_md = format_vision_pattern_md(
                            _bq_analysis, language
                        )
                    # TODO(S5/LIVE): when not vision_shadow(), inject
                    # _bq_verdict into the entry matrix (Step 2 of the
                    # trading_scenario_agent prompt) so it gates real
                    # buys. Do NOT implement live injection until S4
                    # backtest passes and the user confirms (S5).
                    _ = vision_shadow  # referenced to mark the LIVE seam
                else:
                    _BQ_LOG.warning(
                        "[BUY_QUALITY][SHADOW] no analysis for %s "
                        "(analyze_base_oneil + fallback both None)",
                        company_code,
                    )
            except Exception as _bqe:
                # Log (do NOT silently swallow) — still never affects pipeline.
                _BQ_LOG.warning("[BUY_QUALITY][SHADOW] hook failed for %s: %s",
                                company_code, _bqe, exc_info=True)

        # Reuse the same macro block already supplied to strategy and summary.

        # 12. Compose final report with proper heading hierarchy
        disclaimer = get_disclaimer(language)

        # Format reference date for display
        formatted_date = f"{reference_date[:4]}.{reference_date[4:6]}.{reference_date[6:]}"

        # Define main section headers by language
        if language == "ko":
            main_headers = {
                "title": f"# {company_name} ({company_code}) 분석 보고서",
                "pub_date": "발행일",
                "tech_analysis": "## 1. 기술적 분석\n\n",
                "fundamental": "## 2. 펀더멘털 분석\n\n",
                "news": "## 3. 뉴스 분석\n\n",
                "market": "## 4. 시장 분석\n\n",
                "strategy": "## 5. 투자 전략\n\n"
            }
        else:
            main_headers = {
                "title": f"# {company_name} ({company_code}) Analysis Report",
                "pub_date": "Publication Date",
                "tech_analysis": "## 1. Technical Analysis\n\n",
                "fundamental": "## 2. Fundamental Analysis\n\n",
                "news": "## 3. News Analysis\n\n",
                "market": "## 4. Market Analysis\n\n",
                "strategy": "## 5. Investment Strategy\n\n"
            }

        # Build final report with title first (disclaimer at the end like US version)
        final_report = f"""{main_headers["title"]}

**{main_headers["pub_date"]}:** {formatted_date}

---

{executive_summary}

"""

        # Add sections with proper main headers
        # Technical Analysis section (price_volume + investor_trading)
        if "price_volume_analysis" in section_reports or "investor_trading_analysis" in section_reports:
            final_report += main_headers["tech_analysis"]
            if "price_volume_analysis" in section_reports:
                final_report += section_reports["price_volume_analysis"] + "\n\n"
                # Vision chart-pattern analysis (opt-in; empty unless
                # PRISM_FEATURE_VISION_IN_REPORT=on). Placed right after the
                # price/volume prose and before the chart images.
                if vision_pattern_md:
                    final_report += vision_pattern_md
                # Add price and volume charts
                if price_chart_html or volume_chart_html:
                    chart_title = "### 가격 및 거래량 차트\n\n" if language == "ko" else "### Price and Volume Charts\n\n"
                    final_report += chart_title
                    if price_chart_html:
                        chart_subtitle = "#### 가격 차트\n\n" if language == "ko" else "#### Price Chart\n\n"
                        final_report += chart_subtitle + price_chart_html + "\n\n"
                    if volume_chart_html:
                        chart_subtitle = "#### 거래량 차트\n\n" if language == "ko" else "#### Trading Volume Chart\n\n"
                        final_report += chart_subtitle + volume_chart_html + "\n\n"
            if "investor_trading_analysis" in section_reports:
                final_report += section_reports["investor_trading_analysis"] + "\n\n"

        # Fundamental Analysis section (company_status + company_overview)
        if "company_status" in section_reports or "company_overview" in section_reports:
            final_report += main_headers["fundamental"]
            if "company_status" in section_reports:
                final_report += section_reports["company_status"] + "\n\n"
                # Add market cap and fundamental indicator charts
                if market_cap_chart_html or fundamentals_chart_html:
                    chart_title = "### 시가총액 및 펀더멘털 차트\n\n" if language == "ko" else "### Market Cap and Fundamental Charts\n\n"
                    final_report += chart_title
                    if market_cap_chart_html:
                        if market_cap_chart_is_earnings:
                            chart_subtitle = "#### 연간 실적 추이 분석\n\n" if language == "ko" else "#### Annual Earnings Trend\n\n"
                        else:
                            chart_subtitle = "#### 시가총액 추이\n\n" if language == "ko" else "#### Market Cap Trend\n\n"
                        final_report += chart_subtitle + market_cap_chart_html + "\n\n"
                    if fundamentals_chart_html:
                        chart_subtitle = "#### 펀더멘털 지표 분석\n\n" if language == "ko" else "#### Fundamental Indicator Analysis\n\n"
                        final_report += chart_subtitle + fundamentals_chart_html + "\n\n"
            if "company_overview" in section_reports:
                final_report += section_reports["company_overview"] + "\n\n"
            if section_reports.get('peer_comparison'):
                final_report += section_reports['peer_comparison'] + '\n\n'

        # News Analysis section
        if "news_analysis" in section_reports:
            final_report += main_headers["news"]
            final_report += section_reports["news_analysis"] + "\n\n"

        # Market Analysis section
        if "market_index_analysis" in section_reports:
            final_report += main_headers["market"]
            final_report += section_reports["market_index_analysis"] + "\n\n"
            if macro_section:
                macro_header = "### 거시경제 환경\n\n" if language == "ko" else "### Macroeconomic Environment\n\n"
                final_report += macro_header + macro_section

        # Investment Strategy section
        if dart_chapter:
            # Images join only the published report, never the synthesis inputs.
            final_report += _with_balance_chart(section_reports['dart_deep_analysis'],
                                                dart_balance_chart_html, language) + '\n\n'
            main_headers['strategy'] = main_headers['strategy'].replace('## 5.', '## 6.', 1)
        elif section_reports.get('dart_depth_limit'):
            final_report += section_reports['dart_depth_limit'] + '\n\n'
        if "investment_strategy" in section_reports:
            final_report += main_headers["strategy"]
            final_report += section_reports["investment_strategy"] + "\n\n"

        # Add disclaimer at the end
        # Preserve deterministic quantities even if the narrative omits them.
        # The existing PDF-to-BUY path carries this same block without a refetch.
        if prefetched.get("flow_evidence"):
            final_report += prefetched.get('flow_evidence_public', prefetched["flow_evidence"]) + "\n"
        references = [prefetched.get('report_calculation_reference', ''),
                      prefetched.get('market_calculation_reference', '')]
        dart = prefetched.get('official_dart', {})
        if isinstance(dart, dict):
            references.append(dart.get('public_receipt', ''))
        if any(references):
            title = '## 자료 기준과 주요 계산 지표' if language == 'ko' else '## Sources and calculated reference values'
            final_report += '\n\n' + title + '\n\n' + '\n\n'.join(x for x in references if x)
        final_report += "---\n\n" + disclaimer + "\n"

        # 12. Final markdown cleanup
        final_report = humanize_report_status(clean_markdown(final_report), language)

        logger.info(f"Finalized report for {company_name} - {len(final_report)} characters")
        logger.info(f"Analysis completed for {company_name}.")

        return final_report
