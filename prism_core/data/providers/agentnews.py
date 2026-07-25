"""Bounded public read-only AgentNews KR/US context adapter."""

from __future__ import annotations

import asyncio
import hashlib
import math
import time as monotonic_time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Awaitable, Callable, Protocol
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

from prism_core.data.providers.agentnews_models import (
    AgentNewsBoard,
    AgentNewsModelError,
    AgentNewsSnapshot,
)


_ALLOWED_URLS = frozenset(board.url for board in AgentNewsBoard)
_MARKDOWN_CONTENT_TYPES = frozenset({"text/markdown", "text/plain"})


class AgentNewsError(RuntimeError):
    """Sanitized AgentNews request or response failure."""


class AgentNewsTransientError(AgentNewsError):
    """A retryable timeout, rate-limit, server, or network failure."""


class AgentNewsTimeoutError(AgentNewsTransientError):
    """A bounded AgentNews request timed out."""


@dataclass(frozen=True)
class AgentNewsHTTPResponse:
    status_code: int
    body: bytes = field(repr=False)
    fetched_at: datetime
    content_type: str
    latency_ms: int

    def __post_init__(self) -> None:
        if not 100 <= self.status_code <= 599:
            raise ValueError("status_code must be a valid HTTP status")
        if self.fetched_at.tzinfo is None or self.fetched_at.utcoffset() is None:
            raise ValueError("fetched_at must be timezone-aware")
        if self.latency_ms < 0:
            raise ValueError("latency_ms must be non-negative")


@dataclass(frozen=True)
class AgentNewsFetchEvidence:
    url: str
    status_code: int | None
    fetched_at: datetime
    latency_ms: int | None
    content_hash: str | None
    outcome: str


@dataclass(frozen=True)
class AgentNewsFetchResult:
    snapshot: AgentNewsSnapshot
    attempts: tuple[AgentNewsFetchEvidence, ...]
    used_last_known_good: bool


class AgentNewsFetchFailed(AgentNewsError):
    """A sanitized terminal failure with immutable per-attempt provenance."""

    def __init__(self, attempts: tuple[AgentNewsFetchEvidence, ...]) -> None:
        super().__init__(
            "AgentNews live fetch failed and no last-known-good snapshot is available"
        )
        self.attempts = attempts


class _AgentNewsResponseError(AgentNewsError):
    def __init__(self, message: str, evidence: AgentNewsFetchEvidence) -> None:
        super().__init__(message)
        self.evidence = evidence


class _AgentNewsTransientResponseError(AgentNewsTransientError):
    def __init__(self, message: str, evidence: AgentNewsFetchEvidence) -> None:
        super().__init__(message)
        self.evidence = evidence


class AgentNewsRequester(Protocol):
    async def request(self, **kwargs: object) -> AgentNewsHTTPResponse: ...


class AioHttpAgentNewsRequester:
    """Exact-URL GET-only requester with bounded time and response size."""

    def __init__(
        self,
        *,
        clock: Callable[[], datetime] | None = None,
        monotonic: Callable[[], float] | None = None,
    ) -> None:
        self._clock = clock or (lambda: datetime.now(tz=ZoneInfo("UTC")))
        self._monotonic = monotonic or monotonic_time.monotonic

    async def request(
        self,
        *,
        method: str,
        url: str,
        connect_timeout_seconds: float,
        read_timeout_seconds: float,
        total_timeout_seconds: float,
        max_response_bytes: int,
    ) -> AgentNewsHTTPResponse:
        parsed = urlparse(url)
        if (
            method != "GET"
            or url not in _ALLOWED_URLS
            or parsed.scheme != "https"
            or parsed.hostname != "agentnews.md"
            or parsed.port not in {None, 443}
            or parsed.query
            or parsed.fragment
        ):
            raise AgentNewsError("AgentNews request URL is not allowlisted")
        started = self._monotonic()
        try:
            import aiohttp

            timeout = aiohttp.ClientTimeout(
                connect=connect_timeout_seconds,
                sock_read=read_timeout_seconds,
                total=total_timeout_seconds,
            )
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(url, allow_redirects=False) as response:
                    declared_size = response.content_length
                    if declared_size is not None and declared_size > max_response_bytes:
                        raise AgentNewsError("AgentNews response exceeded the size limit")
                    chunks: list[bytes] = []
                    received_bytes = 0
                    while True:
                        chunk = await response.content.read(
                            max_response_bytes + 1 - received_bytes
                        )
                        if not chunk:
                            break
                        chunks.append(chunk)
                        received_bytes += len(chunk)
                        if received_bytes > max_response_bytes:
                            raise AgentNewsError("AgentNews response exceeded the size limit")
                    body = b"".join(chunks)
                    return AgentNewsHTTPResponse(
                        status_code=response.status,
                        body=body,
                        fetched_at=self._clock(),
                        content_type=response.headers.get("Content-Type", ""),
                        latency_ms=max(0, round((self._monotonic() - started) * 1000)),
                    )
        except asyncio.TimeoutError:
            raise AgentNewsTimeoutError("AgentNews request timed out") from None
        except (AgentNewsError, AgentNewsTimeoutError):
            raise
        except Exception as exc:
            del exc
            raise AgentNewsTransientError("AgentNews request failed") from None


