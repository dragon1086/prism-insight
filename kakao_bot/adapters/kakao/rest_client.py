"""Async client for Kakao Bot outbound REST APIs."""

from __future__ import annotations

import asyncio
import json
import random
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from enum import Enum
from typing import Protocol

import aiohttp

from kakao_bot.domain.models import MessageSendErrorCode, MessageSendResult

DEFAULT_BASE_URL = "https://kapi.kakao.com"
SEND_MESSAGE_PATH = "/v1/bot/send_message"
CALLBACK_PATH = "/v1/bot/callback"


class RequestKind(str, Enum):
    SEND_MESSAGE = "send_message"
    CALLBACK = "callback"


@dataclass(frozen=True)
class EndpointRetryPolicy:
    """Retry budget for one Bot REST endpoint."""

    max_attempts: int

    def __post_init__(self) -> None:
        if self.max_attempts <= 0:
            raise ValueError("max_attempts must be positive")


@dataclass(frozen=True)
class KakaoHttpRequest:
    url: str
    headers: dict[str, str]
    json_body: dict[str, object]


@dataclass(frozen=True)
class KakaoHttpResponse:
    status: int
    headers: Mapping[str, str]
    body: object


class KakaoHttpTransport(Protocol):
    async def post(
        self,
        request: KakaoHttpRequest,
        *,
        timeout: aiohttp.ClientTimeout,
    ) -> KakaoHttpResponse:
        """Execute one HTTP POST."""


class AioHttpTransport:
    """Small aiohttp adapter, injectable in contract and retry tests."""

    def __init__(self, session: aiohttp.ClientSession | None = None) -> None:
        self._session = session

    async def post(
        self,
        request: KakaoHttpRequest,
        *,
        timeout: aiohttp.ClientTimeout,
    ) -> KakaoHttpResponse:
        if self._session is not None:
            return await self._post(self._session, request, timeout=timeout)
        async with aiohttp.ClientSession() as session:
            return await self._post(session, request, timeout=timeout)

    @staticmethod
    async def _post(
        session: aiohttp.ClientSession,
        request: KakaoHttpRequest,
        *,
        timeout: aiohttp.ClientTimeout,
    ) -> KakaoHttpResponse:
        async with session.post(
            request.url,
            headers=request.headers,
            json=request.json_body,
            timeout=timeout,
        ) as response:
            raw_body = await response.text()
            try:
                body: object = json.loads(raw_body) if raw_body else None
            except json.JSONDecodeError:
                body = raw_body
            return KakaoHttpResponse(
                status=response.status,
                headers=dict(response.headers),
                body=body,
            )


class KakaoRequestBuilder:
    """Isolates the Bot REST envelope pending live contract smoke."""

    def __init__(
        self,
        bot_token: str,
        *,
        base_url: str = DEFAULT_BASE_URL,
    ) -> None:
        if not bot_token.strip():
            raise ValueError("bot_token must not be empty")
        if not base_url.strip():
            raise ValueError("base_url must not be empty")
        self._authorization = f"KakaoAK {bot_token}"
        self._base_url = base_url.rstrip("/")

    def send_message(
        self,
        room_id: str,
        skill_response: Mapping[str, object],
    ) -> KakaoHttpRequest:
        if not room_id.strip():
            raise ValueError("room_id must not be empty")
        return KakaoHttpRequest(
            url=f"{self._base_url}{SEND_MESSAGE_PATH}",
            headers=self._headers(),
            json_body={
                "botGroupKey": room_id,
                "skillResponse": dict(skill_response),
            },
        )

    def callback(
        self,
        callback_token: str,
        skill_response: Mapping[str, object],
    ) -> KakaoHttpRequest:
        if not callback_token.strip():
            raise ValueError("callback_token must not be empty")
        headers = self._headers()
        headers["X-Bot-Callback-Token"] = callback_token
        return KakaoHttpRequest(
            url=f"{self._base_url}{CALLBACK_PATH}",
            headers=headers,
            json_body={"skillResponse": dict(skill_response)},
        )

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": self._authorization,
            "Content-Type": "application/json",
        }


Sleep = Callable[[float], Awaitable[None]]
Jitter = Callable[[float, float], float]


