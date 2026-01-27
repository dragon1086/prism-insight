"""
US Trading Memory Compression Manager

Handles compression of old journal entries and cleanup of stale data for US stocks.
Based on compress_trading_memory.py but adapted for US market with market='US' filter.
"""

import json
import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)


class USCompressionManager:
    """Manages memory compression for US trading data."""

    MARKET = "US"  # Market identifier for shared tables

    def __init__(self, cursor, conn):
        """
        Initialize USCompressionManager.

        Args:
            cursor: SQLite cursor
            conn: SQLite connection
        """
        self.cursor = cursor
        self.conn = conn

    def get_compression_stats(self) -> Dict:
        """
        Get current compression statistics for US market.

        Returns:
            Dict with compression layer counts and stats
        """
        try:
            stats = {
                "entries_by_layer": {},
                "active_intuitions": 0,
                "active_principles": 0,
                "oldest_uncompressed": None,
                "avg_intuition_confidence": None,
                "avg_intuition_success_rate": None
            }

            # Count entries by layer for US market
            self.cursor.execute("""
                SELECT compression_layer, COUNT(*)
                FROM trading_journal
                WHERE market = ?
                GROUP BY compression_layer
            """, (self.MARKET,))

            layer_labels = {1: "layer1_detailed", 2: "layer2_summarized", 3: "layer3_compressed"}
            for row in self.cursor.fetchall():
                layer = row[0] or 1
                stats["entries_by_layer"][layer_labels.get(layer, f"layer{layer}")] = row[1]

            # Active intuitions for US
            self.cursor.execute("""
                SELECT COUNT(*) FROM trading_intuitions
                WHERE is_active = 1 AND market = ?
            """, (self.MARKET,))
            stats["active_intuitions"] = self.cursor.fetchone()[0]

            # Active principles for US
            self.cursor.execute("""
                SELECT COUNT(*) FROM trading_principles
                WHERE is_active = 1 AND market = ?
            """, (self.MARKET,))
            stats["active_principles"] = self.cursor.fetchone()[0]

            # Oldest uncompressed entry for US
            self.cursor.execute("""
                SELECT MIN(trade_date) FROM trading_journal
                WHERE compression_layer = 1 AND market = ?
            """, (self.MARKET,))
            oldest = self.cursor.fetchone()[0]
            if oldest:
                stats["oldest_uncompressed"] = oldest

            # Average intuition metrics for US
            self.cursor.execute("""
                SELECT AVG(confidence), AVG(success_rate)
                FROM trading_intuitions
                WHERE is_active = 1 AND market = ?
            """, (self.MARKET,))
            avg_stats = self.cursor.fetchone()
            if avg_stats[0]:
                stats["avg_intuition_confidence"] = avg_stats[0]
            if avg_stats[1]:
                stats["avg_intuition_success_rate"] = avg_stats[1]

            return stats

        except Exception as e:
            logger.error(f"Error getting US compression stats: {e}")
            return {"error": str(e)}

    async def compress_old_journal_entries(
        self,
        layer1_age_days: int = 7,
        layer2_age_days: int = 30,
        min_entries_for_compression: int = 3
    ) -> Dict:
        """
        Compress old journal entries for US market.

        Layer 1 (0-7 days): Full detail
        Layer 2 (8-30 days): Summarized
        Layer 3 (31+ days): Compressed intuitions

        Args:
            layer1_age_days: Days before Layer 1 entries are compressed
            layer2_age_days: Days before Layer 2 entries are compressed
            min_entries_for_compression: Minimum entries to trigger compression

        Returns:
            Dict with compression results
        """
        results = {
            "layer1_to_layer2": {"compressed": 0, "skipped": 0},
            "layer2_to_layer3": {"compressed": 0, "skipped": 0},
            "intuitions_generated": 0
        }

        try:
            cutoff_layer1 = (datetime.now() - timedelta(days=layer1_age_days)).strftime("%Y-%m-%d")
            cutoff_layer2 = (datetime.now() - timedelta(days=layer2_age_days)).strftime("%Y-%m-%d")

            # Compress Layer 1 → Layer 2 for US
            self.cursor.execute("""
                SELECT id, ticker, company_name, profit_rate, holding_days,
                       one_line_summary, lessons, pattern_tags
                FROM trading_journal
                WHERE compression_layer = 1 AND trade_date < ? AND market = ?
            """, (cutoff_layer1, self.MARKET))

            layer1_entries = self.cursor.fetchall()

            if len(layer1_entries) >= min_entries_for_compression:
                for entry in layer1_entries:
                    # Update to Layer 2 (keep essential data, mark as summarized)
                    self.cursor.execute("""
                        UPDATE trading_journal
                        SET compression_layer = 2
                        WHERE id = ?
                    """, (entry[0],))
                    results["layer1_to_layer2"]["compressed"] += 1

                self.conn.commit()
                logger.info(f"US: Compressed {results['layer1_to_layer2']['compressed']} entries from Layer 1 to Layer 2")

            # Compress Layer 2 → Layer 3 for US
            self.cursor.execute("""
                SELECT id, ticker, company_name, profit_rate, holding_days,
                       one_line_summary, lessons, pattern_tags, buy_scenario
                FROM trading_journal
                WHERE compression_layer = 2 AND trade_date < ? AND market = ?
            """, (cutoff_layer2, self.MARKET))

            layer2_entries = self.cursor.fetchall()

            if len(layer2_entries) >= min_entries_for_compression:
                # Extract patterns and create intuitions
                pattern_groups = {}
                for entry in layer2_entries:
                    try:
                        pattern_tags = json.loads(entry[7]) if entry[7] else []
                        for tag in pattern_tags:
                            if tag not in pattern_groups:
                                pattern_groups[tag] = []
                            pattern_groups[tag].append({
                                "ticker": entry[1],
                                "profit_rate": entry[3],
                                "lessons": json.loads(entry[6]) if entry[6] else []
                            })
                    except:
                        pass

                # Generate intuitions from patterns
                for pattern, trades in pattern_groups.items():
                    if len(trades) >= 2:
                        avg_profit = sum(t["profit_rate"] for t in trades) / len(trades)
                        success_rate = sum(1 for t in trades if t["profit_rate"] > 0) / len(trades)

                        self._save_intuition(
                            category=pattern,
                            condition=f"Pattern: {pattern}",
                            insight=f"Avg return {avg_profit:.1f}% over {len(trades)} trades",
                            confidence=min(0.9, 0.4 + success_rate * 0.5),
                            success_rate=success_rate,
                            supporting_count=len(trades)
                        )
                        results["intuitions_generated"] += 1

                # Update entries to Layer 3
                for entry in layer2_entries:
                    self.cursor.execute("""
                        UPDATE trading_journal
                        SET compression_layer = 3
                        WHERE id = ?
                    """, (entry[0],))
                    results["layer2_to_layer3"]["compressed"] += 1

                self.conn.commit()
                logger.info(f"US: Compressed {results['layer2_to_layer3']['compressed']} entries from Layer 2 to Layer 3")
                logger.info(f"US: Generated {results['intuitions_generated']} intuitions")

            return results

        except Exception as e:
            logger.error(f"Error during US compression: {e}")
            return results

    def _save_intuition(
        self,
        category: str,
        condition: str,
        insight: str,
        confidence: float,
        success_rate: float,
        supporting_count: int
    ) -> bool:
        """Save an intuition to database with market='US'."""
        try:
            now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

            # Check for existing similar intuition
            self.cursor.execute("""
                SELECT id, supporting_count
                FROM trading_intuitions
                WHERE category = ? AND condition = ? AND is_active = 1 AND market = ?
            """, (category, condition, self.MARKET))

            existing = self.cursor.fetchone()

            if existing:
                # Update existing
                self.cursor.execute("""
                    UPDATE trading_intuitions
                    SET confidence = ?,
                        success_rate = ?,
                        supporting_count = supporting_count + ?,
                        last_validated_at = ?
                    WHERE id = ?
                """, (confidence, success_rate, supporting_count, now, existing[0]))
            else:
                # Insert new
                self.cursor.execute("""
                    INSERT INTO trading_intuitions
                    (category, condition, insight, confidence, success_rate,
                     supporting_count, created_at, is_active, market)
                    VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?)
                """, (category, condition, insight, confidence, success_rate,
                      supporting_count, now, self.MARKET))

            self.conn.commit()
            return True

        except Exception as e:
            logger.error(f"Error saving US intuition: {e}")
            return False

    def cleanup_stale_data(
        self,
        max_principles: int = 50,
        max_intuitions: int = 50,
        stale_days: int = 90,
        archive_layer3_days: int = 365,
        dry_run: bool = False
    ) -> Dict:
        """
        Clean up stale data for US market.

        Args:
            max_principles: Maximum active principles to keep
            max_intuitions: Maximum active intuitions to keep
            stale_days: Days without validation before deactivation
            archive_layer3_days: Days after which to archive Layer 3 entries
            dry_run: If True, only count what would be cleaned

        Returns:
            Dict with cleanup results
        """
        results = {
            "principles_deactivated": 0,
            "intuitions_deactivated": 0,
            "journal_entries_archived": 0,
            "low_confidence_principles": 0,
            "stale_principles": 0,
            "low_confidence_intuitions": 0,
            "stale_intuitions": 0,
            "old_layer3_entries": 0
        }

        try:
            stale_cutoff = (datetime.now() - timedelta(days=stale_days)).strftime("%Y-%m-%d")
            archive_cutoff = (datetime.now() - timedelta(days=archive_layer3_days)).strftime("%Y-%m-%d")

            # Count/deactivate low-confidence principles for US
            self.cursor.execute("""
                SELECT COUNT(*) FROM trading_principles
                WHERE is_active = 1 AND confidence < 0.3 AND market = ?
            """, (self.MARKET,))
            results["low_confidence_principles"] = self.cursor.fetchone()[0]

            # Count/deactivate stale principles for US
            self.cursor.execute("""
                SELECT COUNT(*) FROM trading_principles
                WHERE is_active = 1 AND (last_validated_at IS NULL OR last_validated_at < ?) AND market = ?
            """, (stale_cutoff, self.MARKET))
            results["stale_principles"] = self.cursor.fetchone()[0]

            # Count/deactivate low-confidence intuitions for US
            self.cursor.execute("""
                SELECT COUNT(*) FROM trading_intuitions
                WHERE is_active = 1 AND confidence < 0.3 AND market = ?
            """, (self.MARKET,))
            results["low_confidence_intuitions"] = self.cursor.fetchone()[0]

            # Count/deactivate stale intuitions for US
            self.cursor.execute("""
                SELECT COUNT(*) FROM trading_intuitions
                WHERE is_active = 1 AND (last_validated_at IS NULL OR last_validated_at < ?) AND market = ?
            """, (stale_cutoff, self.MARKET))
            results["stale_intuitions"] = self.cursor.fetchone()[0]

            # Count old Layer 3 entries for US
            self.cursor.execute("""
                SELECT COUNT(*) FROM trading_journal
                WHERE compression_layer = 3 AND trade_date < ? AND market = ?
            """, (archive_cutoff, self.MARKET))
            results["old_layer3_entries"] = self.cursor.fetchone()[0]

            if not dry_run:
                # Deactivate low-confidence principles
                self.cursor.execute("""
                    UPDATE trading_principles
                    SET is_active = 0
                    WHERE is_active = 1 AND confidence < 0.3 AND market = ?
                """, (self.MARKET,))
                results["principles_deactivated"] += self.cursor.rowcount

                # Deactivate stale principles
                self.cursor.execute("""
                    UPDATE trading_principles
                    SET is_active = 0
                    WHERE is_active = 1 AND (last_validated_at IS NULL OR last_validated_at < ?) AND market = ?
                """, (stale_cutoff, self.MARKET))
                results["principles_deactivated"] += self.cursor.rowcount

                # Deactivate low-confidence intuitions
                self.cursor.execute("""
                    UPDATE trading_intuitions
                    SET is_active = 0
                    WHERE is_active = 1 AND confidence < 0.3 AND market = ?
                """, (self.MARKET,))
                results["intuitions_deactivated"] += self.cursor.rowcount

                # Deactivate stale intuitions
                self.cursor.execute("""
                    UPDATE trading_intuitions
                    SET is_active = 0
                    WHERE is_active = 1 AND (last_validated_at IS NULL OR last_validated_at < ?) AND market = ?
                """, (stale_cutoff, self.MARKET))
                results["intuitions_deactivated"] += self.cursor.rowcount

                # Archive (delete) old Layer 3 entries
                self.cursor.execute("""
                    DELETE FROM trading_journal
                    WHERE compression_layer = 3 AND trade_date < ? AND market = ?
                """, (archive_cutoff, self.MARKET))
                results["journal_entries_archived"] = self.cursor.rowcount

                # Enforce max limits for principles
                self.cursor.execute("""
                    SELECT COUNT(*) FROM trading_principles
                    WHERE is_active = 1 AND market = ?
                """, (self.MARKET,))
                active_principles = self.cursor.fetchone()[0]

                if active_principles > max_principles:
                    excess = active_principles - max_principles
                    self.cursor.execute("""
                        UPDATE trading_principles
                        SET is_active = 0
                        WHERE id IN (
                            SELECT id FROM trading_principles
                            WHERE is_active = 1 AND market = ?
                            ORDER BY confidence ASC, supporting_trades ASC
                            LIMIT ?
                        )
                    """, (self.MARKET, excess))
                    results["principles_deactivated"] += self.cursor.rowcount

                # Enforce max limits for intuitions
                self.cursor.execute("""
                    SELECT COUNT(*) FROM trading_intuitions
                    WHERE is_active = 1 AND market = ?
                """, (self.MARKET,))
                active_intuitions = self.cursor.fetchone()[0]

                if active_intuitions > max_intuitions:
                    excess = active_intuitions - max_intuitions
                    self.cursor.execute("""
                        UPDATE trading_intuitions
                        SET is_active = 0
                        WHERE id IN (
                            SELECT id FROM trading_intuitions
                            WHERE is_active = 1 AND market = ?
                            ORDER BY confidence ASC, supporting_count ASC
                            LIMIT ?
                        )
                    """, (self.MARKET, excess))
                    results["intuitions_deactivated"] += self.cursor.rowcount

                self.conn.commit()

            return results

        except Exception as e:
            logger.error(f"Error during US cleanup: {e}")
            return results
