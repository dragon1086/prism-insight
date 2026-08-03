"""Apply mapped Gateway events without exposing Kakao wire payloads."""

from __future__ import annotations

import logging

from kakao_bot.domain.errors import RoomNotFoundError
from kakao_bot.domain.models import (
    ApprovalStatus,
    InboundMessage,
    OutboundDelivery,
    RoomLifecycleEvent,
    RoomLifecycleType,
)
from kakao_bot.ports.repositories import KakaoRepository

logger = logging.getLogger(__name__)

WELCOME = "room_welcome"


class GatewayInboundService:
    """Turn a lifecycle or message event into persisted room state.

    Two policies live here rather than in the repository, because they are
    decisions about how the product should behave rather than about how rows
    get written:

    `auto_approve` — whether an invitation counts as approval. Off by default,
    which is right once the bot has real users. On during the review period:
    ten reviewers adding the bot to their own rooms cannot each wait for an
    operator to SSH in and run the approval CLI, and what they would see in the
    meantime is "이 채팅방은 아직 승인되지 않았습니다" — a bot that looks broken.

    `greet_on_join` — whether to say anything on arrival. Kakao delivers group
    messages to the bot only when it is mentioned, so a silent bot is invisible:
    nobody has been told it is there, let alone what to type at it.
    """

    def __init__(
        self,
        repository: KakaoRepository,
        *,
        auto_approve: bool = False,
        greet_on_join: bool = True,
    ) -> None:
        self._repository = repository
        self._auto_approve = auto_approve
        self._greet_on_join = greet_on_join

    def handle(self, event: InboundMessage | RoomLifecycleEvent) -> bool:
        event_type = event.event_type if isinstance(event, RoomLifecycleEvent) else None
        created = self._repository.apply_gateway_event(
            event.event_id,
            room_id=event.room_id,
            event_type=event_type,
            occurred_at=event.occurred_at,
            auto_approve=self._auto_approve,
        )

        # Only a first-time event greets. Redelivery after RESUME returns False
        # here, which is exactly what keeps the room from being greeted twice.
        if created and event_type is RoomLifecycleType.ENTRANCE:
            self._greet(event.room_id)
        return created

    def _greet(self, room_id: str) -> None:
        """Introduce the bot, but only where it can actually be used.

        Greeting a room that is still PENDING would promise something the very
        next message refuses, so an unapproved room is left alone.

        Failure here must not propagate: the room row is already committed, and
        losing the Gateway session over a greeting would cost far more than the
        greeting is worth.
        """

        if not self._greet_on_join:
            return
        try:
            room = self._repository.get_room(room_id)
        except RoomNotFoundError:
            return
        if room is None or room.approval_status is not ApprovalStatus.APPROVED:
            return

        try:
            self._repository.enqueue_outbound(
                OutboundDelivery(
                    delivery_key=f"welcome:{room_id}",
                    room_id=room_id,
                    message_type=WELCOME,
                    payload={"room_id": room_id},
                )
            )
        except Exception:  # never break the Gateway session over a greeting
            logger.exception("Could not queue a welcome for room %s", room_id)
