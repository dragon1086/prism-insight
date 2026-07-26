"""Deterministic Telegram report publication with injectable external effects."""

from __future__ import annotations

import asyncio
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable, Mapping, Protocol

from prism_core.runtime.effects import TelegramTestSendCapability
from prism_core.telegram.config import TelegramConfig


class PublicationStatus(str, Enum):
    DRY_RUN = "dry_run"
    SENT = "sent"
    DUPLICATE = "duplicate"
    RATE_LIMITED = "rate_limited"


class AuditStatus(str, Enum):
    DRY_RUN = "dry_run"
    ATTEMPTED = "attempted"
    SENT = "sent"
    DUPLICATE = "duplicate"
    RATE_LIMITED = "rate_limited"
    REJECTED = "rejected"
    UNKNOWN = "unknown"


class PublicationValidationError(ValueError):
    """A publication envelope is malformed or unsafe."""


class PublicationOutcomeUnknown(RuntimeError):
    """The transport failed after an attempt and must not be blindly retried."""


class PublicationRejected(RuntimeError):
    """Telegram definitively rejected the message before delivery."""


@dataclass(frozen=True)
class PublicationResult:
    status: PublicationStatus
    serialized_payload: str


@dataclass(frozen=True)
class TelegramAuditRecord:
    status: AuditStatus
    recorded_at: datetime
    request_id: str
    dedupe_key: str
    report_job_id: str | None
    environment: str | None
    is_test: bool


@dataclass(frozen=True)
class SentMessage:
    chat_id: str = field(repr=False)
    text: str = field(repr=False)


class TelegramTransport(Protocol):
    async def send_message(self, *, chat_id: str, text: str) -> None: ...


class TelegramBotApiRequester(Protocol):
    async def post_form(
        self,
        *,
        url: str,
        fields: Mapping[str, str],
        timeout_seconds: float,
    ) -> Mapping[str, Any]: ...


class TelegramAuditSink(Protocol):
    def append(self, record: TelegramAuditRecord) -> None: ...


class TelegramDedupeStore(Protocol):
    """Claims must be atomic and must not yield between inspection and reservation."""

    def claim(self, dedupe_key: str) -> bool: ...

    def mark_sent(self, dedupe_key: str) -> None: ...

    def mark_unknown(self, dedupe_key: str) -> None: ...

    def release(self, dedupe_key: str) -> None: ...


class TelegramRateLimiter(Protocol):
    def allow(self, scope: str) -> bool: ...


class FakeTelegramTransport:
    """Deterministic default transport; it never performs network I/O."""

    def __init__(self) -> None:
        self.sent: list[SentMessage] = []

    async def send_message(self, *, chat_id: str, text: str) -> None:
        self.sent.append(SentMessage(chat_id=chat_id, text=text))


class TelegramTransportError(RuntimeError):
    """Sanitized Telegram transport failure without token-bearing details."""


class _TelegramMessageRejected(TelegramTransportError):
    """The Bot API returned a definitive unsuccessful response."""


class StdlibTelegramBotApiRequester:
    """Small async-safe Bot API form requester with no third-party dependency."""

    async def post_form(
        self,
        *,
        url: str,
        fields: Mapping[str, str],
        timeout_seconds: float,
    ) -> Mapping[str, Any]:
        return await asyncio.to_thread(
            self._post_form,
            url=url,
            fields=fields,
            timeout_seconds=timeout_seconds,
        )

    @staticmethod
    def _post_form(
        *,
        url: str,
        fields: Mapping[str, str],
        timeout_seconds: float,
    ) -> Mapping[str, Any]:
        body = urllib.parse.urlencode(fields).encode("utf-8")
        request = urllib.request.Request(url, data=body, method="POST")
        request.add_header("Content-Type", "application/x-www-form-urlencoded")
        try:
            with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
                raw_response = response.read()
        except urllib.error.HTTPError as exc:
            try:
                raw_response = exc.read()
            finally:
                exc.close()
        decoded = json.loads(raw_response.decode("utf-8"))
        if not isinstance(decoded, dict):
            raise ValueError("Telegram Bot API returned a non-object response")
        return decoded


