import asyncio
import io
import json
from datetime import datetime, timezone
from urllib.error import HTTPError

import pytest

from prism_core.runtime.effects import (
    EffectControls,
    RuntimeEnvironment,
    authorize_telegram_test_send,
)
from prism_core.runtime.settings import RuntimeSettings
from prism_core.telegram.config import TelegramConfig
from prism_core.telegram.publisher import (
    AuditStatus,
    FakeTelegramTransport,
    InMemoryFixedWindowRateLimiter,
    PublicationRejected,
    PublicationOutcomeUnknown,
    PublicationStatus,
    PublicationValidationError,
    TelegramBotApiTransport,
    TelegramPublisher,
    StdlibTelegramBotApiRequester,
    TelegramTransportError,
)


def _config() -> TelegramConfig:
    return TelegramConfig(
        enabled=True,
        bot_token="secret-test-token",
        allowed_chat_id="chat-1",
        allowed_user_id="user-1",
    )


@pytest.mark.asyncio
async def test_dry_run_serializes_without_calling_transport() -> None:
    publisher = TelegramPublisher(_config())

    result = await publisher.publish_report(
        report_job_id="daily-kr:2026-07-26",
        request_id="req-1",
        text="[KR DAILY] report body",
        dry_run=True,
    )

    assert result.status is PublicationStatus.DRY_RUN
    payload = json.loads(result.serialized_payload)
    assert payload == {
        "chat_id": "chat-1",
        "dedupe_key": "report:daily-kr:2026-07-26",
        "environment": None,
        "report_job_id": "daily-kr:2026-07-26",
        "request_id": "req-1",
        "test": False,
        "text": "[KR DAILY] report body",
    }
    assert publisher.fake_transport.sent == []
    assert "secret-test-token" not in result.serialized_payload


@pytest.mark.asyncio
async def test_duplicate_report_job_does_not_double_send_and_is_audited() -> None:
    recorded_at = datetime(2026, 7, 26, 12, 0, tzinfo=timezone.utc)
    publisher = TelegramPublisher(_config(), audit_clock=lambda: recorded_at)

    first = await publisher.publish_report(
        report_job_id="daily-us:2026-07-26",
        request_id="req-first",
        text="[US DAILY] report body",
    )
    duplicate = await publisher.publish_report(
        report_job_id="daily-us:2026-07-26",
        request_id="req-retry",
        text="[US DAILY] report body",
    )

    assert first.status is PublicationStatus.SENT
    assert duplicate.status is PublicationStatus.DUPLICATE
    assert len(publisher.fake_transport.sent) == 1
    assert [record.status for record in publisher.audit_records] == [
        AuditStatus.ATTEMPTED,
        AuditStatus.SENT,
        AuditStatus.DUPLICATE,
    ]
    assert all(record.recorded_at == recorded_at for record in publisher.audit_records)
    assert all("report body" not in repr(record) for record in publisher.audit_records)


