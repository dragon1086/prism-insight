"""Safety regressions introduced by the repository maintenance pass."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

BTC_ROOT = Path(__file__).resolve().parents[1]
if str(BTC_ROOT) not in sys.path:
    sys.path.insert(0, str(BTC_ROOT))

from collector import backfill, update
from live.demo import DemoAdapter
from live.runner import tick


class BtcMaintenanceSafetyTests(unittest.TestCase):
    def test_live_mode_fails_closed_before_opening_state(self):
        result = tick(mode="live")
        self.assertFalse(result["updated"])
        self.assertIn("not implemented", result["error"])

    def test_demo_adapter_rejects_missing_session(self):
        with patch("live.demo._make_session", return_value=(None, "missing credentials")):
            with self.assertRaisesRegex(RuntimeError, "missing credentials"):
                DemoAdapter(None, {}, [], [])

    def test_update_all_raises_when_any_timeframe_fails(self):
        def fake_update(tf, db_path=None):
            if tf == "4h":
                raise OSError("feed unavailable")
            return 1

        with patch("collector.update.update_tf", side_effect=fake_update):
            with self.assertRaisesRegex(RuntimeError, "4h: feed unavailable"):
                update.update_all()

    def test_backfill_marks_only_newest_page_active_candle_unconfirmed(self):
        pages = [
            [["200", "1", "2", "0", "1", "10", "10"]],
            [["100", "1", "2", "0", "1", "10", "10"]],
        ]
        current_open_times = []

        class FakeConnection:
            def close(self):
                pass

        def fake_upsert(conn, tf, rows, current_open_time=None):
            current_open_times.append(current_open_time)
            return len(rows)

        with patch("collector.backfill.get_connection", return_value=FakeConnection()), patch(
            "collector.backfill.iter_klines_backwards", return_value=iter(pages)
        ), patch("collector.backfill.upsert_rows", side_effect=fake_upsert), patch(
            "collector.backfill.get_row_count", return_value=2
        ):
            self.assertEqual(backfill.backfill_tf("30m"), 2)

        self.assertEqual(current_open_times, [200, None])


if __name__ == "__main__":
    unittest.main()
