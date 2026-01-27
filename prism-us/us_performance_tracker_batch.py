#!/usr/bin/env python3
"""
US Stock Performance Tracker Batch Script

Tracks the 7/14/30 day performance of analyzed stocks (both traded and watched)
to collect statistics on which trigger types actually produce good results.

Usage:
    python us_performance_tracker_batch.py              # Update all tracking targets
    python us_performance_tracker_batch.py --dry-run    # Test without DB updates
    python us_performance_tracker_batch.py --report     # Show current tracking status report
"""
from dotenv import load_dotenv
load_dotenv()

import os
import sys
import sqlite3
import argparse
import logging
from datetime import datetime, timedelta
from typing import Dict, List, Any, Optional
from pathlib import Path

# Project paths
PROJECT_ROOT = Path(__file__).parent.parent
PRISM_US_DIR = Path(__file__).parent
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PRISM_US_DIR))

# Logging setup
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(PRISM_US_DIR / f"us_performance_tracker_{datetime.now().strftime('%Y%m%d')}.log")
    ]
)
logger = logging.getLogger(__name__)

# Database path
DB_PATH = PROJECT_ROOT / "stock_tracking_db.sqlite"

# Import yfinance for price data
try:
    import yfinance as yf
    YFINANCE_AVAILABLE = True
except ImportError:
    YFINANCE_AVAILABLE = False
    logger.warning("yfinance not installed. Price fetching will be unavailable.")

# Import US market day checker
try:
    from check_market_day import is_us_market_day, get_last_trading_day
    MARKET_CALENDAR_AVAILABLE = True
except ImportError:
    MARKET_CALENDAR_AVAILABLE = False
    logger.warning("US market calendar not available.")


