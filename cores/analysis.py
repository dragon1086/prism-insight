import os
import asyncio
from datetime import datetime
from dotenv import load_dotenv

from mcp_agent.app import MCPApp

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

async def analyze_stock(company_code: str = "000660", company_name: str = "SK하이닉스", reference_date: str = None, language: str = "ko"):
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
    app = MCPApp(name="stock_analysis")

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

        # 4. Get agents
        agents = get_agent_directory(company_name, company_code, reference_date, base_sections, language)

        # 5. Execute base analysis
        # 병렬 처리 옵션: .env 파일에서 PRISM_PARALLEL_REPORT=true 설정 시 활성화
        # ⚠️ 주의: 병렬 처리는 속도를 크게 향상시키지만, OpenAI API rate limit에 걸릴 수 있습니다.
        # GPT-5.2 등 고급 모델 사용 시 rate limit이 더 엄격할 수 있으므로 주의하세요.
        parallel_enabled = os.getenv("PRISM_PARALLEL_REPORT", "false").lower() == "true"

        if parallel_enabled:
            # Parallel execution mode
            # 각 섹션마다 독립적인 MCPApp 컨텍스트를 생성하여 MCP 서버 충돌 방지
            logger.info(f"Running analysis in PARALLEL mode for {company_name}...")

            async def process_section(section):
                """Process a single section with its own MCPApp context"""
                if section not in agents:
                    return section, None

                # 각 섹션마다 독립적인 MCPApp 인스턴스 생성
                section_app = MCPApp(name=f"stock_analysis_{section}")

                async with section_app.run() as section_context:
                    section_logger = section_context.logger
                    section_logger.info(f"Processing {section} for {company_name}...")
                    try:
                        agent = agents[section]
                        if section == "market_index_analysis":
                            if "report" in _market_analysis_cache:
                                section_logger.info(f"Using cached market analysis")
                                return section, _market_analysis_cache["report"]
                            else:
                                section_logger.info(f"Generating new market analysis")
                                report = await generate_market_report(agent, section, reference_date, section_logger, language)
                                _market_analysis_cache["report"] = report
                                return section, report
                        else:
                            report = await generate_report(agent, section, company_name, company_code, reference_date, section_logger, language)
                            return section, report
                    except Exception as e:
                        section_logger.error(f"Final failure processing {section}: {e}")
                        return section, f"Analysis failed: {section}"

            # Execute all sections in parallel (each with its own MCPApp context)
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
                                logger.info(f"Using cached market analysis")
                                report = _market_analysis_cache["report"]
                            else:
                                logger.info(f"Generating new market analysis")
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
                days=30  # 수급 분석은 1개월 기준
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

        # 11. Compose final report with proper heading hierarchy
        disclaimer = get_disclaimer(language)

        # Format reference date for display
        formatted_date = f"{reference_date[:4]}.{reference_date[4:6]}.{reference_date[6:]}"

        # Define main section headers by language
        if language == "ko":
            main_headers = {
                "title": f"# {company_name} ({company_code}) 분석 보고서",
                "pub_date": "발행일",
                "tech_analysis": f"## 1. 기술적 분석\n\n",
                "fundamental": f"## 2. 펀더멘털 분석\n\n",
                "news": f"## 3. 뉴스 분석\n\n",
                "market": f"## 4. 시장 분석\n\n",
                "strategy": f"## 5. 투자 전략\n\n"
            }
        else:
            main_headers = {
                "title": f"# {company_name} ({company_code}) Analysis Report",
                "pub_date": "Publication Date",
                "tech_analysis": f"## 1. Technical Analysis\n\n",
                "fundamental": f"## 2. Fundamental Analysis\n\n",
                "news": f"## 3. News Analysis\n\n",
                "market": f"## 4. Market Analysis\n\n",
                "strategy": f"## 5. Investment Strategy\n\n"
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
