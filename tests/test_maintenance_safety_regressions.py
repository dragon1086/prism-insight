"""Standalone regressions for maintenance safety fixes (stdlib only)."""

from __future__ import annotations

import asyncio
import ast
import datetime
import importlib.util
import logging
from pathlib import Path
import re
import sqlite3
import time
import unittest
from unittest.mock import patch

from messaging import gcp_pubsub_signal_publisher as gcp_module
from messaging import redis_signal_publisher as redis_module
from tools.hardstop_seller import iter_account_position_groups as hardstop_groups
from tools.trend_exit_seller import iter_account_position_groups as trend_groups
from tools.trend_exit_seller import INFLIGHT_TTL_SEC, _iso, _now, has_open_inflight
from prism_us.trading.market_session import is_exchange_session_open
from prism_us.trading.order_submission import OrderOutcomeUnknown, submit_blocking_order

_HELPERS_SPEC = importlib.util.spec_from_file_location(
    "maintenance_tracking_helpers", Path(__file__).parents[1] / "tracking" / "helpers.py"
)
_HELPERS = importlib.util.module_from_spec(_HELPERS_SPEC)
assert _HELPERS_SPEC.loader is not None  # nosec B101
_HELPERS_SPEC.loader.exec_module(_HELPERS)
compute_fractional_sell_quantity = _HELPERS.compute_fractional_sell_quantity
parse_price_value = _HELPERS.parse_price_value


def _load_function(path: Path, name: str):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    node = next(item for item in tree.body if isinstance(item, ast.FunctionDef) and item.name == name)
    namespace = {"re": re}
    exec(compile(ast.Module(body=[node], type_ignores=[]), str(path), "exec"), namespace)
    return namespace[name]


class _FakeIloc:
    def __init__(self, row):
        self.row = row

    def __getitem__(self, index):
        if index != 0:
            raise IndexError(index)
        return self.row


class _FakeSchedule:
    def __init__(self, row=None):
        self.empty = row is None
        self.iloc = _FakeIloc(row)


class _FakeTimestamp:
    def __init__(self, value):
        self.value = value

    def tz_convert(self, timezone):
        return self

    def to_pydatetime(self):
        return self.value


class _FakeCalendar:
    def __init__(self, row=None):
        self.row = row

    def schedule(self, **kwargs):
        return _FakeSchedule(self.row)


class _RetryingRedisPublisher:
    def __init__(self):
        self.connect_calls = 0
        self.connected = False

    def _is_connected(self):
        return self.connected

    async def connect(self):
        self.connect_calls += 1
        self.connected = self.connect_calls > 1


class _RetryingGcpPublisher:
    def __init__(self):
        self.connect_calls = 0
        self._publisher = None

    async def connect(self):
        self.connect_calls += 1
        if self.connect_calls > 1:
            self._publisher = object()


