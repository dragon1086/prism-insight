import logging

import pytest

from prism_core.telegram.config import TelegramConfig
from telegram_config import TelegramConfig as LegacyTelegramConfig


def test_config_is_disabled_by_default() -> None:
    config = TelegramConfig.from_environment({})

    assert config.enabled is False
    assert config.is_ready is False


def test_enabled_config_loads_one_bot_chat_and_user_without_exposing_secrets() -> None:
    config = TelegramConfig.from_environment(
        {
            "PRISM_TELEGRAM_ENABLED": "true",
            "TELEGRAM_BOT_TOKEN": "token-secret",
            "TELEGRAM_ALLOWED_CHAT_ID": "chat-private",
            "TELEGRAM_ALLOWED_USER_ID": "user-private",
        }
    )

    assert config.enabled is True
    assert config.is_ready is True
    assert config.bot_token == "token-secret"
    assert config.allowed_chat_id == "chat-private"
    assert config.allowed_user_id == "user-private"
    representation = repr(config)
    assert "token-secret" not in representation
    assert "chat-private" not in representation
    assert "user-private" not in representation


def test_legacy_channel_id_is_only_a_warned_chat_fallback(caplog) -> None:
    with caplog.at_level(logging.WARNING, logger="prism_core.telegram.config"):
        config = TelegramConfig.from_environment(
            {
                "TELEGRAM_CHANNEL_ID": "legacy-chat",
                "TELEGRAM_ALLOWED_USER_ID": "user-private",
            }
        )

    assert config.allowed_chat_id == "legacy-chat"
    assert "TELEGRAM_CHANNEL_ID" in caplog.text
    assert "TELEGRAM_ALLOWED_CHAT_ID" in caplog.text


def test_explicit_allowed_chat_id_wins_without_legacy_warning(caplog) -> None:
    with caplog.at_level(logging.WARNING, logger="prism_core.telegram.config"):
        config = TelegramConfig.from_environment(
            {
                "TELEGRAM_ALLOWED_CHAT_ID": "allowed-chat",
                "TELEGRAM_CHANNEL_ID": "legacy-chat",
            }
        )

    assert config.allowed_chat_id == "allowed-chat"
    assert "TELEGRAM_CHANNEL_ID" not in caplog.text


def test_invalid_enabled_flag_is_rejected() -> None:
    with pytest.raises(ValueError, match="PRISM_TELEGRAM_ENABLED"):
        TelegramConfig.from_environment({"PRISM_TELEGRAM_ENABLED": "sometimes"})


def test_direct_config_requires_a_real_boolean_enabled_flag() -> None:
    with pytest.raises(ValueError, match="enabled.*boolean"):
        TelegramConfig(enabled="false")


def test_legacy_config_prefers_target_allowed_chat_id(monkeypatch, caplog) -> None:
    monkeypatch.setattr(LegacyTelegramConfig, "_load_env", lambda self: None)
    monkeypatch.setenv("TELEGRAM_ALLOWED_CHAT_ID", "target-chat")
    monkeypatch.setenv("TELEGRAM_CHANNEL_ID", "legacy-chat")

    with caplog.at_level(logging.WARNING, logger="telegram_config"):
        config = LegacyTelegramConfig(bot_token="test-token")

    assert config.channel_id == "target-chat"
    assert "compatibility fallback" not in caplog.text


def test_legacy_config_warns_when_using_channel_id_fallback(monkeypatch, caplog) -> None:
    monkeypatch.setattr(LegacyTelegramConfig, "_load_env", lambda self: None)
    monkeypatch.delenv("TELEGRAM_ALLOWED_CHAT_ID", raising=False)
    monkeypatch.setenv("TELEGRAM_CHANNEL_ID", "legacy-chat")

    with caplog.at_level(logging.WARNING, logger="telegram_config"):
        config = LegacyTelegramConfig(bot_token="test-token")

    assert config.channel_id == "legacy-chat"
    assert "TELEGRAM_CHANNEL_ID" in caplog.text
    assert "compatibility fallback" in caplog.text
