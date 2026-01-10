#!/usr/bin/env python3
"""
Migration Script: Extract lessons from trading_journal and insert into trading_principles

This script migrates existing lessons from trading_journal entries (e.g., 카카오, 삼성전자)
to the new trading_principles table for universal trading wisdom.

Usage:
    python migrate_lessons_to_principles.py [--db-path DB_PATH] [--dry-run]

Options:
    --db-path: Path to SQLite database (default: stock_tracking_db.sqlite)
    --dry-run: Preview changes without committing to database
"""

import sqlite3
import json
import argparse
import logging
from datetime import datetime
from typing import List, Dict, Any, Optional

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def get_existing_journals(conn: sqlite3.Connection) -> List[Dict[str, Any]]:
    """Fetch all trading_journal entries with lessons."""
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    cursor.execute("""
        SELECT id, ticker, company_name, lessons, profit_rate, trade_date
        FROM trading_journal
        WHERE lessons IS NOT NULL AND lessons != '[]'
        ORDER BY trade_date DESC
    """)

    return [dict(row) for row in cursor.fetchall()]


def parse_lessons(lessons_json: str) -> List[Dict[str, Any]]:
    """Parse lessons JSON string into list of lesson dictionaries."""
    try:
        lessons = json.loads(lessons_json)
        if isinstance(lessons, list):
            return lessons
        return []
    except (json.JSONDecodeError, TypeError):
        return []


def determine_scope(lesson: Dict[str, Any]) -> str:
    """
    Determine the scope of a lesson based on its priority.

    - high priority -> universal (applies to all trades)
    - medium priority -> sector (sector-specific)
    - low priority -> ticker (stock-specific, don't migrate)
    """
    priority = lesson.get('priority', 'medium')
    if priority == 'high':
        return 'universal'
    elif priority == 'medium':
        return 'universal'  # For migration, treat medium as universal too
    else:
        return 'sector'  # Low priority stays sector-specific


def check_duplicate(
    cursor: sqlite3.Cursor,
    condition: str,
    action: str
) -> Optional[int]:
    """Check if a similar principle already exists."""
    cursor.execute("""
        SELECT id FROM trading_principles
        WHERE condition = ? AND action = ? AND is_active = 1
    """, (condition, action))
    result = cursor.fetchone()
    return result[0] if result else None


def migrate_lesson_to_principle(
    cursor: sqlite3.Cursor,
    lesson: Dict[str, Any],
    source_journal_id: int,
    dry_run: bool = False
) -> bool:
    """
    Migrate a single lesson to trading_principles table.

    Returns True if principle was created/updated, False otherwise.
    """
    condition = lesson.get('condition', '')
    action = lesson.get('action', '')
    reason = lesson.get('reason', '')
    priority = lesson.get('priority', 'medium')

    if not condition or not action:
        logger.debug(f"Skipping lesson with missing condition/action: {lesson}")
        return False

    scope = determine_scope(lesson)
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # Check for existing principle
    existing_id = check_duplicate(cursor, condition, action)

    if existing_id:
        if dry_run:
            logger.info(f"[DRY-RUN] Would update existing principle (id={existing_id})")
        else:
            # Update existing principle
            cursor.execute("""
                SELECT source_journal_ids FROM trading_principles WHERE id = ?
            """, (existing_id,))
            existing_ids = cursor.fetchone()[0] or ''
            new_ids = f"{existing_ids},{source_journal_id}" if existing_ids else str(source_journal_id)

            cursor.execute("""
                UPDATE trading_principles
                SET supporting_trades = supporting_trades + 1,
                    confidence = MIN(1.0, confidence + 0.1),
                    source_journal_ids = ?,
                    last_validated_at = ?
                WHERE id = ?
            """, (new_ids, now, existing_id))
            logger.info(f"Updated existing principle (id={existing_id})")
        return True
    else:
        if dry_run:
            logger.info(f"[DRY-RUN] Would create new principle: {condition[:50]}...")
        else:
            # Insert new principle
            cursor.execute("""
                INSERT INTO trading_principles
                (scope, scope_context, condition, action, reason, priority,
                 confidence, supporting_trades, source_journal_ids, created_at, is_active)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                scope,
                None,  # scope_context
                condition,
                action,
                reason,
                priority,
                0.6,  # Initial confidence (slightly higher for migrated data)
                1,
                str(source_journal_id),
                now,
                1  # is_active
            ))
            logger.info(f"Created new principle: {condition[:50]}...")
        return True


def ensure_tables_exist(conn: sqlite3.Connection) -> bool:
    """Ensure trading_principles table exists."""
    cursor = conn.cursor()

    # Check if trading_principles table exists
    cursor.execute("""
        SELECT name FROM sqlite_master
        WHERE type='table' AND name='trading_principles'
    """)

    if not cursor.fetchone():
        logger.info("Creating trading_principles table...")
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS trading_principles (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                scope TEXT NOT NULL DEFAULT 'universal',
                scope_context TEXT,
                condition TEXT NOT NULL,
                action TEXT NOT NULL,
                reason TEXT,
                priority TEXT DEFAULT 'medium',
                confidence REAL DEFAULT 0.5,
                supporting_trades INTEGER DEFAULT 1,
                source_journal_ids TEXT,
                created_at TEXT NOT NULL,
                last_validated_at TEXT,
                is_active INTEGER DEFAULT 1
            )
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_principles_scope
            ON trading_principles(scope)
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_principles_priority
            ON trading_principles(priority)
        """)
        conn.commit()
        logger.info("trading_principles table created successfully")

    return True


