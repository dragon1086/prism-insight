"""
Trading Operations for Stock Tracking

Buy/sell decision logic and message formatting.
Extracted from stock_tracking_agent.py for LLM context efficiency.
"""

import json
import logging
from datetime import datetime
from typing import Any, Dict, Tuple


logger = logging.getLogger(__name__)


def analyze_sell_decision(stock_data: Dict[str, Any]) -> Tuple[bool, str]:
    """
    Sell decision analysis.

    Args:
        stock_data: Stock information

    Returns:
        Tuple[bool, str]: Whether to sell, sell reason
    """
    try:
        ticker = stock_data.get('ticker', '')
        buy_price = stock_data.get('buy_price', 0)
        buy_date = stock_data.get('buy_date', '')
        current_price = stock_data.get('current_price', 0)
        target_price = stock_data.get('target_price', 0)
        stop_loss = stock_data.get('stop_loss', 0)

        # Calculate profit rate
        profit_rate = ((current_price - buy_price) / buy_price) * 100

        # Days elapsed from buy date
        buy_datetime = datetime.strptime(buy_date, "%Y-%m-%d %H:%M:%S")
        days_passed = (datetime.now() - buy_datetime).days

        # Extract scenario information
        scenario_str = stock_data.get('scenario', '{}')
        investment_period = "Medium-term"

        try:
            if isinstance(scenario_str, str):
                scenario_data = json.loads(scenario_str)
                investment_period = scenario_data.get('investment_period', 'Medium-term')
        except:
            pass

        # Check stop-loss condition
        if stop_loss > 0 and current_price <= stop_loss:
            return True, f"손절 조건 도달 (손절가: {stop_loss:,.0f}원)"

        # Check target price reached
        if target_price > 0 and current_price >= target_price:
            return True, f"목표가 달성 (목표가: {target_price:,.0f}원)"

        # Sell conditions by investment period
        if investment_period == "Short-term":
            if days_passed >= 15 and profit_rate >= 5:
                return True, f"단기 투자 목표 달성 (보유: {days_passed}일, 수익률: {profit_rate:.2f}%)"
            if days_passed >= 10 and profit_rate <= -3:
                return True, f"단기 투자 손실 방어 (보유: {days_passed}일, 수익률: {profit_rate:.2f}%)"

        # General sell conditions
        if profit_rate >= 10:
            return True, f"수익률 10% 이상 달성 (현재 수익률: {profit_rate:.2f}%)"

        if profit_rate <= -5:
            return True, f"손실 -5% 이상 발생 (현재 수익률: {profit_rate:.2f}%)"

        if days_passed >= 30 and profit_rate < 0:
            return True, f"30일 이상 보유 중 손실 (보유: {days_passed}일, 수익률: {profit_rate:.2f}%)"

        if days_passed >= 60 and profit_rate >= 3:
            return True, f"60일 이상 보유 중 3% 이상 수익 (보유: {days_passed}일, 수익률: {profit_rate:.2f}%)"

        if investment_period == "Long-term" and days_passed >= 90 and profit_rate < 0:
            return True, f"장기 투자 손실 정리 (보유: {days_passed}일, 수익률: {profit_rate:.2f}%)"

        return False, "보유 지속"

    except Exception as e:
        logger.error(f"Error analyzing sell: {str(e)}")
        return False, "분석 오류"


def format_buy_message(
    company_name: str,
    ticker: str,
    current_price: float,
    scenario: Dict[str, Any],
    rank_change_msg: str = ""
) -> str:
    """
    Format buy message for Telegram.

    Args:
        company_name: Company name
        ticker: Stock code
        current_price: Current price
        scenario: Trading scenario
        rank_change_msg: Ranking change message

    Returns:
        str: Formatted message
    """
    from prism_core.buy_message import render_buy_message

    return render_buy_message(market="KR", company_name=company_name, ticker=ticker,
                              current_price=current_price, scenario=scenario,
                              rank_change_msg=rank_change_msg)


def format_sell_message(
    company_name: str,
    ticker: str,
    buy_price: float,
    sell_price: float,
    profit_rate: float,
    holding_days: int,
    sell_reason: str
) -> str:
    """
    Format sell message for Telegram.

    Args:
        company_name: Company name
        ticker: Stock code
        buy_price: Buy price
        sell_price: Sell price
        profit_rate: Profit rate (%)
        holding_days: Holding period (days)
        sell_reason: Sell reason

    Returns:
        str: Formatted message
    """
    arrow = "⬆️" if profit_rate > 0 else "⬇️" if profit_rate < 0 else "➖"
    message = f"📉 매도: {company_name}({ticker})\n" \
              f"매수가: {buy_price:,.0f}원\n" \
              f"매도가: {sell_price:,.0f}원\n" \
              f"수익률: {arrow} {abs(profit_rate):.2f}%\n" \
              f"보유기간: {holding_days}일\n" \
              f"매도이유: {sell_reason}"
    return message


def calculate_profit_rate(buy_price: float, current_price: float) -> float:
    """Calculate profit rate percentage."""
    if buy_price <= 0:
        return 0.0
    return ((current_price - buy_price) / buy_price) * 100


def calculate_holding_days(buy_date: str) -> int:
    """Calculate holding period in days."""
    try:
        buy_datetime = datetime.strptime(buy_date, "%Y-%m-%d %H:%M:%S")
        return (datetime.now() - buy_datetime).days
    except:
        return 0
