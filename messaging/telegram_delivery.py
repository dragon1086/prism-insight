"""Conservative delivery boundary: sendMessage has no idempotency key."""

import asyncio
import logging

from telegram.error import BadRequest, NetworkError, RetryAfter, TelegramError

logger = logging.getLogger(__name__)


class TelegramDeliveryUnknown(TelegramError):
    """The server may have accepted the message; manual reconciliation required."""

    def __init__(self):
        super().__init__("TELEGRAM_DELIVERY_UNKNOWN: automatic resend suppressed; manual review required")


async def send_message_once_or_rate_retry(bot, *, chat_id, text, attempts=4, **kwargs):
    """Retry only explicit rejection, never a lost network acknowledgement."""
    if attempts < 1:
        raise ValueError("attempts must be positive")
    for attempt in range(attempts):
        try:
            return await bot.send_message(
                chat_id=chat_id, text=text, read_timeout=30, write_timeout=30,
                connect_timeout=10, pool_timeout=10, **kwargs,
            )
        except RetryAfter as error:
            if attempt + 1 == attempts:
                raise
            delay = error.retry_after
            delay = delay.total_seconds() if hasattr(delay, 'total_seconds') else float(delay)
            await asyncio.sleep(delay + 1)
        except BadRequest:
            # Explicit API rejection, including parse errors: not ambiguous.
            raise
        except NetworkError:
            logger.error("TELEGRAM_DELIVERY_UNKNOWN: automatic resend suppressed; manual review required")
            raise TelegramDeliveryUnknown() from None
