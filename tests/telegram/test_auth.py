import pytest

from prism_core.telegram.auth import TelegramAuthorizationError, TelegramAuthorizer
from prism_core.telegram.config import TelegramConfig


def _enabled_config() -> TelegramConfig:
    return TelegramConfig(
        enabled=True,
        bot_token="test-token",
        allowed_chat_id="chat-1",
        allowed_user_id="user-1",
    )


@pytest.mark.parametrize(
    ("chat_id", "user_id", "expected"),
    [
        ("chat-1", "user-1", True),
        ("chat-other", "user-1", False),
        ("chat-1", "user-other", False),
        ("chat-other", "user-other", False),
    ],
)
def test_chat_and_user_must_both_match(
    chat_id: str, user_id: str, expected: bool
) -> None:
    authorizer = TelegramAuthorizer(_enabled_config())

    assert authorizer.is_allowed(chat_id=chat_id, user_id=user_id) is expected


def test_disabled_or_incomplete_config_cannot_create_authorizer() -> None:
    with pytest.raises(TelegramAuthorizationError, match="enabled.*configured"):
        TelegramAuthorizer(TelegramConfig())


def test_authorizer_repr_does_not_expose_allowlist_ids() -> None:
    representation = repr(TelegramAuthorizer(_enabled_config()))

    assert "chat-1" not in representation
    assert "user-1" not in representation
