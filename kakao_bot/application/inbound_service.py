"""Inbound event idempotency use case."""

from __future__ import annotations

from datetime import datetime

from kakao_bot.ports.repositories import KakaoRepository


class InboundService:
    def __init__(self, repository: KakaoRepository) -> None:
        self._repository = repository

    def accept(self, event_id: str, *, occurred_at: datetime | None = None) -> bool:
        if not event_id.strip():
            raise ValueError("event_id must not be empty")
        return self._repository.record_inbound_event(
            event_id,
            occurred_at=occurred_at,
        )
