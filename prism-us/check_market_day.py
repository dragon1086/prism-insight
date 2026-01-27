#!/usr/bin/env python3
"""
US Stock Market Business Day Checker

Uses pandas-market-calendars for accurate NYSE/NASDAQ holiday detection.
Returns exit code 0 if today is a market day, 1 otherwise.

US Market Holidays:
- New Year's Day (January 1)
- Martin Luther King Jr. Day (Third Monday of January)
- Presidents Day (Third Monday of February)
- Good Friday (Friday before Easter)
- Memorial Day (Last Monday of May)
- Juneteenth National Independence Day (June 19)
- Independence Day (July 4)
- Labor Day (First Monday of September)
- Thanksgiving (Fourth Thursday of November)
- Christmas (December 25)

Trading Hours: 09:30-16:00 EST (23:30-06:00 KST)
"""

import sys
import logging
from datetime import date, datetime
from pathlib import Path

import pandas_market_calendars as mcal
import pytz

# Project root directory
PROJECT_ROOT = Path(__file__).resolve().parent

# Logging setup
logging.basicConfig(
    filename=PROJECT_ROOT / 'us_stock_scheduler.log',
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)

logger = logging.getLogger(__name__)

# NYSE calendar (also covers NASDAQ holidays)
NYSE_CALENDAR = mcal.get_calendar('NYSE')

# Timezone definitions
EST = pytz.timezone('America/New_York')
KST = pytz.timezone('Asia/Seoul')


def is_us_market_day(check_date: date = None) -> bool:
    """
    Check if the given date is a US stock market trading day.

    Args:
        check_date: Date to check (defaults to today in EST)

    Returns:
        bool: True if it's a market day, False otherwise
    """
    if check_date is None:
        # Use current date in EST timezone
        check_date = datetime.now(EST).date()

    # Weekend check (5: Saturday, 6: Sunday)
    if check_date.weekday() >= 5:
        logger.debug(f"{check_date} is a weekend.")
        return False

    # Get valid trading days for the date range
    start = check_date.strftime('%Y-%m-%d')
    end = check_date.strftime('%Y-%m-%d')

    valid_days = NYSE_CALENDAR.valid_days(start_date=start, end_date=end)

    if len(valid_days) == 0:
        # It's a holiday
        logger.debug(f"{check_date} is a US market holiday.")
        return False

    # It's a trading day
    return True


def get_holiday_name(check_date: date = None) -> str:
    """
    Get the name of the holiday for a given date.

    Args:
        check_date: Date to check (defaults to today in EST)

    Returns:
        str: Holiday name or empty string if not a holiday
    """
    import pandas as pd

    if check_date is None:
        check_date = datetime.now(EST).date()

    # Get holidays for the year
    holidays = NYSE_CALENDAR.holidays()

    # Convert to list and check
    holiday_dates = holidays.holidays

    for holiday_date in holiday_dates:
        # Convert numpy.datetime64 to date for comparison
        if pd.Timestamp(holiday_date).date() == check_date:
            return "US Market Holiday"

    return ""


def get_next_trading_day(from_date: date = None) -> date:
    """
    Get the next trading day from a given date.

    Args:
        from_date: Starting date (defaults to today in EST)

    Returns:
        date: Next trading day
    """
    if from_date is None:
        from_date = datetime.now(EST).date()

    # Look ahead up to 10 days
    start = from_date.strftime('%Y-%m-%d')
    end_date = date(from_date.year, from_date.month + 1 if from_date.month < 12 else 1,
                    from_date.day if from_date.month < 12 else from_date.day)
    end = end_date.strftime('%Y-%m-%d')

    valid_days = NYSE_CALENDAR.valid_days(start_date=start, end_date=end)

    for day in valid_days:
        if day.date() > from_date:
            return day.date()

    return None


def get_last_trading_day(from_date: date = None) -> date:
    """
    Get the most recent trading day on or before the given date.

    This is useful for testing on weekends/holidays - it returns
    the last day when market data is available.

    Args:
        from_date: Starting date (defaults to today in EST)

    Returns:
        date: Most recent trading day (could be from_date itself if it's a trading day)
    """
    if from_date is None:
        from_date = datetime.now(EST).date()

    # If today is a trading day, return today
    if is_us_market_day(from_date):
        return from_date

    # Look back up to 10 days
    from datetime import timedelta
    start_date = from_date - timedelta(days=10)
    start = start_date.strftime('%Y-%m-%d')
    end = from_date.strftime('%Y-%m-%d')

    valid_days = NYSE_CALENDAR.valid_days(start_date=start, end_date=end)

    if len(valid_days) > 0:
        # Return the most recent trading day
        return valid_days[-1].date()

    return None


def get_reference_date(from_date: date = None) -> str:
    """
    Get the reference date for analysis in YYYYMMDD format.

    Returns the most recent trading day, which is useful for
    testing on weekends/holidays.

    Args:
        from_date: Starting date (defaults to today in EST)

    Returns:
        str: Reference date in YYYYMMDD format (e.g., "20260117")
    """
    last_trading_day = get_last_trading_day(from_date)
    if last_trading_day:
        return last_trading_day.strftime('%Y%m%d')
    return None


def is_market_open() -> bool:
    """
    Check if the US market is currently open.

    Returns:
        bool: True if market is open, False otherwise
    """
    now_est = datetime.now(EST)

    # First check if it's a trading day
    if not is_us_market_day(now_est.date()):
        return False

    # Market hours: 09:30 - 16:00 EST
    market_open = now_est.replace(hour=9, minute=30, second=0, microsecond=0)
    market_close = now_est.replace(hour=16, minute=0, second=0, microsecond=0)

    return market_open <= now_est <= market_close


def get_market_status() -> dict:
    """
    Get comprehensive market status information.

    Returns:
        dict: Market status including open/closed, times, etc.
    """
    now_est = datetime.now(EST)
    now_kst = datetime.now(KST)
    today = now_est.date()

    is_trading_day = is_us_market_day(today)
    is_open = is_market_open()

    status = {
        "current_time_est": now_est.strftime("%Y-%m-%d %H:%M:%S EST"),
        "current_time_kst": now_kst.strftime("%Y-%m-%d %H:%M:%S KST"),
        "is_trading_day": is_trading_day,
        "is_market_open": is_open,
        "market_hours_est": "09:30-16:00",
        "market_hours_kst": "23:30-06:00 (next day)",
    }

    if not is_trading_day:
        status["reason"] = get_holiday_name(today) or "Weekend"
        status["next_trading_day"] = str(get_next_trading_day(today))

    return status


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Check US market trading day status")
    parser.add_argument("--verbose", "-v", action="store_true", help="Show detailed status")
    parser.add_argument("--date", "-d", type=str, help="Check specific date (YYYY-MM-DD)")
    args = parser.parse_args()

    if args.date:
        check_date = date.fromisoformat(args.date)
        is_trading = is_us_market_day(check_date)
        if args.verbose:
            print(f"Date: {check_date}")
            print(f"Is trading day: {is_trading}")
            if not is_trading:
                print(f"Reason: {get_holiday_name(check_date) or 'Weekend'}")
        sys.exit(0 if is_trading else 1)
    else:
        if args.verbose:
            status = get_market_status()
            print("=== US Market Status ===")
            for key, value in status.items():
                print(f"{key}: {value}")

        if is_us_market_day():
            # Trading day - exit code 0
            sys.exit(0)
        else:
            # Not a trading day - exit code 1
            sys.exit(1)
