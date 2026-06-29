"""Tests for the recent stop-out churn-guard penalty in tracking/journal.py (KR).

Cases:
  (a) recent loss-sell within window + sector "+1" bonus -> net-negative + churn guard reason
  (b) no recent loss -> adjustment unchanged from baseline
  (c) JOURNAL_RECENT_LOSS_PENALTY=0 -> disabled, no change
  (d) recent sell was a WIN -> no penalty
  (e) real recent_loss() against a tiny seeded sqlite DB
"""
import importlib
import os
import sqlite3
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

# Ensure repo root is on path so reentry_cooldown can be imported
REPO_ROOT = str(Path(__file__).resolve().parent.parent)
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_journal_manager(cursor, conn):
    """Import JournalManager freshly and return an instance with journal enabled."""
    # Force re-import so module-level constants re-read env
    if "tracking.journal" in sys.modules:
        del sys.modules["tracking.journal"]
    # Stub out heavy deps before import
    cores_stub = types.ModuleType("cores")
    cores_stub.openai_error_logging = types.ModuleType("cores.openai_error_logging")
    cores_stub.openai_error_logging.log_openai_error = lambda *a, **k: None
    cores_stub.utils = types.ModuleType("cores.utils")
    cores_stub.utils.parse_llm_json = lambda *a, **k: None
    sys.modules.setdefault("cores", cores_stub)
    sys.modules.setdefault("cores.openai_error_logging", cores_stub.openai_error_logging)
    sys.modules.setdefault("cores.utils", cores_stub.utils)

    tracking_pkg = types.ModuleType("tracking")
    sys.modules.setdefault("tracking", tracking_pkg)

    spec = importlib.util.spec_from_file_location(
        "tracking.journal",
        Path(REPO_ROOT) / "tracking" / "journal.py",
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.JournalManager(cursor=cursor, conn=conn, enable_journal=True)


def _make_db():
    """Return (conn, cursor) for an in-memory SQLite with minimal schema."""
    conn = sqlite3.connect(":memory:")
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE trading_journal (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ticker TEXT, trade_date TEXT, profit_rate REAL,
            buy_scenario TEXT, market TEXT
        )
    """)
    cur.execute("""
        CREATE TABLE analysis_performance_tracker (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            trigger_type TEXT, tracking_status TEXT,
            tracked_30d_return REAL, was_traded INTEGER
        )
    """)
    conn.commit()
    return conn, cur


# ---------------------------------------------------------------------------
# Test cases
# ---------------------------------------------------------------------------

class TestJournalRecentLossPenaltyKR(unittest.TestCase):

    def setUp(self):
        self.conn, self.cur = _make_db()

    def tearDown(self):
        self.conn.close()
        # Clean up env
        for k in ("JOURNAL_RECENT_LOSS_HOURS", "JOURNAL_RECENT_LOSS_PENALTY"):
            os.environ.pop(k, None)

    def _get_jm(self):
        return _make_journal_manager(self.cur, self.conn)

    # (a) Recent loss within window + sector "+1" -> net-negative + churn guard reason
    def test_recent_loss_cancels_positive_bonus(self):
        os.environ["JOURNAL_RECENT_LOSS_HOURS"] = "48"
        os.environ["JOURNAL_RECENT_LOSS_PENALTY"] = "2"
        jm = self._get_jm()

        # Patch reentry_cooldown.recent_loss to simulate a loss 1.3h ago
        loss_info = {"gap_hours": 1.3, "last_ret": -7.5, "last_sell": "2026-06-30 10:00:00"}
        with patch.dict(sys.modules, {}):
            import reentry_cooldown
            with patch.object(reentry_cooldown, "recent_loss", return_value=loss_info):
                # Inject a sector bonus: insert 3 profitable trades for sector "Tech"
                for i in range(3):
                    self.cur.execute(
                        "INSERT INTO trading_journal (ticker, trade_date, profit_rate, buy_scenario) "
                        'VALUES (?, ?, ?, ?)',
                        (f"OTHER{i}", "2026-01-01 00:00:00", 8.0, '{"sector": "Tech"}'),
                    )
                self.conn.commit()

                adj, reasons = jm.get_score_adjustment("MU", sector="Tech")

        # sector gave +1, but churn guard should yield net-negative
        self.assertLess(adj, 0, f"Expected net-negative adjustment, got {adj}")
        self.assertTrue(
            any("churn guard" in r for r in reasons),
            f"Expected churn guard reason, got {reasons}",
        )

    # (b) No recent loss -> adjustment unchanged from baseline
    def test_no_recent_loss_no_penalty(self):
        os.environ["JOURNAL_RECENT_LOSS_HOURS"] = "48"
        os.environ["JOURNAL_RECENT_LOSS_PENALTY"] = "2"
        jm = self._get_jm()

        import reentry_cooldown
        with patch.object(reentry_cooldown, "recent_loss", return_value=None):
            adj, reasons = jm.get_score_adjustment("AAPL")

        self.assertEqual(adj, 0)
        self.assertFalse(any("churn guard" in r for r in reasons))

    # (c) JOURNAL_RECENT_LOSS_PENALTY=0 -> disabled, no change
    def test_penalty_disabled_via_env(self):
        os.environ["JOURNAL_RECENT_LOSS_HOURS"] = "48"
        os.environ["JOURNAL_RECENT_LOSS_PENALTY"] = "0"
        jm = self._get_jm()

        import reentry_cooldown
        loss_info = {"gap_hours": 1.0, "last_ret": -5.0, "last_sell": "2026-06-30 10:00:00"}
        with patch.object(reentry_cooldown, "recent_loss", return_value=loss_info):
            adj, reasons = jm.get_score_adjustment("MU")

        self.assertEqual(adj, 0)
        self.assertFalse(any("churn guard" in r for r in reasons))

    # (d) Recent sell was a WIN -> no penalty
    def test_recent_win_no_penalty(self):
        os.environ["JOURNAL_RECENT_LOSS_HOURS"] = "48"
        os.environ["JOURNAL_RECENT_LOSS_PENALTY"] = "2"
        jm = self._get_jm()

        import reentry_cooldown
        # recent_loss returns None when the sell was a win
        with patch.object(reentry_cooldown, "recent_loss", return_value=None):
            adj, reasons = jm.get_score_adjustment("NVDA")

        self.assertEqual(adj, 0)
        self.assertFalse(any("churn guard" in r for r in reasons))

    # (e) Real recent_loss() against a seeded sqlite DB
    def test_recent_loss_real_db(self):
        """Exercise reentry_cooldown.recent_loss against a real seeded DB."""
        import reentry_cooldown

        with tempfile.NamedTemporaryFile(suffix=".sqlite", delete=False) as f:
            db_path = f.name

        try:
            conn = sqlite3.connect(db_path)
            conn.execute("""
                CREATE TABLE trading_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ticker TEXT,
                    sell_date TEXT,
                    profit_rate REAL
                )
            """)
            # Insert a recent loss sell (2h ago)
            from datetime import datetime, timedelta
            sell_ts = (datetime.now() - timedelta(hours=2)).strftime("%Y-%m-%d %H:%M:%S")
            conn.execute(
                "INSERT INTO trading_history (ticker, sell_date, profit_rate) VALUES (?, ?, ?)",
                ("000660", sell_ts, -7.5),
            )
            conn.commit()
            conn.close()

            with patch.dict(os.environ, {"REENTRY_COOLDOWN_DB": db_path}):
                result = reentry_cooldown.recent_loss("000660", market="KR")

            self.assertIsNotNone(result, "Expected a loss dict, got None")
            self.assertAlmostEqual(result["last_ret"], -7.5)
            self.assertLess(result["gap_hours"], 3.0)

        finally:
            os.unlink(db_path)

    # Edge: loss outside the window -> still get penalised (window check is in journal, not recent_loss)
    # but gap_hours > JOURNAL_RECENT_LOSS_HOURS -> no penalty
    def test_loss_outside_window_no_penalty(self):
        os.environ["JOURNAL_RECENT_LOSS_HOURS"] = "1"  # very short window
        os.environ["JOURNAL_RECENT_LOSS_PENALTY"] = "2"
        jm = self._get_jm()

        import reentry_cooldown
        # gap_hours=5 > window=1 -> no penalty
        loss_info = {"gap_hours": 5.0, "last_ret": -7.5, "last_sell": "2026-06-30 05:00:00"}
        with patch.object(reentry_cooldown, "recent_loss", return_value=loss_info):
            adj, reasons = jm.get_score_adjustment("MU")

        self.assertEqual(adj, 0)
        self.assertFalse(any("churn guard" in r for r in reasons))


if __name__ == "__main__":
    unittest.main()
