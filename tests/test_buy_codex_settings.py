from dataclasses import FrozenInstanceError
from pathlib import Path
import subprocess
import sys

import pytest

from prism_core.codex_config import BuyCodexSettings, resolve_buy_codex_settings, resolve_sell_codex_settings
from cores.llm.codex_oauth_fast_backend import CodexFastError, _command


def test_defaults_are_unchanged_and_frozen():
    settings = resolve_buy_codex_settings({})
    assert (settings.model, settings.reasoning_effort, settings.timeout) == ("gpt-5.6-sol", None, 90)
    with pytest.raises(FrozenInstanceError):
        settings.model = "gpt-6-astra"
    assert not any("model_reasoning_effort" in arg for arg in _command("codex", settings.model, None))


def test_sell_defaults_frozen_and_buy_import_compatible():
    settings = resolve_sell_codex_settings({})
    assert isinstance(resolve_buy_codex_settings({}), BuyCodexSettings)
    assert (settings.model, settings.reasoning_effort, settings.timeout) == ("gpt-5.6-sol", None, 90)
    with pytest.raises(FrozenInstanceError):
        settings.timeout = 120
    assert resolve_sell_codex_settings({"PRISM_CODEX_FAST_TIMEOUT": "120"}).timeout == 120


def test_buy_sell_overrides_are_independent():
    env = {"PRISM_BUY_CODEX_MODEL": "gpt-6-astra", "PRISM_BUY_CODEX_EFFORT": "high",
           "PRISM_BUY_CODEX_TIMEOUT": "240", "PRISM_SELL_CODEX_MODEL": "gpt-5.6-sol",
           "PRISM_SELL_CODEX_EFFORT": "medium", "PRISM_SELL_CODEX_TIMEOUT": "120",
           "PRISM_CODEX_FAST_TIMEOUT": "55"}
    assert resolve_buy_codex_settings(env) == BuyCodexSettings("gpt-6-astra", "high", 240)
    assert resolve_sell_codex_settings(env) == BuyCodexSettings("gpt-5.6-sol", "medium", 120)
    assert resolve_sell_codex_settings({k: v for k, v in env.items() if "SELL" not in k}) == BuyCodexSettings("gpt-5.6-sol", None, 55)
    assert resolve_buy_codex_settings({k: v for k, v in env.items() if "BUY" not in k}) == BuyCodexSettings("gpt-5.6-sol", None, 55)


@pytest.mark.parametrize("key,value", [("MODEL", "arbitrary"), ("EFFORT", ""), ("EFFORT", "invalid"), *[("TIMEOUT", value) for value in ["", "nan", "inf", "-1", "0", "601", "abc"]]])
def test_invalid_sell_settings_fail_closed(key, value):
    with pytest.raises(CodexFastError):
        resolve_sell_codex_settings({f"PRISM_SELL_CODEX_{key}": value})


def test_sell_environment_resolution(monkeypatch):
    monkeypatch.setenv("PRISM_SELL_CODEX_MODEL", "gpt-6-astra")
    monkeypatch.setenv("PRISM_SELL_CODEX_EFFORT", "high")
    monkeypatch.setenv("PRISM_SELL_CODEX_TIMEOUT", "120")
    assert resolve_sell_codex_settings() == BuyCodexSettings("gpt-6-astra", "high", 120)


def test_explicit_astra_effort_and_timeout_precedence():
    settings = resolve_buy_codex_settings({"PRISM_BUY_CODEX_MODEL": "gpt-6-astra", "PRISM_BUY_CODEX_EFFORT": "high", "PRISM_BUY_CODEX_TIMEOUT": "120", "PRISM_CODEX_FAST_TIMEOUT": "60"})
    assert (settings.model, settings.reasoning_effort, settings.timeout) == ("gpt-6-astra", "high", 120)
    command = _command("codex", settings.model, "kr_trading", settings.reasoning_effort)
    assert 'model_reasoning_effort="high"' in command
    assert 'service_tier="fast"' in command
    assert "--ephemeral" in command and "read-only" in command
    assert resolve_buy_codex_settings({"PRISM_CODEX_FAST_TIMEOUT": "55"}).timeout == 55


@pytest.mark.parametrize("key,value", [("PRISM_BUY_CODEX_MODEL", "arbitrary"), ("PRISM_BUY_CODEX_EFFORT", "invalid"), ("PRISM_BUY_CODEX_EFFORT", ""), *[("PRISM_BUY_CODEX_TIMEOUT", value) for value in ["", "nan", "inf", "-1", "0", "601", "abc"]]])
def test_invalid_settings_fail_closed(key, value):
    with pytest.raises(CodexFastError):
        resolve_buy_codex_settings({key: value})


@pytest.mark.parametrize("effort", ["low", "medium", "high", "xhigh", "max", "ultra"])
def test_effort_allowlist(effort):
    assert f'model_reasoning_effort="{effort}"' in _command("codex", "gpt-6-astra", None, effort)
    with pytest.raises(CodexFastError):
        _command("codex", "gpt-6-astra", None, effort + '";evil')


def test_environment_resolution_and_timeout_boundary(monkeypatch):
    monkeypatch.setenv("PRISM_BUY_CODEX_MODEL", "gpt-6-astra")
    monkeypatch.setenv("PRISM_BUY_CODEX_EFFORT", "ultra")
    monkeypatch.setenv("PRISM_BUY_CODEX_TIMEOUT", "600")
    settings = resolve_buy_codex_settings()
    assert (settings.model, settings.reasoning_effort, settings.timeout) == ("gpt-6-astra", "ultra", 600)
    assert resolve_buy_codex_settings({"PRISM_BUY_CODEX_TIMEOUT": "0.01"}).timeout == 0.01


def test_settings_and_file_loaded_backend_with_us_cores_namespace():
    root = Path(__file__).resolve().parents[1]
    script = '''
import importlib.util
import pathlib
import sys
root = pathlib.Path.cwd()
sys.path.insert(0, str(root / "prism-us"))
import cores
assert "prism-us" in cores.__file__
from prism_core.codex_config import CodexFastError, resolve_buy_codex_settings
spec = importlib.util.spec_from_file_location("codex_oauth_fast_backend", root / "cores/llm/codex_oauth_fast_backend.py")
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)
assert module.CodexFastError is CodexFastError
assert resolve_buy_codex_settings({}).model == "gpt-5.6-sol"
'''
    result = subprocess.run([sys.executable, "-c", script], cwd=root, capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