class TelegramBotApiTransport:
    """Opt-in real transport; construction requires complete target config."""

    def __init__(
        self,
        *,
        bot_token: str,
        requester: TelegramBotApiRequester,
        timeout_seconds: float = 10.0,
    ) -> None:
        self._bot_token = bot_token
        self._requester = requester
        self._timeout_seconds = timeout_seconds

    @classmethod
    def from_config(
        cls,
        config: TelegramConfig,
        *,
        requester: TelegramBotApiRequester | None = None,
        timeout_seconds: float = 10.0,
    ) -> "TelegramBotApiTransport":
        if not config.is_ready or config.bot_token is None:
            raise ValueError("real Telegram transport requires enabled complete configuration")
        return cls(
            bot_token=config.bot_token,
            requester=requester or StdlibTelegramBotApiRequester(),
            timeout_seconds=timeout_seconds,
        )

    async def send_message(self, *, chat_id: str, text: str) -> None:
        url = f"https://api.telegram.org/bot{self._bot_token}/sendMessage"
        try:
            response = await self._requester.post_form(
                url=url,
                fields={"chat_id": chat_id, "text": text},
                timeout_seconds=self._timeout_seconds,
            )
        except Exception:
            raise TelegramTransportError("Telegram Bot API request failed") from None
        if response.get("ok") is False:
            raise _TelegramMessageRejected("Telegram Bot API rejected the message")
        if response.get("ok") is not True:
            raise TelegramTransportError("Telegram Bot API returned a malformed response")

    def __repr__(self) -> str:
        return f"{type(self).__name__}(timeout_seconds={self._timeout_seconds!r})"


class InMemoryTelegramAuditSink:
    def __init__(self) -> None:
        self.records: list[TelegramAuditRecord] = []

    def append(self, record: TelegramAuditRecord) -> None:
        self.records.append(record)


class InMemoryTelegramDedupeStore:
    """Atomic within one process because claim has no await boundary."""

    def __init__(self) -> None:
        self._claimed: set[str] = set()
        self._sent: set[str] = set()
        self._unknown: set[str] = set()

    def claim(self, dedupe_key: str) -> bool:
        if (
            dedupe_key in self._claimed
            or dedupe_key in self._sent
            or dedupe_key in self._unknown
        ):
            return False
        self._claimed.add(dedupe_key)
        return True

    def mark_sent(self, dedupe_key: str) -> None:
        self._claimed.discard(dedupe_key)
        self._sent.add(dedupe_key)

    def mark_unknown(self, dedupe_key: str) -> None:
        self._claimed.discard(dedupe_key)
        self._unknown.add(dedupe_key)

    def release(self, dedupe_key: str) -> None:
        self._claimed.discard(dedupe_key)


