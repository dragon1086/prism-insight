"""Private operations transport must never invoke the public fanout sender."""
import asyncio
import builtins
from types import SimpleNamespace

import pytest
from live import healthcheck, ops_alerts, swing, telegram_reporter, tracking


@pytest.fixture(autouse=True)
def private_environment(monkeypatch):
    import os
    for key in list(os.environ):
        if "CHANNEL_ID" in key or key in ("TELEGRAM_BOT_TOKEN", "OAUTH_ALERT_BOT_TOKEN", "OAUTH_ALERT_CHAT_ID"):
            monkeypatch.delenv(key)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test-token")
    monkeypatch.setenv("BTC_OPS_CHANNEL_ID", "-100900")
    monkeypatch.setenv("TELEGRAM_CHANNEL_ID", "-100100")
    monkeypatch.setattr(healthcheck, "_load_env", lambda: None)

    def forbidden(*args, **kwargs):
        pytest.fail("public fanout must never be called")
    monkeypatch.setattr(telegram_reporter, "_send", forbidden)
    original_import = builtins.__import__

    def guarded_import(name, *args, **kwargs):
        if name in ("tracking.telegram", "firebase_bridge"):
            pytest.fail(f"forbidden public dependency: {name}")
        return original_import(name, *args, **kwargs)
    monkeypatch.setattr(builtins, "__import__", guarded_import)


def test_private_direct_bot_one_delivery():
    calls = []

    class Bot:
        def __init__(self, *, token):
            assert token == "test-token"

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def send_message(self, **kwargs):
            calls.append(kwargs)
            return SimpleNamespace(message_id=72)

    assert asyncio.run(ops_alerts._send(
        "test-token", ops_alerts._resolve_ops_channel(), "private warning", bot_factory=Bot)) is True
    assert calls == [{"chat_id": "-100900", "text": "private warning"}]


@pytest.mark.parametrize("key", ["TELEGRAM_CHANNEL_ID", "BTC_TELEGRAM_CHANNEL_ID",
                                  "TELEGRAM_CHANNEL_ID_EN", "TELEGRAM_CHANNEL_ID_JA"])
def test_public_id_rejected_before_bot_creation(monkeypatch, key):
    monkeypatch.setenv(key, " @PublicRoom ")
    monkeypatch.setenv("BTC_OPS_CHANNEL_ID", "@publicroom")
    assert ops_alerts._resolve_ops_channel() is None

    def forbidden_factory(**kwargs):
        pytest.fail("public destination must be rejected before constructing Bot")
    assert asyncio.run(ops_alerts._send(
        "test-token", "@publicroom", "private warning", bot_factory=forbidden_factory)) is False


def test_missing_ops_channel_cannot_fall_back_to_public(monkeypatch):
    monkeypatch.delenv("BTC_OPS_CHANNEL_ID")
    assert ops_alerts._resolve_ops_channel() is None
    assert healthcheck._dispatch("private warning") is False


def test_existing_monitoring_room_uses_its_own_bot(monkeypatch):
    monkeypatch.setenv("OAUTH_ALERT_BOT_TOKEN", "monitor-token")
    monkeypatch.setenv("OAUTH_ALERT_CHAT_ID", "-100700")
    calls = []

    async def capture(token, channel, message):
        calls.append((token, channel, message))
        return True
    monkeypatch.setattr(healthcheck, "_send", capture)
    assert healthcheck._dispatch("private warning") is True
    assert calls == [("monitor-token", "-100700", "private warning")]


@pytest.mark.parametrize("token,channel", [("monitor-token", ""), ("", "-100700"),
                                          ("monitor-token", "-100100")])
def test_partial_or_public_monitoring_pair_fails_closed(monkeypatch, token, channel):
    monkeypatch.setenv("OAUTH_ALERT_BOT_TOKEN", token)
    monkeypatch.setenv("OAUTH_ALERT_CHAT_ID", channel)
    assert ops_alerts._resolve_ops_destination() == (None, None)
    assert healthcheck._dispatch("private warning") is False


