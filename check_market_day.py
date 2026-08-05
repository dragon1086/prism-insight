#!/usr/bin/env python3
from holidays.countries import KR
from datetime import date
import sys
import logging
from pathlib import Path

# Auto-detect project root directory
PROJECT_ROOT = Path(__file__).resolve().parent

# Logging configuration
logging.basicConfig(
    filename=PROJECT_ROOT / 'stock_scheduler.log',
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)

logger = logging.getLogger(__name__)

def is_market_day(target_date=None):
    """Check if a date is a Korean stock market trading day.

    ``target_date`` defaults to today, which is how the scheduler and the
    orchestrator call it. ``trigger_batch`` passes an explicit date so it can
    walk backwards to the most recent session without reaching for KRX.
    """
    today = target_date or date.today()

    # Weekend check (5: Saturday, 6: Sunday)
    if today.weekday() >= 5:
        logger.debug(f"{today} is a weekend.")
        return False

    # Korean holiday check
    kr_holidays = KR()
    if today in kr_holidays:
        holiday_name = kr_holidays.get(today)
        logger.debug(f"{today} is a holiday ({holiday_name}).")
        return False

    # Labor Day (May 1) check - Stock market closed
    if today.month == 5 and today.day == 1:
        logger.debug(f"{today} is Labor Day.")
        return False

    # Year-end (December 31) check - Stock market closed
    if today.month == 12 and today.day == 31:
        logger.debug(f"{today} is year-end closing day.")
        return False

    # 2025 special holidays/substitute holidays check
    if today.year == 2025:
        # Temporary holidays
        if ((today.month == 1 and today.day == 27) or  # Lunar New Year special holiday
                (today.month == 3 and today.day == 3) or   # Independence Movement Day substitute
                (today.month == 5 and today.day == 6) or   # Children's Day/Buddha's Birthday substitute
                (today.month == 10 and today.day == 8)):   # Chuseok substitute
            logger.debug(f"{today} is a 2025 temporary/substitute holiday.")
            return False

        # Presidential election day - June 3, 2025
        if today.month == 6 and today.day == 3:
            logger.debug(f"{today} is Presidential Election Day.")
            return False

        # Possible temporary holiday (mentioned in news)
        # if today.month == 10 and today.day == 10:
        #     logger.debug(f"{today} is a possible temporary holiday.")
        #     return False

    # 2026 and later special holidays check (needs annual update)
    elif today.year == 2026:
        # Add 2026 special holidays here
        pass

    # Trading day
    return True

if __name__ == "__main__":
    if is_market_day():
        # Trading day, exit code 0 (normal)
        sys.exit(0)
    else:
        # Not a trading day, exit code 1 (abnormal)
        sys.exit(1)