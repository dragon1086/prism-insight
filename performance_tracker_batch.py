#!/usr/bin/env python3
"""
Post-Analysis Performance Tracking Batch Script

Tracks prices at 7/14/30 days after analysis for all stocks (both traded and watched)
to collect statistics on which trigger types actually perform well.

Usage:
    python performance_tracker_batch.py              # Update all tracking targets
    python performance_tracker_batch.py --dry-run    # Test without actual DB updates
    python performance_tracker_batch.py --report     # Current tracking status report
"""
from dotenv import load_dotenv
load_dotenv()

import sqlite3
import argparse
import logging
from datetime import datetime, timedelta
from typing import Dict, List, Any, Optional
from pathlib import Path

# Logging setup
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(f"performance_tracker_{datetime.now().strftime('%Y%m%d')}.log")
    ]
)
logger = logging.getLogger(__name__)

# Project root path
PROJECT_ROOT = Path(__file__).parent
DB_PATH = PROJECT_ROOT / "stock_tracking_db.sqlite"

# krx_data_client import
try:
    from krx_data_client import (
        get_market_ohlcv_by_date,
        get_nearest_business_day_in_a_week,
    )
    KRX_AVAILABLE = True
except ImportError:
    KRX_AVAILABLE = False
    logger.warning("krx_data_client package is not installed.")


