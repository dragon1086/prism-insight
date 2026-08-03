"""Tests for the fail-closed trading-signal publish guard.

Regression cover for the 2026-07 incident: running the test suite broadcast
synthetic fixture sells (buy 100.00 / sell 92.00 / TIER1_ABS7, tickers 005930
and AAPL) onto the LIVE public Pub/Sub topic that real mirroring subscribers
trade on. 44 signals reached production between 07-11 and 07-29.

The invariant these tests protect: **no test run may open a real signal
transport, even with full production credentials present in the environment.**
"""
import asyncio
import os
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

from messaging.publish_guard import (  # noqa: E402
    DISABLE_ENV_VAR,
    block_reason,
    signal_publishing_disabled,
)


class TestGuardPredicate:
    def test_disabled_while_running_under_pytest(self):
        """PYTEST_CURRENT_TEST is set by pytest around every test — the guard
        must trip on it with no other configuration."""
        assert os.environ.get("PYTEST_CURRENT_TEST")
        assert signal_publishing_disabled() is True

    def test_conftest_set_the_kill_switch(self):
        """The repo-root conftest must have armed the switch at import time,
        which is what covers collection and module-level code."""
        assert os.environ.get(DISABLE_ENV_VAR) == "1"

    def test_explicit_env_var_alone_is_sufficient(self, monkeypatch):
        """Outside pytest, the operator kill switch alone must block."""
        monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
        monkeypatch.setenv(DISABLE_ENV_VAR, "1")
        assert signal_publishing_disabled() is True

    @pytest.mark.parametrize("value", ["1", "true", "TRUE", "yes", "on", " on "])
    def test_truthy_spellings(self, monkeypatch, value):
        monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
        monkeypatch.setenv(DISABLE_ENV_VAR, value)
        assert signal_publishing_disabled() is True

    @pytest.mark.parametrize("value", ["", "0", "false", "no", "off"])
    def test_production_is_not_blocked(self, monkeypatch, value):
        """Critical: the guard must NOT disable publishing in production."""
        monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
        monkeypatch.setenv(DISABLE_ENV_VAR, value)
        assert signal_publishing_disabled() is False

    def test_unset_environment_is_not_blocked(self, monkeypatch):
        monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
        monkeypatch.delenv(DISABLE_ENV_VAR, raising=False)
        assert signal_publishing_disabled() is False

    def test_block_reason_is_informative(self):
        assert "pytest" in block_reason()


class TestPublishersRefuseToConnect:
    """The transports must stay unconnected even with valid-looking creds."""

    def test_gcp_publisher_refuses_with_production_credentials(self, monkeypatch):
        from messaging.gcp_pubsub_signal_publisher import SignalPublisher

        monkeypatch.setenv("GCP_PROJECT_ID", "prism-prod")
        monkeypatch.setenv("GCP_PUBSUB_TOPIC_ID", "prism-trading-signals")

        pub = SignalPublisher()
        assert pub.project_id == "prism-prod"  # creds really are present
        asyncio.run(pub.connect())

        assert pub._is_connected() is False, "GCP client was built during a test"
        assert pub._publisher is None

    def test_redis_publisher_refuses_with_production_credentials(self, monkeypatch):
        from messaging.redis_signal_publisher import SignalPublisher

        monkeypatch.setenv("UPSTASH_REDIS_REST_URL", "https://example.upstash.io")
        monkeypatch.setenv("UPSTASH_REDIS_REST_TOKEN", "token")

        pub = SignalPublisher()
        assert pub.redis_url  # creds really are present
        asyncio.run(pub.connect())

        assert pub._redis is None, "Redis client was built during a test"

    def test_gcp_publish_is_a_noop_when_unconnected(self, monkeypatch):
        """Even if something calls publish directly, nothing leaves."""
        from messaging.gcp_pubsub_signal_publisher import SignalPublisher

        monkeypatch.setenv("GCP_PROJECT_ID", "prism-prod")
        pub = SignalPublisher()
        asyncio.run(pub.connect())

        message_id = asyncio.run(
            pub.publish_signal(
                signal_type="SELL",
                ticker="005930",
                company_name="005930",
                price=92.0,
            )
        )
        assert message_id is None


class TestEndToEndBroadcastIsBlocked:
    def test_publish_loop_sell_emits_nothing(self):
        """sell_broadcast.publish_loop_sell is the loops' broadcast entry point.

        This reproduces the exact shape of the leaked signal (005930, buy 100,
        sell 92, TIER1_ABS7) and asserts it cannot reach a transport.
        """
        import sell_broadcast

        # Must not raise, and must not construct any transport client.
        asyncio.run(
            sell_broadcast.publish_loop_sell(
                market="KR",
                ticker="005930",
                company_name="005930",
                price=92.0,
                buy_price=100.0,
                sell_reason="TIER1_ABS7: loss -8.00% <= -7.0%",
            )
        )

        from messaging.gcp_pubsub_signal_publisher import SignalPublisher as GcpPub
        from messaging.redis_signal_publisher import SignalPublisher as RedisPub

        gcp = GcpPub()
        asyncio.run(gcp.connect())
        assert gcp._is_connected() is False

        redis_pub = RedisPub()
        asyncio.run(redis_pub.connect())
        assert redis_pub._redis is None
