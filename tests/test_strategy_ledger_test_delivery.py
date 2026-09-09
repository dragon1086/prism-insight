"""No Telegram calls: every HTTP session is a test double."""
import asyncio
import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from prism_core.strategy_ledger import StrategyLedger
from prism_core.strategy_ledger_outbox import StrategyLedgerOutbox
from prism_core import strategy_ledger_test_delivery as delivery

TOKEN = "123456:CANARY_NOT_A_REAL_TELEGRAM_TOKEN_12345"
CHAT = delivery.AUTHORIZED_TEST_CHAT_ID
T0 = "2026-09-10T00:00:00Z"
T1 = "2026-09-10T01:00:00Z"
T2 = "2026-09-10T02:00:00Z"


@pytest.fixture(autouse=True)
def deny_real_http(monkeypatch):
    factory = MagicMock(side_effect=AssertionError("real HTTP is forbidden"))
    monkeypatch.setattr(delivery.aiohttp, "ClientSession", factory)
    return factory


@pytest.fixture
def box(tmp_path):
    ledger = StrategyLedger(tmp_path / "test-delivery.sqlite")
    ledger.create_book("book", "US", mode="SHADOW")
    ledger.apply_target("buy", "book", "campaign", "SYNTHETIC-DEMO 합성 예시 실제 보유 아님", 50, 100, T0)
    outbox = StrategyLedgerOutbox(ledger)
    notice, = outbox.pending()
    return outbox, notice


def ack_payload(message_id=123, chat_id=CHAT):
    return {"ok": True, "result": {"message_id": message_id, "chat": {"id": chat_id}}}


class Stream:
    def __init__(self, value):
        self.remaining = value
        self.reads = 0

    async def read(self, amount):
        self.reads += 1
        # Deliberately split JSON over many chunks, unlike a one-read fixture.
        chunk, self.remaining = self.remaining[:min(amount, 11)], self.remaining[min(amount, 11):]
        return chunk


class Response:
    def __init__(self, body, status=200, enter_error=None, delay=0):
        self.status = status
        self.content = Stream(body)
        self.enter_error, self.delay = enter_error, delay

    async def __aenter__(self):
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.enter_error:
            raise self.enter_error
        return self

    async def __aexit__(self, *_args):
        return False


def fake_http(monkeypatch, outbox, notice, *, payload=None, body=None, status=200,
              enter_error=None, delay=0):
    calls = []
    response = Response(body if body is not None else json.dumps(
        ack_payload() if payload is None else payload).encode(), status, enter_error, delay)

    class Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        def post(self, url, **kwargs):
            # A separate connection observes committed UNKNOWN BEFORE HTTP.
            reopened = StrategyLedgerOutbox(StrategyLedger(outbox.ledger.path))
            assert reopened.status(notice["notice_id"])["status"] == "UNKNOWN"
            calls.append((url, kwargs))
            return response

    factory = MagicMock(return_value=Session())
    monkeypatch.setattr(delivery.aiohttp, "ClientSession", factory)
    return calls, response, factory


async def send(outbox, notice, **kwargs):
    options = {"bot_token": TOKEN, "chat_id": CHAT, "clock": lambda: T1}
    options.update(kwargs)
    return await delivery.deliver_test_notice(outbox, notice["notice_id"], **options)


