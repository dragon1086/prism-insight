"""
Helper Utilities for Stock Tracking

Standalone functions for ticker/price/sector operations.
Extracted from stock_tracking_agent.py for LLM context efficiency.
"""

import json
import logging
import re
import traceback
from datetime import timedelta
from pathlib import Path
from typing import Any, Dict, Tuple

logger = logging.getLogger(__name__)


def extract_ticker_info(report_path: str) -> Tuple[str, str]:
    """
    Extract ticker code and company name from report file path.

    Args:
        report_path: Report file path

    Returns:
        Tuple[str, str]: Ticker code, company name
    """
    try:
        file_name = Path(report_path).stem
        pattern = r'^([A-Za-z0-9]+)_([^_]+)'
        match = re.match(pattern, file_name)

        if match:
            ticker = match.group(1)
            company_name = match.group(2)
            return ticker, company_name
        else:
            # Legacy fallback
            parts = file_name.split('_')
            if len(parts) >= 2:
                return parts[0], parts[1]

        logger.error(f"Cannot extract ticker info from filename: {file_name}")
        return "", ""
    except Exception as e:
        logger.error(f"Error extracting ticker info: {str(e)}")
        return "", ""


async def get_current_stock_price(cursor, ticker: str) -> float:
    """
    Get current stock price.

    Args:
        cursor: SQLite cursor
        ticker: Stock code

    Returns:
        float: Current stock price
    """
    try:
        from krx_data_client import get_nearest_business_day_in_a_week, get_market_ohlcv_by_ticker
        import datetime

        today = datetime.datetime.now().strftime("%Y%m%d")
        trade_date = get_nearest_business_day_in_a_week(today, prev=True)
        logger.info(f"Target date: {trade_date}")

        df = get_market_ohlcv_by_ticker(trade_date)

        if ticker in df.index:
            current_price = df.loc[ticker, "Close"]
            logger.info(f"{ticker} current price: {current_price:,.0f} KRW")
            return float(current_price)
        else:
            logger.warning(f"Cannot find ticker {ticker}")
            return _get_last_price_from_db(cursor, ticker)

    except Exception as e:
        logger.error(f"Error querying current price for {ticker}: {str(e)}")
        logger.error(traceback.format_exc())
        return _get_last_price_from_db(cursor, ticker)


def _get_last_price_from_db(cursor, ticker: str) -> float:
    """Get last saved price from DB as fallback."""
    try:
        cursor.execute(
            "SELECT current_price FROM stock_holdings WHERE ticker = ?",
            (ticker,)
        )
        row = cursor.fetchone()
        if row and row[0]:
            last_price = float(row[0])
            logger.warning(f"{ticker} price query failed, using last price: {last_price}")
            return last_price
    except:
        pass
    return 0.0


async def get_trading_value_rank_change(ticker: str) -> Tuple[float, str]:
    """
    Calculate trading value ranking change for a stock.

    Args:
        ticker: Stock code

    Returns:
        Tuple[float, str]: Ranking change percentage, analysis result message
    """
    try:
        from krx_data_client import get_nearest_business_day_in_a_week, get_market_ohlcv_by_ticker
        import datetime

        today = datetime.datetime.now().strftime("%Y%m%d")

        # Get recent 2 business days
        recent_date = get_nearest_business_day_in_a_week(today, prev=True)
        previous_date_obj = datetime.datetime.strptime(recent_date, "%Y%m%d") - timedelta(days=1)
        previous_date = get_nearest_business_day_in_a_week(
            previous_date_obj.strftime("%Y%m%d"),
            prev=True
        )

        logger.info(f"Recent trading day: {recent_date}, Previous trading day: {previous_date}")

        recent_df = get_market_ohlcv_by_ticker(recent_date)
        previous_df = get_market_ohlcv_by_ticker(previous_date)

        # Sort by trading value to generate rankings
        recent_rank = recent_df.sort_values(by="Amount", ascending=False).reset_index()
        previous_rank = previous_df.sort_values(by="Amount", ascending=False).reset_index()

        # Find ranking for ticker
        recent_ticker_rank = 0
        previous_ticker_rank = 0

        if ticker in recent_rank['Ticker'].values:
            recent_ticker_rank = recent_rank[recent_rank['Ticker'] == ticker].index[0] + 1

        if ticker in previous_rank['Ticker'].values:
            previous_ticker_rank = previous_rank[previous_rank['Ticker'] == ticker].index[0] + 1

        if recent_ticker_rank == 0 or previous_ticker_rank == 0:
            return 0, "No trading value ranking info"

        # Calculate ranking change
        rank_change = previous_ticker_rank - recent_ticker_rank
        rank_change_percentage = (rank_change / previous_ticker_rank) * 100

        recent_value = int(recent_df.loc[ticker, "Amount"]) if ticker in recent_df.index else 0
        previous_value = int(previous_df.loc[ticker, "Amount"]) if ticker in previous_df.index else 0
        value_change_percentage = ((recent_value - previous_value) / previous_value * 100) if previous_value > 0 else 0

        result_msg = (
            f"Trading value rank: #{recent_ticker_rank} (prev: #{previous_ticker_rank}, "
            f"change: {'▲' if rank_change > 0 else '▼' if rank_change < 0 else '='}{abs(rank_change)}), "
            f"Trading value: {recent_value:,} KRW (prev: {previous_value:,} KRW, "
            f"change: {'▲' if value_change_percentage > 0 else '▼' if value_change_percentage < 0 else '='}{abs(value_change_percentage):.1f}%)"
        )

        logger.info(f"{ticker} {result_msg}")
        return rank_change_percentage, result_msg

    except Exception as e:
        logger.error(f"Error analyzing trading value ranking for {ticker}: {str(e)}")
        logger.error(traceback.format_exc())
        return 0, "Trading value ranking analysis failed"