class PerformanceTrackerBatch:
    """Batch processor for tracking analyzed stock performance"""

    # Tracking day thresholds
    TRACK_DAYS = [7, 14, 30]

    def __init__(self, db_path: str = None, dry_run: bool = False):
        """
        Args:
            db_path: SQLite DB path
            dry_run: If True, test only without actual DB updates
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

    def get_tracking_targets(self) -> List[Dict[str, Any]]:
        """Query stocks that need tracking

        Returns:
            List of stocks that need tracking
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
                    analyzed_date,
                    analyzed_price,
                    decision,
                    was_traded,
                    skip_reason,
                    buy_score,
                    min_score,
                    target_price,
                    stop_loss,
                    risk_reward_ratio,
                    tracked_7d_date,
                    tracked_7d_price,
                    tracked_7d_return,
                    tracked_14d_date,
                    tracked_14d_price,
                    tracked_14d_return,
                    tracked_30d_date,
                    tracked_30d_price,
                    tracked_30d_return,
                    tracking_status
                FROM analysis_performance_tracker
                WHERE tracking_status IN ('pending', 'in_progress')
                ORDER BY analyzed_date ASC
            """)

            rows = cursor.fetchall()
            return [dict(row) for row in rows]
        finally:
            conn.close()

    def get_current_price(self, ticker: str) -> Optional[float]:
        """Query current stock price

        Args:
            ticker: Stock code (6 digits)

        Returns:
            Current close price or None
        """
        if not KRX_AVAILABLE:
            logger.error("krx_data_client is not available.")
            return None

        try:
            # Get nearest business day within last 7 days
            today = datetime.now().strftime("%Y%m%d")
            week_ago = (datetime.now() - timedelta(days=7)).strftime("%Y%m%d")

            df = get_market_ohlcv_by_date(week_ago, today, ticker)

            if df is None or df.empty:
                logger.warning(f"[{ticker}] No price data available")
                return None

            # Return most recent close price (krx_data_client uses English column names)
            close_col = 'Close' if 'Close' in df.columns else '종가'
            latest_close = df[close_col].iloc[-1]
            return float(latest_close)

        except Exception as e:
            logger.error(f"[{ticker}] Price query failed: {e}")
            return None

    def calculate_days_elapsed(self, analyzed_date: str) -> int:
        """Calculate days elapsed since analysis date

        Args:
            analyzed_date: Analysis date (YYYY-MM-DD or YYYY-MM-DD HH:MM:SS)

        Returns:
            Days elapsed
        """
        try:
            # Extract date only if timestamp is included
            date_only = analyzed_date.split(' ')[0] if ' ' in analyzed_date else analyzed_date
            analyzed = datetime.strptime(date_only, "%Y-%m-%d")
            today = datetime.now()
            return (today - analyzed).days
        except Exception as e:
            logger.error(f"Date calculation error: {e}")
            return 0

    def calculate_return(self, analyzed_price: float, current_price: float) -> float:
        """Calculate return rate

        Args:
            analyzed_price: Price at analysis time
            current_price: Current price

        Returns:
            Return rate (e.g., 0.05 = 5%)
        """
        if analyzed_price <= 0:
            return 0.0
        return (current_price - analyzed_price) / analyzed_price

    def update_tracking_record(
        self,
        record: Dict[str, Any],
        days_elapsed: int,
        current_price: float,
        analyzed_price: float
    ) -> Dict[str, Any]:
        """Update tracking record

        Args:
            record: Existing record info (to check already recorded values)
            days_elapsed: Days elapsed
            current_price: Current price
            analyzed_price: Price at analysis time

        Returns:
            Fields and values to update
        """
        updates = {}
        return_rate = self.calculate_return(analyzed_price, current_price)

        # 7-day update (if not yet recorded and 7+ days elapsed)
        if days_elapsed >= 7 and record.get('tracked_7d_return') is None:
            updates['tracked_7d_date'] = self.today
            updates['tracked_7d_price'] = current_price
            updates['tracked_7d_return'] = return_rate

        # 14-day update (if not yet recorded and 14+ days elapsed)
        if days_elapsed >= 14 and record.get('tracked_14d_return') is None:
            updates['tracked_14d_date'] = self.today
            updates['tracked_14d_price'] = current_price
            updates['tracked_14d_return'] = return_rate

        # 30-day update (if not yet recorded and 30+ days elapsed)
        if days_elapsed >= 30 and record.get('tracked_30d_return') is None:
            updates['tracked_30d_date'] = self.today
            updates['tracked_30d_price'] = current_price
            updates['tracked_30d_return'] = return_rate
            updates['tracking_status'] = 'completed'
        elif days_elapsed >= 7 and record.get('tracking_status') == 'pending':
            updates['tracking_status'] = 'in_progress'

        if updates:
            updates['updated_at'] = self.today

        return updates

    def apply_updates(self, record_id: int, updates: Dict[str, Any]) -> bool:
        """Apply updates to database

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
            # Dynamically generate UPDATE query
            set_clause = ", ".join([f"{k} = ?" for k in updates.keys()])
            values = list(updates.values()) + [record_id]

            query = f"UPDATE analysis_performance_tracker SET {set_clause} WHERE id = ?"
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
        """Execute batch

        Returns:
            Execution result statistics
        """
        logger.info("="*60)
        logger.info(f"Performance tracking batch started: {self.today}")
        if self.dry_run:
            logger.info("[DRY-RUN mode] No actual DB updates")
        logger.info("="*60)

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

        # Query tracking targets
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
            analyzed_date = record['analyzed_date']
            analyzed_price = record['analyzed_price']
            was_traded = record['was_traded']

            # Calculate days elapsed
            days_elapsed = self.calculate_days_elapsed(analyzed_date)

            # Skip if tracking already completed for this period
            should_update = False
            if days_elapsed >= 7 and record['tracked_7d_price'] is None:
                should_update = True
            if days_elapsed >= 14 and record['tracked_14d_price'] is None:
                should_update = True
            if days_elapsed >= 30 and record['tracked_30d_price'] is None:
                should_update = True

            if not should_update:
                logger.debug(f"[{ticker}] {company_name}: No update needed ({days_elapsed} days elapsed)")
                stats['skipped'] += 1
                continue

            logger.info(f"[{ticker}] {company_name}: {days_elapsed} days elapsed, trigger={trigger_type}")

            # Query current price
            current_price = self.get_current_price(ticker)
            if current_price is None:
                logger.warning(f"[{ticker}] Price query failed, skipping")
                stats['errors'] += 1
                continue

            # Calculate return
            return_rate = self.calculate_return(analyzed_price, current_price)
            logger.info(f"  Analyzed: {analyzed_price:,.0f} → Current: {current_price:,.0f} ({return_rate*100:+.2f}%)")

            # Determine updates
            updates = self.update_tracking_record(
                record,
                days_elapsed,
                current_price,
                analyzed_price
            )

            # Apply DB updates
            if self.apply_updates(record['id'], updates):
                stats['updated'] += 1

                # Statistics by trigger type
                if trigger_type not in stats['by_trigger_type']:
                    stats['by_trigger_type'][trigger_type] = {'count': 0, 'returns': []}
                stats['by_trigger_type'][trigger_type]['count'] += 1
                stats['by_trigger_type'][trigger_type]['returns'].append(return_rate)

                # Classify traded/watched
                if was_traded:
                    stats['by_decision']['traded'] += 1
                else:
                    stats['by_decision']['watched'] += 1

                # Count completed
                if updates.get('tracking_status') == 'completed':
                    stats['completed'] += 1
            else:
                stats['errors'] += 1

        # Summary
        logger.info("="*60)
        logger.info("Batch execution completed")
        logger.info(f"  Total: {stats['total']}, Updated: {stats['updated']}, "
                   f"Skipped: {stats['skipped']}, Errors: {stats['errors']}")
        logger.info(f"  Completed: {stats['completed']}, Traded: {stats['by_decision']['traded']}, "
                   f"Watched: {stats['by_decision']['watched']}")
        logger.info("="*60)

        return stats

    def generate_report(self) -> str:
        """Generate tracking status report

        Returns:
            Report string
        """
        conn = self.connect_db()
        try:
            # Overall statistics
            cursor = conn.execute("""
                SELECT
                    tracking_status,
                    COUNT(*) as count
                FROM analysis_performance_tracker
                GROUP BY tracking_status
            """)
            status_stats = {row['tracking_status']: row['count'] for row in cursor.fetchall()}

            # Statistics by trigger type
            cursor = conn.execute("""
                SELECT
                    trigger_type,
                    COUNT(*) as count,
                    SUM(CASE WHEN was_traded = 1 THEN 1 ELSE 0 END) as traded_count,
                    AVG(tracked_7d_return) as avg_7d_return,
                    AVG(tracked_14d_return) as avg_14d_return,
                    AVG(tracked_30d_return) as avg_30d_return
                FROM analysis_performance_tracker
                WHERE tracking_status = 'completed'
                GROUP BY trigger_type
            """)
            trigger_stats = cursor.fetchall()

            # Traded vs watched performance comparison
            cursor = conn.execute("""
                SELECT
                    CASE WHEN was_traded = 1 THEN 'Traded' ELSE 'Watched' END as decision,
                    COUNT(*) as count,
                    AVG(tracked_7d_return) as avg_7d_return,
                    AVG(tracked_14d_return) as avg_14d_return,
                    AVG(tracked_30d_return) as avg_30d_return
                FROM analysis_performance_tracker
                WHERE tracking_status = 'completed'
                GROUP BY was_traded
            """)
            decision_stats = cursor.fetchall()

            # Generate report
            report = []
            report.append("="*70)
            report.append(f"📊 Analyzed Stock Performance Tracking Report ({self.today})")
            report.append("="*70)
            report.append("")

            # Status overview
            report.append("## 1. Status Overview")
            report.append("-"*40)
            for status, count in status_stats.items():
                status_name = {
                    'pending': 'Pending',
                    'in_progress': 'In Progress',
                    'completed': 'Completed'
                }.get(status, status)
                report.append(f"  {status_name}: {count} records")
            report.append("")

            # Performance by trigger type
            report.append("## 2. Performance by Trigger Type (Completed only)")
            report.append("-"*40)
            if trigger_stats:
                report.append(f"{'Trigger Type':<25} {'Count':>6} {'Traded':>6} {'7d':>8} {'14d':>8} {'30d':>8}")
                report.append("-"*70)
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
                report.append("  No completed tracking data.")
            report.append("")

            # Traded vs watched performance
            report.append("## 3. Traded vs Watched Performance")
            report.append("-"*40)
            if decision_stats:
                report.append(f"{'Type':<10} {'Count':>6} {'7d':>10} {'14d':>10} {'30d':>10}")
                report.append("-"*50)
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
                report.append("  No completed tracking data.")
            report.append("")

            # Recently completed tracking
            cursor = conn.execute("""
                SELECT
                    ticker,
                    company_name,
                    trigger_type,
                    analyzed_date,
                    analyzed_price,
                    tracked_30d_price,
                    tracked_30d_return,
                    was_traded,
                    decision
                FROM analysis_performance_tracker
                WHERE tracking_status = 'completed'
                ORDER BY updated_at DESC
                LIMIT 10
            """)
            recent = cursor.fetchall()

            report.append("## 4. Recently Completed Tracking (max 10)")
            report.append("-"*40)
            if recent:
                for row in recent:
                    ticker = row['ticker']
                    name = row['company_name']
                    trigger = row['trigger_type'] or 'unknown'
                    analyzed_price = row['analyzed_price']
                    final_price = row['tracked_30d_price']
                    return_rate = row['tracked_30d_return']
                    was_traded = "Traded" if row['was_traded'] else "Watched"

                    ret_str = f"{return_rate*100:+.1f}%" if return_rate else "N/A"
                    final_str = f"{final_price:,.0f}" if final_price else "N/A"
                    analyzed_str = f"{analyzed_price:,.0f}" if analyzed_price else "N/A"
                    report.append(f"  [{ticker}] {name}")
                    report.append(f"    Trigger: {trigger}, Decision: {was_traded}")
                    report.append(f"    Analyzed: {analyzed_str} → 30d later: {final_str} ({ret_str})")
            else:
                report.append("  No completed tracking data.")
            report.append("")

            report.append("="*70)

            return "\n".join(report)

        finally:
            conn.close()


def main():
    parser = argparse.ArgumentParser(
        description="Analyzed Stock Performance Tracking Batch",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
    python performance_tracker_batch.py              # Update all tracking
    python performance_tracker_batch.py --dry-run    # Test mode
    python performance_tracker_batch.py --report     # Print status report only
        """
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Test only without actual DB updates"
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
        help="SQLite DB path (default: ./stock_tracking_db.sqlite)"
    )

    args = parser.parse_args()

    tracker = PerformanceTrackerBatch(db_path=args.db, dry_run=args.dry_run)

    if args.report:
        # Print report only
        report = tracker.generate_report()
        print(report)
    else:
        # Execute batch
        stats = tracker.run()

        # Also print result report
        print("\n")
        report = tracker.generate_report()
        print(report)


if __name__ == "__main__":
    main()
