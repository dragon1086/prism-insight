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
            logger.info(f"Running analysis in PARALLEL mode for {company_name}...")

            async def process_section(section):
                """Process a single section and return (section_name, report)"""
                if section not in agents:
                    return section, None

                logger.info(f"Processing {section} for {company_name}...")
                try:
                    agent = agents[section]
                    if section == "market_index_analysis":
                        if "report" in _market_analysis_cache:
                            logger.info(f"Using cached market analysis")
                            return section, _market_analysis_cache["report"]
                        else:
                            logger.info(f"Generating new market analysis")
                            report = await generate_market_report(agent, section, reference_date, logger, language)
                            _market_analysis_cache["report"] = report
                            return section, report
                    else:
                        report = await generate_report(agent, section, company_name, company_code, reference_date, logger, language)
                        return section, report
                except Exception as e:
                    logger.error(f"Final failure processing {section}: {e}")
                    return section, f"Analysis failed: {section}"

            # Execute all sections in parallel
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
        except Exception as e:
            logger.error(f"Error generating executive summary: {e}")
            executive_summary = "# Key Investment Points\n\nProblem occurred while generating analysis summary."

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
                days=730
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

        # 11. Compose final report
        disclaimer = get_disclaimer(language)
        final_report = disclaimer + "\n\n" + executive_summary + "\n\n"

        all_sections = base_sections + ["investment_strategy"]
        for section in all_sections:
            if section in section_reports:
                final_report += section_reports[section] + "\n\n"

                # Add price and volume charts after price_volume_analysis section
                if section == "price_volume_analysis" and (price_chart_html or volume_chart_html):
                    final_report += "\n## Price and Volume Charts\n\n"

                    if price_chart_html:
                        final_report += f"### Price Chart\n\n"
                        final_report += price_chart_html + "\n\n"

                    if volume_chart_html:
                        final_report += f"### Trading Volume Chart\n\n"
                        final_report += volume_chart_html + "\n\n"

                # Add market cap and fundamental indicator charts after company_status section
                elif section == "company_status" and (market_cap_chart_html or fundamentals_chart_html):
                    final_report += "\n## Market Cap and Fundamental Indicator Charts\n\n"

                    if market_cap_chart_html:
                        final_report += f"### Market Cap Trend\n\n"
                        final_report += market_cap_chart_html + "\n\n"

                    if fundamentals_chart_html:
                        final_report += f"### Fundamental Indicator Analysis\n\n"
                        final_report += fundamentals_chart_html + "\n\n"

        # 12. Final markdown cleanup
        final_report = clean_markdown(final_report)

        logger.info(f"Finalized report for {company_name} - {len(final_report)} characters")
        logger.info(f"Analysis completed for {company_name}.")

        return final_report
