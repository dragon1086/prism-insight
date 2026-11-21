import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from telegram.error import NetworkError, TimedOut

from messaging.telegram_delivery import (
    TelegramDeliveryUnknown,
    send_message_once_or_rate_retry,
)


@pytest.fixture(autouse=True)
def isolated_spool(monkeypatch, tmp_path):
    path = tmp_path / "events.jsonl"
    monkeypatch.setenv("PRISM_OBSERVABILITY_SPOOL", str(path))
    return path


@pytest.mark.asyncio
async def test_unknown_has_safe_type_and_fingerprint_without_retry(isolated_spool, caplog):
    error = TimedOut("https://api.telegram.org/botSECRET private-message private-chat")
    bot = SimpleNamespace(send_message=AsyncMock(side_effect=error))
    with pytest.raises(TelegramDeliveryUnknown) as raised:
        await send_message_once_or_rate_retry(bot, chat_id="private-chat", text="private-message", message_kind="analysis")
    assert bot.send_message.await_count == 1
    assert raised.value.category == "timeout"
    assert len(raised.value.message_ref) == 24
    raw = isolated_spool.read_text()
    attrs = json.loads(raw)["attributes"]
    assert attrs["status"] == "UNKNOWN" and attrs["message_kind"] == "analysis"
    for secret in ("SECRET", "private-chat", "private-message", "https://"):
        assert secret not in raw + caplog.text + str(raised.value)


@pytest.mark.asyncio
async def test_allowlisted_transport_cause(isolated_spool):
    import httpx
    error = NetworkError("untrusted-detail")
    error.__cause__ = httpx.ReadTimeout("SECRET URL")
    bot = SimpleNamespace(send_message=AsyncMock(side_effect=error))
    with pytest.raises(TelegramDeliveryUnknown) as raised:
        await send_message_once_or_rate_retry(bot, chat_id=1, text="text")
    assert raised.value.category == "read_timeout"
    assert bot.send_message.await_count == 1


@pytest.mark.asyncio
async def test_ack_fingerprint_and_id(isolated_spool):
    sent = SimpleNamespace(message_id=51)
    bot = SimpleNamespace(send_message=AsyncMock(return_value=sent))
    assert await send_message_once_or_rate_retry(bot, chat_id=1, text="text", message_kind=[]) is sent
    attrs = json.loads(isolated_spool.read_text())["attributes"]
    assert attrs["status"] == "ACKNOWLEDGED" and attrs["message_id"] == 51
    assert attrs["message_kind"] == "unknown"


@pytest.mark.asyncio
async def test_telemetry_failure_preserves_ack_and_unknown(monkeypatch):
    def fail(*args, **kwargs):
        raise OSError("secret-persistence-detail")
    monkeypatch.setattr("observability.events.emit_event", fail)
    sent = SimpleNamespace(message_id=1)
    bot = SimpleNamespace(send_message=AsyncMock(return_value=sent))
    assert await send_message_once_or_rate_retry(bot, chat_id=1, text="text") is sent
    bot.send_message = AsyncMock(side_effect=NetworkError("secret-network-detail"))
    with pytest.raises(TelegramDeliveryUnknown):
        await send_message_once_or_rate_retry(bot, chat_id=1, text="text")
    assert bot.send_message.await_count == 1


@pytest.mark.asyncio
async def test_new_caller_with_old_loaded_helper_does_not_leak_metadata(monkeypatch):
    import sys

    import telegram_bot_agent as caller

    async def legacy_helper(bot, **kwargs):
        assert "message_kind" not in kwargs
        return SimpleNamespace(message_id=1)

    monkeypatch.setattr(caller, "send_message_once_or_rate_retry", legacy_helper)
    monkeypatch.setitem(sys.modules, "firebase_bridge", SimpleNamespace(notify=AsyncMock()))
    agent = caller.TelegramBotAgent.__new__(caller.TelegramBotAgent)
    agent.bot = object()
    assert await agent.send_message("chat", "message", msg_type="analysis") is True


@pytest.mark.asyncio
async def test_broadcast_resends_one_unknown_then_stops(isolated_spool):
    # es channel read timeouts, 2026-10-29: translated broadcasts prefer a rare duplicate to a miss.
    sent = SimpleNamespace(message_id=7)
    bot = SimpleNamespace(send_message=AsyncMock(side_effect=[TimedOut("x"), sent]))
    assert await send_message_once_or_rate_retry(bot, chat_id=1, text="t", resend_unknown_once=True) is sent
    assert bot.send_message.await_count == 2

    bot = SimpleNamespace(send_message=AsyncMock(side_effect=[TimedOut("x"), TimedOut("x"), sent]))
    with pytest.raises(TelegramDeliveryUnknown):
        await send_message_once_or_rate_retry(bot, chat_id=1, text="t", resend_unknown_once=True)
    assert bot.send_message.await_count == 2


@pytest.mark.asyncio
async def test_bot_agent_resends_only_broadcast_sends(isolated_spool):
    # en channel US summary read_error, 2026-10-29 23:26.
    from telegram_bot_agent import TelegramBotAgent

    agent = TelegramBotAgent.__new__(TelegramBotAgent)
    sent = SimpleNamespace(message_id=7)
    agent.bot = SimpleNamespace(send_message=AsyncMock(side_effect=[TimedOut("x"), sent]))
    assert await agent.send_message(1, "t", broadcast=True) is True
    assert agent.bot.send_message.await_count == 2

    agent.bot = SimpleNamespace(send_message=AsyncMock(side_effect=[TimedOut("x"), sent]))
    assert await agent.send_message(1, "t") is False
    assert agent.bot.send_message.await_count == 1


@pytest.mark.asyncio
async def test_markdown_parse_rejection_retried_as_plain_text_is_a_warning(isolated_spool, caplog):
    # US afternoon es/en summaries, 2026-10-30 03:46: plain-text retry delivered, alert was noise.
    from telegram.error import BadRequest
    from telegram_bot_agent import TelegramBotAgent

    agent = TelegramBotAgent.__new__(TelegramBotAgent)
    agent.bot = SimpleNamespace(send_message=AsyncMock(side_effect=[
        BadRequest("Can't parse entities: can't find end of the entity"), SimpleNamespace(message_id=7)]))
    assert await agent.send_message(1, "*t") is True
    assert not [r for r in caplog.records if r.levelname == "ERROR"]
