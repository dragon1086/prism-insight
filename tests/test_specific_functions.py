#!/usr/bin/env python3
"""
주식 트래킹 에이전트 특정 기능 테스트 스크립트
"""
import asyncio
import json
import logging
import pytest

from stock_tracking_enhanced_agent import EnhancedStockTrackingAgent as StockTrackingAgent
from stock_tracking_agent import app

# 로깅 설정
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

@pytest.mark.asyncio
async def test_specific_functions():
    """특정 기능 테스트 함수"""

    async with app.run():
        # 에이전트 초기화
        agent = StockTrackingAgent()
        await agent.initialize()

        # 테스트할 보고서 파일
        report_path = "../reports/307950_현대오토에버_20251102_morning_gpt4.1.md"

        # 1. 종목 정보 추출 테스트
        ticker, company_name = await agent._extract_ticker_info(report_path)
        logger.info(f"종목 정보 추출: {ticker}, {company_name}")

        # 2. 현재 주가 조회 테스트
        current_price = await agent._get_current_stock_price(ticker)
        logger.info(f"현재 주가: {current_price}")

        # 3. 보고서에서 매매 시나리오 추출 테스트
        with open(report_path, 'r', encoding='utf-8') as f:
            report_content = f.read()

        scenario = await agent._extract_trading_scenario(report_content)
        logger.info(f"매매 시나리오: {json.dumps(scenario, indent=2, ensure_ascii=False)}")

        # 4. 현재 보유 종목 수 조회
        count = await agent._get_current_slots_count()
        logger.info(f"현재 보유 종목 수: {count}")

        # 5. 보유종목 업데이트
        sold_stocks = await agent.update_holdings()
        logger.info(f"매도된 종목: {sold_stocks}")

if __name__ == "__main__":
    # 테스트 실행
    asyncio.run(test_specific_functions())
