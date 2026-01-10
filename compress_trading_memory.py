#!/usr/bin/env python3
"""
Trading Memory Compression & Cleanup Script

This script compresses old trading journal entries into summarized insights,
extracts trading intuitions, and cleans up stale data to prevent unbounded growth.

Compression Strategy:
- Layer 1 (0-7 days): Full detail retention
- Layer 2 (8-30 days): Summarized records
- Layer 3 (31+ days): Compressed intuitions

Cleanup Strategy:
- Deactivate low-confidence principles/intuitions (< 0.3)
- Deactivate stale items (not validated in 90 days)
- Enforce max count limits (50 principles, 50 intuitions)
- Archive (delete) Layer 3 entries older than 365 days

Usage:
    # Run compression and cleanup with default settings
    python compress_trading_memory.py

    # Run with custom age thresholds
    python compress_trading_memory.py --layer1-age 7 --layer2-age 30

    # Dry run (show what would be compressed/cleaned)
    python compress_trading_memory.py --dry-run

    # Force compression regardless of minimum entry count
    python compress_trading_memory.py --force

    # Skip cleanup phase (only run compression)
    python compress_trading_memory.py --skip-cleanup

    # Custom cleanup settings
    python compress_trading_memory.py --max-principles 30 --max-intuitions 30 --stale-days 60

Recommended Cron Schedule:
    # Run every Sunday at 3:00 AM
    0 3 * * 0 cd /path/to/prism-insight && python compress_trading_memory.py >> logs/compression.log 2>&1
"""

import argparse
import asyncio
import json
import logging
import sys
from datetime import datetime, timedelta
from pathlib import Path

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(
            f"compression_{datetime.now().strftime('%Y%m%d')}.log",
            encoding='utf-8'
        )
    ]
)
logger = logging.getLogger(__name__)