class MaintenanceSafetyRegressionTests(unittest.TestCase):
    def tearDown(self):
        redis_module._global_publisher = None
        gcp_module._global_publisher = None

    def test_fractional_zero_is_not_a_full_liquidation_request(self):
        self.assertEqual(compute_fractional_sell_quantity(1, 3), 0)

    def test_sell_resolvers_distinguish_none_from_explicit_zero(self):
        root = Path(__file__).parents[1]
        for path in (
            root / "trading" / "domestic_stock_trading.py",
            root / "prism-us" / "trading" / "us_stock_trading.py",
        ):
            resolver = _load_function(path, "_resolve_sell_quantity")
            self.assertEqual(resolver(7, None), 7)
            self.assertEqual(resolver(7, 0), 0)
            self.assertEqual(resolver(7, -1), 0)
            self.assertEqual(resolver(7, "bad"), 0)
            self.assertEqual(resolver(7, 99), 7)

    def test_same_ticker_in_distinct_accounts_is_not_pyramiding(self):
        holdings = {
            "005930": [
                {"ticker": "005930", "account_key": "acct-a"},
                {"ticker": "005930", "account_key": "acct-b"},
            ]
        }
        for grouper in (hardstop_groups, trend_groups):
            groups = list(grouper(holdings))
            self.assertEqual([len(rows) for _, rows in groups], [1, 1])

    def test_same_account_rows_remain_a_pyramid_group(self):
        holdings = {
            "005930": [
                {"ticker": "005930", "account_key": "acct-a"},
                {"ticker": "005930", "account_key": "acct-a"},
            ]
        }
        for grouper in (hardstop_groups, trend_groups):
            groups = list(grouper(holdings))
            self.assertEqual([len(rows) for _, rows in groups], [2])

    def test_price_ranges_accept_units_labels_and_negative_endpoints(self):
        self.assertEqual(parse_price_value("1,700원~2,000원"), 1850.0)
        self.assertEqual(parse_price_value("최소 1,500 ~ 최대 2,000"), 1750.0)
        self.assertEqual(parse_price_value("-1000~-500"), -750.0)

    def test_redis_cached_publisher_reconnects_after_transient_failure(self):
        fake = _RetryingRedisPublisher()
        with patch.object(redis_module, "SignalPublisher", return_value=fake):
            asyncio.run(redis_module.get_signal_publisher())
            asyncio.run(redis_module.get_signal_publisher())
        self.assertEqual(fake.connect_calls, 2)
        self.assertTrue(fake.connected)

    def test_gcp_cached_publisher_reconnects_after_transient_failure(self):
        fake = _RetryingGcpPublisher()
        with patch.object(gcp_module, "SignalPublisher", return_value=fake):
            asyncio.run(gcp_module.get_signal_publisher())
            asyncio.run(gcp_module.get_signal_publisher())
        self.assertEqual(fake.connect_calls, 2)
        self.assertIsNotNone(fake._publisher)

    def test_trend_shadow_and_stale_open_do_not_block(self):
        conn = sqlite3.connect(":memory:")
        conn.execute(
            "CREATE TABLE loop_b_inflight_orders (ticker TEXT, market TEXT, "
            "side TEXT, status TEXT, submitted_ts TEXT)"
        )
        conn.execute(
            "INSERT INTO loop_b_inflight_orders VALUES ('005930','KR','SELL','SHADOW',?)",
            (_iso(_now()),),
        )
        self.assertFalse(has_open_inflight(conn, "005930", "KR"))
        conn.execute("DELETE FROM loop_b_inflight_orders")
        conn.execute(
            "INSERT INTO loop_b_inflight_orders VALUES ('005930','KR','SELL','OPEN',?)",
            (_iso(_now() - datetime.timedelta(seconds=INFLIGHT_TTL_SEC + 1)),),
        )
        self.assertFalse(has_open_inflight(conn, "005930", "KR"))

    def test_market_session_uses_holiday_and_early_close_schedule(self):
        tz = datetime.timezone.utc
        regular_open = datetime.datetime(2026, 11, 25, 14, 30, tzinfo=tz)
        regular_close = datetime.datetime(2026, 11, 25, 21, 0, tzinfo=tz)
        row = {
            "market_open": _FakeTimestamp(regular_open),
            "market_close": _FakeTimestamp(regular_close),
        }
        self.assertTrue(
            is_exchange_session_open(
                datetime.datetime(2026, 11, 25, 16, 0, tzinfo=tz),
                _FakeCalendar(row),
                tz,
            )
        )
        self.assertFalse(
            is_exchange_session_open(
                datetime.datetime(2026, 7, 3, 16, 0, tzinfo=tz),
                _FakeCalendar(),
                tz,
            )
        )
        early_row = {
            "market_open": _FakeTimestamp(datetime.datetime(2026, 11, 27, 14, 30, tzinfo=tz)),
            "market_close": _FakeTimestamp(datetime.datetime(2026, 11, 27, 18, 0, tzinfo=tz)),
        }
        self.assertFalse(
            is_exchange_session_open(
                datetime.datetime(2026, 11, 27, 19, 0, tzinfo=tz),
                _FakeCalendar(early_row),
                tz,
            )
        )

    def test_timed_out_submission_returns_unknown_without_waiting_for_worker(self):
        async def scenario():
            registry = set()
            start = time.monotonic()
            with self.assertRaises(OrderOutcomeUnknown):
                await asyncio.wait_for(
                    submit_blocking_order(
                        registry,
                        time.sleep,
                        0.05,
                        logger=logging.getLogger("test.order"),
                    ),
                    timeout=0.001,
                )
            elapsed = time.monotonic() - start
            self.assertLess(elapsed, 0.03)
            self.assertTrue(registry)
            await asyncio.gather(*tuple(registry))

        asyncio.run(scenario())

    def test_sqlite_write_filter_rejects_schema_and_attachment_statements(self):
        server_path = (
            Path(__file__).parents[1]
            / "sqlite"
            / "src"
            / "mcp_server_sqlite"
            / "server.py"
        )
        statement_type = _load_function(server_path, "_write_statement_type")
        self.assertEqual(statement_type("-- note\n INSERT INTO t VALUES (1)"), "INSERT")
        for query in ("DROP TABLE t", "ALTER TABLE t ADD x", "PRAGMA writable_schema=1", "ATTACH 'x' AS y"):
            self.assertIsNone(statement_type(query))


if __name__ == "__main__":
    unittest.main()