class USPerformanceTrackerBatch:
    """US Stock Performance Tracker Batch Processor"""

    # Tracking day intervals
    TRACK_DAYS = [7, 14, 30]

    def __init__(self, db_path: str = None, dry_run: bool = False):
        """
        Args:
            db_path: SQLite database path
            dry_run: If True, test without actual DB updates
        """
        self.db_path = db_path or str(DB_PATH)
        self.dry_run = dry_run
        self.today = datetime.now().strftime("%Y-%m-%d")
        self.today_yyyymmdd = datetime.now().strftime("%Y%m%d")

    def connect_db(self) -> sqlite3.Connection:
        """Connect to database"""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def ensure_columns_exist(self):
        """Ensure the required columns exist in the table (migration)"""
        conn = self.connect_db()
        try:
            cursor = conn.cursor()

            # Check and add missing columns
            migrations = [
                ("tracking_status", "TEXT DEFAULT 'pending'"),
                ("was_traded", "INTEGER DEFAULT 0"),
                ("risk_reward_ratio", "REAL"),
                ("skip_reason", "TEXT"),
            ]

            for column_name, column_type in migrations:
                try:
                    cursor.execute(f"ALTER TABLE us_analysis_performance_tracker ADD COLUMN {column_name} {column_type}")
                    conn.commit()
                    logger.info(f"Added column: {column_name}")
                except sqlite3.OperationalError as e:
                    if "duplicate column name" in str(e).lower():
                        pass  # Column already exists
                    else:
                        logger.warning(f"Migration warning: {e}")

            # Update existing records with calculated tracking_status
            cursor.execute("""
                UPDATE us_analysis_performance_tracker
                SET tracking_status = CASE
                    WHEN return_30d IS NOT NULL THEN 'completed'
                    WHEN return_7d IS NOT NULL THEN 'in_progress'
                    ELSE 'pending'
                END
                WHERE tracking_status IS NULL
            """)
            conn.commit()

        finally:
            conn.close()

    def get_tracking_targets(self) -> List[Dict[str, Any]]:
        """
        Get stocks that need tracking updates.

        Returns:
            List of stocks that need performance tracking
        """
        conn = self.connect_db()
        try:
            cursor = conn.execute("""
                SELECT
                    id,
                    ticker,
                    company_name,
                    trigger_type,
                    trigger_mode,
                    analysis_date,
                    analysis_price,
                    decision,
                    was_traded,
                    skip_reason,
                    buy_score,
                    target_price,
                    stop_loss,
                    risk_reward_ratio,
                    price_7d,
                    price_14d,
                    price_30d,
                    return_7d,
                    return_14d,
                    return_30d,
                    tracking_status,
                    sector
                FROM us_analysis_performance_tracker
                WHERE tracking_status IN ('pending', 'in_progress')
                   OR tracking_status IS NULL
                ORDER BY analysis_date ASC
            """)

            rows = cursor.fetchall()
            return [dict(row) for row in rows]
        finally:
            conn.close()

    def get_current_price(self, ticker: str) -> Optional[float]:
        """
        Get current stock price using yfinance.

        Args:
            ticker: Stock ticker symbol (e.g., 'AAPL', 'MSFT')

        Returns:
            Current price in USD or None if unavailable
        """
        if not YFINANCE_AVAILABLE:
            logger.error("yfinance not available. Cannot fetch price.")
            return None

        try:
            stock = yf.Ticker(ticker)

            # Try to get current price from info
            info = stock.info
            current_price = info.get('regularMarketPrice') or info.get('currentPrice')

            if current_price:
                return float(current_price)

            # Fallback: Get last close from history
            hist = stock.history(period="5d")
            if not hist.empty:
                return float(hist['Close'].iloc[-1])

            logger.warning(f"[{ticker}] No price data available")
            return None

        except Exception as e:
            logger.error(f"[{ticker}] Price fetch failed: {e}")
            return None

    def calculate_days_elapsed(self, analysis_date: str) -> int:
        """
        Calculate days elapsed since analysis.

        Args:
            analysis_date: Analysis date (YYYY-MM-DD or YYYY-MM-DD HH:MM:SS)

        Returns:
            Number of days elapsed
        """
        try:
            # Extract date only if datetime format
            date_only = analysis_date.split(' ')[0] if ' ' in analysis_date else analysis_date
            analyzed = datetime.strptime(date_only, "%Y-%m-%d")
            today = datetime.now()
            return (today - analyzed).days
        except Exception as e:
            logger.error(f"Date calculation error: {e}")
            return 0

    def calculate_return(self, analysis_price: float, current_price: float) -> float:
        """
        Calculate return rate.

        Args:
            analysis_price: Price at analysis time
            current_price: Current price

        Returns:
            Return rate (e.g., 0.05 = 5%)
        """
        if analysis_price <= 0:
            return 0.0
        return (current_price - analysis_price) / analysis_price

    def update_tracking_record(
        self,
        record: Dict[str, Any],
        days_elapsed: int,
        current_price: float,
        analysis_price: float
    ) -> Dict[str, Any]:
        """
        Determine what updates are needed for a tracking record.

        Args:
            record: Existing record data (to check what's already tracked)
            days_elapsed: Days since analysis
            current_price: Current stock price
            analysis_price: Price at analysis time

        Returns:
            Dictionary of fields to update
        """
        updates = {}
        return_rate = self.calculate_return(analysis_price, current_price)

        # 7-day update (only if not already recorded and >= 7 days)
        if days_elapsed >= 7 and record.get('return_7d') is None:
            updates['price_7d'] = current_price
            updates['return_7d'] = return_rate

        # 14-day update
        if days_elapsed >= 14 and record.get('return_14d') is None:
            updates['price_14d'] = current_price
            updates['return_14d'] = return_rate

        # 30-day update
        if days_elapsed >= 30 and record.get('return_30d') is None:
            updates['price_30d'] = current_price
            updates['return_30d'] = return_rate
            updates['tracking_status'] = 'completed'
        elif days_elapsed >= 7 and record.get('tracking_status') in ('pending', None):
            updates['tracking_status'] = 'in_progress'

        # Check hit_target and hit_stop_loss
        target_price = record.get('target_price')
        stop_loss = record.get('stop_loss')

        if target_price and current_price >= target_price and not record.get('hit_target'):
            updates['hit_target'] = 1

        if stop_loss and current_price <= stop_loss and not record.get('hit_stop_loss'):
            updates['hit_stop_loss'] = 1

        if updates:
            updates['last_updated'] = self.today

        return updates

    def apply_updates(self, record_id: int, updates: Dict[str, Any]) -> bool:
        """
        Apply updates to database.

        Args:
            record_id: Record ID
            updates: Fields and values to update

        Returns:
            Success status
        """
        if not updates:
            return True

        if self.dry_run:
            logger.info(f"[DRY-RUN] ID {record_id}: {updates}")
            return True

        conn = self.connect_db()
        try:
            # Build dynamic UPDATE query
            set_clause = ", ".join([f"{k} = ?" for k in updates.keys()])
            values = list(updates.values()) + [record_id]

            query = f"UPDATE us_analysis_performance_tracker SET {set_clause} WHERE id = ?"
            conn.execute(query, values)
            conn.commit()
            return True
        except Exception as e:
            logger.error(f"DB update failed (ID {record_id}): {e}")
            conn.rollback()
            return False
        finally:
            conn.close()

    def run(self) -> Dict[str, Any]:
        """
        Execute batch processing.

        Returns:
            Execution statistics
        """
        logger.info("=" * 60)
        logger.info(f"US Performance Tracker Batch Start: {self.today}")
        if self.dry_run:
            logger.info("[DRY-RUN MODE] No actual DB updates")
        logger.info("=" * 60)

        # Ensure columns exist (migration)
        self.ensure_columns_exist()

        # Statistics
        stats = {
            'total': 0,
            'updated': 0,
            'skipped': 0,
            'errors': 0,
            'completed': 0,
            'by_trigger_type': {},
            'by_decision': {'traded': 0, 'watched': 0}
        }

        # Get tracking targets
        targets = self.get_tracking_targets()
        stats['total'] = len(targets)
        logger.info(f"Tracking targets: {stats['total']} stocks")

        if not targets:
            logger.info("No stocks to track.")
            return stats

        # Process each stock
        for record in targets:
            ticker = record['ticker']
            company_name = record['company_name']
            trigger_type = record['trigger_type'] or 'unknown'
            analysis_date = record['analysis_date']
            analysis_price = record['analysis_price']
            was_traded = record.get('was_traded', 0)

            # Calculate days elapsed
            days_elapsed = self.calculate_days_elapsed(analysis_date)

            # Check if update is needed
            should_update = False
            if days_elapsed >= 7 and record.get('price_7d') is None:
                should_update = True
            if days_elapsed >= 14 and record.get('price_14d') is None:
                should_update = True
            if days_elapsed >= 30 and record.get('price_30d') is None:
                should_update = True

            if not should_update:
                logger.debug(f"[{ticker}] {company_name}: No update needed (elapsed {days_elapsed} days)")
                stats['skipped'] += 1
                continue

            logger.info(f"[{ticker}] {company_name}: elapsed {days_elapsed} days, trigger={trigger_type}")

            # Get current price
            current_price = self.get_current_price(ticker)
            if current_price is None:
                logger.warning(f"[{ticker}] Price fetch failed, skipping")
                stats['errors'] += 1
                continue

            # Calculate return rate
            return_rate = self.calculate_return(analysis_price, current_price)
            logger.info(f"  Analysis: ${analysis_price:.2f} -> Current: ${current_price:.2f} ({return_rate*100:+.2f}%)")

            # Determine updates
            updates = self.update_tracking_record(
                record,
                days_elapsed,
                current_price,
                analysis_price
            )

            # Apply updates
            if self.apply_updates(record['id'], updates):
                stats['updated'] += 1

                # Trigger type statistics
                if trigger_type not in stats['by_trigger_type']:
                    stats['by_trigger_type'][trigger_type] = {'count': 0, 'returns': []}
                stats['by_trigger_type'][trigger_type]['count'] += 1
                stats['by_trigger_type'][trigger_type]['returns'].append(return_rate)

                # Traded/Watched classification
                if was_traded:
                    stats['by_decision']['traded'] += 1
                else:
                    stats['by_decision']['watched'] += 1

                # Completed count
                if updates.get('tracking_status') == 'completed':
                    stats['completed'] += 1
            else:
                stats['errors'] += 1

        # Summary
        logger.info("=" * 60)
        logger.info("Batch Execution Complete")
        logger.info(f"  Total: {stats['total']}, Updated: {stats['updated']}, "
                   f"Skipped: {stats['skipped']}, Errors: {stats['errors']}")
        logger.info(f"  Completed: {stats['completed']}, Traded: {stats['by_decision']['traded']}, "
                   f"Watched: {stats['by_decision']['watched']}")
        logger.info("=" * 60)

        return stats

    def generate_report(self) -> str:
        """
        Generate tracking status report.

        Returns:
            Report string
        """
        conn = self.connect_db()
        try:
            # Overall statistics
            cursor = conn.execute("""
                SELECT
                    COALESCE(tracking_status, 'pending') as status,
                    COUNT(*) as count
                FROM us_analysis_performance_tracker
                GROUP BY tracking_status
            """)
            status_stats = {row['status']: row['count'] for row in cursor.fetchall()}

            # Trigger type performance
            cursor = conn.execute("""
                SELECT
                    trigger_type,
                    COUNT(*) as count,
                    SUM(CASE WHEN was_traded = 1 THEN 1 ELSE 0 END) as traded_count,
                    AVG(return_7d) as avg_7d_return,
                    AVG(return_14d) as avg_14d_return,
                    AVG(return_30d) as avg_30d_return
                FROM us_analysis_performance_tracker
                WHERE return_30d IS NOT NULL
                GROUP BY trigger_type
            """)
            trigger_stats = cursor.fetchall()

            # Traded vs Watched performance
            cursor = conn.execute("""
                SELECT
                    CASE WHEN was_traded = 1 THEN 'Traded' ELSE 'Watched' END as decision,
                    COUNT(*) as count,
                    AVG(return_7d) as avg_7d_return,
                    AVG(return_14d) as avg_14d_return,
                    AVG(return_30d) as avg_30d_return
                FROM us_analysis_performance_tracker
                WHERE return_30d IS NOT NULL
                GROUP BY was_traded
            """)
            decision_stats = cursor.fetchall()

            # Build report
            report = []
            report.append("=" * 70)
            report.append(f"US Stock Performance Tracker Report ({self.today})")
            report.append("=" * 70)
            report.append("")

            # Tracking status overview
            report.append("## 1. Tracking Status Overview")
            report.append("-" * 40)
            for status, count in status_stats.items():
                status_name = {
                    'pending': 'Pending',
                    'in_progress': 'In Progress',
                    'completed': 'Completed'
                }.get(status, status)
                report.append(f"  {status_name}: {count}")
            report.append("")

            # Trigger type performance
            report.append("## 2. Trigger Type Performance (Completed Only)")
            report.append("-" * 40)
            if trigger_stats:
                report.append(f"{'Trigger Type':<25} {'Count':>6} {'Traded':>6} {'7D':>8} {'14D':>8} {'30D':>8}")
                report.append("-" * 70)
                for row in trigger_stats:
                    trigger_type = row['trigger_type'] or 'unknown'
                    count = row['count']
                    traded = row['traded_count'] or 0
                    avg_7d = row['avg_7d_return']
                    avg_14d = row['avg_14d_return']
                    avg_30d = row['avg_30d_return']

                    # Format returns
                    r7 = f"{avg_7d*100:+.1f}%" if avg_7d else "N/A"
                    r14 = f"{avg_14d*100:+.1f}%" if avg_14d else "N/A"
                    r30 = f"{avg_30d*100:+.1f}%" if avg_30d else "N/A"

                    report.append(f"{trigger_type:<25} {count:>6} {traded:>6} {r7:>8} {r14:>8} {r30:>8}")
            else:
                report.append("  No completed tracking data available.")
            report.append("")

            # Traded vs Watched performance
            report.append("## 3. Traded vs Watched Performance")
            report.append("-" * 40)
            if decision_stats:
                report.append(f"{'Decision':<10} {'Count':>6} {'7D':>10} {'14D':>10} {'30D':>10}")
                report.append("-" * 50)
                for row in decision_stats:
                    decision = row['decision']
                    count = row['count']
                    avg_7d = row['avg_7d_return']
                    avg_14d = row['avg_14d_return']
                    avg_30d = row['avg_30d_return']

                    r7 = f"{avg_7d*100:+.1f}%" if avg_7d else "N/A"
                    r14 = f"{avg_14d*100:+.1f}%" if avg_14d else "N/A"
                    r30 = f"{avg_30d*100:+.1f}%" if avg_30d else "N/A"

                    report.append(f"{decision:<10} {count:>6} {r7:>10} {r14:>10} {r30:>10}")
            else:
                report.append("  No completed tracking data available.")
            report.append("")

            # Recent completed stocks
            cursor = conn.execute("""
                SELECT
                    ticker,
                    company_name,
                    trigger_type,
                    analysis_date,
                    analysis_price,
                    price_30d,
                    return_30d,
                    was_traded,
                    decision
                FROM us_analysis_performance_tracker
                WHERE tracking_status = 'completed'
                ORDER BY last_updated DESC
                LIMIT 10
            """)
            recent = cursor.fetchall()

            report.append("## 4. Recently Completed Tracking (Max 10)")
            report.append("-" * 40)
            if recent:
                for row in recent:
                    ticker = row['ticker']
                    name = row['company_name']
                    trigger = row['trigger_type'] or 'unknown'
                    analysis_price = row['analysis_price']
                    final_price = row['price_30d']
                    return_rate = row['return_30d']
                    was_traded = "Traded" if row['was_traded'] else "Watched"

                    ret_str = f"{return_rate*100:+.1f}%" if return_rate else "N/A"
                    final_str = f"${final_price:.2f}" if final_price else "N/A"
                    analysis_str = f"${analysis_price:.2f}" if analysis_price else "N/A"
                    report.append(f"  [{ticker}] {name}")
                    report.append(f"    Trigger: {trigger}, Decision: {was_traded}")
                    report.append(f"    Analysis: {analysis_str} -> 30D: {final_str} ({ret_str})")
            else:
                report.append("  No completed tracking data available.")
            report.append("")

            report.append("=" * 70)

            return "\n".join(report)

        finally:
            conn.close()


def main():
    parser = argparse.ArgumentParser(
        description="US Stock Performance Tracker Batch",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
    python us_performance_tracker_batch.py              # Full tracking update
    python us_performance_tracker_batch.py --dry-run    # Test mode
    python us_performance_tracker_batch.py --report     # Show status report only
        """
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Test without actual DB updates"
    )
    parser.add_argument(
        "--report",
        action="store_true",
        help="Print current tracking status report"
    )
    parser.add_argument(
        "--db",
        type=str,
        default=None,
        help="SQLite DB path (default: ../stock_tracking_db.sqlite)"
    )

    args = parser.parse_args()

    tracker = USPerformanceTrackerBatch(db_path=args.db, dry_run=args.dry_run)

    if args.report:
        # Report only
        report = tracker.generate_report()
        print(report)
    else:
        # Run batch
        stats = tracker.run()

        # Also print report
        print("\n")
        report = tracker.generate_report()
        print(report)


if __name__ == "__main__":
    main()
