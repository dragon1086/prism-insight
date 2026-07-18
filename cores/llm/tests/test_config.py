"""Tests for cores.llm.config_loader — interpolation, secret resolution, registry loading.

All tests are network-free and filesystem-contained (tmp_path fixtures).
"""
from __future__ import annotations

import re

import pytest
import yaml

from cores.llm.config_loader import (
    _interpolate_obj,
    interpolate_env,
    load_mcp_registry,
    load_report_mcp_registry,
    resolve_openai_api_key,
    resolve_secret,
)


# ---------------------------------------------------------------------------
# interpolate_env
# ---------------------------------------------------------------------------

class TestInterpolateEnv:
    def test_set_var(self, monkeypatch):
        monkeypatch.setenv("MY_KEY", "hello")
        assert interpolate_env("${MY_KEY}") == "hello"

    def test_unset_var_no_default(self, monkeypatch):
        monkeypatch.delenv("UNSET_VAR_XYZ", raising=False)
        assert interpolate_env("${UNSET_VAR_XYZ}") == ""

    def test_var_with_default_set(self, monkeypatch):
        monkeypatch.setenv("MY_KEY", "real")
        assert interpolate_env("${MY_KEY:-fallback}") == "real"

    def test_var_with_default_unset(self, monkeypatch):
        monkeypatch.delenv("UNSET_VAR_XYZ", raising=False)
        assert interpolate_env("${UNSET_VAR_XYZ:-mydefault}") == "mydefault"

    def test_literal_passthrough(self):
        assert interpolate_env("no-placeholders") == "no-placeholders"

    def test_non_string_passthrough(self):
        assert interpolate_env(42) == 42  # type: ignore[arg-type]
        assert interpolate_env(None) is None  # type: ignore[arg-type]

    def test_mixed_string(self, monkeypatch):
        monkeypatch.setenv("HOST", "localhost")
        assert interpolate_env("http://${HOST}:8080") == "http://localhost:8080"


class TestInterpolateObj:
    def test_dict_values_interpolated(self, monkeypatch):
        monkeypatch.setenv("FOO", "bar")
        result = _interpolate_obj({"key": "${FOO}"})
        assert result == {"key": "bar"}

    def test_nested_dict(self, monkeypatch):
        monkeypatch.setenv("A", "1")
        result = _interpolate_obj({"outer": {"inner": "${A}"}})
        assert result["outer"]["inner"] == "1"

    def test_list_items(self, monkeypatch):
        monkeypatch.setenv("X", "val")
        result = _interpolate_obj(["${X}", "plain"])
        assert result == ["val", "plain"]

    def test_non_string_values_unchanged(self):
        result = _interpolate_obj({"num": 42, "flag": True})
        assert result == {"num": 42, "flag": True}


# ---------------------------------------------------------------------------
# resolve_secret
# ---------------------------------------------------------------------------

class TestResolveSecret:
    def test_present(self, monkeypatch):
        monkeypatch.setenv("MY_SECRET", "s3cr3t")
        assert resolve_secret("MY_SECRET") == "s3cr3t"

    def test_missing_with_default(self, monkeypatch):
        monkeypatch.delenv("NO_SUCH_VAR", raising=False)
        assert resolve_secret("NO_SUCH_VAR", "fallback") == "fallback"

    def test_missing_no_default(self, monkeypatch):
        monkeypatch.delenv("NO_SUCH_VAR", raising=False)
        assert resolve_secret("NO_SUCH_VAR") is None

    def test_required_missing_raises(self, monkeypatch):
        monkeypatch.delenv("REQUIRED_KEY", raising=False)
        with pytest.raises(RuntimeError, match="REQUIRED_KEY"):
            resolve_secret("REQUIRED_KEY", required=True)

    def test_required_present_ok(self, monkeypatch):
        monkeypatch.setenv("REQUIRED_KEY", "present")
        assert resolve_secret("REQUIRED_KEY", required=True) == "present"


class TestResolveOpenaiApiKey:
    def test_env_takes_precedence(self, tmp_path, monkeypatch):
        legacy = tmp_path / "legacy-secrets.yaml"
        legacy.write_text("openai:\n  api_key: legacy-key\n")
        monkeypatch.setenv("OPENAI_API_KEY", "env-key")

        assert resolve_openai_api_key(legacy) == "env-key"

    def test_legacy_yaml_is_transitional_fallback(self, tmp_path, monkeypatch):
        legacy = tmp_path / "legacy-secrets.yaml"
        legacy.write_text("openai:\n  api_key: legacy-key\n")
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)

        assert resolve_openai_api_key(legacy) == "legacy-key"

    def test_missing_sources_return_none(self, tmp_path, monkeypatch):
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        assert resolve_openai_api_key(tmp_path / "missing.yaml") is None