def _smoke_capability(environment: RuntimeEnvironment, *, dedupe_key: str = "smoke:key"):
    return authorize_telegram_test_send(
        RuntimeSettings(
            telegram_enabled=True,
            allowed_chat_id="chat-1",
            allowed_user_id="user-1",
        ),
        environment=environment,
        destination_chat_id="chat-1",
        inbound_user_id="user-1",
        request_id="smoke-request",
        dedupe_key=dedupe_key,
        message="bounded transport smoke",
        controls=EffectControls(True, True, True),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("environment", list(RuntimeEnvironment))
async def test_allowlisted_live_smoke_is_marked_rate_limited_and_audited(
    environment: RuntimeEnvironment,
) -> None:
    publisher = TelegramPublisher(_config())

    result = await publisher.publish_test_smoke(_smoke_capability(environment))

    assert result.status is PublicationStatus.SENT
    assert publisher.fake_transport.sent[0].text.startswith(
        f"[TEST] environment={environment.value} request_id=smoke-request "
        "dedupe_key=smoke:key\n"
    )
    assert publisher.audit_records[-1].status is AuditStatus.SENT
    assert publisher.audit_records[-1].environment == environment.value
    assert publisher.audit_records[-1].request_id == "smoke-request"
    assert publisher.audit_records[-1].dedupe_key == "smoke:key"
    assert publisher.audit_records[-1].is_test is True


@pytest.mark.asyncio
async def test_rate_limit_refuses_second_distinct_smoke_without_transport_call() -> None:
    publisher = TelegramPublisher(
        _config(),
        rate_limiter=InMemoryFixedWindowRateLimiter(
            max_messages=1,
            window_seconds=60,
            clock=lambda: 100.0,
        ),
    )

    first = await publisher.publish_test_smoke(
        _smoke_capability(RuntimeEnvironment.TEST, dedupe_key="smoke:first")
    )
    second = await publisher.publish_test_smoke(
        _smoke_capability(RuntimeEnvironment.TEST, dedupe_key="smoke:second")
    )

    assert first.status is PublicationStatus.SENT
    assert second.status is PublicationStatus.RATE_LIMITED
    assert len(publisher.fake_transport.sent) == 1
    assert publisher.audit_records[-1].status is AuditStatus.RATE_LIMITED


class _BotApiRequester:
    def __init__(self, response=None, error: Exception | None = None) -> None:
        self.response = response or {"ok": True, "result": {"message_id": 1}}
        self.error = error
        self.calls = []

    async def post_form(self, *, url, fields, timeout_seconds):
        self.calls.append((url, fields, timeout_seconds))
        if self.error is not None:
            raise self.error
        return self.response


@pytest.mark.asyncio
async def test_bot_api_transport_can_execute_bounded_smoke_with_injected_requester() -> None:
    requester = _BotApiRequester()
    transport = TelegramBotApiTransport.from_config(_config(), requester=requester)
    publisher = TelegramPublisher(_config(), transport=transport)

    result = await publisher.publish_test_smoke(
        _smoke_capability(RuntimeEnvironment.OPERATIONS)
    )

    assert result.status is PublicationStatus.SENT
    assert requester.calls == [
        (
            "https://api.telegram.org/botsecret-test-token/sendMessage",
            {"chat_id": "chat-1", "text": result_payload_text(result.serialized_payload)},
            10.0,
        )
    ]
    assert "secret-test-token" not in repr(transport)


def result_payload_text(serialized_payload: str) -> str:
    return json.loads(serialized_payload)["text"]


@pytest.mark.asyncio
async def test_bot_api_transport_sanitizes_request_failures() -> None:
    transport = TelegramBotApiTransport.from_config(
        _config(),
        requester=_BotApiRequester(error=RuntimeError("secret-test-token leaked")),
    )

    with pytest.raises(TelegramTransportError) as exc_info:
        await transport.send_message(chat_id="chat-1", text="safe")

    assert "secret-test-token" not in str(exc_info.value)
    assert exc_info.value.__cause__ is None


@pytest.mark.asyncio
async def test_known_bot_api_rejection_is_retryable_and_distinct_from_unknown() -> None:
    requester = _BotApiRequester(response={"ok": False, "description": "rejected"})
    publisher = TelegramPublisher(
        _config(),
        transport=TelegramBotApiTransport.from_config(_config(), requester=requester),
    )

    with pytest.raises(PublicationRejected):
        await publisher.publish_report(
            report_job_id="daily-kr:rejected",
            request_id="req-rejected",
            text="[KR DAILY] rejected",
        )
    requester.response = {"ok": True, "result": {"message_id": 2}}
    retry = await publisher.publish_report(
        report_job_id="daily-kr:rejected",
        request_id="req-retry",
        text="[KR DAILY] retry",
    )

    assert retry.status is PublicationStatus.SENT
    assert [record.status for record in publisher.audit_records] == [
        AuditStatus.ATTEMPTED,
        AuditStatus.REJECTED,
        AuditStatus.ATTEMPTED,
        AuditStatus.SENT,
    ]


@pytest.mark.asyncio
async def test_stdlib_requester_preserves_definitive_http_error_json(monkeypatch) -> None:
    response_body = io.BytesIO(b'{"ok":false,"error_code":400,"description":"bad"}')

    def reject(*args, **kwargs):
        raise HTTPError(
            "https://api.telegram.org/bottest/sendMessage",
            400,
            "Bad Request",
            hdrs=None,
            fp=response_body,
        )

    monkeypatch.setattr("urllib.request.urlopen", reject)

    response = await StdlibTelegramBotApiRequester().post_form(
        url="https://api.telegram.org/bottest/sendMessage",
        fields={"chat_id": "chat-1", "text": "test"},
        timeout_seconds=1.0,
    )

    assert response == {"ok": False, "error_code": 400, "description": "bad"}


@pytest.mark.asyncio
async def test_malformed_bot_api_object_is_unknown_not_retryable_rejection() -> None:
    publisher = TelegramPublisher(
        _config(),
        transport=TelegramBotApiTransport.from_config(
            _config(), requester=_BotApiRequester(response={"description": "proxy error"})
        ),
    )

    with pytest.raises(PublicationOutcomeUnknown):
        await publisher.publish_report(
            report_job_id="daily-kr:malformed-response",
            request_id="req-malformed",
            text="[KR DAILY] malformed response",
        )

    assert publisher.audit_records[-1].status is AuditStatus.UNKNOWN


class _FailingTransport:
    def __init__(self) -> None:
        self.calls = 0

    async def send_message(self, *, chat_id: str, text: str) -> None:
        self.calls += 1
        raise RuntimeError("network outcome unknown")


@pytest.mark.asyncio
async def test_unknown_transport_outcome_is_audited_and_not_automatically_retried() -> None:
    transport = _FailingTransport()
    publisher = TelegramPublisher(_config(), transport=transport)

    with pytest.raises(PublicationOutcomeUnknown):
        await publisher.publish_report(
            report_job_id="daily-kr:unknown",
            request_id="req-unknown",
            text="[KR DAILY] unknown outcome",
        )
    retry = await publisher.publish_report(
        report_job_id="daily-kr:unknown",
        request_id="req-retry",
        text="[KR DAILY] unknown outcome",
    )

    assert retry.status is PublicationStatus.DUPLICATE
    assert transport.calls == 1
    assert [record.status for record in publisher.audit_records] == [
        AuditStatus.ATTEMPTED,
        AuditStatus.UNKNOWN,
        AuditStatus.DUPLICATE,
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("report_job_id", "request_id", "text"),
    [
        ("daily\nforged", "req-1", "valid"),
        ("daily", "req=forged", "valid"),
        ("daily", "req-1", ""),
        ("daily", "req-1", "x" * 4097),
    ],
)
async def test_report_envelope_is_validated_before_any_effect(
    report_job_id: str, request_id: str, text: str
) -> None:
    transport = FakeTelegramTransport()
    publisher = TelegramPublisher(_config(), transport=transport)

    with pytest.raises(PublicationValidationError):
        await publisher.publish_report(
            report_job_id=report_job_id,
            request_id=request_id,
            text=text,
        )

    assert transport.sent == []
    assert publisher.audit_records == ()


class _BlockingTransport:
    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.calls = 0

    async def send_message(self, *, chat_id: str, text: str) -> None:
        self.calls += 1
        self.started.set()
        await self.release.wait()


@pytest.mark.asyncio
async def test_concurrent_duplicate_is_blocked_while_first_send_is_in_flight() -> None:
    transport = _BlockingTransport()
    publisher = TelegramPublisher(_config(), transport=transport)
    first = asyncio.create_task(
        publisher.publish_report(
            report_job_id="daily-kr:concurrent",
            request_id="req-first",
            text="[KR DAILY] concurrent",
        )
    )
    await transport.started.wait()

    duplicate = await publisher.publish_report(
        report_job_id="daily-kr:concurrent",
        request_id="req-second",
        text="[KR DAILY] concurrent",
    )
    transport.release.set()
    first_result = await first

    assert first_result.status is PublicationStatus.SENT
    assert duplicate.status is PublicationStatus.DUPLICATE
    assert transport.calls == 1


@pytest.mark.asyncio
async def test_report_and_smoke_use_separate_rate_limit_buckets() -> None:
    publisher = TelegramPublisher(
        _config(),
        rate_limiter=InMemoryFixedWindowRateLimiter(
            max_messages=1,
            window_seconds=60,
            clock=lambda: 100.0,
        ),
    )

    report = await publisher.publish_report(
        report_job_id="daily-kr:bucket",
        request_id="req-report",
        text="[KR DAILY] report",
    )
    smoke = await publisher.publish_test_smoke(
        _smoke_capability(RuntimeEnvironment.TEST, dedupe_key="smoke:bucket")
    )

    assert report.status is PublicationStatus.SENT
    assert smoke.status is PublicationStatus.SENT
    assert len(publisher.fake_transport.sent) == 2
