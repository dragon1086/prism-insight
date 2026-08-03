from __future__ import annotations

from datetime import datetime, timezone

import pytest

from kakao_bot.adapters.kakao.event_mapper import (
    GatewayEventMappingError,
    map_gateway_dispatch,
)
from kakao_bot.adapters.kakao.gateway_protocol import GatewayDispatch
from kakao_bot.adapters.persistence.sqlite import (
    SQLiteGatewayStateStore,
    SQLiteKakaoRepository,
)
from kakao_bot.application.gateway_inbound_service import GatewayInboundService
from kakao_bot.domain.models import (
    ApprovalStatus,
    InboundMessage,
    RoomLifecycleEvent,
    RoomLifecycleType,
)
from kakao_bot.ports.gateway_state import GatewayState

OCCURRED_AT = datetime(2026, 7, 23, 5, 0, tzinfo=timezone.utc)


def test_message_create_maps_to_transport_neutral_inbound_message():
    dispatch = GatewayDispatch(
        sequence=17,
        event_type="MESSAGE_CREATE",
        data={
            "id": "event-17",
            "botGroupKey": "room-1",
            "userKey": "user-1",
            "nickname": "테스터",
            "content": {"text": "/report 005930"},
            "callbackToken": "callback-secret",
            "timestamp": 1_784_782_800_000,
        },
    )

    event = map_gateway_dispatch(dispatch)

    assert event == InboundMessage(
        event_id="event-17",
        sequence=17,
        room_id="room-1",
        user_id="user-1",
        nickname="테스터",
        text="/report 005930",
        callback_token="callback-secret",
        occurred_at=OCCURRED_AT,
    )
    assert "callback-secret" not in repr(event)


@pytest.mark.parametrize(
    ("event_type", "expected_type"),
    [
        ("ENTRANCE", RoomLifecycleType.ENTRANCE),
        ("CHAT_JOIN", RoomLifecycleType.CHAT_JOIN),
        ("LEAVE", RoomLifecycleType.LEAVE),
    ],
)
def test_room_lifecycle_dispatches_are_mapped(event_type, expected_type):
    event = map_gateway_dispatch(
        GatewayDispatch(
            sequence=18,
            event_type=event_type,
            data={
                "id": f"event-{event_type.lower()}",
                "botGroupKey": "room-1",
                "timestamp": "2026-07-23T05:00:00Z",
            },
        )
    )

    assert event == RoomLifecycleEvent(
        event_id=f"event-{event_type.lower()}",
        sequence=18,
        room_id="room-1",
        event_type=expected_type,
        occurred_at=OCCURRED_AT,
    )


def test_mapper_rejects_known_event_without_required_room_or_user():
    with pytest.raises(GatewayEventMappingError, match="room key"):
        map_gateway_dispatch(
            GatewayDispatch(
                sequence=1,
                event_type="ENTRANCE",
                data={"id": "event-1"},
            )
        )

    with pytest.raises(GatewayEventMappingError, match="user key"):
        map_gateway_dispatch(
            GatewayDispatch(
                sequence=2,
                event_type="MESSAGE_CREATE",
                data={
                    "id": "event-2",
                    "botGroupKey": "room-1",
                    "content": "hello",
                },
            )
        )


def test_gateway_inbound_service_atomically_discovers_and_deduplicates_room(
    tmp_path,
):
    with SQLiteKakaoRepository(tmp_path / "kakao.sqlite") as repository:
        service = GatewayInboundService(repository)
        event = RoomLifecycleEvent(
            event_id="entrance-1",
            sequence=1,
            room_id="room-1",
            event_type=RoomLifecycleType.ENTRANCE,
            occurred_at=OCCURRED_AT,
        )

        assert service.handle(event) is True
        assert service.handle(event) is False
        assert repository.get_room("room-1").approval_status is ApprovalStatus.PENDING


def test_leave_revokes_room_and_cancels_pending_outbox(tmp_path):
    with SQLiteKakaoRepository(tmp_path / "kakao.sqlite") as repository:
        service = GatewayInboundService(repository)
        service.handle(
            RoomLifecycleEvent(
                event_id="entrance-1",
                sequence=1,
                room_id="room-1",
                event_type=RoomLifecycleType.ENTRANCE,
                occurred_at=OCCURRED_AT,
            )
        )
        repository.set_room_approval("room-1", ApprovalStatus.APPROVED)
        from kakao_bot.domain.models import OutboundDelivery

        assert repository.enqueue_outbound(
            OutboundDelivery(
                delivery_key="pending-before-leave",
                room_id="room-1",
                message_type="test",
                payload={"version": "2.0"},
            )
        )

        assert service.handle(
            RoomLifecycleEvent(
                event_id="leave-1",
                sequence=2,
                room_id="room-1",
                event_type=RoomLifecycleType.LEAVE,
                occurred_at=OCCURRED_AT,
            )
        )

        assert repository.get_room("room-1").approval_status is ApprovalStatus.REJECTED
        [delivery] = repository.list_outbox()
        assert delivery["status"] == "DEAD"
        assert delivery["last_error"] == "room approval revoked"


@pytest.mark.asyncio
async def test_gateway_state_is_persisted_across_repository_instances(tmp_path):
    database_path = tmp_path / "kakao.sqlite"
    with SQLiteKakaoRepository(database_path) as repository:
        state_store = SQLiteGatewayStateStore(repository)
        assert await state_store.load() == GatewayState()
        await state_store.save(GatewayState(session_id="session-1", sequence=44))

    with SQLiteKakaoRepository(database_path) as reopened:
        state_store = SQLiteGatewayStateStore(reopened)
        assert await state_store.load() == GatewayState(
            session_id="session-1",
            sequence=44,
        )
        await state_store.clear()
        assert await state_store.load() == GatewayState()
