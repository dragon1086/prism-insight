"""Durable outbox delivery use case."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from kakao_bot.domain.models import (
    ClaimedOutboundDelivery,
    MessageSendErrorCode,
    MessageSendResult,
)
from kakao_bot.ports.repositories import KakaoMessageSender, KakaoRepository

DeliveryRenderer = Callable[[ClaimedOutboundDelivery], dict[str, object]]


@dataclass(frozen=True)
class DeliveryRunResult:
    claimed: int
    sent: int
    retry_scheduled: int
    dead: int
    stale_lease: int


class OutboxDeliveryService:
    """Claim, render, and send a bounded batch of durable deliveries."""

    def __init__(
        self,
        repository: KakaoRepository,
        sender: KakaoMessageSender,
        renderer: DeliveryRenderer,
        *,
        lease_owner: str,
        lease_seconds: int = 30,
        batch_size: int = 20,
        max_attempts: int = 5,
        retry_base_seconds: int = 30,
        retry_max_seconds: int = 900,
        ambiguous_retry_seconds: int = 300,
    ) -> None:
        if not lease_owner.strip():
            raise ValueError("lease_owner must not be empty")
        if lease_seconds <= 0:
            raise ValueError("lease_seconds must be positive")
        if batch_size <= 0:
            raise ValueError("batch_size must be positive")
        if max_attempts <= 0:
            raise ValueError("max_attempts must be positive")
        if retry_base_seconds <= 0:
            raise ValueError("retry_base_seconds must be positive")
        if retry_max_seconds < retry_base_seconds:
            raise ValueError("retry_max_seconds must be at least retry_base_seconds")
        if ambiguous_retry_seconds <= 0:
            raise ValueError("ambiguous_retry_seconds must be positive")

        self._repository = repository
        self._sender = sender
        self._renderer = renderer
        self._lease_owner = lease_owner
        self._lease_seconds = lease_seconds
        self._batch_size = batch_size
        self._max_attempts = max_attempts
        self._retry_base_seconds = retry_base_seconds
        self._retry_max_seconds = retry_max_seconds
        self._ambiguous_retry_seconds = ambiguous_retry_seconds

    async def run_once(self, *, now: datetime | None = None) -> DeliveryRunResult:
        run_at = _as_utc(now or datetime.now(timezone.utc))
        deliveries = self._repository.claim_outbound(
            lease_owner=self._lease_owner,
            now=run_at,
            lease_seconds=self._lease_seconds,
            limit=self._batch_size,
        )

        sent = 0
        retry_scheduled = 0
        dead = 0
        stale_lease = 0

        for delivery in deliveries:
            if delivery.attempt_count > self._max_attempts:
                marked = self._repository.mark_outbound_dead(
                    delivery.delivery_key,
                    lease_owner=self._lease_owner,
                    error="maximum delivery attempts exceeded",
                )
                dead += int(marked)
                stale_lease += int(not marked)
                continue

            try:
                skill_response = self._renderer(delivery)
            except (TypeError, ValueError) as error:
                marked = self._repository.mark_outbound_dead(
                    delivery.delivery_key,
                    lease_owner=self._lease_owner,
                    error=f"render error: {error}",
                )
                dead += int(marked)
                stale_lease += int(not marked)
                continue

            result = await self._sender.send_message(
                delivery.room_id,
                skill_response,
            )
            if result.success:
                marked = self._repository.mark_outbound_sent(
                    delivery.delivery_key,
                    lease_owner=self._lease_owner,
                    sent_at=run_at,
                )
                sent += int(marked)
                stale_lease += int(not marked)
                continue

            error = _result_error(result)
            if not result.retryable or delivery.attempt_count >= self._max_attempts:
                marked = self._repository.mark_outbound_dead(
                    delivery.delivery_key,
                    lease_owner=self._lease_owner,
                    error=error,
                )
                dead += int(marked)
                stale_lease += int(not marked)
                continue

            delay_seconds = (
                self._ambiguous_retry_seconds
                if result.ambiguous
                else min(
                    self._retry_max_seconds,
                    self._retry_base_seconds * (2 ** (delivery.attempt_count - 1)),
                )
            )
            marked = self._repository.release_outbound(
                delivery.delivery_key,
                lease_owner=self._lease_owner,
                next_attempt_at=run_at + timedelta(seconds=delay_seconds),
                error=error,
            )
            retry_scheduled += int(marked)
            stale_lease += int(not marked)

        return DeliveryRunResult(
            claimed=len(deliveries),
            sent=sent,
            retry_scheduled=retry_scheduled,
            dead=dead,
            stale_lease=stale_lease,
        )


def _result_error(result: MessageSendResult) -> str:
    error_code = (
        result.error_code.value
        if isinstance(result.error_code, MessageSendErrorCode)
        else result.error_code
    )
    parts = [
        str(value)
        for value in (
            error_code,
            result.error_message,
            (f"HTTP {result.status_code}" if result.status_code is not None else None),
        )
        if value
    ]
    return ": ".join(parts)[:1_000] or "unknown delivery error"


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)
