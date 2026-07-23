import builtins
from pathlib import Path
from typing import Any

import pytest

from prism_core.runtime.settings import (
    Phase1BrokerCapabilityError,
    ProductMode,
    RuntimeSettings,
)


def test_runtime_defaults_to_research_with_all_effects_disabled():
    settings = RuntimeSettings()

    assert settings.product_mode is ProductMode.RESEARCH
    assert settings.telegram_enabled is False
    assert settings.broker_enabled is False
    assert settings.research_db_path == Path("research.sqlite")
    assert settings.paper_db_path == Path("paper.sqlite")
    assert settings.ops_db_path == Path("ops.sqlite")


def test_live_is_intentionally_absent_from_product_modes():
    assert {mode.value for mode in ProductMode} == {
        "research",
        "shadow",
        "internal_paper",
        "broker_paper",
    }
    assert not hasattr(ProductMode, "LIVE")


@pytest.mark.parametrize(
    "mode",
    [ProductMode.RESEARCH, ProductMode.SHADOW, ProductMode.INTERNAL_PAPER],
)
def test_phase1_broker_capability_is_denied_before_any_trading_import(
    monkeypatch, mode
):
    original_import = builtins.__import__

    def reject_trading_import(name, *args, **kwargs):
        if name == "trading" or name.startswith("trading."):
            raise AssertionError("trading module import attempted")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", reject_trading_import)

    with pytest.raises(Phase1BrokerCapabilityError, match="Phase 1"):
        RuntimeSettings(product_mode=mode, broker_enabled=True)


def test_broker_paper_mode_does_not_enable_broker_by_default():
    settings = RuntimeSettings(product_mode=ProductMode.BROKER_PAPER)

    assert settings.broker_enabled is False


def test_only_broker_paper_mode_can_enable_broker_capability():
    settings = RuntimeSettings(
        product_mode=ProductMode.BROKER_PAPER,
        broker_enabled=True,
    )

    assert settings.product_mode is ProductMode.BROKER_PAPER
    assert settings.broker_enabled is True


def test_secret_values_are_not_exposed_by_settings_repr():
    settings = RuntimeSettings(
        telegram_bot_token="fake-secret-for-test",
        allowed_chat_id="private-chat-id",
        allowed_user_id="private-user-id",
    )

    representation = repr(settings)
    assert "fake-secret-for-test" not in representation
    assert "private-chat-id" not in representation
    assert "private-user-id" not in representation
    assert "telegram_bot_token" not in representation


def test_string_product_mode_is_normalized_before_broker_policy_check():
    with pytest.raises(Phase1BrokerCapabilityError, match="Phase 1"):
        RuntimeSettings(product_mode="research", broker_enabled=True)


def test_unapproved_live_string_cannot_create_a_runtime_mode():
    with pytest.raises(ValueError, match="not an approved product mode"):
        RuntimeSettings(product_mode="live")


@pytest.mark.parametrize("field_name", ["telegram_enabled", "broker_enabled"])
def test_effect_enable_flags_require_real_booleans(field_name):
    kwargs: dict[str, Any] = {field_name: "false"}
    with pytest.raises(ValueError, match="boolean"):
        RuntimeSettings(**kwargs)