class AgentNewsProvider:
    """Fetch immutable untrusted context snapshots from two exact public boards."""

    def __init__(
        self,
        *,
        requester: AgentNewsRequester | None = None,
        freshness_window: timedelta = timedelta(hours=12),
        max_attempts: int = 2,
        connect_timeout_seconds: float = 3.0,
        read_timeout_seconds: float = 7.0,
        total_timeout_seconds: float = 10.0,
        max_response_bytes: int = 131_072,
        retry_delay_seconds: float = 0.25,
        sleeper: Callable[[float], Awaitable[None]] = asyncio.sleep,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if freshness_window <= timedelta(0):
            raise ValueError("freshness_window must be positive")
        if not 1 <= max_attempts <= 3:
            raise ValueError("max_attempts must be between 1 and 3")
        if (
            not math.isfinite(connect_timeout_seconds)
            or not math.isfinite(read_timeout_seconds)
            or not math.isfinite(total_timeout_seconds)
            or connect_timeout_seconds <= 0
            or read_timeout_seconds <= 0
            or total_timeout_seconds <= 0
            or total_timeout_seconds < max(connect_timeout_seconds, read_timeout_seconds)
            or max_response_bytes < 1
            or not math.isfinite(retry_delay_seconds)
            or retry_delay_seconds < 0
        ):
            raise ValueError("request bounds must be finite and positive")
        self._requester = requester or AioHttpAgentNewsRequester()
        self._freshness_window = freshness_window
        self._max_attempts = max_attempts
        self._connect_timeout_seconds = connect_timeout_seconds
        self._read_timeout_seconds = read_timeout_seconds
        self._total_timeout_seconds = total_timeout_seconds
        self._max_response_bytes = max_response_bytes
        self._retry_delay_seconds = retry_delay_seconds
        self._sleeper = sleeper
        self._clock = clock or (lambda: datetime.now(tz=ZoneInfo("UTC")))
        self._last_known_good: dict[AgentNewsBoard, AgentNewsSnapshot] = {}

    async def fetch(self, board: AgentNewsBoard) -> AgentNewsSnapshot:
        return (await self.fetch_result(board)).snapshot

    async def fetch_result(self, board: AgentNewsBoard) -> AgentNewsFetchResult:
        attempts: list[AgentNewsFetchEvidence] = []
        for attempt in range(1, self._max_attempts + 1):
            try:
                snapshot, evidence = await self._fetch_once(board)
                attempts.append(evidence)
                return AgentNewsFetchResult(
                    snapshot=snapshot,
                    attempts=tuple(attempts),
                    used_last_known_good=False,
                )
            except _AgentNewsTransientResponseError as exc:
                attempts.append(exc.evidence)
                if attempt < self._max_attempts:
                    await self._sleeper(self._retry_delay_seconds)
            except _AgentNewsResponseError as exc:
                attempts.append(exc.evidence)
                break
            except AgentNewsTransientError:
                attempts.append(
                    AgentNewsFetchEvidence(
                        url=board.url,
                        status_code=None,
                        fetched_at=self._clock(),
                        latency_ms=None,
                        content_hash=None,
                        outcome="REQUEST_FAILED",
                    )
                )
                if attempt < self._max_attempts:
                    await self._sleeper(self._retry_delay_seconds)
            except AgentNewsError:
                attempts.append(
                    AgentNewsFetchEvidence(
                        url=board.url,
                        status_code=None,
                        fetched_at=self._clock(),
                        latency_ms=None,
                        content_hash=None,
                        outcome="REQUEST_REJECTED",
                    )
                )
                break
        last_known_good = self._last_known_good.get(board)
        if last_known_good is not None:
            evaluated_at = max(self._clock(), last_known_good.fetched_at)
            return AgentNewsFetchResult(
                snapshot=last_known_good.as_stale_fallback(
                    reason="LIVE_FETCH_FAILED", evaluated_at=evaluated_at
                ),
                attempts=tuple(attempts),
                used_last_known_good=True,
            )
        raise AgentNewsFetchFailed(tuple(attempts)) from None

    async def _fetch_once(
        self, board: AgentNewsBoard
    ) -> tuple[AgentNewsSnapshot, AgentNewsFetchEvidence]:
        response = await self._requester.request(
            method="GET",
            url=board.url,
            connect_timeout_seconds=self._connect_timeout_seconds,
            read_timeout_seconds=self._read_timeout_seconds,
            total_timeout_seconds=self._total_timeout_seconds,
            max_response_bytes=self._max_response_bytes,
        )
        content_hash = hashlib.sha256(response.body).hexdigest()
        evidence = AgentNewsFetchEvidence(
            url=board.url,
            status_code=response.status_code,
            fetched_at=response.fetched_at,
            latency_ms=response.latency_ms,
            content_hash=content_hash,
            outcome="RESPONSE_RECEIVED",
        )
        if response.status_code == 429 or 500 <= response.status_code < 600:
            raise _AgentNewsTransientResponseError(
                "AgentNews returned a transient status", evidence
            )
        if not 200 <= response.status_code < 300:
            raise _AgentNewsResponseError(
                "AgentNews returned a non-success status", evidence
            )
        media_type = response.content_type.partition(";")[0].strip().lower()
        if media_type not in _MARKDOWN_CONTENT_TYPES:
            raise _AgentNewsResponseError(
                "AgentNews returned an unexpected content type", evidence
            )
        try:
            snapshot = AgentNewsSnapshot.from_markdown(
                board=board,
                url=board.url,
                raw_body=response.body,
                fetched_at=response.fetched_at,
                freshness_window=self._freshness_window,
            )
        except AgentNewsModelError:
            raise _AgentNewsResponseError(
                "AgentNews returned malformed Markdown", evidence
            ) from None
        self._last_known_good[board] = snapshot
        return snapshot, evidence
