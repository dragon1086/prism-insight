from __future__ import annotations

import pytest

from kakao_bot.domain.models import MessageSendResult
from kakao_bot.runtime.rest_smoke_main import RestSmokeConfig, run_smoke


class FakeSender:
    def __init__(self) -> None:
        self.calls = []

    async def send_message(self, room_id, skill_response):
        self.calls.append((room_id, skill_response))
        return MessageSendResult(success=True, status_code=200)


@pytest.mark.asyncio
async def test_rest_smoke_uses_injected_sender_without_exposing_token():
    sender = FakeSender()
    config = RestSmokeConfig(
        token="secret-token",
        room_id="room-1",
        message="연결 확인",
    )

    result = await run_smoke(config, sender=sender)

    assert result.success is True
    [(room_id, response)] = sender.calls
    assert room_id == "room-1"
    assert response["template"]["outputs"][0]["simpleText"]["text"] == ("연결 확인")
    assert "secret-token" not in repr(config)
