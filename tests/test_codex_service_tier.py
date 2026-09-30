import base64
import json

import pytest

from cores.llm.codex_oauth_fast_backend import _command, codex_service_tier


@pytest.fixture
def session(tmp_path, monkeypatch):
    """Point the active-account lookup at a temp OAuth file for ``email``."""
    for key in ("PRISM_CODEX_SERVICE_TIER", "PRISM_CODEX_TIER_BY_ACCOUNT"):
        monkeypatch.delenv(key, raising=False)
    auth = tmp_path / "chatgpt_auth.json"
    monkeypatch.setenv("PRISM_CODEX_AUTH_FILE", str(auth))

    def login(email):
        claims = {"https://api.openai.com/profile": {"email": email}}
        payload = base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip("=")
        auth.write_text(json.dumps({"access_token": f"h.{payload}.s"}))

    return login


def test_fast_command_is_unchanged(session):
    cmd = _command("codex", "gpt-6-astra", "kr_trading", "medium")
    assert 'service_tier="fast"' in cmd and "features.fast_mode=true" in cmd
    assert "features.fast_mode=false" not in cmd


def test_standard_command_disables_fast_mode():
    cmd = _command("codex", "gpt-6-astra", "kr_trading", "medium", fast_tier=False)
    assert "features.fast_mode=false" in cmd
    assert not any("service_tier" in arg for arg in cmd)
    assert cmd[-2:] == ["--json", "-"]


def test_default_is_fast(session):
    session("a@example.com")
    assert codex_service_tier() == "fast"


@pytest.mark.parametrize("value", ["standard", " STANDARD ", "fast"])
def test_global_setting(session, monkeypatch, value):
    monkeypatch.setenv("PRISM_CODEX_SERVICE_TIER", value)
    assert codex_service_tier() == value.strip().lower()


def test_account_mapping_overrides_default(session, monkeypatch):
    session("Plus@Example.com")
    monkeypatch.setenv("PRISM_CODEX_TIER_BY_ACCOUNT", "pro@example.com=fast,plus@example.com=standard")
    assert codex_service_tier() == "standard"
    monkeypatch.setenv("PRISM_CODEX_SERVICE_TIER", "standard")
    session("pro@example.com")
    assert codex_service_tier() == "fast"


def test_unmapped_or_unknown_account_keeps_default(session, monkeypatch):
    monkeypatch.setenv("PRISM_CODEX_TIER_BY_ACCOUNT", "plus@example.com=standard")
    assert codex_service_tier() == "fast"  # no session file
    session("other@example.com")
    assert codex_service_tier() == "fast"


def test_invalid_value_falls_back_to_fast(session, monkeypatch):
    monkeypatch.setenv("PRISM_CODEX_SERVICE_TIER", "turbo")
    assert codex_service_tier() == "fast"


def test_command_resolves_tier_when_not_given(session, monkeypatch):
    monkeypatch.setenv("PRISM_CODEX_SERVICE_TIER", "standard")
    assert "features.fast_mode=false" in _command("codex", "gpt-6-astra", "kr_trading", "medium")
