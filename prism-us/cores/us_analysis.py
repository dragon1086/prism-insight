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


def figure_to_base64_html(fig, chart_name: str = "chart", width: int = 900,
                          dpi: int = 80, image_format: str = 'jpg') -> str:
    """
    Convert a matplotlib figure to base64 HTML img tag.

    Args:
        fig: matplotlib figure object
        chart_name: name for the chart (used in alt text)
        width: image width in pixels
        dpi: image resolution
        image_format: 'jpg' or 'png'

    Returns:
        HTML img tag with embedded base64 image
    """
    import base64
    from io import BytesIO
    import matplotlib.pyplot as plt

    try:
        buffer = BytesIO()

        save_kwargs = {
            'format': image_format,
            'bbox_inches': 'tight',
            'dpi': dpi
        }

        if image_format.lower() == 'png':
            save_kwargs['transparent'] = False
            save_kwargs['facecolor'] = 'white'

        fig.savefig(buffer, **save_kwargs)
        plt.close(fig)
        buffer.seek(0)

        # Optional JPEG compression
        if image_format.lower() in ['jpg', 'jpeg']:
            try:
                from PIL import Image
                img = Image.open(buffer)
                new_buffer = BytesIO()
                img.save(new_buffer, format='JPEG', quality=85, optimize=True)
                buffer = new_buffer
                buffer.seek(0)
            except ImportError:
                pass

        img_str = base64.b64encode(buffer.getvalue()).decode('utf-8')

        content_type = f"image/{image_format.lower()}"
        if image_format.lower() == 'jpg':
            content_type = 'image/jpeg'

        return f'<img src="data:{content_type};base64,{img_str}" alt="{chart_name}" width="{width}" />'

    except Exception as e:
        import logging
        logging.getLogger(__name__).warning(f"Failed to convert figure to base64: {e}")
        return None


