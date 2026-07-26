"""Safe, fake-by-default Telegram configuration."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Mapping


logger = logging.getLogger(__name__)


def _optional_value(environment: Mapping[str, str], key: str) -> str | None:
    value = environment.get(key)
    if value is None:
        return None
    normalized = value.strip()
    return normalized or None


def _enabled_value(environment: Mapping[str, str]) -> bool:
    raw = environment.get("PRISM_TELEGRAM_ENABLED", "false").strip().lower()
    if raw in {"1", "true", "yes", "on"}:
        return True
    if raw in {"0", "false", "no", "off", ""}:
        return False
    raise ValueError("PRISM_TELEGRAM_ENABLED must be a boolean value")


@dataclass(frozen=True)
class TelegramConfig:
    """Target Telegram settings with external transport disabled by default."""

    enabled: bool = False
    bot_token: str | None = field(default=None, repr=False, compare=False)
    allowed_chat_id: str | None = field(default=None, repr=False)
    allowed_user_id: str | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        if type(self.enabled) is not bool:
            raise ValueError("enabled must be a boolean value")
        for field_name in ("bot_token", "allowed_chat_id", "allowed_user_id"):
            value = getattr(self, field_name)
            if value is not None and (not isinstance(value, str) or not value.strip()):
                raise ValueError(f"{field_name} must be a non-empty string when set")

    @classmethod
    def from_environment(cls, environment: Mapping[str, str]) -> "TelegramConfig":
        allowed_chat_id = _optional_value(environment, "TELEGRAM_ALLOWED_CHAT_ID")
        if allowed_chat_id is None:
            allowed_chat_id = _optional_value(environment, "TELEGRAM_CHANNEL_ID")
            if allowed_chat_id is not None:
                logger.warning(
                    "TELEGRAM_CHANNEL_ID is a deprecated compatibility fallback; "
                    "configure TELEGRAM_ALLOWED_CHAT_ID"
                )
        return cls(
            enabled=_enabled_value(environment),
            bot_token=_optional_value(environment, "TELEGRAM_BOT_TOKEN"),
            allowed_chat_id=allowed_chat_id,
            allowed_user_id=_optional_value(environment, "TELEGRAM_ALLOWED_USER_ID"),
        )

    @property
    def is_ready(self) -> bool:
        return bool(
            self.enabled
            and self.bot_token
            and self.allowed_chat_id
            and self.allowed_user_id
        )
