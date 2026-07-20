"""Tests for journal intuition-noise fix.

Fix 1: cleanup_stale_data must enforce max_intuitions cap (mirrors existing max_principles logic).
Fix 2: get_context_for_ticker must inject diverse categories (per-category cap + backfill).

TDD flow: run pre-fix to confirm failures, implement fixes, run again to confirm passes.
"""

import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from tracking.compression import CompressionManager
from tracking.journal import JournalManager

# Import constants added by Fix 2.  Fall back to spec values so Fix 1 tests
# can be run independently before Fix 2 is implemented (ImportError expected pre-fix).
try:
    from tracking.journal import INTUITION_TOTAL_LIMIT, INTUITION_PER_CATEGORY_CAP
except ImportError:
    INTUITION_TOTAL_LIMIT = 10
    INTUITION_PER_CATEGORY_CAP = 3


# ---------------------------------------------------------------------------
# Schema / helpers
# ---------------------------------------------------------------------------

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS trading_intuitions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    category TEXT NOT NULL,
    subcategory TEXT,
    condition TEXT NOT NULL,
    insight TEXT NOT NULL,
    confidence REAL,
    supporting_trades INTEGER DEFAULT 0,
    success_rate REAL DEFAULT 0.5,
    source_journal_ids TEXT,
    created_at TEXT NOT NULL,
    last_validated_at TEXT,
    is_active INTEGER DEFAULT 1
);

CREATE TABLE IF NOT EXISTS trading_principles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    scope TEXT,
    scope_context TEXT,
    condition TEXT NOT NULL,
    action TEXT NOT NULL,
    reason TEXT,
    priority TEXT DEFAULT 'medium',
    confidence REAL DEFAULT 0.5,
    supporting_trades INTEGER DEFAULT 0,
    source_journal_ids TEXT,
    created_at TEXT NOT NULL,
    last_validated_at TEXT,
    is_active INTEGER DEFAULT 1
);

CREATE TABLE IF NOT EXISTS trading_journal (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker TEXT,
    company_name TEXT,
    trade_date TEXT,
    trade_type TEXT,
    buy_price REAL,
    buy_date TEXT,
    buy_scenario TEXT,
    buy_market_context TEXT,
    sell_price REAL,
    sell_reason TEXT,
    profit_rate REAL,
    holding_days INTEGER,
    situation_analysis TEXT,
    judgment_evaluation TEXT,
    lessons TEXT,
    pattern_tags TEXT,
    one_line_summary TEXT,
    confidence_score REAL,
    compression_layer INTEGER DEFAULT 1,
    compressed_summary TEXT,
    created_at TEXT,
    last_compressed_at TEXT,
    exit_intent_id TEXT
);