@pytest.mark.parametrize("token,channel", [(None, "-100900"), ("", "-100900"),
                                          ("test", None), ("test", " ")])
def test_missing_config_is_not_delivery(token, channel, capsys):
    assert asyncio.run(ops_alerts._send(token, channel, "private warning")) is False
    assert capsys.readouterr().out == ""


def test_missing_telegram_dependency_is_not_delivery(monkeypatch):
    original_import = builtins.__import__

    def unavailable(name, *args, **kwargs):
        if name == "telegram":
            raise ImportError("unavailable")
        return original_import(name, *args, **kwargs)
    monkeypatch.setattr(builtins, "__import__", unavailable)
    assert healthcheck._dispatch("private warning") is False


@pytest.mark.parametrize("outcome", ["timeout_after_accept", "no_receipt", "bad_receipt", "init_failure"])
def test_unknown_delivery_has_no_retry_or_fanout(outcome):
    calls = []

    class Bot:
        def __init__(self, **kwargs):
            if outcome == "init_failure":
                raise RuntimeError("init failed")

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def send_message(self, **kwargs):
            calls.append(kwargs)
            if outcome == "timeout_after_accept":
                raise TimeoutError("accepted but response lost")
            return None if outcome == "no_receipt" else SimpleNamespace(message_id=0)

    assert asyncio.run(ops_alerts._send("test", "-100900", "private warning", bot_factory=Bot)) is False
    assert len(calls) == (0 if outcome == "init_failure" else 1)


@pytest.mark.parametrize("kind", ["entry", "close"])
def test_old_public_marker_does_not_suppress_private_alert(tmp_path, monkeypatch, kind):
    conn = tracking.get_connection(tmp_path / "alerts.sqlite")
    tracking.ensure_schema(conn)
    pending = {"link_id": "exact", "status": "HALTED_SUBMISSION_UNKNOWN",
               "observation": "PARTIAL_UNKNOWN"}
    suffix = pending["observation"] if kind == "entry" else pending["status"]
    tracking.set_meta(conn, f"swing_{kind}_pending", pending, "swing")
    tracking.set_meta(conn, f"swing_{kind}_halt_notified", f"exact:{suffix}", "swing")
    messages = []
    monkeypatch.setattr(swing, "_notify", lambda *args: pytest.fail("public trade sender called"))
    monkeypatch.setattr(swing, "_notify_ops", lambda mode, msg: messages.append(msg) or True)
    notify = getattr(swing, f"_notify_unresolved_{kind}")
    notify(conn, "demo")
    notify(conn, "demo")
    assert len(messages) == 1
    assert tracking.get_meta(conn, f"swing_{kind}_halt_ops_notified", "swing") == f"exact:{suffix}"
    conn.close()


def test_failed_private_alert_does_not_mark_delivered(tmp_path, monkeypatch):
    conn = tracking.get_connection(tmp_path / "alerts.sqlite")
    tracking.ensure_schema(conn)
    tracking.set_meta(conn, "swing_entry_pending", {"link_id": "exact"}, "swing")
    monkeypatch.setattr(swing, "_notify_ops", lambda *args: False)
    swing._notify_unresolved_entry(conn, "demo")
    assert tracking.get_meta(conn, "swing_entry_halt_ops_notified", "swing") is None
    conn.close()


@pytest.mark.parametrize("daily,issues", [(True, []), (False, [{"level": "alert", "code": "test", "msg": "test"}])])
def test_health_sent_reflects_failed_dispatch(tmp_path, monkeypatch, daily, issues):
    conn = tracking.get_connection(tmp_path / "health.sqlite")
    tracking.ensure_schema(conn)
    monkeypatch.setattr(healthcheck, "run_healthcheck", lambda *args, **kwargs: issues)
    monkeypatch.setattr(healthcheck, "_dispatch", lambda message: False)
    assert healthcheck.notify_health(conn, daily=daily)["sent"] is False
    conn.close()
