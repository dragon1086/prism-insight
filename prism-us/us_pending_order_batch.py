#!/usr/bin/env python3
"""
US Pending Order Batch Processor

Processes queued reserved orders that were placed outside the KIS API time window.
KIS reserved order window: 10:00~23:20 KST (except 16:30~16:45)

This script is intended to run via cron at 10:05 KST (Tue-Sat):
  5 10 * * 2-6 cd /app/prism-insight && python3 prism-us/us_pending_order_batch.py

Flow:
  1. Check if reserved order window is currently open
  2. Query pending orders from us_pending_orders table (today only)
  3. Execute each order via buy_reserved_order / sell_reserved_order
  4. Update order status (executed / failed)
  5. Expire old pending orders (created before today)
"""

import os
import sys
import json
import sqlite3
import logging
import argparse
import datetime
from pathlib import Path

# Add project paths (prism-us first so its trading/ takes priority over KR trading/)
_prism_us_dir = str(Path(__file__).resolve().parent)
_project_root = str(Path(__file__).resolve().parent.parent)
sys.path.insert(0, _project_root)
sys.path.insert(0, _prism_us_dir)

import pytz

# Logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

KST = pytz.timezone('Asia/Seoul')

# DB path (same as trading module)
DB_PATH = Path(__file__).resolve().parent.parent / "stock_tracking_db.sqlite"


def get_pending_orders(conn: sqlite3.Connection, today_str: str) -> list:
    """Get today's pending orders."""
    cursor = conn.cursor()
    cursor.execute(
        """SELECT id, ticker, order_type, limit_price, buy_amount, exchange
           FROM us_pending_orders
           WHERE status = 'pending' AND date(created_at) = ?
           ORDER BY id ASC""",
        (today_str,)
    )
    columns = [desc[0] for desc in cursor.description]
    return [dict(zip(columns, row)) for row in cursor.fetchall()]


def update_order_status(conn: sqlite3.Connection, order_id: int,
                        status: str, result: dict = None, failure_reason: str = None):
    """Update order status after execution attempt."""
    now_kst = datetime.datetime.now(KST).strftime('%Y-%m-%d %H:%M:%S')
    conn.execute(
        """UPDATE us_pending_orders
           SET status = ?, executed_at = ?, order_result = ?, failure_reason = ?
           WHERE id = ?""",
        (status, now_kst, json.dumps(result) if result else None, failure_reason, order_id)
    )
    conn.commit()


def expire_old_orders(conn: sqlite3.Connection, today_str: str) -> int:
    """Mark old pending orders (before today) as expired."""
    cursor = conn.cursor()
    cursor.execute(
        """UPDATE us_pending_orders
           SET status = 'expired', failure_reason = 'Order expired (not processed on creation day)'
           WHERE status = 'pending' AND date(created_at) < ?""",
        (today_str,)
    )
    conn.commit()
    return cursor.rowcount


def process_pending_orders(dry_run: bool = False):
    """Main processing logic."""
    now_kst = datetime.datetime.now(KST)
    today_str = now_kst.strftime('%Y-%m-%d')

    logger.info(f"=== US Pending Order Batch Start ({today_str} {now_kst.strftime('%H:%M:%S')} KST) ===")

    # Connect to DB
    if not DB_PATH.exists():
        logger.warning(f"Database not found: {DB_PATH}")
        return

    conn = sqlite3.connect(str(DB_PATH))

    # Expire old orders first
    expired_count = expire_old_orders(conn, today_str)
    if expired_count > 0:
        logger.info(f"Expired {expired_count} old pending order(s)")

    # Get today's pending orders
    pending_orders = get_pending_orders(conn, today_str)

    if not pending_orders:
        logger.info("No pending orders to process")
        conn.close()
        return

    logger.info(f"Found {len(pending_orders)} pending order(s) to process")

    # Import trading module (prism-us/trading/ is first in sys.path)
    from trading.us_stock_trading import USStockTrading

    # Initialize trading (uses config from environment)
    try:
        trader = USStockTrading()
    except Exception as e:
        logger.error(f"Failed to initialize trading module: {e}")
        conn.close()
        return

    # Check if reserved order window is open
    if not trader.is_reserved_order_available():
        logger.error("Reserved order window is not open. Aborting batch.")
        conn.close()
        return

    # Process each order
    success_count = 0
    fail_count = 0

    for order in pending_orders:
        order_id = order['id']
        ticker = order['ticker']
        order_type = order['order_type']
        limit_price = order['limit_price']
        buy_amount = order['buy_amount']
        exchange = order['exchange']

        logger.info(f"Processing order #{order_id}: {order_type} {ticker} @ ${limit_price:.2f}")

        if dry_run:
            logger.info(f"  [DRY RUN] Would execute {order_type} for {ticker}")
            continue

        try:
            if order_type == 'buy':
                result = trader.buy_reserved_order(
                    ticker=ticker,
                    limit_price=limit_price,
                    buy_amount=buy_amount,
                    exchange=exchange
                )
            elif order_type == 'sell':
                result = trader.sell_reserved_order(
                    ticker=ticker,
                    limit_price=limit_price if limit_price > 0 else None,
                    exchange=exchange
                )
            else:
                logger.warning(f"  Unknown order type: {order_type}")
                update_order_status(conn, order_id, 'failed', failure_reason=f'Unknown order type: {order_type}')
                fail_count += 1
                continue

            if result.get('success'):
                logger.info(f"  Order #{order_id} executed successfully: {result.get('message')}")
                update_order_status(conn, order_id, 'executed', result=result)
                success_count += 1
            else:
                error_msg = result.get('message', 'Unknown error')
                logger.error(f"  Order #{order_id} failed: {error_msg}")
                update_order_status(conn, order_id, 'failed', result=result, failure_reason=error_msg)
                fail_count += 1

        except Exception as e:
            logger.error(f"  Order #{order_id} exception: {e}")
            update_order_status(conn, order_id, 'failed', failure_reason=str(e))
            fail_count += 1

        # Rate limit between orders
        import time
        time.sleep(0.5)

    conn.close()

    logger.info(f"=== Batch Complete: {success_count} success, {fail_count} failed, {len(pending_orders)} total ===")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="US Pending Order Batch Processor")
    parser.add_argument("--dry-run", action="store_true", help="Dry run (don't execute orders)")
    args = parser.parse_args()

    process_pending_orders(dry_run=args.dry_run)
