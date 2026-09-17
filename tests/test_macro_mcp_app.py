"""Offline macro configuration parity; no server or model is started."""
import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("macro_mcp_app_test", ROOT / "cores/llm/macro_mcp_app.py")
runtime = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(runtime)


class App:
    def __init__(self, *, name, settings):
        self.name = name
        self.settings = settings
        self._dotenv_loaded = False


@pytest.fixture(autouse=True)
def environment(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("REPORT_MCP_CONFIG", raising=False)
    monkeypatch.delenv("PRISM_MCP_CONFIG", raising=False)
    monkeypatch.setenv("PERPLEXITY_API_KEY", "test-perplexity")
    monkeypatch.setenv("OPENAI_API_KEY", "test-openai")
    monkeypatch.setenv("OPENAI_BASE_URL", "http://127.0.0.1:9876/v1")


def test_native_registry_without_ignored_yaml_retains_proxy():
    app = runtime.create_macro_mcp_app(App, name="test")
    settings = app.settings
    assert app._dotenv_loaded is True
    assert set(settings.mcp.servers) == {"perplexity"}
    server = settings.mcp.servers["perplexity"]
    assert server.command == "npx"
    assert server.args == ["-y", "@perplexity-ai/mcp-server@1.2.0"]
    assert server.env == {"PERPLEXITY_API_KEY": "test-perplexity"}
    assert server.read_timeout_seconds == 75
    assert settings.openai.base_url == "http://127.0.0.1:9876/v1"
    assert settings.openai.api_key == "test-openai"


def test_us_shadowed_cores_restored(monkeypatch):
    shadow = ModuleType("cores")
    shadow.__path__ = [str(ROOT / "prism-us/cores")]
    monkeypatch.setitem(sys.modules, "cores", shadow)
    saved_path = list(sys.path)
    app = runtime.create_macro_mcp_app(App, name="test")
    assert sys.modules["cores"] is shadow
    assert sys.path == saved_path
    assert "perplexity" in app.settings.mcp.servers


def test_direct_api_default(monkeypatch):
    monkeypatch.delenv("OPENAI_BASE_URL")
    app = runtime.create_macro_mcp_app(App, name="test")
    assert app.settings.openai.base_url == "https://api.openai.com/v1"


def test_proxy_without_api_key(monkeypatch):
    registry, _ = runtime._report_configuration()
    monkeypatch.setattr(runtime, "_report_configuration", lambda: (registry, None))
    app = runtime.create_macro_mcp_app(App, name="test")
    assert app.settings.openai.api_key == "chatgpt-oauth-placeholder"


def test_missing_provider_fails_without_exposing_credentials(monkeypatch):
    registry, _ = runtime._report_configuration()
    monkeypatch.setattr(runtime, "_report_configuration", lambda: (registry, None))
    monkeypatch.delenv("OPENAI_BASE_URL")
    with pytest.raises(RuntimeError, match="requires OpenAI credentials"):
        runtime.create_macro_mcp_app(App, name="test")
