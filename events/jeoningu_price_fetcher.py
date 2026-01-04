"""
Jeon Ingu Trading - Real-time price fetcher using krx_data_client

Fetches current prices for KODEX Leverage and KODEX Inverse 2X
"""
from dotenv import load_dotenv
load_dotenv()  # .env 파일에서 환경변수 로드

from krx_data_client import (
    get_index_ohlcv_by_date,
    get_market_ohlcv_by_date,
)
from datetime import datetime, timedelta
import logging


# pykrx 호환 래퍼
class stock:
    @staticmethod
    def get_index_ohlcv_by_date(fromdate, todate, ticker):
        return get_index_ohlcv_by_date(fromdate, todate, ticker)

    @staticmethod
    def get_market_ohlcv_by_date(fromdate, todate, ticker):
        return get_market_ohlcv_by_date(fromdate, todate, ticker)

logger = logging.getLogger(__name__)

# Stock codes
KODEX_LEVERAGE = "122630"
KODEX_INVERSE_2X = "252670"


def get_latest_trading_date() -> str:
    """
    Get latest trading date (excluding weekends and holidays)

    Returns:
        Date string in YYYYMMDD format
    """
    today = datetime.now()

    # Try today first, then go back up to 5 days
    for i in range(5):
        check_date = today - timedelta(days=i)
        date_str = check_date.strftime("%Y%m%d")

        # Skip weekends
        if check_date.weekday() >= 5:  # Saturday=5, Sunday=6
            continue

        try:
            # Test if market was open by fetching KOSPI index
            test_data = stock.get_index_ohlcv_by_date(
                fromdate=date_str,
                todate=date_str,
                ticker="1001"  # KOSPI
            )
            if not test_data.empty:
                logger.info(f"Latest trading date: {date_str}")
                return date_str
        except Exception:
            continue

    # Fallback to today
    return today.strftime("%Y%m%d")


def get_stock_price(stock_code: str, date: str = None) -> dict:
    """
    Get stock price information

    Args:
        stock_code: Stock code (069500 or 114800)
        date: Date in YYYYMMDD format (default: latest trading day)

    Returns:
        Dictionary with price info
    """
    if date is None:
        date = get_latest_trading_date()

    try:
        # Get OHLCV data
        df = stock.get_market_ohlcv_by_date(
            fromdate=date,
            todate=date,
            ticker=stock_code
        )

        if df.empty:
            logger.warning(f"No data for {stock_code} on {date}")
            return None

        # Get latest row
        latest = df.iloc[-1]

        return {
            "stock_code": stock_code,
            "date": date,
            "open": int(latest['시가']),
            "high": int(latest['고가']),
            "low": int(latest['저가']),
            "close": int(latest['종가']),
            "volume": int(latest['거래량'])
        }

    except Exception as e:
        logger.error(f"Error fetching price for {stock_code}: {e}")
        return None


def get_kodex_prices(date: str = None) -> dict:
    """
    Get prices for both KODEX Leverage and KODEX Inverse 2X

    Args:
        date: Date in YYYYMMDD format (default: latest trading day)

    Returns:
        Dictionary with both prices
    """
    if date is None:
        date = get_latest_trading_date()

    kodex_leverage_price = get_stock_price(KODEX_LEVERAGE, date)
    kodex_inverse_2x_price = get_stock_price(KODEX_INVERSE_2X, date)

    return {
        "date": date,
        "KODEX_LEVERAGE": kodex_leverage_price,
        "KODEX_INVERSE_2X": kodex_inverse_2x_price
    }


def get_current_price(stock_code: str) -> int:
    """
    Get current closing price (simplified)

    Args:
        stock_code: Stock code

    Returns:
        Current closing price (integer)
    """
    price_info = get_stock_price(stock_code)

    if price_info:
        return price_info['close']
    else:
        # Fallback to mock prices if API fails
        logger.warning(f"Using mock price for {stock_code}")
        if stock_code == KODEX_LEVERAGE:
            return 20000  # Mock for Leverage
        elif stock_code == KODEX_INVERSE_2X:
            return 5000  # Mock for Inverse 2X
        else:
            return 10000


# Test function
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)

    print("Testing price fetcher...")

    # Get latest trading date
    latest_date = get_latest_trading_date()
    print(f"Latest trading date: {latest_date}")

    # Get KODEX prices
    prices = get_kodex_prices()
    print(f"\nKODEX Leverage: {prices['KODEX_LEVERAGE']}")
    print(f"KODEX Inverse 2X: {prices['KODEX_INVERSE_2X']}")

    # Get current price
    current_leverage = get_current_price(KODEX_LEVERAGE)
    current_inverse_2x = get_current_price(KODEX_INVERSE_2X)
    print(f"\nCurrent KODEX Leverage: {current_leverage:,}원")
    print(f"Current KODEX Inverse 2X: {current_inverse_2x:,}원")