@pytest.mark.asyncio
async def test_frozen_slots_text_and_exact_destination_ack_once_then_restart(box, monkeypatch):
    outbox, notice = box
    calls, response, factory = fake_http(monkeypatch, outbox, notice)
    outbox.ledger.mark("later-mark", "campaign", 200, T2)
    result = await send(outbox, notice)
    assert result == {"status": "SENT", "reason": "VALIDATED_TELEGRAM_RECEIPT_ACKED",
                      "send_attempted": True, "receipt_ref": f"telegram:test-chat:{CHAT}:message:123"}
    assert len(calls) == 1 and response.content.reads > 1
    url, request = calls[0]
    assert url == f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    assert request == {"json": {"chat_id": CHAT, "text": notice["text"],
                               "link_preview_options": {"is_disabled": True}}, "allow_redirects": False}
    assert "50%" in notice["text"] and "슬롯" in notice["text"]
    assert "합성 예시 실제 보유 아님" in notice["text"]
    assert "**" not in notice["text"]
    assert "가상 현금" not in notice["text"]
    assert factory.call_args.kwargs["trust_env"] is False
    assert factory.call_args.kwargs["trace_configs"] == []
    assert factory.call_args.kwargs["raise_for_status"] is False
    assert outbox.status(notice["notice_id"])["status"] == "SENT"
    reopened = StrategyLedgerOutbox(StrategyLedger(outbox.ledger.path))
    duplicate = await send(reopened, notice)
    assert duplicate["status"] == "SENT" and duplicate["send_attempted"] is False
    assert len(calls) == 1
    assert TOKEN.encode() not in Path(outbox.ledger.path).read_bytes()


@pytest.mark.asyncio
@pytest.mark.parametrize("chat", [None, str(CHAT), True, 0, -CHAT, CHAT + 1, "@broadcast"])
async def test_wrong_chat_blocks_before_claim_or_transport(box, deny_real_http, chat):
    outbox, notice = box
    assert (await send(outbox, notice, chat_id=chat))["reason"] == "UNAUTHORIZED_TEST_CHAT"
    assert outbox.status(notice["notice_id"])["status"] == "READY"
    deny_real_http.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("token", [None, "", "CANARY", "12:abc/other", "12:abc\n", "12:a?b"])
async def test_missing_or_unsafe_token_never_reads_environment_or_sends(box, deny_real_http, monkeypatch, token):
    outbox, notice = box
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", TOKEN)
    result = await send(outbox, notice, bot_token=token)
    assert result["reason"] == "INVALID_INJECTED_TOKEN"
    assert TOKEN not in str(result)
    assert outbox.status(notice["notice_id"])["status"] == "READY"
    deny_real_http.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("payload", [
    {"ok": False, "description": TOKEN},
    {"ok": 1, "result": {"message_id": 123, "chat": {"id": CHAT}}},
    ack_payload(chat_id=CHAT + 1), ack_payload(chat_id=str(CHAT)),
    ack_payload(message_id=True), ack_payload(message_id=0), ack_payload(message_id=-1),
    ack_payload(message_id="123"), {"ok": True}, [], None,
])
async def test_unverified_receipt_stays_unknown_never_retries(box, monkeypatch, payload):
    outbox, notice = box
    body = json.dumps(payload).encode()
    calls, _, _ = fake_http(monkeypatch, outbox, notice, body=body)
    result = await send(outbox, notice)
    assert result["status"] == "UNKNOWN"
    assert "receipt_ref" not in result and TOKEN not in str(result)
    assert outbox.status(notice["notice_id"])["status"] == "UNKNOWN"
    reopened = StrategyLedgerOutbox(StrategyLedger(outbox.ledger.path))
    assert (await send(reopened, notice))["send_attempted"] is False
    assert len(calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [301, 302, 400, 401, 429, 500])
async def test_http_errors_or_redirects_never_ack_or_retry(box, monkeypatch, status):
    outbox, notice = box
    calls, response, _ = fake_http(monkeypatch, outbox, notice, status=status, body=TOKEN.encode())
    result = await send(outbox, notice)
    assert result["status"] == "UNKNOWN" and TOKEN not in str(result)
    assert response.content.reads == 0
    assert len(calls) == 1
    assert outbox.status(notice["notice_id"])["status"] == "UNKNOWN"


@pytest.mark.asyncio
async def test_bounded_timeout_keeps_unknown_and_token_secret(box, monkeypatch, caplog):
    outbox, notice = box
    calls, _, _ = fake_http(monkeypatch, outbox, notice, delay=1)
    result = await send(outbox, notice, timeout_seconds=0.01)
    assert result["reason"] == "TRANSPORT_TIMEOUT"
    assert outbox.status(notice["notice_id"])["status"] == "UNKNOWN"
    assert (await send(outbox, notice))["send_attempted"] is False
    assert len(calls) == 1
    assert TOKEN not in caplog.text + json.dumps(result)
    assert TOKEN.encode() not in Path(outbox.ledger.path).read_bytes()


