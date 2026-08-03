"""What happens when the bot is added to a room.

Two things used to make the bot look broken to someone who had just invited
it: it said nothing at all, and the first command it was sent was refused
because the room sat in PENDING until an operator ran a CLI. Both are on the
very first impression, so both are pinned here.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from kakao_bot.adapters.kakao.delivery_renderer import render_delivery
from kakao_bot.adapters.persistence.sqlite import SQLiteKakaoRepository
from kakao_bot.application.gateway_inbound_service import (
    WELCOME,
    GatewayInboundService,
)
from kakao_bot.domain.models import (
    ApprovalStatus,
    ClaimedOutboundDelivery,
    RoomLifecycleEvent,
    RoomLifecycleType,
    RoomSubscription,
)

NOW = datetime(2026, 8, 3, 5, 0, tzinfo=timezone.utc)
ROOM = "room-1"


def entrance(room_id: str = ROOM, *, at: datetime = NOW) -> RoomLifecycleEvent:
    return RoomLifecycleEvent(
        event_id=f"ENTRANCE:{room_id}:{at.isoformat()}",
        sequence=1,
        room_id=room_id,
        event_type=RoomLifecycleType.ENTRANCE,
        occurred_at=at,
    )


def leave(room_id: str = ROOM, *, at: datetime = NOW) -> RoomLifecycleEvent:
    return RoomLifecycleEvent(
        event_id=f"LEAVE:{room_id}:{at.isoformat()}",
        sequence=2,
        room_id=room_id,
        event_type=RoomLifecycleType.LEAVE,
        occurred_at=at,
    )


@pytest.fixture
def repository(tmp_path):
    with SQLiteKakaoRepository(tmp_path / "kakao.sqlite") as repo:
        yield repo


class TestApprovalOnInvite:
    def test_an_invite_stays_pending_by_default(self, repository):
        # Real users: being added to a room is not consent to broadcast into it.
        GatewayInboundService(repository).handle(entrance())

        assert repository.get_room(ROOM).approval_status is ApprovalStatus.PENDING

    def test_auto_approve_makes_the_invite_the_approval(self, repository):
        GatewayInboundService(repository, auto_approve=True).handle(entrance())

        assert repository.get_room(ROOM).approval_status is ApprovalStatus.APPROVED

    def test_an_auto_approved_room_gets_the_same_profile_as_a_hand_approved_one(
        self, repository
    ):
        GatewayInboundService(repository, auto_approve=True).handle(entrance())

        # `set_room_approval` turns on KR afternoon and nothing else; an
        # auto-approved room must not end up subscribed differently.
        assert repository.list_delivery_targets(
            *_kr_afternoon()
        ) == (ROOM,)

    def test_a_pending_room_is_not_a_delivery_target(self, repository):
        GatewayInboundService(repository).handle(entrance())

        assert repository.list_delivery_targets(*_kr_afternoon()) == ()


class TestReInvite:
    def test_re_entrance_does_not_strip_an_existing_approval(self, repository):
        # Kakao redelivers ENTRANCE, and being kicked then re-added is normal.
        # This used to silently reset the room to PENDING: no error anywhere,
        # the bot simply started refusing every command.
        service = GatewayInboundService(repository)
        service.handle(entrance())
        repository.set_room_approval(ROOM, ApprovalStatus.APPROVED)

        service.handle(entrance(at=NOW + timedelta(days=1)))

        assert repository.get_room(ROOM).approval_status is ApprovalStatus.APPROVED

    def test_re_entrance_keeps_the_subscriptions_the_room_chose(self, repository):
        service = GatewayInboundService(repository)
        service.handle(entrance())
        repository.set_room_approval(ROOM, ApprovalStatus.APPROVED)
        repository.configure_subscription(
            RoomSubscription(room_id=ROOM, kr_afternoon=False, us_afternoon=True)
        )

        service.handle(entrance(at=NOW + timedelta(days=1)))

        targets = repository.list_delivery_targets(*_us_afternoon())
        assert targets == (ROOM,)

    def test_a_room_that_removed_the_bot_can_come_back(self, repository):
        service = GatewayInboundService(repository, auto_approve=True)
        service.handle(entrance())
        service.handle(leave(at=NOW + timedelta(hours=1)))
        assert repository.get_room(ROOM).approval_status is ApprovalStatus.REJECTED

        service.handle(entrance(at=NOW + timedelta(hours=2)))

        assert repository.get_room(ROOM).approval_status is ApprovalStatus.APPROVED


class TestWelcome:
    def test_an_approved_room_is_greeted_on_arrival(self, repository):
        GatewayInboundService(repository, auto_approve=True).handle(entrance())

        [delivery] = repository.list_outbox()
        assert delivery["message_type"] == WELCOME
        assert delivery["room_id"] == ROOM

    def test_a_pending_room_is_not_greeted(self, repository):
        # A greeting the next message refuses is worse than silence.
        GatewayInboundService(repository).handle(entrance())

        assert repository.list_outbox() == ()

    def test_redelivery_does_not_greet_twice(self, repository):
        service = GatewayInboundService(repository, auto_approve=True)
        event = entrance()

        service.handle(event)
        service.handle(event)

        assert len(repository.list_outbox()) == 1

    def test_greeting_can_be_switched_off(self, repository):
        GatewayInboundService(
            repository, auto_approve=True, greet_on_join=False
        ).handle(entrance())

        assert repository.list_outbox() == ()

    def test_a_message_event_never_greets(self, repository):
        from kakao_bot.domain.models import InboundMessage

        GatewayInboundService(repository, auto_approve=True).handle(
            InboundMessage(
                event_id="evt-1",
                sequence=3,
                room_id=ROOM,
                user_id="user-1",
                nickname=None,
                text="리포트 삼성전자",
                callback_token="cb",
                occurred_at=NOW,
            )
        )

        assert repository.list_outbox() == ()


class TestWelcomeRendering:
    def delivery(self) -> ClaimedOutboundDelivery:
        return ClaimedOutboundDelivery(
            delivery_key=f"welcome:{ROOM}",
            room_id=ROOM,
            message_type=WELCOME,
            payload={"room_id": ROOM},
            attempt_count=1,
            lease_owner="worker-1",
            lease_expires_at=NOW,
            created_at=NOW,
        )

    def test_the_greeting_says_what_the_bot_is_and_how_to_reach_it(self):
        response = render_delivery(self.delivery())

        text = response["template"]["outputs"][0]["simpleText"]["text"]
        assert "PRISM" in text
        # Group rooms only deliver mentioned messages, so this is the one thing
        # the room has to be told.
        assert "멘션" in text

    def test_the_greeting_offers_tappable_commands(self):
        response = render_delivery(self.delivery())

        card = response["template"]["outputs"][1]["listCard"]
        titles = [item["title"] for item in card["items"]]
        assert any("리포트" in title for title in titles)

    def test_the_greeting_only_offers_commands_that_work(self):
        # Reuses the help card, which reads IMPLEMENTED_COMMANDS, so it cannot
        # advertise something that answers "아직 준비 중인 기능입니다".
        response = render_delivery(self.delivery())

        card = response["template"]["outputs"][1]["listCard"]
        titles = [item["title"] for item in card["items"]]
        assert not any("평가" in title for title in titles)


def _kr_afternoon():
    from kakao_bot.domain.models import Market, Session

    return (Market.KR, Session.AFTERNOON)


def _us_afternoon():
    from kakao_bot.domain.models import Market, Session

    return (Market.US, Session.AFTERNOON)
