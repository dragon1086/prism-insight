"""Standalone safety tests for the US pending-order batch."""

from __future__ import annotations

import datetime
import importlib.util
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

MODULE_PATH = Path(__file__).resolve().parents[1] / "us_pending_order_batch.py"
SPEC = importlib.util.spec_from_file_location("pending_batch_safety", MODULE_PATH)
pending_batch = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(pending_batch)


def _create_db(path: Path) -> None:
    with sqlite3.connect(path) as conn:
        conn.execute(
            """CREATE TABLE us_pending_orders (
                id INTEGER PRIMARY KEY,
                account_key TEXT,
                account_name TEXT,
                product_code TEXT,
                mode TEXT,
                ticker TEXT,
                order_type TEXT,
                limit_price REAL,
                buy_amount REAL,
                exchange TEXT,
                status TEXT,
                failure_reason TEXT,
                created_at TEXT,
                claimed_at TEXT,
                submission_started_at TEXT,
                executed_at TEXT,
                order_result TEXT
            )"""
        )


class PendingOrderBatchSafetyTests(unittest.TestCase):
    def test_claim_is_atomic_across_connections(self):
        with tempfile.TemporaryDirectory() as directory:
            db_path = Path(directory) / "orders.sqlite"
            _create_db(db_path)
            now = datetime.datetime.now(pending_batch.KST).strftime('%Y-%m-%d %H:%M:%S')
            with sqlite3.connect(db_path) as conn:
                conn.execute(
                    """INSERT INTO us_pending_orders
                       (id, ticker, order_type, limit_price, status, created_at)
                       VALUES (1, 'AAPL', 'buy', 200, 'pending', ?)""",
                    (now,),
                )
            first = sqlite3.connect(db_path)
            second = sqlite3.connect(db_path)
            try:
                self.assertTrue(pending_batch.claim_pending_order(first, 1))
                self.assertFalse(pending_batch.claim_pending_order(second, 1))
            finally:
                first.close()
                second.close()

    def test_dry_run_is_read_only_and_never_imports_trading(self):
        with tempfile.TemporaryDirectory() as directory:
            db_path = Path(directory) / "orders.sqlite"
            _create_db(db_path)
            old = "2020-01-01 10:00:00"
            with sqlite3.connect(db_path) as conn:
                conn.execute(
                    """INSERT INTO us_pending_orders
                       (id, ticker, order_type, limit_price, status, created_at)
                       VALUES (1, 'AAPL', 'buy', 200, 'pending', ?)""",
                    (old,),
                )

            real_import = __import__

            def guarded_import(name, *args, **kwargs):
                if name == "trading.us_stock_trading":
                    raise AssertionError("dry-run imported the trading client")
                return real_import(name, *args, **kwargs)

            with patch.object(pending_batch, "DB_PATH", db_path), patch(
                "builtins.__import__", side_effect=guarded_import
            ):
                pending_batch.process_pending_orders(dry_run=True)

            with sqlite3.connect(db_path) as conn:
                status = conn.execute(
                    "SELECT status FROM us_pending_orders WHERE id = 1"
                ).fetchone()[0]
            self.assertEqual(status, "pending")

    def test_stale_claim_recovery_distinguishes_pre_and_post_submit(self):
        with tempfile.TemporaryDirectory() as directory:
            db_path = Path(directory) / "orders.sqlite"
            _create_db(db_path)
            stale = "2020-01-01 10:00:00"
            with sqlite3.connect(db_path) as conn:
                conn.executemany(
                    """INSERT INTO us_pending_orders
                       (id, ticker, order_type, limit_price, status, created_at,
                        claimed_at, submission_started_at)
                       VALUES (?, 'AAPL', 'buy', 200, ?, ?, ?, ?)""",
                    [
                        (1, "claimed", stale, stale, None),
                        (2, "submitting", stale, stale, stale),
                    ],
                )
                recovered, unknown = pending_batch.recover_stale_claims(
                    conn, datetime.datetime.now(pending_batch.KST)
                )
                statuses = dict(
                    conn.execute("SELECT id, status FROM us_pending_orders").fetchall()
                )
            self.assertEqual((recovered, unknown), (1, 1))
            self.assertEqual(statuses, {1: "pending", 2: "unknown"})


if __name__ == "__main__":
    unittest.main()