@pytest.mark.asyncio
async def test_transport_exception_and_ack_exception_cannot_leak_token(box, monkeypatch, caplog):
    outbox, notice = box
    calls, _, _ = fake_http(monkeypatch, outbox, notice, enter_error=RuntimeError(
        f"https://api.telegram.org/bot{TOKEN}/sendMessage"))
    result = await send(outbox, notice)
    assert result["reason"] == "TRANSPORT_ERROR" and len(calls) == 1
    assert TOKEN not in str(result) + caplog.text
    assert TOKEN.encode() not in Path(outbox.ledger.path).read_bytes()


@pytest.mark.asyncio
async def test_valid_receipt_but_ack_failure_requires_manual_reconciliation(box, monkeypatch, caplog):
    outbox, notice = box
    calls, _, _ = fake_http(monkeypatch, outbox, notice)
    monkeypatch.setattr(outbox, "ack", MagicMock(side_effect=RuntimeError(TOKEN)))
    result = await send(outbox, notice)
    assert result["status"] == "UNKNOWN" and result["reason"] == "ACK_PERSISTENCE_UNCERTAIN"
    assert result["receipt_ref"] == f"telegram:test-chat:{CHAT}:message:123"
    assert result["acknowledged_at"] == "2026-09-10T01:00:00+00:00"
    assert outbox.status(notice["notice_id"])["status"] == "UNKNOWN"
    reopened = StrategyLedgerOutbox(StrategyLedger(outbox.ledger.path))
    assert (await send(reopened, notice))["send_attempted"] is False
    assert len(calls) == 1
    assert TOKEN not in str(result) + caplog.text
    assert TOKEN.encode() not in Path(outbox.ledger.path).read_bytes()


@pytest.mark.asyncio
async def test_concurrent_consumers_authorize_only_one_post(box, monkeypatch):
    outbox, notice = box
    calls, _, _ = fake_http(monkeypatch, outbox, notice, delay=0.02)
    second = StrategyLedgerOutbox(StrategyLedger(outbox.ledger.path))
    results = await asyncio.gather(send(outbox, notice), send(second, notice))
    assert len(calls) == 1
    assert sum(r["send_attempted"] for r in results) == 1
    assert outbox.status(notice["notice_id"])["status"] == "SENT"


@pytest.mark.asyncio
@pytest.mark.parametrize("body", [b"invalid-json", b"x" * (delivery.MAX_RESPONSE_BYTES + 1)])
async def test_invalid_or_oversized_response_never_ack(box, monkeypatch, body):
    outbox, notice = box
    calls, _, _ = fake_http(monkeypatch, outbox, notice, body=body)
    assert (await send(outbox, notice))["status"] == "UNKNOWN"
    assert len(calls) == 1
    assert outbox.status(notice["notice_id"])["status"] == "UNKNOWN"


@pytest.mark.asyncio
@pytest.mark.parametrize("change", [{"delivery_scope": "BROADCAST"}, {"kind": "other"},
                                   {"text": " "}, {"text": "😀" * 2049}, {"text": TOKEN}])
async def test_invalid_frozen_notice_not_reformatted_or_sent(box, monkeypatch, deny_real_http, change):
    outbox, notice = box
    monkeypatch.setattr(outbox, "status", lambda _ref: {**notice, **change})
    claim = MagicMock()
    monkeypatch.setattr(outbox, "claim", claim)
    result = await send(outbox, notice)
    assert result["reason"] == "INVALID_FROZEN_TEST_NOTICE"
    assert TOKEN not in str(result)
    claim.assert_not_called()
    deny_real_http.assert_not_called()


