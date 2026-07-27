"""Regression tests pinned to a real Kakao Gateway MESSAGE_CREATE payload.

Captured from a live channel chatroom on 2026-07-27 during Phase 0 contract
verification. The bot group/user keys are anonymized but keep the real shape
and length; every other field is reproduced exactly as Kakao sent it.

The captured payload broke two assumptions in the original mapper:

1. ``id`` arrives as a JSON **number**, not a string.
2. ``MESSAGE_CREATE`` carries **no** ``userKey`` or ``sender`` object — the only
   user-scoped identifier is ``botUserKey``.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from kakao_bot.adapters.kakao.event_mapper import (
    GatewayEventMappingError,
    map_gateway_dispatch,
)
from kakao_bot.adapters.kakao.gateway_inbound_handler import GatewayDispatchHandler
from kakao_bot.adapters.kakao.gateway_protocol import GatewayDispatch
from kakao_bot.adapters.persistence.sqlite import SQLiteKakaoRepository
from kakao_bot.application.gateway_inbound_service import GatewayInboundService
from kakao_bot.domain.models import InboundMessage, RoomLifecycleType

BOT_GROUP_KEY = "a1b2c3d4e5f60718293a4b5c6d7e8f90a1b2c3d4e5f60718293a4b5c6d7e8f9012"
BOT_USER_KEY = "0f1e2d3c4b5a69788796a5b4c3d2e1f00f1e2d3c4b5a69788796a5b4c3d2e1f012"

LIVE_MESSAGE_CREATE = {
    "id": 869509512613945331,
    "isChannelChatroom": True,
    "botGroupKey": BOT_GROUP_KEY,
    "botUserKey": BOT_USER_KEY,
    "content": "안녕",
    "timestamp": "2026-07-27T09:20:22.512392989Z",
    "callbackToken": "callback-secret",
    "type": "MESSAGE_CREATE",
}


def _dispatch(**overrides: object) -> GatewayDispatch:
    data = dict(LIVE_MESSAGE_CREATE)
    data.update(overrides)
    return GatewayDispatch(sequence=1, event_type="MESSAGE_CREATE", data=data)


def test_live_channel_chat_payload_maps_completely():
    event = map_gateway_dispatch(_dispatch())

    assert event == InboundMessage(
        event_id="869509512613945331",
        sequence=1,
        room_id=BOT_GROUP_KEY,
        user_id=BOT_USER_KEY,
        nickname=None,
        text="안녕",
        callback_token="callback-secret",
        occurred_at=datetime(
            2026, 7, 27, 9, 20, 22, 512392, tzinfo=timezone.utc
        ),
    )
    assert "callback-secret" not in repr(event)


def test_numeric_event_id_is_normalized_to_string():
    assert map_gateway_dispatch(_dispatch()).event_id == "869509512613945331"


def test_boolean_is_never_accepted_as_an_identifier():
    with pytest.raises(GatewayEventMappingError, match="event id"):
        map_gateway_dispatch(_dispatch(id=True))


def test_explicit_user_key_wins_over_bot_user_key():
    event = map_gateway_dispatch(_dispatch(userKey="user-42"))

    assert event.user_id == "user-42"


def test_nanosecond_precision_timestamp_is_truncated_to_microseconds():
    event = map_gateway_dispatch(_dispatch())

    assert event.occurred_at.microsecond == 512392
    assert event.occurred_at.tzinfo is timezone.utc


def test_entrance_without_id_gets_a_deterministic_synthesized_event_id():
    """Kakao omits ``id`` on ENTRANCE (observed live 2026-07-27)."""

    data = {
        "botGroupKey": BOT_GROUP_KEY,
        "timestamp": "2026-07-27T09:49:28.641000000Z",
        "type": "ENTRANCE",
    }
    first = map_gateway_dispatch(
        GatewayDispatch(sequence=2, event_type="ENTRANCE", data=data)
    )
    # A RESUME redelivery arrives with a different sequence number.
    replayed = map_gateway_dispatch(
        GatewayDispatch(sequence=99, event_type="ENTRANCE", data=data)
    )

    assert first.room_id == BOT_GROUP_KEY
    assert first.event_type is RoomLifecycleType.ENTRANCE
    assert first.event_id == replayed.event_id, "redelivery must deduplicate"
    assert BOT_GROUP_KEY in first.event_id


def test_explicit_lifecycle_id_still_wins_over_synthesis():
    event = map_gateway_dispatch(
        GatewayDispatch(
            sequence=2,
            event_type="LEAVE",
            data={
                "id": "entrance-real-id",
                "botGroupKey": BOT_GROUP_KEY,
                "timestamp": "2026-07-27T09:49:28Z",
            },
        )
    )

    assert event.event_id == "entrance-real-id"


@pytest.mark.asyncio
async def test_handler_skips_unmappable_event_without_breaking_connection(
    tmp_path,
):
    """A malformed event must not propagate and tear down the Gateway."""

    with SQLiteKakaoRepository(tmp_path / "kakao.sqlite") as repository:
        handler = GatewayDispatchHandler(GatewayInboundService(repository))
        unmappable = GatewayDispatch(
            sequence=2,
            event_type="MESSAGE_CREATE",
            data={"content": "no id, no room, no user"},
        )

        await handler(unmappable)

        assert not repository.list_rooms()


@pytest.mark.asyncio
async def test_handler_persists_the_live_payload(tmp_path):
    with SQLiteKakaoRepository(tmp_path / "kakao.sqlite") as repository:
        handler = GatewayDispatchHandler(GatewayInboundService(repository))

        await handler(_dispatch())

        assert repository.get_room(BOT_GROUP_KEY) is not None
