from typing import Any

import pytest

from prism_core.runtime.effects import (
    AGENTNEWS_URLS,
    AgentNewsLiveFetchCapability,
    BrokerEffectCapability,
    CapabilityDenied,
    EffectControls,
    RuntimeEnvironment,
    TelegramInteractiveCapability,
    TelegramTestSendCapability,
    authorize_agentnews_live_fetch,
    authorize_broker_effect,
    authorize_telegram_interactive_startup,
    authorize_telegram_test_send,
)
from prism_core.runtime.settings import ProductMode, RuntimeSettings


def _telegram_settings(**overrides):
    values = {
        "telegram_enabled": True,
        "allowed_chat_id": "chat-allowed",
        "allowed_user_id": "user-allowed",
    }
    values.update(overrides)
    return RuntimeSettings(**values)


def test_telegram_is_off_by_default():
    with pytest.raises(CapabilityDenied, match="disabled"):
        authorize_telegram_interactive_startup(RuntimeSettings())


@pytest.mark.parametrize(
    "factory",
    [
        lambda: TelegramInteractiveCapability("chat", "user"),
        lambda: TelegramTestSendCapability(
            RuntimeEnvironment.TEST,
            "chat",
            "user",
            "req-1",
            "dedupe:req-1",
            "[TEST] payload",
        ),
        lambda: AgentNewsLiveFetchCapability(
            RuntimeEnvironment.TEST,
            AGENTNEWS_URLS["KR"],
        ),
        lambda: BrokerEffectCapability(ProductMode.BROKER_PAPER),
    ],
)
def test_capabilities_cannot_be_constructed_without_authorization(factory):
    with pytest.raises(CapabilityDenied, match="factory"):
        factory()


@pytest.mark.parametrize("missing_field", ["allowed_chat_id", "allowed_user_id"])
def test_missing_allowlist_blocks_interactive_startup(missing_field):
    settings = _telegram_settings(**{missing_field: None})

    with pytest.raises(CapabilityDenied, match="allowlist"):
        authorize_telegram_interactive_startup(settings)


def test_allowlist_entries_must_be_nonempty_strings():
    invalid_chat_id: Any = 123
    invalid_user_id: Any = 456
    settings = RuntimeSettings(
        telegram_enabled=True,
        allowed_chat_id=invalid_chat_id,
        allowed_user_id=invalid_user_id,
    )

    with pytest.raises(CapabilityDenied, match="allowlist"):
        authorize_telegram_interactive_startup(settings)


def test_interactive_capability_repr_redacts_private_allowlist_ids():
    capability = authorize_telegram_interactive_startup(_telegram_settings())

    representation = repr(capability)
    assert "chat-allowed" not in representation
    assert "user-allowed" not in representation


@pytest.mark.parametrize("environment", list(RuntimeEnvironment))
def test_allowlisted_test_send_is_environment_independent(environment):
    capability = authorize_telegram_test_send(
        _telegram_settings(),
        environment=environment,
        destination_chat_id="chat-allowed",
        inbound_user_id="user-allowed",
        request_id="req-123",
        dedupe_key="daily-kr:req-123",
        message="runtime safety smoke",
        controls=EffectControls(
            rate_limiter_configured=True,
            dedupe_store_configured=True,
            audit_sink_configured=True,
        ),
    )

    assert capability.environment is environment
    assert capability.destination_chat_id == "chat-allowed"
    assert capability.inbound_user_id == "user-allowed"
    assert capability.request_id == "req-123"
    assert capability.dedupe_key == "daily-kr:req-123"
    assert capability.payload.startswith(
        f"[TEST] environment={environment.value} request_id=req-123 "
        "dedupe_key=daily-kr:req-123\n"
    )


@pytest.mark.parametrize(
    "controls",
    [
        EffectControls(False, True, True),
        EffectControls(True, False, True),
        EffectControls(True, True, False),
    ],
)
def test_test_send_requires_rate_limit_dedupe_and_audit(controls):
    with pytest.raises(CapabilityDenied, match="rate limit|dedupe|audit"):
        authorize_telegram_test_send(
            _telegram_settings(),
            environment=RuntimeEnvironment.TEST,
            destination_chat_id="chat-allowed",
            inbound_user_id="user-allowed",
            request_id="req-123",
            dedupe_key="test:req-123",
            message="smoke",
            controls=controls,
        )


def test_effect_controls_require_real_booleans():
    invalid_flag: Any = "false"
    with pytest.raises(ValueError, match="boolean"):
        EffectControls(invalid_flag, True, True)


