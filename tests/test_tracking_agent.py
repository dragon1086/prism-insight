#!/usr/bin/env python3
"""
Stock tracking agent test script
"""
import asyncio
import logging

from stock_tracking_enhanced_agent import EnhancedStockTrackingAgent as StockTrackingAgent
from stock_tracking_agent import app

# Logging setup
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

async def run_agent_check():
    """Test function"""
    # Specify report file paths to test
    report_paths = [
        "reports/013700_까뮤이앤씨_20250311_morning_gpt4o.md",
        "reports/006660_삼성공조_20250312_morning_gpt4o.md"
    ]

    async with app.run():
        # Initialize agent
        agent = StockTrackingAgent()
        await agent.initialize()

        logger.info("===== Individual report analysis test =====")
        # Report analysis test
        for report_path in report_paths:
            logger.info(f"Analyzing report: {report_path}")
            result = await agent.analyze_report(report_path)
            logger.info(f"Analysis result: {result}")
            logger.info("-" * 50)

        logger.info("\n===== Full process test =====")
        # Full process test
        buy_count, sell_count = await agent.process_reports(report_paths)
        logger.info(f"Processing result: {buy_count} buys, {sell_count} sells")

        logger.info("\n===== Report summary test =====")
        # Report summary test
        summary = await agent.generate_report_summary()
        logger.info(f"Summary report:\n{summary}")

if __name__ == "__main__":
    # Run test
    asyncio.run(run_agent_check())
