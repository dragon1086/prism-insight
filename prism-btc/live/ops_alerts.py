"""Private operational alerts: one Telegram attempt, never public/app fanout."""
from __future__ import annotations

import logging
import os

log = logging.getLogger("live.ops_alerts")


def _public_channels() -> set[str]:
    # Match reporter destinations and TelegramConfig's multilingual broadcasts.
    return {
        value.strip().casefold()
        for key, value in os.environ.items()
        if value.strip() and (
            key in ("TELEGRAM_CHANNEL_ID", "BTC_TELEGRAM_CHANNEL_ID")
            or key.startswith("TELEGRAM_CHANNEL_ID_")
        )
    }


def _resolve_ops_channel() -> str | None:
    """Return the monitoring room, with a strictly private legacy fallback."""
    return _resolve_ops_destination()[1]


def _resolve_ops_destination() -> tuple[str | None, str | None]:
    """Keep the existing monitoring bot and room paired; fail closed if partial."""
    monitoring_token = (os.environ.get("OAUTH_ALERT_BOT_TOKEN") or "").strip()
    monitoring_channel = (os.environ.get("OAUTH_ALERT_CHAT_ID") or "").strip()
    if monitoring_token or monitoring_channel:
        token, channel = monitoring_token, monitoring_channel
    else:
        token = (os.environ.get("TELEGRAM_BOT_TOKEN") or "").strip()
        channel = (os.environ.get("BTC_OPS_CHANNEL_ID")
                   or os.environ.get("TELEGRAM_CHANNEL_ID") or "").strip()
    if not token:
        return None, None
    if not channel or channel.casefold() in _public_channels():
        return None, None
    return token, channel


async def _send(token: str | None, channel: str | None, message: str,
                *, bot_factory=None) -> bool:
    """Return confirmed delivery only; unknown outcomes never trigger a fallback.

    Do not use tracking.TelegramSender: it also publishes to Firebase/FCM and
    translated public channels. Dependency/transport failures stay private.
    """
    if (not token or not token.strip() or not channel or not channel.strip()
            or channel.strip().casefold() in _public_channels()):
        log.error("ops alert delivery unavailable: missing/private destination config")
        return False
    try:
        if bot_factory is None:
            from telegram import Bot
            bot_factory = Bot
        async with bot_factory(token=token) as bot:
            receipt = await bot.send_message(chat_id=channel.strip(), text=message)
        message_id = getattr(receipt, "message_id", None)
        return type(message_id) is int and message_id > 0
    except Exception as exc:  # noqa: BLE001 — never retry an uncertain delivery
        log.warning("ops alert delivery unconfirmed (%s)", type(exc).__name__)
        return False
