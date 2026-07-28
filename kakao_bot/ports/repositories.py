"""Repository contracts consumed by Kakao bot application services."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol

from kakao_bot.domain.models import (
    AnalysisJob,
    ApprovalStatus,
    BatchCampaign,
    ClaimedAnalysisJob,
    ClaimedOutboundDelivery,
    Market,
    MessageSendResult,
    OutboundDelivery,
    Room,
    RoomLifecycleType,
    RoomSubscription,
    Session,
)


class KakaoRepository(Protocol):
    def discover_room(
        self, room_id: str, *, discovered_at: datetime | None = None
    ) -> Room:
        """Record a room without approving or subscribing it."""

    def set_room_approval(self, room_id: str, approval_status: ApprovalStatus) -> None:
        """Update an existing room's explicit approval state."""

    def configure_subscription(self, subscription: RoomSubscription) -> None:
        """Persist settings for an approved room."""

    def get_subscription(self, room_id: str) -> RoomSubscription:
        """Return a room's current subscription settings."""

    def get_room(self, room_id: str) -> Room:
        """Return the current room approval state."""

    def list_rooms(self) -> tuple[Room, ...]:
        """Return all discovered rooms for operator approval."""

    def record_inbound_event(
        self, event_id: str, *, occurred_at: datetime | None = None
    ) -> bool:
        """Return True only when a new event is recorded."""

    def apply_gateway_event(
        self,
        event_id: str,
        *,
        room_id: str,
        event_type: RoomLifecycleType | None,
        occurred_at: datetime,
    ) -> bool:
        """Atomically deduplicate an event and apply room lifecycle state."""

    def save_campaign(self, campaign: BatchCampaign) -> bool:
        """Return True only when a new campaign_id is recorded."""

    def list_delivery_targets(
        self,
        market: Market,
        session: Session,
        *,
        require_rest_notices: bool = False,
    ) -> tuple[str, ...]:
        """List approved rooms enabled for the requested campaign slot."""

    def enqueue_outbound(self, delivery: OutboundDelivery) -> bool:
        """Return True only when a new delivery key is enqueued."""

    def claim_outbound(
        self,
        *,
        lease_owner: str,
        now: datetime,
        lease_seconds: int,
        limit: int,
    ) -> tuple[ClaimedOutboundDelivery, ...]:
        """Atomically lease due deliveries to one sender."""

    def mark_outbound_sent(
        self,
        delivery_key: str,
        *,
        lease_owner: str,
        sent_at: datetime,
    ) -> bool:
        """Mark the caller's currently leased delivery as sent."""

    def release_outbound(
        self,
        delivery_key: str,
        *,
        lease_owner: str,
        next_attempt_at: datetime,
        error: str,
    ) -> bool:
        """Release the caller's lease and make a delivery retryable later."""

    def mark_outbound_dead(
        self,
        delivery_key: str,
        *,
        lease_owner: str,
        error: str,
    ) -> bool:
        """Permanently stop retrying the caller's leased delivery."""

    def enqueue_analysis_job(self, job: AnalysisJob, *, now: datetime) -> bool:
        """Return True only when a new job_id is recorded."""

    def claim_analysis_jobs(
        self,
        *,
        now: datetime,
        lease_seconds: int,
        limit: int,
    ) -> tuple[ClaimedAnalysisJob, ...]:
        """Atomically lease due analysis jobs to one worker."""

    def complete_analysis_job(
        self,
        job_id: str,
        *,
        summary: str,
        artifact_token: str | None,
        now: datetime,
    ) -> None:
        """Record a completed analysis job's result."""

    def fail_analysis_job(self, job_id: str, *, error_code: str, now: datetime) -> None:
        """Record a permanently failed analysis job."""

    def release_analysis_job(self, job_id: str, *, now: datetime) -> None:
        """Release a claimed job's lease so it can be retried."""

    def count_analysis_jobs_since(
        self,
        *,
        room_id: str | None,
        user_id: str | None,
        since: datetime,
    ) -> int:
        """Count analysis jobs requested since a cutoff, for daily limits."""

    def list_analysis_jobs(self) -> tuple[dict, ...]:
        """Return decoded analysis job rows for diagnostics and tests."""


class KakaoMessageSender(Protocol):
    async def send_message(
        self,
        room_id: str,
        skill_response: dict[str, object],
    ) -> MessageSendResult:
        """Send one proactive room message."""