def run_migration(db_path: str, dry_run: bool = False) -> Dict[str, int]:
    """
    Run the migration process.

    Returns statistics about the migration.
    """
    stats = {
        'journals_processed': 0,
        'lessons_found': 0,
        'principles_created': 0,
        'principles_updated': 0,
        'errors': 0
    }

    conn = sqlite3.connect(db_path)

    try:
        # Ensure tables exist
        ensure_tables_exist(conn)

        # Get all journals with lessons
        journals = get_existing_journals(conn)
        logger.info(f"Found {len(journals)} journal entries with lessons")

        cursor = conn.cursor()

        for journal in journals:
            stats['journals_processed'] += 1
            logger.info(f"\nProcessing journal {journal['id']}: "
                       f"{journal['company_name']} ({journal['ticker']}) "
                       f"- {journal['trade_date'][:10]}")

            lessons = parse_lessons(journal['lessons'])
            stats['lessons_found'] += len(lessons)

            for lesson in lessons:
                try:
                    migrated = migrate_lesson_to_principle(
                        cursor,
                        lesson,
                        journal['id'],
                        dry_run
                    )
                    if migrated:
                        if check_duplicate(cursor, lesson.get('condition', ''), lesson.get('action', '')):
                            stats['principles_updated'] += 1
                        else:
                            stats['principles_created'] += 1
                except Exception as e:
                    logger.error(f"Error migrating lesson: {e}")
                    stats['errors'] += 1

        if not dry_run:
            conn.commit()
            logger.info("\nMigration committed to database")
        else:
            logger.info("\n[DRY-RUN] No changes committed to database")

    finally:
        conn.close()

    return stats


def main():
    parser = argparse.ArgumentParser(
        description='Migrate trading_journal lessons to trading_principles table'
    )
    parser.add_argument(
        '--db-path',
        default='stock_tracking_db.sqlite',
        help='Path to SQLite database (default: stock_tracking_db.sqlite)'
    )
    parser.add_argument(
        '--dry-run',
        action='store_true',
        help='Preview changes without committing to database'
    )

    args = parser.parse_args()

    logger.info(f"Starting migration...")
    logger.info(f"Database: {args.db_path}")
    logger.info(f"Dry run: {args.dry_run}")

    stats = run_migration(args.db_path, args.dry_run)

    print("\n" + "=" * 50)
    print("Migration Statistics")
    print("=" * 50)
    print(f"Journals processed: {stats['journals_processed']}")
    print(f"Lessons found: {stats['lessons_found']}")
    print(f"Principles created: {stats['principles_created']}")
    print(f"Principles updated: {stats['principles_updated']}")
    print(f"Errors: {stats['errors']}")
    print("=" * 50)

    if args.dry_run:
        print("\n[DRY-RUN] Run without --dry-run to apply changes")


if __name__ == "__main__":
    main()
