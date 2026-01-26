"""
US Stock Analysis Module

Generate comprehensive stock analysis reports for US stocks.
Uses yfinance MCP server for market data and US-specific agents.
"""
import os
import asyncio
from datetime import datetime
from pathlib import Path

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

# Import report_generation from main project's cores
_report_gen_module = _import_from_project_root(
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
import importlib.util
_agents_path = _prism_us_dir / "cores" / "agents" / "__init__.py"
_spec = importlib.util.spec_from_file_location("us_agents", _agents_path)
_us_agents_module = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_us_agents_module)
get_us_agent_directory = _us_agents_module.get_us_agent_directory

# Market analysis cache storage (global variable)
_us_market_analysis_cache = {}

# Import chart functions from us_stock_chart module
_chart_module = _import_from_project_root(
    "us_stock_chart",
    _prism_us_dir / "cores" / "us_stock_chart.py"
)
get_us_price_chart_html = _chart_module.get_us_price_chart_html
get_us_institutional_chart_html = _chart_module.get_us_institutional_chart_html
get_us_technical_chart_html = _chart_module.get_us_technical_chart_html


async def analyze_us_stock(
    ticker: str = "AAPL",
    company_name: str = "Apple Inc.",
    reference_date: str = None,
    language: str = "en"
) -> str:
    """
    Generate comprehensive stock analysis report for US stock.

    Args:
        ticker: Stock ticker symbol (e.g., "AAPL", "MSFT")
        company_name: Company name (e.g., "Apple Inc.")
        reference_date: Analysis reference date (YYYYMMDD format)
        language: Language code (default: "en" for US market)

    Returns:
        str: Generated final report markdown text
    """
    # 1. Initial setup and preprocessing
    app = MCPApp(name="us_stock_analysis")

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
        parallel_sections = [
            "news_analysis",                   # perplexity/firecrawl (no yfinance)
        ]
        base_sections = yfinance_sections + parallel_sections

        # 4. Get US-specific agents
        agents = get_us_agent_directory(company_name, ticker, reference_date, base_sections, language)

        # 5. Execute base analysis using HYBRID mode
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
                        if section == "market_index_analysis":
                            if "report" in _us_market_analysis_cache:
                                logger.info(f"Using cached US market analysis")
                                report = _us_market_analysis_cache["report"]
                            else:
                                logger.info(f"Generating new US market analysis")
                                report = await generate_market_report(
                                    agent, section, reference_date, logger, language
                                )
                                _us_market_analysis_cache["report"] = report
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
        parallel_tasks = [process_parallel_section(s) for s in parallel_sections]
        yfinance_task = process_yfinance_sections()

        # Run both concurrently
        all_results = await asyncio.gather(yfinance_task, *parallel_tasks)

        # Collect results
        # First result is yfinance sections dict
        yfinance_results = all_results[0]
        section_reports.update(yfinance_results)

        # Remaining results are (section, report) tuples from parallel sections
        for result in all_results[1:]:
            if result and result[1] is not None:
                section_reports[result[0]] = result[1]

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

        final_report = f"""{headers["title"]}

**{headers["pub_date"]}:** {formatted_date}

---

{section_reports.get("summary", headers["exec_summary"] + "\n\nSummary not available")}

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
---

{headers["strategy"]}

{section_reports.get("investment_strategy", "Strategy not available")}

---

{get_disclaimer(language)}
"""

        # 11. Clean up markdown formatting
        final_report = clean_markdown(final_report)

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
    output_path = f"AAPL_Apple Inc_{datetime.now().strftime('%Y%m%d')}_gpt5.md"
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(result)

    end = time.time()
    print(f"Total execution time: {end - start:.2f} seconds")
    print(f"Final report length: {len(result):,} characters")
    print(f"Report saved to: {output_path}")