# ---------------------------------------------------------------------------
# load_mcp_registry
# ---------------------------------------------------------------------------

_NATIVE_YAML = """\
servers:
  myserver:
    command: uvx
    args:
      - mcp-server-time
    env:
      TESTKEY: ${TESTKEY}
"""

_LEGACY_YAML = """\
mcp:
  servers:
    legacyserver:
      command: uvx
      args:
        - mcp-server-time
"""


class TestLoadMcpRegistry:
    def test_native_yaml_with_env_interpolation(self, tmp_path, monkeypatch):
        cfg = tmp_path / "mcp_servers.yaml"
        cfg.write_text(_NATIVE_YAML)
        monkeypatch.setenv("TESTKEY", "resolved_value")

        registry = load_mcp_registry(cfg)
        spec = registry.get("myserver")
        assert spec.env["TESTKEY"] == "resolved_value"

    def test_legacy_yaml_mcp_servers_shape(self, tmp_path):
        cfg = tmp_path / "mcp_agent.config.yaml"
        cfg.write_text(_LEGACY_YAML)

        registry = load_mcp_registry(cfg)
        assert "legacyserver" in registry.names()

    def test_missing_file_raises(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            load_mcp_registry(tmp_path / "nonexistent.yaml")

    def test_prism_mcp_config_env_override(self, tmp_path, monkeypatch):
        cfg = tmp_path / "override.yaml"
        cfg.write_text(_NATIVE_YAML)
        monkeypatch.setenv("PRISM_MCP_CONFIG", str(cfg))
        monkeypatch.setenv("TESTKEY", "from_env_override")
        # Pass no config_path — should pick up PRISM_MCP_CONFIG
        # Temporarily patch _NATIVE_CONFIG so it doesn't exist
        import cores.llm.config_loader as config_mod
        orig_native = config_mod._NATIVE_CONFIG
        orig_legacy = config_mod._LEGACY_CONFIG
        config_mod._NATIVE_CONFIG = tmp_path / "does_not_exist.yaml"
        config_mod._LEGACY_CONFIG = tmp_path / "also_not_there.yaml"
        try:
            registry = load_mcp_registry(None)
        finally:
            config_mod._NATIVE_CONFIG = orig_native
            config_mod._LEGACY_CONFIG = orig_legacy
        assert "myserver" in registry.names()

    def test_none_path_loads_native_config(self, tmp_path, monkeypatch):
        """When config_path=None and no PRISM_MCP_CONFIG, native yaml is used."""
        import cores.llm.config_loader as config_mod
        cfg = tmp_path / "mcp_servers.yaml"
        cfg.write_text(_NATIVE_YAML)
        monkeypatch.delenv("PRISM_MCP_CONFIG", raising=False)
        monkeypatch.setenv("TESTKEY", "nativeload")
        orig_native = config_mod._NATIVE_CONFIG
        config_mod._NATIVE_CONFIG = cfg
        try:
            registry = load_mcp_registry(None)
        finally:
            config_mod._NATIVE_CONFIG = orig_native
        assert "myserver" in registry.names()

    def test_report_registry_explicit_config_preserves_current_servers(self, tmp_path):
        cfg = tmp_path / "report-mcp.yaml"
        cfg.write_text(_LEGACY_YAML)

        registry = load_report_mcp_registry(cfg)

        assert "legacyserver" in registry.names()


# ---------------------------------------------------------------------------
# Real mcp_servers.yaml: exists and contains no inline secrets
# ---------------------------------------------------------------------------

class TestRealMcpServersYaml:
    def test_file_exists(self):
        from cores.llm.config_loader import _NATIVE_CONFIG
        assert _NATIVE_CONFIG.exists(), f"Native config not found: {_NATIVE_CONFIG}"

    def test_parses_cleanly(self):
        from cores.llm.config_loader import _NATIVE_CONFIG
        data = yaml.safe_load(_NATIVE_CONFIG.read_text())
        assert "servers" in data
        assert len(data["servers"]) > 0

    def test_no_inline_secrets_in_env_values(self):
        """Every env value must be ${VAR} — never a raw secret string."""
        from cores.llm.config_loader import _NATIVE_CONFIG
        data = yaml.safe_load(_NATIVE_CONFIG.read_text())
        inline = [
            (server_name, key, val)
            for server_name, spec in data["servers"].items()
            for key, val in (spec.get("env") or {}).items()
            if not re.match(r"^\$\{.*\}$", str(val))
        ]
        assert inline == [], (
            f"Found inline secret values (should be ${{VAR}}): {inline}"
        )
