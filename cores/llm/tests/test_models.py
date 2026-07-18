"""Tests for cores.llm.models — ModelRegistry."""

import pytest
from cores.llm.models import ModelRegistry
from cores.llm.ports import LLMParams


class TestModelRegistryDefaults:
    def test_resolve_sell_decision(self):
        reg = ModelRegistry.defaults()
        model_id, params = reg.resolve("sell_decision")
        assert model_id == "gpt-5.6-sol"
        assert params.reasoning_effort == "high"
        assert params.max_tokens == 30000

    def test_resolve_journal(self):
        reg = ModelRegistry.defaults()
        model_id, params = reg.resolve("journal")
        assert model_id == "gpt-5.4-mini"
        assert params.reasoning_effort == "none"
        assert params.max_tokens == 16000

    def test_resolve_summary(self):
        reg = ModelRegistry.defaults()
        model_id, params = reg.resolve("summary")
        assert model_id == "gpt-5.4-mini"

    def test_resolve_trading(self):
        reg = ModelRegistry.defaults()
        model_id, _ = reg.resolve("trading")
        assert model_id == "gpt-5.6-sol"

    def test_missing_role_raises_key_error(self):
        reg = ModelRegistry.defaults()
        with pytest.raises(KeyError, match="nonexistent_role"):
            reg.resolve("nonexistent_role")

    def test_missing_role_error_lists_available(self):
        reg = ModelRegistry.defaults()
        with pytest.raises(KeyError) as exc_info:
            reg.resolve("ghost")
        assert "Available roles" in str(exc_info.value)

    def test_roles_returns_list(self):
        reg = ModelRegistry.defaults()
        roles = reg.roles()
        assert isinstance(roles, list)
        assert "sell_decision" in roles


class TestModelRegistryFromMapping:
    def test_custom_mapping_overrides_default(self):
        reg = ModelRegistry.from_mapping(
            {"sell_decision": ("gpt-custom", LLMParams(max_tokens=5000))}
        )
        model_id, params = reg.resolve("sell_decision")
        assert model_id == "gpt-custom"
        assert params.max_tokens == 5000

    def test_custom_mapping_preserves_other_defaults(self):
        reg = ModelRegistry.from_mapping(
            {"custom_role": ("gpt-x", LLMParams())}
        )
        # original defaults still present
        model_id, _ = reg.resolve("journal")
        assert model_id == "gpt-5.4-mini"
        # new role also present
        model_id2, _ = reg.resolve("custom_role")
        assert model_id2 == "gpt-x"

    def test_empty_mapping_uses_defaults(self):
        reg = ModelRegistry.from_mapping({})
        model_id, _ = reg.resolve("trading")
        assert model_id == "gpt-5.6-sol"
