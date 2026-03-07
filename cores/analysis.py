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
_market_analysis_lock = asyncio.Lock()

async def _get_or_generate_market_report(agent, section, reference_date, logger, language):
    async with _market_analysis_lock:
        if "report" in _market_analysis_cache:
            logger.info(f"Using cached market analysis")
            return _market_analysis_cache["report"]
        
        logger.info(f"Generating new market analysis")
        report = await generate_market_report(agent, section, reference_date, logger, language)
        _market_analysis_cache["report"] = report
        return report

async def _execute_parallel_analysis(company_name, company_code, reference_date, language, agents, base_sections, logger):
    section_reports = {}
    logger.info(f"Running analysis in PARALLEL mode for {company_name}...")

    async def process_section(section):
        if section not in agents:
            return section, None

        section_app = MCPApp(name=f"stock_analysis_{section}")
        async with section_app.run() as section_context:
            section_logger = section_context.logger
            section_logger.info(f"Processing {section} for {company_name}...")
            try:
                agent = agents[section]
                if section == "market_index_analysis":
                    report = await _get_or_generate_market_report(agent, section, reference_date, section_logger, language)
                    return section, report
                else:
                    report = await generate_report(agent, section, company_name, company_code, reference_date, section_logger, language)
                    return section, report
            except Exception as e:
                section_logger.error(f"Final failure processing {section}: {e}")
                return section, f"Analysis failed: {section}"

    results = await asyncio.gather(*[process_section(section) for section in base_sections])
    for section, report in results:
        if report is not None:
            section_reports[section] = report
    return section_reports

async def _execute_sequential_analysis(company_name, company_code, reference_date, language, agents, base_sections, logger):
    section_reports = {}
    logger.info(f"Running analysis in SEQUENTIAL mode for {company_name}...")
    for section in base_sections:
        if section in agents:
            logger.info(f"Processing {section} for {company_name}...")
            try:
                agent = agents[section]
                if section == "market_index_analysis":
                    report = await _get_or_generate_market_report(agent, section, reference_date, logger, language)
                    section_reports[section] = report
                else:
                    report = await generate_report(agent, section, company_name, company_code, reference_date, logger, language)
                section_reports[section] = report
            except Exception as e:
                logger.error(f"Final failure processing {section}: {e}")
                section_reports[section] = f"Analysis failed: {section}"
    return section_reports

def _generate_charts(company_code, company_name, reference_date, logger):
    charts_dir = os.path.join("../charts", f"{company_code}_{reference_date}")
    os.makedirs(charts_dir, exist_ok=True)
    
    DEFAULT_CHART_KWARGS = {'width': 900, 'dpi': 80, 'image_format': 'jpg', 'compress': True}
    
    try:
        price_chart_html = get_chart_as_base64_html(
            company_code, company_name, create_price_chart, 'Price Chart', **DEFAULT_CHART_KWARGS,
            days=730, adjusted=True
        )
        volume_chart_html = get_chart_as_base64_html(
            company_code, company_name, create_trading_volume_chart, 'Trading Volume Chart', **DEFAULT_CHART_KWARGS,
            days=30  
        )
        market_cap_chart_html = get_chart_as_base64_html(
            company_code, company_name, create_market_cap_chart, 'Market Cap Trend', **DEFAULT_CHART_KWARGS,
            days=730
        )
        fundamentals_chart_html = get_chart_as_base64_html(
            company_code, company_name, create_fundamentals_chart, 'Fundamental Indicators', **DEFAULT_CHART_KWARGS,
            days=730
        )
        return price_chart_html, volume_chart_html, market_cap_chart_html, fundamentals_chart_html
    except Exception as e:
        logger.error(f"Error occurred while generating charts: {str(e)}")
        return None, None, None, None

