"""Plan the post-screening report, decision, and portfolio story."""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from datetime import timedelta, timezone

from kakao_bot.domain.models import BatchStoryEvent, BatchStoryKind, OutboundDelivery
from kakao_bot.ports.repositories import KakaoRepository

_TOKEN_BYTES = 24


@dataclass(frozen=True)
class BatchStoryPlanResult:
    deliveries_created: int


class BatchStoryService:
    def __init__(
        self,
        repository: KakaoRepository,
        *,
        public_base_url: str | None = None,
        link_ttl_hours: int = 72,
        delivery_ttl_hours: int = 24,
    ) -> None:
        self._repository = repository
        self._public_base_url = (public_base_url or "").rstrip("/")
        self._link_ttl = timedelta(hours=link_ttl_hours)
        self._delivery_ttl = timedelta(hours=delivery_ttl_hours)

    def ingest_and_plan(self, event: BatchStoryEvent) -> BatchStoryPlanResult:
        rooms = self._repository.list_delivery_targets(event.market, event.session)
        created = 0
        for room_id in rooms:
            payload: dict[str, object] = {
                "event_id": event.event_id,
                "campaign_id": event.campaign_id,
                "market": event.market.value,
                "session": event.session.value,
                "trade_date": event.trade_date.isoformat(),
                "message": event.message,
            }
            message_type = {
                BatchStoryKind.REPORT: "campaign_report",
                BatchStoryKind.DECISION: "campaign_decision",
                BatchStoryKind.PORTFOLIO: "campaign_portfolio",
            }[event.kind]
            if event.kind is BatchStoryKind.REPORT:
                payload.update(
                    {
                        "ticker": event.ticker,
                        "company_name": event.company_name,
                        "pdf_url": self._mint_link(event, room_id),
                    }
                )
            created += int(
                self._repository.enqueue_outbound(
                    OutboundDelivery(
                        delivery_key=f"story:{event.event_id}:room:{room_id}",
                        room_id=room_id,
                        message_type=message_type,
                        payload=payload,
                        created_at=event.created_at,
                        expires_at=event.created_at + self._delivery_ttl,
                    )
                )
            )
        return BatchStoryPlanResult(created)

    def _mint_link(self, event: BatchStoryEvent, room_id: str) -> str | None:
        if not self._public_base_url or not event.artifact_path:
            return None
        token = secrets.token_urlsafe(_TOKEN_BYTES)
        now = event.created_at.astimezone(timezone.utc)
        self._repository.create_report_link(
            token,
            artifact_path=event.artifact_path,
            room_id=room_id,
            now=now,
            expires_at=now + self._link_ttl,
        )
        return f"{self._public_base_url}/kakao/reports/{token}"
