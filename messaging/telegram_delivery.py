"""Conservative delivery boundary: sendMessage has no idempotency key."""

import asyncio
import hashlib
import logging

from telegram.error import BadRequest, NetworkError, RetryAfter, TelegramError, TimedOut

logger = logging.getLogger(__name__)


class TelegramDeliveryUnknown(TelegramError):
    """The server may have accepted the message; manual reconciliation required."""

    def __init__(self, *, category="network_error", message_ref=None):
        self.category = category if category in _CATEGORIES else "network_error"
        self.message_ref = message_ref if isinstance(message_ref, str) and len(message_ref) == 24 and all(
            c in "0123456789abcdef" for c in message_ref) else None
        super().__init__("TELEGRAM_DELIVERY_UNKNOWN: automatic resend suppressed; manual review required"
                         f"; category={self.category}; message_ref={self.message_ref or 'unavailable'}")


_CAUSE_CATEGORIES = {"ConnectTimeout": "connect_timeout", "ReadTimeout": "read_timeout",
                     "WriteTimeout": "write_timeout", "PoolTimeout": "pool_timeout",
                     "ConnectError": "connect_error", "ReadError": "read_error",
                     "WriteError": "write_error", "RemoteProtocolError": "protocol_error"}
_CATEGORIES = set(_CAUSE_CATEGORIES.values()) | {"timeout", "network_error"}


def _delivery_record(status, *, chat_id, text, kind, category=None, message_id=None):
    """Persist only fingerprints and allowlisted metadata; never message/token/URL."""
    ref = None
    try:
        ref = hashlib.sha256((str(chat_id) + "\0" + str(text)).encode()).hexdigest()[:24]
        safe_kind = kind if isinstance(kind, str) and kind in {
            "analysis", "trade", "summary", "general", "monitoring"} else "unknown"
        from observability.events import emit_event
        emit_event("telegram.delivery_result", service="telegram-text-delivery", attributes={
            "status": status, "message_ref": ref, "message_kind": safe_kind,
            "destination_ref": hashlib.sha256(str(chat_id).encode()).hexdigest()[:16],
            "error_category": category if category in _CATEGORIES else None,
            "message_id": message_id if type(message_id) is int and message_id > 0 else None,
            "automatic_resend": False,
        })
    except Exception:  # noqa: BLE001 - optional audit must not change transport behavior
        return ref
    return ref


async def send_message_once_or_rate_retry(bot, *, chat_id, text, attempts=4, message_kind=None, **kwargs):
    """Retry only explicit rejection, never a lost network acknowledgement."""
    if attempts < 1:
        raise ValueError("attempts must be positive")
    for attempt in range(attempts):
        try:
            sent = await bot.send_message(
                chat_id=chat_id, text=text, read_timeout=30, write_timeout=30,
                connect_timeout=10, pool_timeout=10, **kwargs,
            )
            _delivery_record("ACKNOWLEDGED", chat_id=chat_id, text=text, kind=message_kind,
                             message_id=getattr(sent, "message_id", None))
            return sent
        except RetryAfter as error:
            if attempt + 1 == attempts:
                raise
            delay = error.retry_after
            delay = delay.total_seconds() if hasattr(delay, 'total_seconds') else float(delay)
            await asyncio.sleep(delay + 1)
        except BadRequest:
            # Explicit API rejection, including parse errors: not ambiguous.
            raise
        except NetworkError as error:
            category = _CAUSE_CATEGORIES.get(type(error.__cause__).__name__,
                                             "timeout" if isinstance(error, TimedOut) else "network_error")
            ref = _delivery_record("UNKNOWN", chat_id=chat_id, text=text, kind=message_kind,
                                   category=category)
            safe_error = TelegramDeliveryUnknown(category=category, message_ref=ref)
            logger.error("%s", safe_error)
            raise safe_error from None