@pytest.mark.asyncio
async def test_cancellation_propagates_sanitized_and_claim_remains_consumed(box, monkeypatch):
    outbox, notice = box
    calls, _, _ = fake_http(monkeypatch, outbox, notice, enter_error=asyncio.CancelledError(TOKEN))
    with pytest.raises(asyncio.CancelledError) as caught:
        await send(outbox, notice)
    assert TOKEN not in str(caught.value)
    assert outbox.status(notice["notice_id"])["status"] == "UNKNOWN"
    assert (await send(outbox, notice))["send_attempted"] is False
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_transport_creation_crash_after_claim_never_reopens_attempt(box, monkeypatch, caplog):
    outbox, notice = box
    factory = MagicMock(side_effect=RuntimeError(TOKEN))
    monkeypatch.setattr(delivery.aiohttp, "ClientSession", factory)
    result = await send(outbox, notice)
    assert result["status"] == "UNKNOWN"
    assert outbox.status(notice["notice_id"])["status"] == "UNKNOWN"
    reopened = StrategyLedgerOutbox(StrategyLedger(outbox.ledger.path))
    assert (await send(reopened, notice))["send_attempted"] is False
    factory.assert_called_once()
    assert TOKEN not in str(result) + caplog.text


@pytest.mark.asyncio
async def test_ack_failure_safe_evidence_can_be_manually_reconciled_without_post(box, monkeypatch):
    outbox, notice = box
    calls, _, _ = fake_http(monkeypatch, outbox, notice)
    monkeypatch.setattr(outbox, "ack", MagicMock(side_effect=RuntimeError(TOKEN)))
    result = await send(outbox, notice)
    reopened = StrategyLedgerOutbox(StrategyLedger(outbox.ledger.path))
    ack = reopened.ack(notice["notice_id"], result["claim_id"], result["receipt_ref"], result["acknowledged_at"])
    assert ack["status"] == "SENT"
    assert (await send(reopened, notice))["send_attempted"] is False
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_invalid_ack_clock_is_not_echoed_or_persisted(box, monkeypatch):
    outbox, notice = box
    calls, _, _ = fake_http(monkeypatch, outbox, notice)
    times = iter([T1, TOKEN])
    result = await send(outbox, notice, clock=lambda: next(times))
    assert result["status"] == "UNKNOWN"
    assert result["acknowledged_at"] is None
    assert TOKEN not in str(result)
    assert outbox.status(notice["notice_id"])["status"] == "UNKNOWN"
    assert len(calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("timeout", [None, True, 0, -1, 31, float("inf"), float("nan")])
async def test_invalid_timeout_never_claims_or_sends(box, deny_real_http, timeout):
    outbox, notice = box
    assert (await send(outbox, notice, timeout_seconds=timeout))["reason"] == "INVALID_TIMEOUT"
    assert outbox.status(notice["notice_id"])["status"] == "READY"
    deny_real_http.assert_not_called()


@pytest.mark.asyncio
async def test_claim_error_after_commit_is_unknown_and_secret_safe(box, monkeypatch, deny_real_http):
    outbox, notice = box
    original = outbox.claim
    def claim_then_error(*args):
        original(*args)
        raise RuntimeError(TOKEN)
    monkeypatch.setattr(outbox, "claim", claim_then_error)
    result = await send(outbox, notice)
    assert result["reason"] == "CLAIM_PERSISTENCE_UNCERTAIN"
    assert result["send_attempted"] is False
    assert TOKEN not in str(result)
    assert outbox.status(notice["notice_id"])["status"] == "UNKNOWN"
    deny_real_http.assert_not_called()


@pytest.mark.asyncio
async def test_unconfirmed_ack_return_never_claims_sent(box, monkeypatch):
    outbox, notice = box
    calls, _, _ = fake_http(monkeypatch, outbox, notice)
    monkeypatch.setattr(outbox, "ack", lambda *_args: None)
    result = await send(outbox, notice)
    assert result["status"] == "UNKNOWN" and result["reason"] == "ACK_PERSISTENCE_UNCERTAIN"
    assert outbox.status(notice["notice_id"])["status"] == "UNKNOWN"
    assert len(calls) == 1