async def run_compression(
    db_path: str = "stock_tracking_db.sqlite",
    layer1_age_days: int = 7,
    layer2_age_days: int = 30,
    min_entries: int = 3,
    dry_run: bool = False,
    force: bool = False,
    language: str = "ko",
    skip_cleanup: bool = False,
    max_principles: int = 50,
    max_intuitions: int = 50,
    stale_days: int = 90,
    archive_layer3_days: int = 365
) -> dict:
    """
    Run the compression and cleanup process.

    Args:
        db_path: Path to SQLite database
        layer1_age_days: Days after which Layer 1 entries are compressed
        layer2_age_days: Days after which Layer 2 entries are compressed
        min_entries: Minimum entries required to trigger compression
        dry_run: If True, only show what would be compressed
        force: If True, compress even with fewer than min_entries
        language: Language for agent prompts ("ko" or "en")
        skip_cleanup: If True, skip the cleanup phase
        max_principles: Maximum active principles to keep (default: 50)
        max_intuitions: Maximum active intuitions to keep (default: 50)
        stale_days: Days without validation before deactivation (default: 90)
        archive_layer3_days: Days after which to archive Layer 3 entries (default: 365)

    Returns:
        dict: Compression and cleanup results
    """
    from stock_tracking_agent import StockTrackingAgent
    from unittest.mock import MagicMock

    logger.info("=" * 60)
    logger.info("Trading Memory Compression Started")
    logger.info(f"Database: {db_path}")
    logger.info(f"Layer 1 → 2 age: {layer1_age_days} days")
    logger.info(f"Layer 2 → 3 age: {layer2_age_days} days")
    logger.info(f"Minimum entries: {min_entries}")
    logger.info(f"Dry run: {dry_run}")
    logger.info("=" * 60)

    try:
        # Initialize agent with journal enabled (required for compression)
        agent = StockTrackingAgent(db_path=db_path, enable_journal=True)
        agent.trading_agent = MagicMock()  # Mock to avoid MCP initialization
        await agent.initialize(language=language)

        # Get current stats
        stats_before = agent.get_compression_stats()
        logger.info("\n📊 Current Status:")
        logger.info(f"  Layer 1 (Detailed): {stats_before.get('entries_by_layer', {}).get('layer1_detailed', 0)}")
        logger.info(f"  Layer 2 (Summarized): {stats_before.get('entries_by_layer', {}).get('layer2_summarized', 0)}")
        logger.info(f"  Layer 3 (Compressed): {stats_before.get('entries_by_layer', {}).get('layer3_compressed', 0)}")
        logger.info(f"  Active Intuitions: {stats_before.get('active_intuitions', 0)}")

        if stats_before.get('oldest_uncompressed'):
            logger.info(f"  Oldest Uncompressed: {stats_before['oldest_uncompressed']}")

        # Check entries that would be compressed
        cutoff_layer1 = (datetime.now() - timedelta(days=layer1_age_days)).strftime("%Y-%m-%d")
        cutoff_layer2 = (datetime.now() - timedelta(days=layer2_age_days)).strftime("%Y-%m-%d")

        agent.cursor.execute("""
            SELECT COUNT(*) FROM trading_journal
            WHERE compression_layer = 1 AND trade_date < ?
        """, (cutoff_layer1,))
        layer1_count = agent.cursor.fetchone()[0]

        agent.cursor.execute("""
            SELECT COUNT(*) FROM trading_journal
            WHERE compression_layer = 2 AND trade_date < ?
        """, (cutoff_layer2,))
        layer2_count = agent.cursor.fetchone()[0]

        logger.info(f"\n📦 Entries to Compress:")
        logger.info(f"  Layer 1 → 2: {layer1_count} entries (older than {layer1_age_days} days)")
        logger.info(f"  Layer 2 → 3: {layer2_count} entries (older than {layer2_age_days} days)")

        if dry_run:
            logger.info("\n🔍 DRY RUN - No changes will be made")

            # Show sample entries that would be compressed
            agent.cursor.execute("""
                SELECT id, ticker, company_name, trade_date, profit_rate, one_line_summary
                FROM trading_journal
                WHERE compression_layer = 1 AND trade_date < ?
                ORDER BY trade_date ASC
                LIMIT 10
            """, (cutoff_layer1,))
            sample_entries = agent.cursor.fetchall()

            if sample_entries:
                logger.info("\n  Sample Layer 1 entries to compress:")
                for entry in sample_entries:
                    logger.info(f"    [{entry['trade_date'][:10]}] {entry['company_name']} ({entry['ticker']})")
                    logger.info(f"      Profit: {entry['profit_rate']:.2f}% | {entry['one_line_summary'][:50]}...")

            agent.conn.close()
            return {
                "status": "dry_run",
                "would_compress": {
                    "layer1_to_layer2": layer1_count,
                    "layer2_to_layer3": layer2_count
                }
            }

        # Check minimum entries requirement
        effective_min = 1 if force else min_entries
        if layer1_count < effective_min and layer2_count < effective_min:
            logger.info(f"\n⏭️  Skipping compression: Not enough entries (min: {min_entries})")
            agent.conn.close()
            return {
                "status": "skipped",
                "reason": "Not enough entries",
                "layer1_count": layer1_count,
                "layer2_count": layer2_count
            }

        # Run compression
        logger.info("\n🔄 Running compression...")
        results = await agent.compress_old_journal_entries(
            layer1_age_days=layer1_age_days,
            layer2_age_days=layer2_age_days,
            min_entries_for_compression=effective_min
        )

        # Get stats after compression
        stats_after = agent.get_compression_stats()

        logger.info("\n✅ Compression Complete:")
        logger.info(f"  Layer 1 → 2: {results.get('layer1_to_layer2', {}).get('compressed', 0)} entries compressed")
        logger.info(f"  Layer 2 → 3: {results.get('layer2_to_layer3', {}).get('compressed', 0)} entries compressed")
        logger.info(f"  Intuitions Generated: {results.get('intuitions_generated', 0)}")

        logger.info("\n📊 Updated Status:")
        logger.info(f"  Layer 1 (Detailed): {stats_after.get('entries_by_layer', {}).get('layer1_detailed', 0)}")
        logger.info(f"  Layer 2 (Summarized): {stats_after.get('entries_by_layer', {}).get('layer2_summarized', 0)}")
        logger.info(f"  Layer 3 (Compressed): {stats_after.get('entries_by_layer', {}).get('layer3_compressed', 0)}")
        logger.info(f"  Active Intuitions: {stats_after.get('active_intuitions', 0)}")

        if stats_after.get('avg_intuition_confidence'):
            logger.info(f"  Avg Intuition Confidence: {stats_after['avg_intuition_confidence']:.2f}")
            logger.info(f"  Avg Intuition Success Rate: {stats_after['avg_intuition_success_rate']:.2f}")

        # Show newly generated intuitions
        agent.cursor.execute("""
            SELECT category, condition, insight, confidence, success_rate
            FROM trading_intuitions
            WHERE is_active = 1
            ORDER BY created_at DESC
            LIMIT 5
        """)
        recent_intuitions = agent.cursor.fetchall()

        if recent_intuitions:
            logger.info("\n💡 Recent Intuitions:")
            for intuition in recent_intuitions:
                conf_bar = "●" * int(intuition['confidence'] * 5) + "○" * (5 - int(intuition['confidence'] * 5))
                logger.info(f"  [{intuition['category']}] {intuition['condition']}")
                logger.info(f"    → {intuition['insight']} ({conf_bar})")

        # Phase 2: Cleanup stale data
        cleanup_results = {}
        if not skip_cleanup:
            logger.info("\n🧹 Running Cleanup...")
            cleanup_results = agent.cleanup_stale_data(
                max_principles=max_principles,
                max_intuitions=max_intuitions,
                stale_days=stale_days,
                archive_layer3_days=archive_layer3_days,
                dry_run=dry_run
            )

            if dry_run:
                logger.info("\n🔍 CLEANUP DRY RUN - No changes will be made")
                logger.info(f"  Low-confidence principles: {cleanup_results.get('low_confidence_principles', 0)}")
                logger.info(f"  Stale principles: {cleanup_results.get('stale_principles', 0)}")
                logger.info(f"  Low-confidence intuitions: {cleanup_results.get('low_confidence_intuitions', 0)}")
                logger.info(f"  Stale intuitions: {cleanup_results.get('stale_intuitions', 0)}")
                logger.info(f"  Old Layer 3 entries: {cleanup_results.get('old_layer3_entries', 0)}")
            else:
                logger.info("\n✅ Cleanup Complete:")
                logger.info(f"  Principles deactivated: {cleanup_results.get('principles_deactivated', 0)}")
                logger.info(f"  Intuitions deactivated: {cleanup_results.get('intuitions_deactivated', 0)}")
                logger.info(f"  Journal entries archived: {cleanup_results.get('journal_entries_archived', 0)}")

            # Show final counts after cleanup
            agent.cursor.execute("SELECT COUNT(*) FROM trading_principles WHERE is_active = 1")
            active_principles = agent.cursor.fetchone()[0]
            agent.cursor.execute("SELECT COUNT(*) FROM trading_intuitions WHERE is_active = 1")
            active_intuitions = agent.cursor.fetchone()[0]

            logger.info(f"\n📊 Final Active Counts:")
            logger.info(f"  Active Principles: {active_principles}")
            logger.info(f"  Active Intuitions: {active_intuitions}")
        else:
            logger.info("\n⏭️  Cleanup skipped (--skip-cleanup)")

        agent.conn.close()

        return {
            "status": "success",
            "results": results,
            "cleanup_results": cleanup_results,
            "stats_before": stats_before,
            "stats_after": stats_after
        }

    except Exception as e:
        logger.error(f"Compression failed: {e}")
        import traceback
        traceback.print_exc()
        return {"status": "error", "error": str(e)}


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Compress old trading journal entries",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
    python compress_trading_memory.py
    python compress_trading_memory.py --dry-run
    python compress_trading_memory.py --layer1-age 7 --layer2-age 30
    python compress_trading_memory.py --force --min-entries 1
        """
    )

    parser.add_argument(
        "--db-path",
        type=str,
        default="stock_tracking_db.sqlite",
        help="Path to SQLite database (default: stock_tracking_db.sqlite)"
    )
    parser.add_argument(
        "--layer1-age",
        type=int,
        default=7,
        help="Days after which Layer 1 entries are compressed to Layer 2 (default: 7)"
    )
    parser.add_argument(
        "--layer2-age",
        type=int,
        default=30,
        help="Days after which Layer 2 entries are compressed to Layer 3 (default: 30)"
    )
    parser.add_argument(
        "--min-entries",
        type=int,
        default=3,
        help="Minimum entries required to trigger compression (default: 3)"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be compressed without making changes"
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Force compression regardless of minimum entry count"
    )
    parser.add_argument(
        "--language",
        type=str,
        default="ko",
        choices=["ko", "en"],
        help="Language for agent prompts (default: ko)"
    )
    parser.add_argument(
        "--skip-cleanup",
        action="store_true",
        help="Skip the cleanup phase (only run compression)"
    )
    parser.add_argument(
        "--max-principles",
        type=int,
        default=50,
        help="Maximum active principles to keep (default: 50)"
    )
    parser.add_argument(
        "--max-intuitions",
        type=int,
        default=50,
        help="Maximum active intuitions to keep (default: 50)"
    )
    parser.add_argument(
        "--stale-days",
        type=int,
        default=90,
        help="Days without validation before deactivation (default: 90)"
    )
    parser.add_argument(
        "--archive-days",
        type=int,
        default=365,
        help="Days after which to archive Layer 3 entries (default: 365)"
    )

    args = parser.parse_args()

    # Run compression and cleanup
    result = asyncio.run(run_compression(
        db_path=args.db_path,
        layer1_age_days=args.layer1_age,
        layer2_age_days=args.layer2_age,
        min_entries=args.min_entries,
        dry_run=args.dry_run,
        force=args.force,
        language=args.language,
        skip_cleanup=args.skip_cleanup,
        max_principles=args.max_principles,
        max_intuitions=args.max_intuitions,
        stale_days=args.stale_days,
        archive_layer3_days=args.archive_days
    ))

    # Print final summary
    logger.info("\n" + "=" * 60)
    logger.info(f"Final Status: {result.get('status', 'unknown')}")
    logger.info("=" * 60)

    if result.get('status') == 'error':
        sys.exit(1)

    return result


if __name__ == "__main__":
    main()