def is_ticker_in_holdings(cursor, ticker: str) -> bool:
    """
    Check if stock is already in holdings.

    Args:
        cursor: SQLite cursor
        ticker: Stock code

    Returns:
        bool: True if holding, False otherwise
    """
    try:
        cursor.execute(
            "SELECT COUNT(*) FROM stock_holdings WHERE ticker = ?",
            (ticker,)
        )
        count = cursor.fetchone()[0]
        return count > 0
    except Exception as e:
        logger.error(f"Error checking holdings: {str(e)}")
        return False


def get_current_slots_count(cursor) -> int:
    """Get current number of holdings."""
    try:
        cursor.execute("SELECT COUNT(*) FROM stock_holdings")
        count = cursor.fetchone()[0]
        return count
    except Exception as e:
        logger.error(f"Error querying holdings count: {str(e)}")
        return 0


def check_sector_diversity(cursor, sector: str, max_same_sector: int, concentration_ratio: float) -> bool:
    """
    Check for over-concentration in same sector.

    Args:
        cursor: SQLite cursor
        sector: Sector name
        max_same_sector: Maximum holdings in same sector
        concentration_ratio: Sector concentration limit ratio

    Returns:
        bool: Investment availability (True: available, False: over-concentrated)
    """
    try:
        if not sector or sector == "알 수 없음":
            return True

        cursor.execute("SELECT scenario FROM stock_holdings")
        holdings_scenarios = cursor.fetchall()

        sectors = []
        for row in holdings_scenarios:
            if row[0]:
                try:
                    scenario_data = json.loads(row[0])
                    if 'sector' in scenario_data:
                        sectors.append(scenario_data['sector'])
                except:
                    pass

        same_sector_count = sum(1 for s in sectors if s and s.lower() == sector.lower())

        if same_sector_count >= max_same_sector or \
           (sectors and same_sector_count / len(sectors) >= concentration_ratio):
            logger.warning(
                f"Sector '{sector}' over-investment risk: "
                f"Currently holding {same_sector_count} stocks "
                f"(max {max_same_sector}, concentration limit {concentration_ratio*100:.0f}%)"
            )
            return False

        return True

    except Exception as e:
        logger.error(f"Error checking sector diversity: {str(e)}")
        return True


def parse_price_value(value: Any) -> float:
    """
    Parse price value and convert to number.

    Args:
        value: Price value (number, string, range, etc.)

    Returns:
        float: Parsed price (0 on failure)
    """
    try:
        if isinstance(value, (int, float)):
            return float(value)

        if isinstance(value, str):
            value = value.replace(',', '')

            range_patterns = [
                r'(\d+(?:\.\d+)?)\s*[-~]\s*(\d+(?:\.\d+)?)',
                r'(\d+(?:\.\d+)?)\s*~\s*(\d+(?:\.\d+)?)',
            ]

            for pattern in range_patterns:
                match = re.search(pattern, value)
                if match:
                    low = float(match.group(1))
                    high = float(match.group(2))
                    return (low + high) / 2

            number_match = re.search(r'(\d+(?:\.\d+)?)', value)
            if number_match:
                return float(number_match.group(1))

        return 0
    except Exception as e:
        logger.warning(f"Failed to parse price value: {value} - {str(e)}")
        return 0


def default_scenario() -> Dict[str, Any]:
    """Return default trading scenario."""
    return {
        "portfolio_analysis": "Analysis failed",
        "buy_score": 0,
        "decision": "관망",
        "target_price": 0,
        "stop_loss": 0,
        "investment_period": "단기",
        "rationale": "Analysis failed",
        "sector": "알 수 없음",
        "considerations": "Analysis failed"
    }
