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