class InMemoryFixedWindowRateLimiter:
    """Small deterministic process-local limiter for bounded publication."""

    def __init__(
        self,
        *,
        max_messages: int = 5,
        window_seconds: float = 60.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if max_messages < 1 or window_seconds <= 0:
            raise ValueError("rate limit values must be positive")
        self._max_messages = max_messages
        self._window_seconds = window_seconds
        self._clock = clock
        self._events: dict[str, list[float]] = {}

    def allow(self, scope: str) -> bool:
        now = self._clock()
        cutoff = now - self._window_seconds
        events = [event for event in self._events.get(scope, []) if event > cutoff]
        if len(events) >= self._max_messages:
            self._events[scope] = events
            return False
        events.append(now)
        self._events[scope] = events
        return True


class TelegramPublisher:
    """Publish to one configured chat through fake or explicitly injected transport."""

    def __init__(
        self,
        config: TelegramConfig,
        *,
        transport: TelegramTransport | None = None,
        dedupe_store: TelegramDedupeStore | None = None,
        audit_sink: TelegramAuditSink | None = None,
        rate_limiter: TelegramRateLimiter | None = None,
        audit_clock: Callable[[], datetime] | None = None,
    ) -> None:
        if not config.is_ready:
            raise ValueError("Telegram publication requires enabled complete configuration")
        self._config = config
        self.fake_transport = FakeTelegramTransport()
        self._transport = transport or self.fake_transport
        self._dedupe_store = dedupe_store or InMemoryTelegramDedupeStore()
        self._audit_sink = audit_sink or InMemoryTelegramAuditSink()
        self._rate_limiter = rate_limiter or InMemoryFixedWindowRateLimiter()
        self._audit_clock = audit_clock or (lambda: datetime.now(timezone.utc))

    @property
    def audit_records(self) -> tuple[TelegramAuditRecord, ...]:
        if not isinstance(self._audit_sink, InMemoryTelegramAuditSink):
            raise TypeError("audit records are owned by the injected audit sink")
        return tuple(self._audit_sink.records)

    async def publish_report(
        self,
        *,
        report_job_id: str,
        request_id: str,
        text: str,
        dry_run: bool = False,
    ) -> PublicationResult:
        _validate_identifier(report_job_id, field_name="report_job_id")
        _validate_identifier(request_id, field_name="request_id")
        _validate_text(text)
        return await self._publish(
            report_job_id=report_job_id,
            request_id=request_id,
            text=text,
            dedupe_key=f"report:{report_job_id}",
            environment=None,
            is_test=False,
            dry_run=dry_run,
        )

    async def publish_test_smoke(
        self,
        capability: TelegramTestSendCapability,
        *,
        dry_run: bool = False,
    ) -> PublicationResult:
        if (
            capability.destination_chat_id != self._config.allowed_chat_id
            or capability.inbound_user_id != self._config.allowed_user_id
        ):
            raise ValueError("Telegram smoke capability does not match publisher allowlist")
        _validate_identifier(capability.request_id, field_name="request_id")
        _validate_identifier(capability.dedupe_key, field_name="dedupe_key")
        _validate_text(capability.payload)
        return await self._publish(
            report_job_id=None,
            request_id=capability.request_id,
            text=capability.payload,
            dedupe_key=capability.dedupe_key,
            environment=capability.environment.value,
            is_test=True,
            dry_run=dry_run,
        )

    async def _publish(
        self,
        *,
        report_job_id: str | None,
        request_id: str,
        text: str,
        dedupe_key: str,
        environment: str | None,
        is_test: bool,
        dry_run: bool,
    ) -> PublicationResult:
        payload = json.dumps(
            {
                "chat_id": self._config.allowed_chat_id,
                "dedupe_key": dedupe_key,
                "environment": environment,
                "report_job_id": report_job_id,
                "request_id": request_id,
                "test": is_test,
                "text": text,
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        if dry_run:
            self._append_audit(
                AuditStatus.DRY_RUN,
                request_id=request_id,
                dedupe_key=dedupe_key,
                report_job_id=report_job_id,
                environment=environment,
                is_test=is_test,
            )
            return PublicationResult(PublicationStatus.DRY_RUN, payload)

        if not self._dedupe_store.claim(dedupe_key):
            self._append_audit(
                AuditStatus.DUPLICATE,
                request_id=request_id,
                dedupe_key=dedupe_key,
                report_job_id=report_job_id,
                environment=environment,
                is_test=is_test,
            )
            return PublicationResult(PublicationStatus.DUPLICATE, payload)

        rate_scope = (
            f"{self._config.allowed_chat_id}:{'smoke' if is_test else 'report'}"
        )
        if not self._rate_limiter.allow(rate_scope):
            self._dedupe_store.release(dedupe_key)
            self._append_audit(
                AuditStatus.RATE_LIMITED,
                request_id=request_id,
                dedupe_key=dedupe_key,
                report_job_id=report_job_id,
                environment=environment,
                is_test=is_test,
            )
            return PublicationResult(PublicationStatus.RATE_LIMITED, payload)

        try:
            self._append_audit(
                AuditStatus.ATTEMPTED,
                request_id=request_id,
                dedupe_key=dedupe_key,
                report_job_id=report_job_id,
                environment=environment,
                is_test=is_test,
            )
        except Exception:
            self._dedupe_store.release(dedupe_key)
            raise

        try:
            await self._transport.send_message(
                chat_id=self._config.allowed_chat_id or "", text=text
            )
        except _TelegramMessageRejected:
            self._dedupe_store.release(dedupe_key)
            self._append_audit(
                AuditStatus.REJECTED,
                request_id=request_id,
                dedupe_key=dedupe_key,
                report_job_id=report_job_id,
                environment=environment,
                is_test=is_test,
            )
            raise PublicationRejected(
                "Telegram definitively rejected the publication"
            ) from None
        except Exception:
            self._dedupe_store.mark_unknown(dedupe_key)
            self._append_audit(
                AuditStatus.UNKNOWN,
                request_id=request_id,
                dedupe_key=dedupe_key,
                report_job_id=report_job_id,
                environment=environment,
                is_test=is_test,
            )
            raise PublicationOutcomeUnknown(
                "Telegram publication outcome is unknown; automatic retry is blocked"
            ) from None

        self._dedupe_store.mark_sent(dedupe_key)
        self._append_audit(
            AuditStatus.SENT,
            request_id=request_id,
            dedupe_key=dedupe_key,
            report_job_id=report_job_id,
            environment=environment,
            is_test=is_test,
        )
        return PublicationResult(PublicationStatus.SENT, payload)

    def _append_audit(
        self,
        status: AuditStatus,
        *,
        request_id: str,
        dedupe_key: str,
        report_job_id: str | None,
        environment: str | None,
        is_test: bool,
    ) -> None:
        self._audit_sink.append(
            TelegramAuditRecord(
                status=status,
                recorded_at=self._audit_clock(),
                request_id=request_id,
                dedupe_key=dedupe_key,
                report_job_id=report_job_id,
                environment=environment,
                is_test=is_test,
            )
        )


def _validate_identifier(value: object, *, field_name: str) -> None:
    if not isinstance(value, str) or re.fullmatch(
        r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}", value
    ) is None:
        raise PublicationValidationError(f"{field_name} has invalid identifier syntax")


def _validate_text(text: object) -> None:
    if not isinstance(text, str) or not text or len(text) > 4096:
        raise PublicationValidationError("Telegram text must contain 1 to 4096 characters")