CREATE TABLE IF NOT EXISTS analysis_performance_tracker (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker TEXT,
    trigger_type TEXT,
    was_traded INTEGER DEFAULT 0,
    tracking_status TEXT,
    tracked_30d_return REAL,
    created_at TEXT
);
"""


def _setup_db() -> tuple:
    """Return (conn, cur) with minimal schema on an in-memory DB."""
    conn = sqlite3.connect(":memory:")
    cur = conn.cursor()
    cur.executescript(_SCHEMA_SQL)
    conn.commit()
    return conn, cur


def _insert_intuition(
    cur: sqlite3.Cursor,
    category: str,
    idx: int,
    confidence: float,
    now: str = "2026-01-01 00:00:00",
) -> None:
    cur.execute(
        """INSERT INTO trading_intuitions
           (category, condition, insight, confidence, supporting_trades,
            success_rate, source_journal_ids, created_at, last_validated_at, is_active)
           VALUES (?, ?, ?, ?, 2, 0.6, '[]', ?, ?, 1)""",
        (category, f"{category}_cond_{idx}", f"{category}_ins_{idx}", confidence, now, now),
    )


# ---------------------------------------------------------------------------
# Fix 1 tests — cleanup_stale_data must enforce max_intuitions
# ---------------------------------------------------------------------------


class TestCleanupMaxIntuitions:
    """Fix 1: cleanup_stale_data must deactivate the excess lowest-confidence
    intuitions when active count exceeds max_intuitions, mirroring the existing
    max_principles enforcement block.
    """

    def setup_method(self):
        self.conn, self.cur = _setup_db()

    def teardown_method(self):
        self.conn.close()

    def _mgr(self) -> CompressionManager:
        return CompressionManager(
            cursor=self.cur, conn=self.conn, language="ko", enable_journal=True
        )

    def test_excess_intuitions_deactivated(self):
        """60 active intuitions all conf>=0.5; cleanup(max=50) deactivates exactly 10."""
        now = "2026-01-01 00:00:00"
        for i in range(60):
            # confidences 0.500, 0.505, …, 0.795 — all strictly >= 0.5
            conf = round(0.50 + i * 0.005, 3)
            _insert_intuition(self.cur, "pattern", i, conf, now)
        self.conn.commit()

        stats = self._mgr().cleanup_stale_data(
            max_intuitions=50, min_confidence=0.3, dry_run=False
        )

        assert stats["intuitions_deactivated"] == 10, (
            f"Expected 10 deactivated, got {stats['intuitions_deactivated']}"
        )

        self.cur.execute(
            "SELECT COUNT(*) FROM trading_intuitions WHERE is_active = 1"
        )
        assert self.cur.fetchone()[0] == 50, "Exactly 50 must remain active"

    def test_lowest_confidence_deactivated_first(self):
        """The 10 deactivated rows must be the lowest-confidence ones."""
        now = "2026-01-01 00:00:00"
        for i in range(60):
            conf = round(0.50 + i * 0.005, 3)
            _insert_intuition(self.cur, "pattern", i, conf, now)
        self.conn.commit()

        self._mgr().cleanup_stale_data(
            max_intuitions=50, min_confidence=0.3, dry_run=False
        )

        self.cur.execute(
            "SELECT MAX(confidence) FROM trading_intuitions WHERE is_active = 0"
        )
        max_deactivated = self.cur.fetchone()[0]

        self.cur.execute(
            "SELECT MIN(confidence) FROM trading_intuitions WHERE is_active = 1"
        )
        min_active = self.cur.fetchone()[0]

        assert max_deactivated <= min_active, (
            f"Deactivated max conf {max_deactivated:.3f} > active min conf {min_active:.3f} — "
            "lowest-confidence intuitions must be deactivated first"
        )

    def test_dry_run_no_changes(self):
        """dry_run=True must not deactivate anything even when count > max_intuitions."""
        now = "2026-01-01 00:00:00"
        for i in range(60):
            _insert_intuition(self.cur, "pattern", i, 0.70, now)
        self.conn.commit()

        stats = self._mgr().cleanup_stale_data(
            max_intuitions=50, min_confidence=0.3, dry_run=True
        )

        self.cur.execute(
            "SELECT COUNT(*) FROM trading_intuitions WHERE is_active = 1"
        )
        assert self.cur.fetchone()[0] == 60, (
            "dry_run=True must not modify DB; expected all 60 still active"
        )
        assert stats["dry_run"] is True

    def test_under_limit_no_extra_deactivation(self):
        """If active count <= max_intuitions, no extra deactivation occurs."""
        now = "2026-01-01 00:00:00"
        for i in range(30):
            _insert_intuition(self.cur, "pattern", i, 0.70, now)
        self.conn.commit()

        stats = self._mgr().cleanup_stale_data(
            max_intuitions=50, min_confidence=0.3, dry_run=False
        )

        assert stats["intuitions_deactivated"] == 0
        self.cur.execute(
            "SELECT COUNT(*) FROM trading_intuitions WHERE is_active = 1"
        )
        assert self.cur.fetchone()[0] == 30


# ---------------------------------------------------------------------------
# Fix 2 tests — get_context_for_ticker must inject diverse categories
# ---------------------------------------------------------------------------


class TestContextIntuitionDiversity:
    """Fix 2: get_context_for_ticker must apply a per-category cap so no single
    category dominates the injected intuition block, then backfill with remaining
    high-confidence items until the total limit is reached.
    """

    def setup_method(self):
        self.conn, self.cur = _setup_db()

    def teardown_method(self):
        self.conn.close()

    def _mgr(self) -> JournalManager:
        return JournalManager(
            cursor=self.cur, conn=self.conn, language="ko", enable_journal=True
        )

    def _intuition_lines(self, context: str) -> list:
        return [ln for ln in context.split("\n") if ln.startswith("- [")]

    def test_dominant_category_is_capped(self):
        """8 'pattern' at conf 0.85 + 3 each of 'market'(0.60)/'sector'(0.55)/'volatility'(0.50).

        Without the fix the top-10 query returns 8 pattern + 2 market (0 sector/volatility).
        With the fix:
          - pattern is capped at INTUITION_PER_CATEGORY_CAP
          - market, sector, volatility each contribute entries
          - total <= INTUITION_TOTAL_LIMIT
        """
        now = "2026-01-01 00:00:00"
        for i in range(8):
            _insert_intuition(self.cur, "pattern", i, 0.85, now)
        for i in range(3):
            _insert_intuition(self.cur, "market", i, 0.60, now)
        for i in range(3):
            _insert_intuition(self.cur, "sector", i, 0.55, now)
        for i in range(3):
            _insert_intuition(self.cur, "volatility", i, 0.50, now)
        self.conn.commit()

        context = self._mgr().get_context_for_ticker("AAPL")
        assert context, "Expected non-empty context when intuitions exist"
        assert "#### Accumulated Trading Intuitions" in context

        lines = context.split("\n")
        pattern_lines = [ln for ln in lines if ln.startswith("- [pattern]")]
        market_lines = [ln for ln in lines if ln.startswith("- [market]")]
        sector_lines = [ln for ln in lines if ln.startswith("- [sector]")]
        all_intuition_lines = self._intuition_lines(context)

        assert len(pattern_lines) <= INTUITION_PER_CATEGORY_CAP, (
            f"pattern: {len(pattern_lines)} lines > cap {INTUITION_PER_CATEGORY_CAP}"
        )
        assert len(market_lines) >= 1, (
            "Expected at least 1 market intuition; diversity not applied"
        )
        assert len(sector_lines) >= 1, (
            "Expected at least 1 sector intuition; diversity not applied"
        )
        assert len(all_intuition_lines) <= INTUITION_TOTAL_LIMIT, (
            f"Total intuition lines {len(all_intuition_lines)} > limit {INTUITION_TOTAL_LIMIT}"
        )

    def test_total_limit_not_exceeded(self):
        """5 categories x 5 entries each (25 total); injected count must be <= INTUITION_TOTAL_LIMIT."""
        now = "2026-01-01 00:00:00"
        for cat in ["a", "b", "c", "d", "e"]:
            for i in range(5):
                _insert_intuition(self.cur, cat, i, 0.70, now)
        self.conn.commit()

        context = self._mgr().get_context_for_ticker("AAPL")
        all_intuition_lines = self._intuition_lines(context)
        assert len(all_intuition_lines) <= INTUITION_TOTAL_LIMIT, (
            f"Total {len(all_intuition_lines)} > limit {INTUITION_TOTAL_LIMIT}"
        )

    def test_backfill_fills_to_limit(self):
        """Single category with 5 entries; all 5 should be included via backfill
        even though per-category cap is INTUITION_PER_CATEGORY_CAP.
        """
        now = "2026-01-01 00:00:00"
        for i in range(5):
            _insert_intuition(self.cur, "pattern", i, 0.70, now)
        self.conn.commit()

        context = self._mgr().get_context_for_ticker("AAPL")
        pattern_lines = [ln for ln in context.split("\n") if ln.startswith("- [pattern]")]
        # Only 5 total: cap pass takes 3, backfill adds 2 → all 5 included
        assert len(pattern_lines) == 5, (
            f"Expected all 5 backfilled, got {len(pattern_lines)}"
        )

    def test_rendering_format_preserved(self):
        """Output lines must match the exact rendering format used before the fix."""
        now = "2026-01-01 00:00:00"
        _insert_intuition(self.cur, "pattern", 0, 1.0, now)  # all 5 dots
        self.conn.commit()

        context = self._mgr().get_context_for_ticker("AAPL")
        # Format: - [category] condition → insight (Confidence: ●●●●●)
        assert "- [pattern] pattern_cond_0 → pattern_ins_0 (Confidence: ●●●●●)" in context
