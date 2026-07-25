"""Apply mapped Gateway events without exposing Kakao wire payloads."""

from __future__ import annotations

from kakao_bot.domain.models import InboundMessage, RoomLifecycleEvent
from kakao_bot.ports.repositories import KakaoRepository


class GatewayInboundService:
    def __init__(self, repository: KakaoRepository) -> None:
        self._repository = repository

    def handle(self, event: InboundMessage | RoomLifecycleEvent) -> bool:
        event_type = event.event_type if isinstance(event, RoomLifecycleEvent) else None
        return self._repository.apply_gateway_event(
            event.event_id,
            room_id=event.room_id,
            event_type=event_type,
            occurred_at=event.occurred_at,
        )
