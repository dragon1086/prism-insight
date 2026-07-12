"""Regression tests for synchronous Redis work exposed through async APIs."""

import asyncio
import json
import threading
import unittest
from unittest.mock import patch

from messaging.redis_health_check import run_health_check_async
from messaging.redis_signal_publisher import SignalPublisher


class _ThreadRecordingRedis:
    def __init__(self, *, error=None):
        self.error = error
        self.thread_id = None
        self.args = None

    def xadd(self, *args):
        self.thread_id = threading.get_ident()
        self.args = args
        if self.error:
            raise self.error
        return "123-0"


class RedisAsyncBoundaryTests(unittest.TestCase):
    def test_publish_signal_runs_xadd_off_event_loop(self):
        redis = _ThreadRecordingRedis()
        publisher = SignalPublisher(redis_url="https://example.invalid", redis_token="token")
        publisher._redis = redis
        event_loop_thread = threading.get_ident()

        message_id = asyncio.run(
            publisher.publish_signal("SELL", "AAPL", "Apple", 210.0)
        )

        self.assertEqual(message_id, "123-0")
        self.assertNotEqual(redis.thread_id, event_loop_thread)
        self.assertEqual(redis.args[:2], ("prism:trading-signals", "*"))
        payload = json.loads(redis.args[2]["data"])
        self.assertEqual(payload["type"], "SELL")
        self.assertEqual(payload["ticker"], "AAPL")

    def test_publish_signal_preserves_failure_contract(self):
        redis = _ThreadRecordingRedis(error=RuntimeError("Redis unavailable"))
        publisher = SignalPublisher(redis_url="https://example.invalid", redis_token="token")
        publisher._redis = redis

        self.assertIsNone(
            asyncio.run(publisher.publish_signal("BUY", "005930", "Samsung", 82000))
        )

    def test_async_health_check_runs_sync_client_off_event_loop(self):
        worker_thread = None

        def fake_health_check():
            nonlocal worker_thread
            worker_thread = threading.get_ident()
            return {"success": True}

        event_loop_thread = threading.get_ident()
        with patch("messaging.redis_health_check.run_health_check", fake_health_check):
            result = asyncio.run(run_health_check_async())

        self.assertEqual(result, {"success": True})
        self.assertNotEqual(worker_thread, event_loop_thread)


if __name__ == "__main__":
    unittest.main()