class KakaoRestClient:
    """Kakao sender with bounded, endpoint-aware retry behavior."""

    def __init__(
        self,
        bot_token: str,
        *,
        base_url: str = DEFAULT_BASE_URL,
        transport: KakaoHttpTransport | None = None,
        request_builder: KakaoRequestBuilder | None = None,
        timeout: aiohttp.ClientTimeout | None = None,
        max_attempts: int = 3,
        callback_max_attempts: int = 2,
        base_retry_delay: float = 0.5,
        max_retry_delay: float = 10.0,
        sleep: Sleep = asyncio.sleep,
        jitter: Jitter = random.uniform,
    ) -> None:
        if base_retry_delay < 0:
            raise ValueError("base_retry_delay must be non-negative")
        if max_retry_delay < base_retry_delay:
            raise ValueError("max_retry_delay must be at least base_retry_delay")

        self._request_builder = request_builder or KakaoRequestBuilder(
            bot_token,
            base_url=base_url,
        )
        self._transport = transport or AioHttpTransport()
        self._timeout = timeout or aiohttp.ClientTimeout(
            total=10,
            connect=3,
            sock_read=7,
        )
        self._retry_policies = {
            RequestKind.SEND_MESSAGE: EndpointRetryPolicy(max_attempts),
            RequestKind.CALLBACK: EndpointRetryPolicy(callback_max_attempts),
        }
        self._base_retry_delay = base_retry_delay
        self._max_retry_delay = max_retry_delay
        self._sleep = sleep
        self._jitter = jitter

    async def send_message(
        self,
        room_id: str,
        skill_response: dict[str, object],
    ) -> MessageSendResult:
        request = self._request_builder.send_message(room_id, skill_response)
        return await self._post_with_retry(request, kind=RequestKind.SEND_MESSAGE)

    async def callback(
        self,
        callback_token: str,
        skill_response: dict[str, object],
    ) -> MessageSendResult:
        request = self._request_builder.callback(callback_token, skill_response)
        return await self._post_with_retry(request, kind=RequestKind.CALLBACK)

    async def _post_with_retry(
        self,
        request: KakaoHttpRequest,
        *,
        kind: RequestKind,
    ) -> MessageSendResult:
        policy = self._retry_policies[kind]
        for attempt in range(1, policy.max_attempts + 1):
            try:
                response = await self._transport.post(
                    request,
                    timeout=self._timeout,
                )
            except aiohttp.ClientConnectorError as error:
                if attempt < policy.max_attempts:
                    await self._sleep(self._retry_delay(attempt))
                    continue
                return MessageSendResult(
                    success=False,
                    error_code=MessageSendErrorCode.CONNECT_ERROR,
                    error_message=_redact(str(error), request),
                    retryable=True,
                )
            except (asyncio.TimeoutError, aiohttp.ClientConnectionError) as error:
                # A POST may already have reached Kakao. Do not duplicate it in
                # this call; the durable outbox decides when to retry later.
                return MessageSendResult(
                    success=False,
                    error_code=MessageSendErrorCode.AMBIGUOUS_NETWORK_ERROR,
                    error_message=_redact(str(error), request),
                    retryable=True,
                    ambiguous=True,
                )
            except aiohttp.ClientError as error:
                return MessageSendResult(
                    success=False,
                    error_code=MessageSendErrorCode.NETWORK_ERROR,
                    error_message=_redact(str(error), request),
                    retryable=True,
                )

            if 200 <= response.status < 300:
                return MessageSendResult(success=True, status_code=response.status)

            error_message = _redact(_response_error(response.body), request)
            if response.status == 429:
                if attempt < policy.max_attempts:
                    retry_after = _retry_after_seconds(response.headers)
                    await self._sleep(
                        retry_after
                        if retry_after is not None
                        else self._retry_delay(attempt)
                    )
                    continue
                return MessageSendResult(
                    success=False,
                    status_code=response.status,
                    error_code=MessageSendErrorCode.RATE_LIMITED,
                    error_message=error_message,
                    retryable=True,
                )

            if 500 <= response.status < 600:
                if attempt < policy.max_attempts:
                    await self._sleep(self._retry_delay(attempt))
                    continue
                return MessageSendResult(
                    success=False,
                    status_code=response.status,
                    error_code=MessageSendErrorCode.SERVER_ERROR,
                    error_message=error_message,
                    retryable=True,
                )

            return MessageSendResult(
                success=False,
                status_code=response.status,
                error_code=MessageSendErrorCode.HTTP_ERROR,
                error_message=error_message,
                retryable=False,
            )

        raise AssertionError(f"unreachable retry state for {kind.value}")

    def _retry_delay(self, attempt: int) -> float:
        upper = min(
            self._max_retry_delay,
            self._base_retry_delay * (2 ** (attempt - 1)),
        )
        return self._jitter(0, upper)


def _retry_after_seconds(headers: Mapping[str, str]) -> float | None:
    raw = headers.get("Retry-After") or headers.get("retry-after")
    if not raw:
        return None
    try:
        return max(0.0, float(raw))
    except ValueError:
        try:
            parsed = parsedate_to_datetime(raw)
        except (TypeError, ValueError):
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)

        return max(0.0, (parsed - datetime.now(timezone.utc)).total_seconds())


def _response_error(body: object) -> str:
    if isinstance(body, Mapping):
        message = body.get("msg") or body.get("message")
        code = body.get("code")
        if message is not None and code is not None:
            return f"{message} (code={code})"
        if message is not None:
            return str(message)
    if body is None:
        return "empty response"
    return str(body)[:500]


def _redact(message: str, request: KakaoHttpRequest) -> str:
    authorization = request.headers.get("Authorization", "")
    token = authorization.removeprefix("KakaoAK ").strip()
    callback_token = request.headers.get("X-Bot-Callback-Token", "")
    redacted = message
    for secret in (token, callback_token, authorization):
        if secret:
            redacted = redacted.replace(secret, "[REDACTED]")
    return redacted[:500]
