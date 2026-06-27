#!/usr/bin/env python3
"""
Trading Journal Retry Script
Retrieves data from trading_history and regenerates journal entries.

Usage:
    # Retry by specific trade ID
    python retry_journal_entry.py --id 40

    # Retry recent trade by ticker
    python retry_journal_entry.py --ticker 035720

    # Retry all trades without journal entries
    python retry_journal_entry.py --all-missing
"""

import argparse
import asyncio
import json
import logging
import sqlite3

from dotenv import load_dotenv
load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


async def retry_journal_entry(db_path: str, trade_id: int = None, ticker: str = None):
    """Regenerate journal entry for a specific trade"""
    from stock_tracking_agent import StockTrackingAgent

    # Query trade info from DB
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    if trade_id:
        cursor.execute("SELECT * FROM trading_history WHERE id = ?", (trade_id,))
    elif ticker:
        cursor.execute(
            "SELECT * FROM trading_history WHERE ticker = ? ORDER BY sell_date DESC LIMIT 1",
            (ticker,)
        )
    else:
        logger.error("Must specify trade_id or ticker")
        return False

    row = cursor.fetchone()
    if not row:
        logger.error(f"Trade record not found (id={trade_id}, ticker={ticker})")
        return False

    trade_data = dict(row)
    logger.info(f"Trade record retrieved: {trade_data['company_name']}({trade_data['ticker']})")
    logger.info(f"  - Buy: {trade_data['buy_price']:,.0f} ({trade_data['buy_date']})")
    logger.info(f"  - Sell: {trade_data['sell_price']:,.0f} ({trade_data['sell_date']})")
    logger.info(f"  - Return: {trade_data['profit_rate']:.2f}%")

    # Check if journal entry already exists
    cursor.execute(
        """
        SELECT id FROM trading_journal
        WHERE ticker = ? AND trade_date LIKE ?
        """,
        (trade_data['ticker'], trade_data['sell_date'][:10] + '%')
    )
    existing = cursor.fetchone()
    if existing:
        logger.warning(f"Journal entry already exists (journal_id={existing['id']})")
        confirm = input("Overwrite? (y/N): ")
        if confirm.lower() != 'y':
            logger.info("Cancelled")
            return False
        # Delete existing entry
        cursor.execute("DELETE FROM trading_journal WHERE id = ?", (existing['id'],))
        conn.commit()
        logger.info(f"Existing journal entry deleted (id={existing['id']})")

    conn.close()

    # Create journal entry using StockTrackingAgent
    agent = StockTrackingAgent(db_path=db_path, enable_journal=True)

    # Construct stock_data
    stock_data = {
        'ticker': trade_data['ticker'],
        'company_name': trade_data['company_name'],
        'buy_price': trade_data['buy_price'],
        'buy_date': trade_data['buy_date'],
        'scenario': trade_data['scenario'] or '{}'
    }

    # Infer sell reason (try extracting from scenario)
    sell_reason = "System sell"
    try:
        scenario = json.loads(trade_data['scenario'] or '{}')
        if trade_data['profit_rate'] < 0:
            stop_loss = scenario.get('stop_loss')
            if stop_loss and trade_data['sell_price'] <= stop_loss:
                sell_reason = f"Stop loss ({stop_loss:,.0f}) reached"
            else:
                sell_reason = "Loss liquidation"
        else:
            target_price = scenario.get('target_price')
            if target_price and trade_data['sell_price'] >= target_price:
                sell_reason = f"Target price ({target_price:,.0f}) reached"
            else:
                sell_reason = "Profit taking"
    except:
        pass

    logger.info(f"Sell reason: {sell_reason}")
    logger.info("Starting journal entry creation...")

    try:
        result = await agent._create_journal_entry(
            stock_data=stock_data,
            sell_price=trade_data['sell_price'],
            profit_rate=trade_data['profit_rate'],
            holding_days=trade_data['holding_days'],
            sell_reason=sell_reason
        )

        if result:
            logger.info("Journal entry created successfully!")
            return True
        else:
            logger.error("Journal entry creation failed")
            return False

    except Exception as e:
        logger.error(f"Error during journal entry creation: {e}")
        import traceback
        traceback.print_exc()
        return False


async def retry_all_missing(db_path: str):
    """Retry all trades without journal entries"""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    # Query trades without journal entries
    cursor.execute("""
        SELECT th.id, th.ticker, th.company_name, th.sell_date, th.profit_rate
        FROM trading_history th
        LEFT JOIN trading_journal tj ON th.ticker = tj.ticker
            AND date(th.sell_date) = date(tj.trade_date)
        WHERE tj.id IS NULL
        ORDER BY th.sell_date DESC
    """)

    missing = cursor.fetchall()
    conn.close()

    if not missing:
        logger.info("All trades have journal entries")
        return

    logger.info(f"Trades without journal: {len(missing)} records")
    for row in missing:
        logger.info(f"  - [{row['id']}] {row['company_name']}({row['ticker']}) "
                   f"{row['sell_date'][:10]} ({row['profit_rate']:.2f}%)")

    confirm = input(f"\nCreate journal for {len(missing)} records? (y/N): ")
    if confirm.lower() != 'y':
        logger.info("Cancelled")
        return

    success = 0
    for row in missing:
        logger.info(f"\n{'='*50}")
        logger.info(f"Processing: {row['company_name']}({row['ticker']})")
        result = await retry_journal_entry(db_path, trade_id=row['id'])
        if result:
            success += 1

    logger.info(f"\nCompleted: {success}/{len(missing)} successful")


def main():
    parser = argparse.ArgumentParser(description='Trading journal retry script')
    parser.add_argument('--db-path', default='stock_tracking_db.sqlite',
                       help='Database path')
    parser.add_argument('--id', type=int, help='Trade ID')
    parser.add_argument('--ticker', help='Ticker code (recent trade)')
    parser.add_argument('--all-missing', action='store_true',
                       help='Retry all trades without journal entries')

    args = parser.parse_args()

    if args.all_missing:
        asyncio.run(retry_all_missing(args.db_path))
    elif args.id or args.ticker:
        asyncio.run(retry_journal_entry(args.db_path, trade_id=args.id, ticker=args.ticker))
    else:
        parser.print_help()
        print("\nExamples:")
        print("  python retry_journal_entry.py --id 40")
        print("  python retry_journal_entry.py --ticker 035720")
        print("  python retry_journal_entry.py --all-missing")


if __name__ == "__main__":
    main()
