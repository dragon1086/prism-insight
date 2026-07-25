"""Consume channel-neutral campaigns from the local SQLite queue."""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from kakao_bot.adapters.prism.batch_campaign_mapper import (
    BatchCampaignPayloadError,
    map_batch_campaign_payload,
)
from kakao_bot.application.batch_campaign_service import BatchCampaignService
from kakao_bot.ports.repositories import KakaoRepository
from messaging.local_campaign_queue import SQLiteBatchCampaignQueue

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class CampaignConsumerRunResult:
    claimed: int
    consumed: int
    retry_scheduled: int
    dead: int
    stale_lease: int


class LocalBatchCampaignConsumer:
    """Move local queue events into Kakao's campaign/outbox application."""

    def __init__(
        self,
        queue: SQLiteBatchCampaignQueue,
        repository_factory: Callable[[], KakaoRepository],
        *,
        lease_owner: str,
        lease_seconds: int = 30,
        batch_size: int = 20,
        retry_seconds: int = 5,
        max_attempts: int = 5,
        service_factory: Callable[
            [KakaoRepository], BatchCampaignService
        ] = BatchCampaignService,
    ) -> None:
        if not lease_owner.strip():
            raise ValueError("lease_owner must not be empty")
        if min(lease_seconds, batch_size, retry_seconds, max_attempts) <= 0:
            raise ValueError("consumer limits must be positive")
        self._queue = queue
        self._repository_factory = repository_factory
        self._lease_owner = lease_owner
        self._lease_seconds = lease_seconds
        self._batch_size = batch_size
        self._retry_seconds = retry_seconds
        self._max_attempts = max_attempts
        self._service_factory = service_factory

    def run_once(
        self,
        *,
        now: datetime | None = None,
    ) -> CampaignConsumerRunResult:
        run_at = _as_utc(now or datetime.now(timezone.utc))
        entries = self._queue.claim(
            lease_owner=self._lease_owner,
            now=run_at,
            lease_seconds=self._lease_seconds,
            limit=self._batch_size,
        )
        consumed = 0
        retry_scheduled = 0
        dead = 0
        stale_lease = 0

        for entry in entries:
            if entry.attempt_count > self._max_attempts:
                marked = self._queue.mark_dead(
                    entry.queue_id,
                    lease_owner=self._lease_owner,
                    error="maximum campaign consume attempts exceeded",
                )
                dead += int(marked)
                stale_lease += int(not marked)
                continue
            try:
                campaign = map_batch_campaign_payload(entry.payload)
            except (BatchCampaignPayloadError, TypeError, ValueError) as exc:
                marked = self._queue.mark_dead(
                    entry.queue_id,
                    lease_owner=self._lease_owner,
                    error=f"invalid campaign payload: {exc}",
                )
                dead += int(marked)
                stale_lease += int(not marked)
                continue

            repository: KakaoRepository | None = None
            try:
                repository = self._repository_factory()
                result = self._service_factory(repository).ingest_and_plan(campaign)
                marked = self._queue.acknowledge(
                    entry.queue_id,
                    lease_owner=self._lease_owner,
                    consumed_at=run_at,
                )
                consumed += int(marked)
                stale_lease += int(not marked)
                logger.info(
                    "Consumed local campaign %s (created=%s, deliveries=%s)",
                    campaign.campaign_id,
                    result.campaign_created,
                    result.deliveries_created,
                )
            except Exception as exc:
                logger.exception(
                    "Local campaign ingestion failed for %s",
                    campaign.campaign_id,
                )
                marked = self._queue.release(
                    entry.queue_id,
                    lease_owner=self._lease_owner,
                    next_attempt_at=run_at + timedelta(seconds=self._retry_seconds),
                    error=str(exc) or type(exc).__name__,
                )
                retry_scheduled += int(marked)
                stale_lease += int(not marked)
            finally:
                if repository is not None:
                    close = getattr(repository, "close", None)
                    if close is not None:
                        try:
                            close()
                        except Exception:
                            logger.exception(
                                "Failed to close Kakao campaign repository"
                            )

        return CampaignConsumerRunResult(
            claimed=len(entries),
            consumed=consumed,
            retry_scheduled=retry_scheduled,
            dead=dead,
            stale_lease=stale_lease,
        )


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)
