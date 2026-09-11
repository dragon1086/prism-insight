#!/usr/bin/env python3
"""
Performance Tracking Data Recovery Script

Recovers 7d/14d/30d returns for the reset analysis_performance_tracker table
by querying historical price data.
"""

import sqlite3
import logging
from datetime import datetime, timedelta
from pathlib import Path

# Support the documented direct-file entrypoint without PYTHONPATH.
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import time

# KIS import
try:
    from cores import market_data as kis_data
    MARKET_DATA_AVAILABLE = True
except ImportError:
    MARKET_DATA_AVAILABLE = False
    print("KIS is not installed. Check the repository KIS provider configuration")

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

DB_PATH = Path(__file__).parent.parent / "stock_tracking_db.sqlite"


def get_historical_price(ticker: str, target_date: str) -> float | None:
    """
    Retrieve the closing price for a specific date.
    If the date is a market holiday, returns the previous business day's price.

    Args:
        ticker: Stock code (6 digits)
        target_date: Query date (YYYY-MM-DD)

    Returns:
        Closing price or None
    """
    if not MARKET_DATA_AVAILABLE:
        return None

    try:
        # Convert date format (YYYY-MM-DD -> YYYYMMDD)
        date_obj = datetime.strptime(target_date, "%Y-%m-%d")
        end_date = date_obj.strftime("%Y%m%d")
        # Query from 5 days prior (to handle market holidays)
        start_date = (date_obj - timedelta(days=5)).strftime("%Y%m%d")

        # Query price using KIS
        df = kis_data.get_market_ohlcv_by_date(start_date, end_date, ticker)

        if df.empty:
            return None

        # Return most recent closing price
        return float(df['Close'].iloc[-1])

    except Exception as e:
        logger.error(f"[{ticker}] Failed to query price for {target_date}: {e}")
        return None


def calculate_target_date(analyzed_date: str, days: int) -> str:
    """Calculate date N days after analysis date"""
    # Remove time portion
    date_only = analyzed_date.split(' ')[0] if ' ' in analyzed_date else analyzed_date
    date_obj = datetime.strptime(date_only, "%Y-%m-%d")
    target = date_obj + timedelta(days=days)
    return target.strftime("%Y-%m-%d")


def backfill_record(conn, record: dict) -> dict:
    """
    Recover 7d/14d/30d returns for a single record.

    Returns:
        Updated fields
    """
    ticker = record['ticker']
    analyzed_date = record['analyzed_date']
    analyzed_price = record['analyzed_price']

    if not analyzed_price or analyzed_price == 0:
        logger.warning(f"[{ticker}] Skipping due to missing analysis price")
        return {}

    updates = {}

    # Day 7
    if record.get('tracked_7d_return') is None:
        target_date = calculate_target_date(analyzed_date, 7)
        if datetime.strptime(target_date, "%Y-%m-%d") <= datetime.now():
            price = get_historical_price(ticker, target_date)
            if price:
                return_rate = (price - analyzed_price) / analyzed_price
                updates['tracked_7d_date'] = target_date
                updates['tracked_7d_price'] = price
                updates['tracked_7d_return'] = return_rate
                logger.info(f"  Day 7: {analyzed_price:,.0f} → {price:,.0f} ({return_rate*100:+.2f}%)")

    # Day 14
    if record.get('tracked_14d_return') is None:
        target_date = calculate_target_date(analyzed_date, 14)
        if datetime.strptime(target_date, "%Y-%m-%d") <= datetime.now():
            price = get_historical_price(ticker, target_date)
            if price:
                return_rate = (price - analyzed_price) / analyzed_price
                updates['tracked_14d_date'] = target_date
                updates['tracked_14d_price'] = price
                updates['tracked_14d_return'] = return_rate
                logger.info(f"  Day 14: {analyzed_price:,.0f} → {price:,.0f} ({return_rate*100:+.2f}%)")

    # Day 30
    if record.get('tracked_30d_return') is None:
        target_date = calculate_target_date(analyzed_date, 30)
        if datetime.strptime(target_date, "%Y-%m-%d") <= datetime.now():
            price = get_historical_price(ticker, target_date)
            if price:
                return_rate = (price - analyzed_price) / analyzed_price
                updates['tracked_30d_date'] = target_date
                updates['tracked_30d_price'] = price
                updates['tracked_30d_return'] = return_rate
                updates['tracking_status'] = 'completed'
                logger.info(f"  Day 30: {analyzed_price:,.0f} → {price:,.0f} ({return_rate*100:+.2f}%)")

    return updates


def run_backfill():
    """Main recovery execution"""
    logger.info("=" * 60)
    logger.info("Starting performance tracking data recovery")
    logger.info("=" * 60)

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    # Query recovery targets (records where 7d/14d/30d values are identical)
    cursor.execute("""
        SELECT * FROM analysis_performance_tracker
        WHERE tracking_status IN ('in_progress', 'completed')
          AND tracked_7d_return IS NOT NULL
          AND tracked_7d_return = tracked_14d_return
          AND tracked_14d_return = tracked_30d_return
        ORDER BY analyzed_date ASC
    """)

    records = [dict(row) for row in cursor.fetchall()]
    logger.info(f"Recovery targets: {len(records)} records")

    if not records:
        logger.info("No records to recover.")
        conn.close()
        return

    updated_count = 0
    error_count = 0

    for i, record in enumerate(records):
        ticker = record['ticker']
        company = record['company_name']
        analyzed_date = record['analyzed_date']

        logger.info(f"[{i+1}/{len(records)}] {company} ({ticker}) - Analysis date: {analyzed_date}")

        try:
            # Reset existing values (to recalculate)
            cursor.execute("""
                UPDATE analysis_performance_tracker
                SET tracked_7d_return = NULL, tracked_7d_price = NULL, tracked_7d_date = NULL,
                    tracked_14d_return = NULL, tracked_14d_price = NULL, tracked_14d_date = NULL,
                    tracked_30d_return = NULL, tracked_30d_price = NULL, tracked_30d_date = NULL,
                    tracking_status = 'in_progress'
                WHERE id = ?
            """, (record['id'],))

            # Recover using historical prices
            record['tracked_7d_return'] = None
            record['tracked_14d_return'] = None
            record['tracked_30d_return'] = None

            updates = backfill_record(conn, record)

            if updates:
                # Generate update query
                set_clause = ", ".join([f"{k} = ?" for k in updates.keys()])
                values = list(updates.values()) + [record['id']]

                cursor.execute(f"""
                    UPDATE analysis_performance_tracker
                    SET {set_clause}, updated_at = datetime('now')
                    WHERE id = ?
                """, values)

                updated_count += 1

            # Prevent API rate limit
            time.sleep(0.3)

        except Exception as e:
            logger.error(f"[{ticker}] Recovery failed: {e}")
            error_count += 1

    conn.commit()
    conn.close()

    logger.info("=" * 60)
    logger.info(f"Recovery complete: Success {updated_count}, Failed {error_count}")
    logger.info("=" * 60)


if __name__ == "__main__":
    run_backfill()
