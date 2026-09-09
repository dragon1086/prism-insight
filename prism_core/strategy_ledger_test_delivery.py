"""Explicit test-chat-only outbox transport, with at most one automatic attempt.

No import-time sending, environment/config routing, retry, broadcast, or orders.
The caller injects a bot token for this call only; it is never persisted/logged.
HTTP failures and ambiguous receipts leave the durable claim UNKNOWN. A valid
receipt with failed ACK persistence is returned for manual reconciliation only.
Crashes may lose a claimed notice; this is not exactly-once Telegram delivery.
"""
import asyncio
from datetime import datetime, timezone
import json
import math
import re
import uuid

import aiohttp

from prism_core.strategy_ledger import _time

AUTHORIZED_TEST_CHAT_ID = 7726642089
MAX_RESPONSE_BYTES = 65536


def _now():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _result(status, reason, attempted=False, **evidence):
    # Never include exception text, response bodies, URLs, or secret arguments.
    return {"status": status, "reason": reason, "send_attempted": attempted, **evidence}


def _frozen_notice_valid(notice, notice_id, token):
    text = notice.get("text")
    return (
        notice.get("kind") == "strategy_notice"
        and notice.get("notice_id") == notice_id
        and notice.get("delivery_scope") == "TEST_CHAT_ONLY"
        and isinstance(text, str) and bool(text.strip())
        and len(text.encode("utf-16-le")) // 2 <= 4096
        and token not in text
    )


def _receipt(payload):
    if not isinstance(payload, dict) or payload.get("ok") is not True:
        return None
    message = payload.get("result")
    if not isinstance(message, dict):
        return None
    chat, message_id = message.get("chat"), message.get("message_id")
    if (not isinstance(chat, dict) or type(chat.get("id")) is not int
            or chat["id"] != AUTHORIZED_TEST_CHAT_ID
            or type(message_id) is not int or message_id <= 0):
        return None
    return f"telegram:test-chat:{AUTHORIZED_TEST_CHAT_ID}:message:{message_id}"


async def _post_once(token, text, timeout_seconds):
    timeout = aiohttp.ClientTimeout(total=timeout_seconds)
    async with aiohttp.ClientSession(
        timeout=timeout, trust_env=False, cookie_jar=aiohttp.DummyCookieJar(),
        trace_configs=[], raise_for_status=False,
    ) as session:
        async with session.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": AUTHORIZED_TEST_CHAT_ID, "text": text,
                  "link_preview_options": {"is_disabled": True}},
            allow_redirects=False,
        ) as response:
            if response.status != 200:
                return None
            chunks, size = [], 0
            while True:
                chunk = await response.content.read(min(8192, MAX_RESPONSE_BYTES + 1 - size))
                if not chunk:
                    break
                size += len(chunk)
                if size > MAX_RESPONSE_BYTES:
                    return None
                chunks.append(chunk)
            return _receipt(json.loads(b"".join(chunks)))


async def deliver_test_notice(outbox, notice_id, *, bot_token, chat_id,
                              timeout_seconds=10.0, clock=_now):
    """Consume one frozen notice; UNKNOWN is inspectable, never auto-retried.

    The destination must be explicitly supplied and match the single authorized
    integer ID. Cancellation propagates with a sanitized message after the
    already-durable UNKNOWN claim; this function never resets that claim.
    """
    if type(chat_id) is not int or chat_id != AUTHORIZED_TEST_CHAT_ID:
        return _result("BLOCKED", "UNAUTHORIZED_TEST_CHAT")
    if (not isinstance(bot_token, str) or len(bot_token) > 256
            or not re.fullmatch(r"[0-9]+:[A-Za-z0-9_-]+", bot_token)):
        return _result("BLOCKED", "INVALID_INJECTED_TOKEN")
    if (isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, (int, float))
            or not math.isfinite(timeout_seconds) or not 0 < timeout_seconds <= 30):
        return _result("BLOCKED", "INVALID_TIMEOUT")
    if not isinstance(notice_id, str) or not re.fullmatch(r"ledger-notice:[0-9a-f]{64}", notice_id):
        return _result("BLOCKED", "INVALID_NOTICE_REFERENCE")
    try:
        notice = outbox.status(notice_id)
        if notice["status"] in {"UNKNOWN", "SENT"}:
            return _result(notice["status"], "CLAIM_ALREADY_CONSUMED")
        if not _frozen_notice_valid(notice, notice_id, bot_token):
            return _result("BLOCKED", "INVALID_FROZEN_TEST_NOTICE")
    except Exception:
        return _result("BLOCKED", "NOTICE_READ_FAILED")

    claim_id = "test-chat-send:" + uuid.uuid4().hex
    try:
        claimed = outbox.claim(notice_id, claim_id, clock())
    except Exception:
        return _result("UNKNOWN", "CLAIM_PERSISTENCE_UNCERTAIN")
    if not isinstance(claimed, dict):
        return _result("UNKNOWN", "INVALID_CLAIMED_TEST_NOTICE")
    if claimed.get("send_authorized") is not True:
        return _result("SENT" if claimed.get("status") == "SENT" else "UNKNOWN", "CLAIM_ALREADY_CONSUMED")
    try:
        if claimed.get("status") != "UNKNOWN" or not _frozen_notice_valid(claimed, notice_id, bot_token):
            return _result("UNKNOWN", "INVALID_CLAIMED_TEST_NOTICE")
        receipt = await asyncio.wait_for(
            _post_once(bot_token, claimed["text"], timeout_seconds), timeout=timeout_seconds,
        )
    except asyncio.CancelledError:
        raise asyncio.CancelledError("test_chat_delivery_cancelled_unknown") from None
    except asyncio.TimeoutError:
        return _result("UNKNOWN", "TRANSPORT_TIMEOUT", True)
    except Exception:
        return _result("UNKNOWN", "TRANSPORT_ERROR", True)
    if receipt is None:
        return _result("UNKNOWN", "UNVERIFIED_TELEGRAM_RECEIPT", True)
    acknowledged_at = None
    try:
        acknowledged_at = _time(clock())
        ack = outbox.ack(notice_id, claim_id, receipt, acknowledged_at)
        if not isinstance(ack, dict) or ack.get("status") != "SENT" or ack.get("receipt_ref") != receipt:
            raise ValueError("ACK result not confirmed")
    except asyncio.CancelledError:
        raise asyncio.CancelledError("test_chat_delivery_cancelled_unknown") from None
    except Exception:
        # The safe receipt/claim are sufficient for an operator to reconcile;
        # the adapter will never make another POST for this notice.
        return _result("UNKNOWN", "ACK_PERSISTENCE_UNCERTAIN", True,
                       receipt_ref=receipt, claim_id=claim_id, acknowledged_at=acknowledged_at)
    return _result("SENT", "VALIDATED_TELEGRAM_RECEIPT_ACKED", True, receipt_ref=receipt)
