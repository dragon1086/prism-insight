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


# --- account-aware effort (2026-09-30) -------------------------------------
import base64 as _b64
import json as _json


def _auth_file(tmp_path, email):
    claims = {"https://api.openai.com/profile": {"email": email}}
    payload = _b64.urlsafe_b64encode(_json.dumps(claims).encode()).decode().rstrip("=")
    path = tmp_path / "chatgpt_auth.json"
    path.write_text(_json.dumps({"access_token": f"h.{payload}.s", "refresh_token": "r"}))
    return str(path)


MAPPING = "dragon1086@naver.com=medium,munsangrok@gmail.com=xhigh"


def test_active_account_picks_the_effort_for_buy_and_sell(tmp_path):
    from prism_core.codex_config import resolve_buy_codex_settings, resolve_sell_codex_settings
    for email, expected in (("dragon1086@naver.com", "medium"), ("MunSangRok@gmail.com", "xhigh")):
        env = {"PRISM_BUY_CODEX_MODEL": "gpt-6-astra", "PRISM_BUY_CODEX_EFFORT": "high",
               "PRISM_SELL_CODEX_MODEL": "gpt-6-astra", "PRISM_SELL_CODEX_EFFORT": "high",
               "PRISM_CODEX_EFFORT_BY_ACCOUNT": MAPPING, "PRISM_CODEX_AUTH_FILE": _auth_file(tmp_path, email)}
        assert resolve_buy_codex_settings(env).reasoning_effort == expected
        assert resolve_sell_codex_settings(env).reasoning_effort == expected


def test_unknown_or_unreadable_account_keeps_the_configured_effort(tmp_path):
    from prism_core.codex_config import resolve_buy_codex_settings
    base = {"PRISM_BUY_CODEX_MODEL": "gpt-6-astra", "PRISM_BUY_CODEX_EFFORT": "high",
            "PRISM_CODEX_EFFORT_BY_ACCOUNT": MAPPING}
    assert resolve_buy_codex_settings({**base, "PRISM_CODEX_AUTH_FILE": _auth_file(tmp_path, "other@x.com")}).reasoning_effort == "high"
    assert resolve_buy_codex_settings({**base, "PRISM_CODEX_AUTH_FILE": str(tmp_path / "missing.json")}).reasoning_effort == "high"
    no_map = {k: v for k, v in base.items() if k != "PRISM_CODEX_EFFORT_BY_ACCOUNT"}
    assert resolve_buy_codex_settings(no_map).reasoning_effort == "high"


def test_mapping_to_an_unsupported_effort_is_rejected(tmp_path):
    import pytest
    from prism_core.codex_config import CodexFastError, resolve_buy_codex_settings
    env = {"PRISM_BUY_CODEX_MODEL": "gpt-6-astra", "PRISM_CODEX_EFFORT_BY_ACCOUNT": "dragon1086@naver.com=turbo",
           "PRISM_CODEX_AUTH_FILE": _auth_file(tmp_path, "dragon1086@naver.com")}
    with pytest.raises(CodexFastError):
        resolve_buy_codex_settings(env)
