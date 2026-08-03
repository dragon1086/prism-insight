"""Gateway → command → reply wiring.

The property that matters most here is that a redelivered event does not run
its command again. Persisting an event is idempotent; placing an analysis job
and answering the room are not.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from kakao_bot.adapters.kakao.gateway_inbound_handler import GatewayDispatchHandler
from kakao_bot.adapters.kakao.gateway_protocol import GatewayDispatch
from kakao_bot.adapters.kakao.message_command_handler import MessageCommandHandler
from kakao_bot.adapters.persistence.sqlite import SQLiteKakaoRepository
from kakao_bot.application.command_service import (
    CommandOutcome,
    CommandOutcomeKind,
)
from kakao_bot.application.gateway_inbound_service import GatewayInboundService
from kakao_bot.domain.models import InboundMessage, MessageSendResult

NOW = datetime(2026, 7, 28, 5, 0, tzinfo=timezone.utc)
ROOM = "bgk_room"


def dispatch(sequence: int, *, event_id: int, content: str) -> GatewayDispatch:
    return GatewayDispatch(
        sequence=sequence,
        event_type="MESSAGE_CREATE",
        data={
            "id": event_id,
            "isChannelChatroom": False,
            "botGroupKey": ROOM,
            "botUserKey": "buk_user",
            "content": content,
            "timestamp": "2026-07-28T05:00:00.000000000Z",
            "callbackToken": "cb-token",
        },
    )


class SpyMessageHandler:
    def __init__(self) -> None:
        self.seen: list[InboundMessage] = []

    async def __call__(self, message: InboundMessage) -> None:
        self.seen.append(message)


class FakeCommandService:
    def __init__(self, outcome: CommandOutcome) -> None:
        self.outcome = outcome
        self.calls: list[InboundMessage] = []

    def handle(self, message: InboundMessage, *, now=None) -> CommandOutcome:
        self.calls.append(message)
        return self.outcome


class SpyCallbackSender:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    async def callback(self, callback_token: str, skill_response: dict):
        self.calls.append((callback_token, skill_response))
        return MessageSendResult(success=True, status_code=200)


def message(text: str = "리포트 삼성전자") -> InboundMessage:
    return InboundMessage(
        event_id="evt-1",
        sequence=1,
        room_id=ROOM,
        user_id="buk_user",
        nickname=None,
        text=text,
        callback_token="cb-token",
        occurred_at=NOW,
    )


@pytest.mark.asyncio
async def test_fresh_message_reaches_the_command_handler(tmp_path):
    with SQLiteKakaoRepository(tmp_path / "kakao.sqlite") as repository:
        spy = SpyMessageHandler()
        handler = GatewayDispatchHandler(
            GatewayInboundService(repository),
            message_handler=spy,
        )

        await handler(dispatch(1, event_id=1, content="리포트 삼성전자"))

        assert [m.text for m in spy.seen] == ["리포트 삼성전자"]


@pytest.mark.asyncio
async def test_redelivered_event_does_not_run_the_command_twice(tmp_path):
    with SQLiteKakaoRepository(tmp_path / "kakao.sqlite") as repository:
        spy = SpyMessageHandler()
        handler = GatewayDispatchHandler(
            GatewayInboundService(repository),
            message_handler=spy,
        )
        frame = dispatch(1, event_id=1, content="리포트 삼성전자")

        await handler(frame)
        # RESUME can replay the same event under a different sequence number.
        await handler(dispatch(99, event_id=1, content="리포트 삼성전자"))

        assert len(spy.seen) == 1


@pytest.mark.asyncio
async def test_lifecycle_events_never_reach_the_command_handler(tmp_path):
    with SQLiteKakaoRepository(tmp_path / "kakao.sqlite") as repository:
        spy = SpyMessageHandler()
        handler = GatewayDispatchHandler(
            GatewayInboundService(repository),
            message_handler=spy,
        )

        await handler(
            GatewayDispatch(
                sequence=1,
                event_type="ENTRANCE",
                data={
                    "botGroupKey": ROOM,
                    "inviter": {"botUserKey": "buk_user"},
                    "params": {},
                    "timestamp": "2026-07-28T05:00:00.000000000Z",
                },
            )
        )

        assert spy.seen == []


@pytest.mark.asyncio
async def test_receive_only_gateway_still_persists(tmp_path):
    with SQLiteKakaoRepository(tmp_path / "kakao.sqlite") as repository:
        handler = GatewayDispatchHandler(GatewayInboundService(repository))

        await handler(dispatch(1, event_id=1, content="리포트 삼성전자"))

        assert repository.get_room(ROOM) is not None


@pytest.mark.asyncio
async def test_accepted_command_is_answered_through_the_callback():
    service = FakeCommandService(
        CommandOutcome(kind=CommandOutcomeKind.ACCEPTED, message="분석을 시작했습니다.")
    )
    sender = SpyCallbackSender()

    await MessageCommandHandler(service, sender)(message())

    [(token, response)] = sender.calls
    assert token == "cb-token"
    text = response["template"]["outputs"][0]["simpleText"]["text"]
    assert "분석을 시작했습니다." in text


@pytest.mark.asyncio
async def test_ignored_outcome_produces_no_reply():
    service = FakeCommandService(CommandOutcome(kind=CommandOutcomeKind.IGNORED))
    sender = SpyCallbackSender()

    await MessageCommandHandler(service, sender)(message("아무말"))

    assert sender.calls == []


@pytest.mark.asyncio
async def test_missing_callback_token_is_survivable():
    service = FakeCommandService(
        CommandOutcome(kind=CommandOutcomeKind.ACCEPTED, message="ok")
    )
    sender = SpyCallbackSender()
    without_token = InboundMessage(
        event_id="evt-2",
        sequence=2,
        room_id=ROOM,
        user_id="buk_user",
        nickname=None,
        text="리포트 삼성전자",
        callback_token=None,
        occurred_at=NOW,
    )

    await MessageCommandHandler(service, sender)(without_token)

    assert sender.calls == []


@pytest.mark.asyncio
async def test_command_failure_does_not_propagate_and_break_the_session():
    class Exploding:
        def handle(self, message, *, now=None):
            raise RuntimeError("boom")

    sender = SpyCallbackSender()

    await MessageCommandHandler(Exploding(), sender)(message())

    assert sender.calls == []


@pytest.mark.asyncio
async def test_help_reply_includes_a_tappable_card():
    service = FakeCommandService(
        CommandOutcome(kind=CommandOutcomeKind.HELP, message="사용법")
    )
    sender = SpyCallbackSender()

    await MessageCommandHandler(service, sender)(message("도움말"))

    [(_, response)] = sender.calls
    outputs = response["template"]["outputs"]
    assert any("listCard" in bubble for bubble in outputs)
