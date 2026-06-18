"""Tests for cores.llm.mcp_registry — McpServerRegistry and McpServerSpec."""

import pytest
from cores.llm.mcp_registry import McpServerRegistry, McpServerSpec

# Sample dict mirroring the real mcp_agent.config.yaml mcp.servers shape,
# with representative servers from the production YAML (perplexity, sqlite, time).
SAMPLE_YAML_DICT = {
    "mcp": {
        "servers": {
            "perplexity": {
                "command": "uvx",
                "args": ["mcp-server-perplexity-ask"],
                "env": {"PERPLEXITY_API_KEY": "pk-test"},
                "read_timeout_seconds": 120,
            },
            "sqlite": {
                "command": "uvx",
                "args": ["mcp-server-sqlite", "--db-path", "/data/trading.db"],
            },
            "time": {
                "command": "uvx",
                "args": ["mcp-server-time", "--local-timezone", "Asia/Seoul"],
            },
        }
    }
}


class TestMcpServerRegistryParsing:
    def setup_method(self):
        self.registry = McpServerRegistry.from_yaml_dict(SAMPLE_YAML_DICT)

    def test_names_returns_all_servers(self):
        assert set(self.registry.names()) == {"perplexity", "sqlite", "time"}

    def test_perplexity_command(self):
        spec = self.registry.get("perplexity")
        assert spec.command == "uvx"

    def test_perplexity_args(self):
        spec = self.registry.get("perplexity")
        assert spec.args == ("mcp-server-perplexity-ask",)

    def test_perplexity_env(self):
        spec = self.registry.get("perplexity")
        assert spec.env == {"PERPLEXITY_API_KEY": "pk-test"}

    def test_perplexity_read_timeout(self):
        spec = self.registry.get("perplexity")
        assert spec.read_timeout_seconds == 120

    def test_sqlite_args_tuple(self):
        spec = self.registry.get("sqlite")
        assert isinstance(spec.args, tuple)
        assert "mcp-server-sqlite" in spec.args

    def test_time_no_env(self):
        spec = self.registry.get("time")
        assert spec.env == {}

    def test_time_no_read_timeout(self):
        spec = self.registry.get("time")
        assert spec.read_timeout_seconds is None

    def test_spec_is_frozen(self):
        spec = self.registry.get("time")
        with pytest.raises((AttributeError, TypeError)):
            spec.command = "other"  # type: ignore[misc]

    def test_spec_name_matches_key(self):
        spec = self.registry.get("perplexity")
        assert spec.name == "perplexity"


class TestMcpServerRegistryErrors:
    def test_missing_server_raises_key_error(self):
        registry = McpServerRegistry.from_yaml_dict(SAMPLE_YAML_DICT)
        with pytest.raises(KeyError, match="yahoo_finance"):
            registry.get("yahoo_finance")

    def test_missing_server_error_lists_available(self):
        registry = McpServerRegistry.from_yaml_dict(SAMPLE_YAML_DICT)
        with pytest.raises(KeyError) as exc_info:
            registry.get("ghost")
        assert "Registered servers" in str(exc_info.value)

    def test_missing_mcp_key_raises(self):
        with pytest.raises(KeyError):
            McpServerRegistry.from_yaml_dict({"other": {}})

    def test_missing_command_raises_value_error(self):
        bad = {"mcp": {"servers": {"broken": {"args": []}}}}
        with pytest.raises(ValueError, match="command"):
            McpServerRegistry.from_yaml_dict(bad)
