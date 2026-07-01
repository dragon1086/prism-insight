"""Tests for the recent stop-out churn-guard penalty in prism-us/tracking/journal.py (US).

Cases:
  (a) recent loss-sell within window + sector "+1" bonus -> net-negative + churn guard reason
  (b) no recent loss -> adjustment unchanged from baseline
  (c) JOURNAL_RECENT_LOSS_PENALTY=0 -> disabled, no change
  (d) recent sell was a WIN -> no penalty
  (e) real recent_loss() against a tiny seeded sqlite DB
"""
import importlib
import importlib.util
import os
import sqlite3
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

# Repo root is parent of parent of parent (prism-us/tests -> prism-us -> prism-insight)
REPO_ROOT = str(Path(__file__).resolve().parent.parent.parent)
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

PRISM_US_ROOT = str(Path(__file__).resolve().parent.parent)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_us_journal_manager(cursor, conn):
    """Import USJournalManager freshly so env constants are re-evaluated."""
    mod_key = "prism_us_tracking_journal"
    if mod_key in sys.modules:
        del sys.modules[mod_key]

    # Stub heavy deps that prism-us/tracking/journal.py loads at module level
    # (parse_llm_json is loaded via _import_from_main_cores at import time)
    cores_stub = types.ModuleType("cores")
    cores_stub.utils = types.ModuleType("cores.utils")
    cores_stub.utils.parse_llm_json = lambda *a, **k: None
    sys.modules.setdefault("cores", cores_stub)
    sys.modules.setdefault("cores.utils", cores_stub.utils)
    sys.modules.setdefault("cores_utils", cores_stub.utils)

    # Patch _import_from_main_cores to return our stub (called at module level)
    us_journal_path = Path(PRISM_US_ROOT) / "tracking" / "journal.py"

    # We need to prevent the real _import_from_main_cores from running since
    # the test environment won't have cores/utils.py accessible the same way.
    # We patch by pre-populating the cores_utils key in sys.modules.
    stub_utils = types.ModuleType("cores_utils")
    stub_utils.parse_llm_json = lambda *a, **k: None
    sys.modules["cores_utils"] = stub_utils

    spec = importlib.util.spec_from_file_location(mod_key, us_journal_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.USJournalManager(cursor=cursor, conn=conn, enable_journal=True)


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
        CREATE TABLE us_analysis_performance_tracker (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            trigger_type TEXT, tracking_status TEXT,
            return_30d REAL, was_traded INTEGER
        )
    """)
    conn.commit()
    return conn, cur


# ---------------------------------------------------------------------------
# Test cases
# ---------------------------------------------------------------------------

class TestJournalRecentLossPenaltyUS(unittest.TestCase):

    def setUp(self):
        self.conn, self.cur = _make_db()

    def tearDown(self):
        self.conn.close()
        for k in ("JOURNAL_RECENT_LOSS_HOURS", "JOURNAL_RECENT_LOSS_PENALTY"):
            os.environ.pop(k, None)

    def _get_jm(self):
        return _make_us_journal_manager(self.cur, self.conn)

    # (a) Recent loss within window + sector "+1" bonus -> net-negative + churn guard reason
    def test_recent_loss_cancels_positive_bonus(self):
        os.environ["JOURNAL_RECENT_LOSS_HOURS"] = "48"
        os.environ["JOURNAL_RECENT_LOSS_PENALTY"] = "2"
        jm = self._get_jm()

        loss_info = {"gap_hours": 1.3, "last_ret": -7.5, "last_sell": "2026-06-30 10:00:00"}
        import reentry_cooldown
        with patch.object(reentry_cooldown, "recent_risk_exit", return_value=loss_info):
            # Give sector bonus: 3 profitable US trades for sector "Technology"
            for i in range(3):
                self.cur.execute(
                    "INSERT INTO trading_journal (ticker, trade_date, profit_rate, buy_scenario, market) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (f"OTHER{i}", "2026-01-01 00:00:00", 8.0, '{"sector": "Technology"}', "US"),
                )
            self.conn.commit()

            adj, reasons = jm.get_score_adjustment("MU", sector="Technology")

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
        with patch.object(reentry_cooldown, "recent_risk_exit", return_value=None):
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
        with patch.object(reentry_cooldown, "recent_risk_exit", return_value=loss_info):
            adj, reasons = jm.get_score_adjustment("MU")

        self.assertEqual(adj, 0)
        self.assertFalse(any("churn guard" in r for r in reasons))

    # (d) Recent sell was a WIN -> no penalty (recent_loss returns None for wins)
    def test_recent_win_no_penalty(self):
        os.environ["JOURNAL_RECENT_LOSS_HOURS"] = "48"
        os.environ["JOURNAL_RECENT_LOSS_PENALTY"] = "2"
        jm = self._get_jm()

        import reentry_cooldown
        with patch.object(reentry_cooldown, "recent_risk_exit", return_value=None):
            adj, reasons = jm.get_score_adjustment("NVDA")

        self.assertEqual(adj, 0)
        self.assertFalse(any("churn guard" in r for r in reasons))

    # (e) Real recent_loss() against a seeded sqlite DB (US table)
    def test_recent_loss_real_db_us(self):
        """Exercise reentry_cooldown.recent_loss against a real seeded DB for US."""
        import reentry_cooldown

        with tempfile.NamedTemporaryFile(suffix=".sqlite", delete=False) as f:
            db_path = f.name

        try:
            conn = sqlite3.connect(db_path)
            conn.execute("""
                CREATE TABLE us_trading_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ticker TEXT,
                    sell_date TEXT,
                    profit_rate REAL
                )
            """)
            from datetime import datetime, timedelta
            sell_ts = (datetime.now() - timedelta(hours=2)).strftime("%Y-%m-%d %H:%M:%S")
            conn.execute(
                "INSERT INTO us_trading_history (ticker, sell_date, profit_rate) VALUES (?, ?, ?)",
                ("MU", sell_ts, -7.5),
            )
            conn.commit()
            conn.close()

            with patch.dict(os.environ, {"REENTRY_COOLDOWN_DB": db_path}):
                result = reentry_cooldown.recent_loss("MU", market="US")

            self.assertIsNotNone(result, "Expected a loss dict, got None")
            self.assertAlmostEqual(result["last_ret"], -7.5)
            self.assertLess(result["gap_hours"], 3.0)

        finally:
            os.unlink(db_path)

    # Edge: loss outside the configured window -> no penalty
    def test_loss_outside_window_no_penalty(self):
        os.environ["JOURNAL_RECENT_LOSS_HOURS"] = "1"
        os.environ["JOURNAL_RECENT_LOSS_PENALTY"] = "2"
        jm = self._get_jm()

        import reentry_cooldown
        loss_info = {"gap_hours": 5.0, "last_ret": -7.5, "last_sell": "2026-06-30 05:00:00"}
        with patch.object(reentry_cooldown, "recent_risk_exit", return_value=loss_info):
            adj, reasons = jm.get_score_adjustment("MU")

        self.assertEqual(adj, 0)
        self.assertFalse(any("churn guard" in r for r in reasons))


if __name__ == "__main__":
    unittest.main()
