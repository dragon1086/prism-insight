"""Deterministic two-dimensional Telegram allowlist authorization."""

from __future__ import annotations

import hmac

from prism_core.telegram.config import TelegramConfig


class TelegramAuthorizationError(ValueError):
    """Telegram authorization cannot be established safely."""


class TelegramAuthorizer:
    """Require exact matches for both the configured chat and inbound user."""

    def __init__(self, config: TelegramConfig) -> None:
        if not config.is_ready:
            raise TelegramAuthorizationError(
                "Telegram must be enabled and fully configured before authorization"
            )
        self._allowed_chat_id = config.allowed_chat_id
        self._allowed_user_id = config.allowed_user_id

    def is_allowed(self, *, chat_id: str | int, user_id: str | int) -> bool:
        chat_matches = hmac.compare_digest(str(chat_id), self._allowed_chat_id or "")
        user_matches = hmac.compare_digest(str(user_id), self._allowed_user_id or "")
        return chat_matches and user_matches
