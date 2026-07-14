#!/usr/bin/env python3
"""
Stock tracking agent specific function test script
"""
import asyncio
import json
import logging

from stock_tracking_enhanced_agent import EnhancedStockTrackingAgent as StockTrackingAgent
from stock_tracking_agent import app

# Logging setup
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

async def run_specific_functions_check():
    """Specific function test"""

    async with app.run():
        # Initialize agent
        agent = StockTrackingAgent()
        await agent.initialize()

        # Report file to test
        report_path = "../reports/013700_까뮤이앤씨_20250311_morning_gpt4o.md"

        # 1. Ticker info extraction test
        ticker, company_name = await agent._extract_ticker_info(report_path)
        logger.info(f"Ticker info extraction: {ticker}, {company_name}")

        # 2. Current stock price check test
        current_price = await agent._get_current_stock_price(ticker)
        logger.info(f"Current price: {current_price}")

        # 3. Trading scenario extraction from report test
        with open(report_path, 'r', encoding='utf-8') as f:
            report_content = f.read()

        scenario = await agent._extract_trading_scenario(report_content)
        logger.info(f"Trading scenario: {json.dumps(scenario, indent=2, ensure_ascii=False)}")

        # 4. Current holdings count check
        count = await agent._get_current_slots_count()
        logger.info(f"Current holdings count: {count}")

        # 5. Update holdings
        sold_stocks = await agent.update_holdings()
        logger.info(f"Sold stocks: {sold_stocks}")

if __name__ == "__main__":
    # Run test
    asyncio.run(run_specific_functions_check())
