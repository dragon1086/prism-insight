"""Subscriber skips signals that sat in Pub/Sub past the age cutoff.

2026-10-03: the Mac mini subscriber had been down since a 9/16 reboot. Pub/Sub keeps
unacked messages for 7 days, so a restart would have replayed the 9/30 NVDA BUY and the
RF머트리얼즈 BUY/SELL pair on a real account, unordered and at stale prices.
"""
import importlib
from datetime import datetime, timedelta, timezone

SUB_MOD = "examples.messaging.gcp_pubsub_subscriber_example"


def _sub():
    return importlib.import_module(SUB_MOD)


NOW = datetime(2026, 10, 3, 3, 0, tzinfo=timezone.utc)


def test_old_signal_is_stale_by_default(monkeypatch):
    monkeypatch.delenv("SUBSCRIBER_MAX_SIGNAL_AGE_MINUTES", raising=False)
    sub = _sub()
    age = sub._stale_signal_age(NOW - timedelta(days=2, hours=12), now=NOW)
    assert age is not None and age > 3000


def test_fresh_signal_is_processed(monkeypatch):
    monkeypatch.delenv("SUBSCRIBER_MAX_SIGNAL_AGE_MINUTES", raising=False)
    sub = _sub()
    assert sub._stale_signal_age(NOW - timedelta(minutes=5), now=NOW) is None
    assert sub._stale_signal_age(None, now=NOW) is None


def test_cutoff_is_configurable_and_can_be_disabled(monkeypatch):
    sub = _sub()
    monkeypatch.setenv("SUBSCRIBER_MAX_SIGNAL_AGE_MINUTES", "120")
    assert sub._stale_signal_age(NOW - timedelta(minutes=90), now=NOW) is None
    assert sub._stale_signal_age(NOW - timedelta(minutes=150), now=NOW) is not None
    monkeypatch.setenv("SUBSCRIBER_MAX_SIGNAL_AGE_MINUTES", "off")
    assert sub._stale_signal_age(NOW - timedelta(days=5), now=NOW) is None
    monkeypatch.setenv("SUBSCRIBER_MAX_SIGNAL_AGE_MINUTES", "garbage")
    assert sub._max_signal_age_minutes() == sub.DEFAULT_MAX_SIGNAL_AGE_MINUTES