@pytest.mark.parametrize(
    ("chat_id", "user_id"),
    [("wrong-chat", "user-allowed"), ("chat-allowed", "wrong-user")],
)
def test_test_send_rejects_non_allowlisted_chat_or_user(chat_id, user_id):
    with pytest.raises(CapabilityDenied, match="allowlist"):
        authorize_telegram_test_send(
            _telegram_settings(),
            environment=RuntimeEnvironment.OPERATIONS,
            destination_chat_id=chat_id,
            inbound_user_id=user_id,
            request_id="req-123",
            dedupe_key="test:req-123",
            message="smoke",
            controls=EffectControls(True, True, True),
        )


@pytest.mark.parametrize(
    ("request_id", "dedupe_key"),
    [
        ("req-123\nforged=true", "test:req-123"),
        ("req-123", "key\rforged"),
        ("req-123 forged=true", "test:req-123"),
        ("req-123=forged", "test:req-123"),
    ],
)
def test_test_send_rejects_unsafe_envelope_metadata(
    request_id, dedupe_key
):
    with pytest.raises(CapabilityDenied, match="metadata"):
        authorize_telegram_test_send(
            _telegram_settings(),
            environment=RuntimeEnvironment.TEST,
            destination_chat_id="chat-allowed",
            inbound_user_id="user-allowed",
            request_id=request_id,
            dedupe_key=dedupe_key,
            message="smoke",
            controls=EffectControls(True, True, True),
        )


def test_test_send_rejects_non_string_envelope_metadata():
    invalid_request_id: Any = 123
    with pytest.raises(CapabilityDenied, match="metadata"):
        authorize_telegram_test_send(
            _telegram_settings(),
            environment=RuntimeEnvironment.TEST,
            destination_chat_id="chat-allowed",
            inbound_user_id="user-allowed",
            request_id=invalid_request_id,
            dedupe_key="test:req-123",
            message="smoke",
            controls=EffectControls(True, True, True),
        )


def test_test_send_capability_repr_redacts_private_destination_metadata():
    capability = authorize_telegram_test_send(
        _telegram_settings(),
        environment=RuntimeEnvironment.TEST,
        destination_chat_id="chat-allowed",
        inbound_user_id="user-allowed",
        request_id="req-123",
        dedupe_key="test:req-123",
        message="private-message-content",
        controls=EffectControls(True, True, True),
    )

    representation = repr(capability)
    assert "chat-allowed" not in representation
    assert "user-allowed" not in representation
    assert "private-message-content" not in representation


@pytest.mark.parametrize("environment", list(RuntimeEnvironment))
@pytest.mark.parametrize("market", ["KR", "US"])
def test_agentnews_live_fetch_is_allowed_in_every_environment(environment, market):
    capability = authorize_agentnews_live_fetch(
        environment=environment,
        url=AGENTNEWS_URLS[market],
    )

    assert capability.environment is environment
    assert capability.url == AGENTNEWS_URLS[market]
    assert capability.read_only is True
    assert capability.requires_credentials is False


def test_agentnews_endpoints_are_exact_public_markdown_contracts():
    assert AGENTNEWS_URLS == {
        "KR": "https://agentnews.md/finance-ko.md",
        "US": "https://agentnews.md/finance.md",
    }


def test_agentnews_capability_rejects_arbitrary_urls():
    with pytest.raises(CapabilityDenied, match="approved public"):
        authorize_agentnews_live_fetch(
            environment=RuntimeEnvironment.DEVELOPMENT,
            url="https://example.com/unapproved.md",
        )


def test_unknown_runtime_environment_is_denied():
    invalid_environment: Any = "production"
    with pytest.raises(CapabilityDenied, match="environment"):
        authorize_agentnews_live_fetch(
            environment=invalid_environment,
            url=AGENTNEWS_URLS["KR"],
        )
    with pytest.raises(CapabilityDenied, match="environment"):
        authorize_telegram_test_send(
            _telegram_settings(),
            environment=invalid_environment,
            destination_chat_id="chat-allowed",
            inbound_user_id="user-allowed",
            request_id="req-123",
            dedupe_key="test:req-123",
            message="smoke",
            controls=EffectControls(True, True, True),
        )


@pytest.mark.parametrize(
    "mode",
    [ProductMode.RESEARCH, ProductMode.SHADOW, ProductMode.INTERNAL_PAPER],
)
def test_phase1_modes_cannot_authorize_broker_effects(mode):
    with pytest.raises(CapabilityDenied, match="Phase 1"):
        authorize_broker_effect(RuntimeSettings(product_mode=mode))


def test_broker_paper_requires_explicit_activation():
    with pytest.raises(CapabilityDenied, match="disabled"):
        authorize_broker_effect(
            RuntimeSettings(product_mode=ProductMode.BROKER_PAPER)
        )


def test_only_explicitly_enabled_broker_paper_can_receive_capability():
    capability = authorize_broker_effect(
        RuntimeSettings(
            product_mode=ProductMode.BROKER_PAPER,
            broker_enabled=True,
        )
    )

    assert capability.product_mode is ProductMode.BROKER_PAPER