def create_us_price_chart(ticker: str, company_name: str, hist_df) -> object:
    """
    Create price chart for US stocks using yfinance historical data.

    Args:
        ticker: Stock ticker symbol (e.g., "AAPL")
        company_name: Company name for chart title
        hist_df: pandas DataFrame with OHLCV data from yfinance

    Returns:
        matplotlib figure object or None if failed
    """
    try:
        import pandas as pd
        import matplotlib.pyplot as plt
        import matplotlib.dates as mdates
        import mplfinance as mpf

        # Ensure DataFrame has required columns
        if hist_df is None or hist_df.empty:
            return None

        # yfinance returns: Open, High, Low, Close, Volume, Dividends, Stock Splits
        # We need: Open, High, Low, Close, Volume
        required_cols = ['Open', 'High', 'Low', 'Close', 'Volume']
        for col in required_cols:
            if col not in hist_df.columns:
                return None

        # Create a copy and ensure proper index
        df = hist_df[required_cols].copy()
        if not isinstance(df.index, pd.DatetimeIndex):
            df.index = pd.to_datetime(df.index)

        # Sort by date
        df = df.sort_index()

        # Calculate moving averages
        df['MA20'] = df['Close'].rolling(window=20).mean()
        df['MA60'] = df['Close'].rolling(window=60).mean()
        df['MA120'] = df['Close'].rolling(window=120).mean()

        # Create OHLCV DataFrame for mplfinance
        ohlc_df = df[['Open', 'High', 'Low', 'Close', 'Volume']].copy()

        # Create chart style
        mc = mpf.make_marketcolors(
            up='#26a69a',
            down='#ef5350',
            edge='inherit',
            wick='inherit',
            volume={'up': '#26a69a', 'down': '#ef5350'}
        )
        s = mpf.make_mpf_style(
            marketcolors=mc,
            gridstyle=':',
            gridcolor='#e0e0e0'
        )

        # Chart title
        title = f"{company_name} ({ticker}) - Price Chart"

        # Additional plot settings for moving averages
        additional_plots = []
        if not df['MA20'].isna().all():
            additional_plots.append(mpf.make_addplot(df['MA20'], color='#ff9500', width=1))
        if not df['MA60'].isna().all():
            additional_plots.append(mpf.make_addplot(df['MA60'], color='#0066cc', width=1.5))
        if not df['MA120'].isna().all():
            additional_plots.append(mpf.make_addplot(df['MA120'], color='#cc3300', width=1.5, linestyle='--'))

        # Create chart
        fig, axes = mpf.plot(
            ohlc_df,
            type='candle',
            style=s,
            title=title,
            ylabel='Price ($)',
            volume=True,
            figsize=(12, 8),
            tight_layout=True,
            addplot=additional_plots if additional_plots else None,
            panel_ratios=(4, 1),
            returnfig=True
        )

        # Add annotations for key price points
        max_idx = df['Close'].idxmax()
        min_idx = df['Close'].idxmin()
        last_idx = df.index[-1]

        ax1 = axes[0]
        bbox_props = dict(boxstyle="round,pad=0.3", fc="#f8f9fa", ec="none", alpha=0.9)

        # High point annotation
        ax1.annotate(
            f"High: ${df.loc[max_idx, 'Close']:,.2f}",
            xy=(max_idx, df.loc[max_idx, 'Close']),
            xytext=(0, 15),
            textcoords='offset points',
            ha='center',
            va='bottom',
            bbox=bbox_props,
            fontsize=9
        )

        # Low point annotation
        ax1.annotate(
            f"Low: ${df.loc[min_idx, 'Close']:,.2f}",
            xy=(min_idx, df.loc[min_idx, 'Close']),
            xytext=(0, -15),
            textcoords='offset points',
            ha='center',
            va='top',
            bbox=bbox_props,
            fontsize=9
        )

        # Current price annotation
        ax1.annotate(
            f"Current: ${df.loc[last_idx, 'Close']:,.2f}",
            xy=(last_idx, df.loc[last_idx, 'Close']),
            xytext=(15, 0),
            textcoords='offset points',
            ha='left',
            va='center',
            bbox=bbox_props,
            fontsize=9
        )

        # Add legend for moving averages
        if additional_plots:
            legend_labels = []
            if not df['MA20'].isna().all():
                legend_labels.append('MA20')
            if not df['MA60'].isna().all():
                legend_labels.append('MA60')
            if not df['MA120'].isna().all():
                legend_labels.append('MA120')
            if legend_labels:
                ax1.legend(legend_labels, loc='upper left', fontsize=8)

        return fig

    except Exception as e:
        import logging
        logging.getLogger(__name__).warning(f"Failed to create US price chart: {e}")
        return None


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
            section_reports["summary"] = summary.lstrip('\n')
            logger.info(f"Completed summary - {len(summary)} characters")
        except Exception as e:
            logger.error(f"Error processing summary: {e}")
            section_reports["summary"] = "Summary generation failed"

        # 9. Generate charts (optional - may fail if yfinance data unavailable)
        chart_section = ""
        try:
            import yfinance as yf

            # Get stock data for charts
            stock = yf.Ticker(ticker)
            hist = stock.history(period="1y")

            if not hist.empty:
                # Create US price chart using yfinance data
                price_chart = create_us_price_chart(ticker, company_name, hist)
                if price_chart:
                    chart_html = figure_to_base64_html(
                        price_chart,
                        chart_name=f"{company_name} ({ticker}) Price Chart",
                        width=900,
                        dpi=80,
                        image_format='jpg'
                    )
                    if chart_html:
                        chart_section = f"\n\n## Price Chart\n\n{chart_html}"
                        logger.info(f"Generated price chart for {ticker}")
        except Exception as e:
            logger.warning(f"Chart generation skipped: {e}")

        # 10. Compile final report
        # Header
        formatted_date = f"{reference_date[:4]}.{reference_date[4:6]}.{reference_date[6:]}"
        final_report = f"""# {company_name} ({ticker}) Analysis Report

**Publication Date:** {formatted_date}

---

## Executive Summary

{section_reports.get("summary", "Summary not available")}

---

## 1. Technical Analysis

### 1-1. Price and Volume Analysis

{section_reports.get("price_volume_analysis", "Analysis not available")}

### 1-2. Institutional Ownership Analysis

{section_reports.get("institutional_holdings_analysis", "Analysis not available")}

---

## 2. Fundamental Analysis

### 2-1. Company Status Analysis

{section_reports.get("company_status", "Analysis not available")}

### 2-2. Company Overview Analysis

{section_reports.get("company_overview", "Analysis not available")}

---

## 3. Recent Major News Summary

{section_reports.get("news_analysis", "Analysis not available")}

---

## 4. Market Analysis

{section_reports.get("market_index_analysis", "Analysis not available")}

---

## 5. Investment Strategy and Opinion

{section_reports.get("investment_strategy", "Strategy not available")}
{chart_section}
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