def _compile_final_report(company_code, company_name, reference_date, language, section_reports, executive_summary, charts):
    import os
    from jinja2 import Environment, FileSystemLoader

    price_chart_html, volume_chart_html, market_cap_chart_html, fundamentals_chart_html = charts
    disclaimer = get_disclaimer(language)
    formatted_date = f"{reference_date[:4]}.{reference_date[4:6]}.{reference_date[6:]}"

    if language == "ko":
        main_headers = {
            "title": f"# {company_name} ({company_code}) 분석 보고서",
            "pub_date": "발행일",
            "tech_analysis": f"## 1. 기술적 분석\n",
            "fundamental": f"## 2. 펀더멘털 분석\n",
            "news": f"## 3. 뉴스 분석\n",
            "market": f"## 4. 시장 분석\n",
            "strategy": f"## 5. 투자 전략\n"
        }
    else:
        main_headers = {
            "title": f"# {company_name} ({company_code}) Analysis Report",
            "pub_date": "Publication Date",
            "tech_analysis": f"## 1. Technical Analysis\n",
            "fundamental": f"## 2. Fundamental Analysis\n",
            "news": f"## 3. News Analysis\n",
            "market": f"## 4. Market Analysis\n",
            "strategy": f"## 5. Investment Strategy\n"
        }

    template_dir = os.path.join(os.path.dirname(__file__), 'templates')
    env = Environment(loader=FileSystemLoader(template_dir))
    template = env.get_template('report_template.md')

    final_report_str = template.render(
        company_name=company_name,
        company_code=company_code,
        language=language,
        formatted_date=formatted_date,
        main_headers=main_headers,
        executive_summary=executive_summary,
        section_reports=section_reports,
        price_chart_html=price_chart_html,
        volume_chart_html=volume_chart_html,
        market_cap_chart_html=market_cap_chart_html,
        fundamentals_chart_html=fundamentals_chart_html,
        disclaimer=disclaimer
    )

    return clean_markdown(final_report_str)

async def analyze_stock(company_code: str = "000660", company_name: str = "SK하이닉스", reference_date: str = None, language: str = "ko"):
    """
    Generate comprehensive stock analysis report
    """
    app = MCPApp(name="stock_analysis")
    
    if reference_date is None:
        reference_date = datetime.now().strftime("%Y%m%d")

    async with app.run() as parallel_app:
        logger = parallel_app.logger
        logger.info(f"Starting: {company_name}({company_code}) analysis - reference date: {reference_date}")
        
        base_sections = ["price_volume_analysis", "investor_trading_analysis", "company_status", "company_overview", "news_analysis", "market_index_analysis"]
        
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

        agents = get_agent_directory(company_name, company_code, reference_date, base_sections, language, prefetched_data=prefetched)
        parallel_enabled = os.getenv("PRISM_PARALLEL_REPORT", "false").lower() == "true"

        if parallel_enabled:
            section_reports = await _execute_parallel_analysis(company_name, company_code, reference_date, language, agents, base_sections, logger)
        else:
            section_reports = await _execute_sequential_analysis(company_name, company_code, reference_date, language, agents, base_sections, logger)

        combined_reports = ""
        for section in base_sections:
            if section in section_reports:
                combined_reports += f"\n\n--- {section.upper()} ---\n\n"
                combined_reports += section_reports[section]

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

        try:
            executive_summary = await generate_summary(
                section_reports, company_name, company_code, reference_date, logger, language
            )
            import re
            executive_summary = executive_summary.lstrip('\n')
            executive_summary = re.sub(
                r'^#\s*' + re.escape(company_name) + r'\s*\(' + re.escape(company_code) + r'\)[^\n]*\n+',
                '',
                executive_summary,
                flags=re.IGNORECASE
            )
            executive_summary = re.sub(
                r'^\*{0,2}(Publication Date|발행일)\*{0,2}\s*:\s*[^\n]+\n+',
                '',
                executive_summary,
                flags=re.IGNORECASE
            )
            executive_summary = re.sub(r'^-{3,}\s*\n+', '', executive_summary)
            executive_summary = executive_summary.lstrip('\n')
        except Exception as e:
            logger.error(f"Error generating executive summary: {e}")
            executive_summary = "## 핵심 요약\n\n요약 생성 중 오류가 발생했습니다." if language == "ko" else "## Executive Summary\n\nProblem occurred while generating analysis summary."

        charts = _generate_charts(company_code, company_name, reference_date, logger)
        
        final_report = _compile_final_report(company_code, company_name, reference_date, language, section_reports, executive_summary, charts)

        logger.info(f"Finalized report for {company_name} - {len(final_report)} characters")
        logger.info(f"Analysis completed for {company_name}.")
        return final_report

