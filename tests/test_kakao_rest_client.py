from __future__ import annotations

import asyncio
from collections.abc import Mapping
from types import SimpleNamespace

import aiohttp
import pytest

from kakao_bot.adapters.kakao.rest_client import (
    CALLBACK_PATH,
    SEND_MESSAGE_PATH,
    KakaoHttpRequest,
    KakaoHttpResponse,
    KakaoRequestBuilder,
    KakaoRestClient,
)
from kakao_bot.adapters.kakao.skill_response import simple_text
from kakao_bot.domain.models import MessageSendErrorCode


class FakeTransport:
    def __init__(self, *outcomes):
        self.outcomes = list(outcomes)
        self.requests: list[KakaoHttpRequest] = []

    async def post(self, request, *, timeout):
        self.requests.append(request)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


def response(
    status: int,
    *,
    headers: Mapping[str, str] | None = None,
    body: object = None,
) -> KakaoHttpResponse:
    return KakaoHttpResponse(
        status=status,
        headers=headers or {},
        body=body,
    )


def connector_error(message: str) -> aiohttp.ClientConnectorError:
    key = SimpleNamespace(host="kapi.kakao.com", port=443, ssl=True)
    return aiohttp.ClientConnectorError(key, OSError(message))


def test_request_builder_isolates_unverified_rest_envelopes():
    builder = KakaoRequestBuilder(
        "secret-token",
        base_url="https://example.test/",
    )
    skill = simple_text("안녕하세요.")

    send = builder.send_message("group-key", skill)
    callback = builder.callback("callback-token", skill)

    assert send.url == f"https://example.test{SEND_MESSAGE_PATH}"
    assert send.headers["Authorization"] == "KakaoAK secret-token"
    assert send.json_body == {
        "botGroupKey": "group-key",
        "skillResponse": skill,
    }
    assert callback.url == f"https://example.test{CALLBACK_PATH}"
    assert callback.headers["Authorization"] == "KakaoAK secret-token"
    assert callback.headers["X-Bot-Callback-Token"] == "callback-token"
    assert callback.json_body == {"skillResponse": skill}


@pytest.mark.asyncio
async def test_send_message_returns_typed_success():
    transport = FakeTransport(response(200, body={"ok": True}))
    client = KakaoRestClient("token", transport=transport)

    result = await client.send_message("room-1", simple_text("ok"))

    assert result.success is True
    assert result.status_code == 200
    assert len(transport.requests) == 1


@pytest.mark.asyncio
async def test_429_honors_retry_after_before_retrying():
    sleeps = []

    async def fake_sleep(seconds):
        sleeps.append(seconds)

    transport = FakeTransport(
        response(429, headers={"Retry-After": "2"}, body={"msg": "slow"}),
        response(200),
    )
    client = KakaoRestClient(
        "token",
        transport=transport,
        sleep=fake_sleep,
        jitter=lambda _low, high: high,
    )

    result = await client.send_message("room-1", simple_text("ok"))

    assert result.success is True
    assert sleeps == [2.0]
    assert len(transport.requests) == 2


@pytest.mark.asyncio
async def test_5xx_and_connect_failure_use_bounded_jittered_retries():
    sleeps = []

    async def fake_sleep(seconds):
        sleeps.append(seconds)

    transport = FakeTransport(
        connector_error("not connected"),
        response(503, body={"msg": "unavailable"}),
        response(503, body={"msg": "still unavailable"}),
    )
    client = KakaoRestClient(
        "token",
        transport=transport,
        max_attempts=3,
        base_retry_delay=1,
        sleep=fake_sleep,
        jitter=lambda _low, high: high,
    )

    result = await client.send_message("room-1", simple_text("ok"))

    assert result.success is False
    assert result.error_code is MessageSendErrorCode.SERVER_ERROR
    assert result.retryable is True
    assert sleeps == [1, 2]
    assert len(transport.requests) == 3


@pytest.mark.asyncio
async def test_4xx_is_not_retried():
    transport = FakeTransport(
        response(400, body={"code": -2, "msg": "bad request"}),
    )
    client = KakaoRestClient("token", transport=transport)

    result = await client.send_message("room-1", simple_text("ok"))

    assert result.success is False
    assert result.error_code is MessageSendErrorCode.HTTP_ERROR
    assert result.retryable is False
    assert result.error_message == "bad request (code=-2)"
    assert len(transport.requests) == 1


@pytest.mark.asyncio
async def test_timeout_is_ambiguous_and_not_retried_in_same_call():
    transport = FakeTransport(asyncio.TimeoutError("token"))
    client = KakaoRestClient("token", transport=transport)

    result = await client.send_message("room-1", simple_text("ok"))

    assert result.success is False
    assert result.ambiguous is True
    assert result.retryable is True
    assert "[REDACTED]" in result.error_message
    assert len(transport.requests) == 1


@pytest.mark.asyncio
async def test_callback_has_its_own_smaller_retry_budget():
    sleeps = []

    async def fake_sleep(seconds):
        sleeps.append(seconds)

    transport = FakeTransport(response(503), response(503), response(200))
    client = KakaoRestClient(
        "token",
        transport=transport,
        max_attempts=3,
        callback_max_attempts=2,
        sleep=fake_sleep,
    )

    result = await client.callback("callback-token", simple_text("ok"))

    assert result.success is False
    assert result.error_code is MessageSendErrorCode.SERVER_ERROR
    assert len(transport.requests) == 2
    assert len(sleeps) == 1
