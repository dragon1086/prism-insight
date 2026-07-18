import os
import asyncio
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
    get_chart_as_base64_html
)
from cores.utils import clean_markdown


# Market analysis cache storage (global variable)
_market_analysis_cache = {}

async def analyze_stock(company_code: str = "000660", company_name: str = "SK하이닉스", reference_date: str = None, language: str = "ko", macro_context: dict = None):
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
            prefetched = prefetch_kr_analysis_data(company_code, reference_date, max_years_ago_calc)
        except Exception as e:
            logger.warning(f"Data prefetch failed, falling back to MCP: {e}")
            prefetched = {}

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
            logger.info(f"Running analysis in PARALLEL mode for {company_name}...")

            async def process_section(section):
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
                            if "report" in _market_analysis_cache:
                                section_logger.info("Using cached market analysis")
                                return section, _market_analysis_cache["report"]
                            else:
                                section_logger.info("Generating new market analysis")
                                report = await generate_market_report(agent, section, reference_date, section_logger, language)
                                _market_analysis_cache["report"] = report
                                return section, report
                        else:
                            report = await generate_report(agent, section, company_name, company_code, reference_date, section_logger, language)
                            return section, report
                    except Exception as e:
                        section_logger.error(f"Final failure processing {section}: {e}")
                        return section, f"Analysis failed: {section}"

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
                            if "report" in _market_analysis_cache:
                                logger.info("Using cached market analysis")
                                report = _market_analysis_cache["report"]
                            else:
                                logger.info("Generating new market analysis")
                                report = await generate_market_report(agent, section, reference_date, logger, language)
                                # Save to cache
                                _market_analysis_cache["report"] = report
                        else:
                            report = await generate_report(agent, section, company_name, company_code, reference_date, logger, language)
                        section_reports[section] = report
                    except Exception as e:
                        logger.error(f"Final failure processing {section}: {e}")
                        section_reports[section] = f"Analysis failed: {section}"

        # 6. Integrate content from other reports
        combined_reports = ""
        for section in base_sections:
            if section in section_reports:
                combined_reports += f"\n\n--- {section.upper()} ---\n\n"
                combined_reports += section_reports[section]

        # 7. Generate investment strategy
        try:
            logger.info(f"Processing investment_strategy for {company_name}...")

            investment_strategy = await generate_investment_strategy(
                section_reports, combined_reports, company_name, company_code, reference_date, logger, language
            )
            section_reports["investment_strategy"] = investment_strategy.lstrip('\n')
            logger.info(f"Completed investment_strategy - {len(investment_strategy)} characters")
        except Exception as e:
            logger.error(f"Error processing investment_strategy: {e}")
            section_reports["investment_strategy"] = "Investment strategy analysis failed"

        # 8. Generate comprehensive report including all sections
        all_reports = ""
        for section in base_sections + ["investment_strategy"]:
            if section in section_reports:
                all_reports += f"\n\n--- {section.upper()} ---\n\n"
                all_reports += section_reports[section]

        # 9. Generate summary
        try:
            executive_summary = await generate_summary(
                section_reports, company_name, company_code, reference_date, logger, language
            )
            # Remove duplicate title/date if the agent added them
            import re
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
        except Exception as e:
            logger.error(f"Error occurred while generating charts: {str(e)}")
            price_chart_html = None
            volume_chart_html = None
            market_cap_chart_html = None
            fundamentals_chart_html = None

        # 10b. Render QA (Phase 6 S2) — OFF by default, non-blocking
        from cores.llm.capabilities import vision_available
        if vision_available():
            try:
                import base64
                import re
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
        if vision_available():
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

        # 11. Build macro section (before final report composition)
        macro_section = ""
        if macro_context:
            report_prose = macro_context.get("report_prose", "")
            if report_prose:
                macro_section = report_prose + "\n\n"
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
                        chart_subtitle = "#### 시가총액 추이\n\n" if language == "ko" else "#### Market Cap Trend\n\n"
                        final_report += chart_subtitle + market_cap_chart_html + "\n\n"
                    if fundamentals_chart_html:
                        chart_subtitle = "#### 펀더멘털 지표 분석\n\n" if language == "ko" else "#### Fundamental Indicator Analysis\n\n"
                        final_report += chart_subtitle + fundamentals_chart_html + "\n\n"
            if "company_overview" in section_reports:
                final_report += section_reports["company_overview"] + "\n\n"

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
        if "investment_strategy" in section_reports:
            final_report += main_headers["strategy"]
            final_report += section_reports["investment_strategy"] + "\n\n"

        # Add disclaimer at the end
        final_report += "---\n\n" + disclaimer + "\n"

        # 12. Final markdown cleanup
        final_report = clean_markdown(final_report)

        logger.info(f"Finalized report for {company_name} - {len(final_report)} characters")
        logger.info(f"Analysis completed for {company_name}.")

        return final_report
