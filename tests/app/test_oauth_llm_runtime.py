from __future__ import annotations

import os
from pathlib import Path

import pytest

from cores.llm.backends.openai_agents_backend import OpenAIAgentsBackend
from prism_app.oauth_llm import ChatGPTOAuthRuntime, OAuthLLMUnavailable


@pytest.mark.asyncio
async def test_oauth_runtime_bootstraps_existing_proxy_and_restores_environment(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    token_path = tmp_path / "chatgpt_auth.json"
    token_path.write_text("{}", encoding="utf-8")
    monkeypatch.setenv("PRISM_OPENAI_AUTH_MODE", "chatgpt_oauth")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://preexisting.invalid/v1")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    calls: list[object] = []

    def inject_env() -> None:
        calls.append("inject")
        os.environ["OPENAI_BASE_URL"] = "http://localhost:18741/v1"
        os.environ["OPENAI_API_KEY"] = "internal-placeholder"

    async def start_proxy() -> bool:
        calls.append("start")
        return True

    async def stop_proxy() -> None:
        calls.append("stop")

    def configure(base_url: str) -> None:
        calls.append(("configure", base_url))

    runtime = ChatGPTOAuthRuntime(
        token_path=token_path,
        inject_env=inject_env,
        start_proxy=start_proxy,
        stop_proxy=stop_proxy,
        configure_backend=configure,
    )
    async with runtime as backend:
        assert isinstance(backend, OpenAIAgentsBackend)
        assert os.environ["OPENAI_BASE_URL"] == "http://localhost:18741/v1"
        assert os.environ["OPENAI_API_KEY"] == "internal-placeholder"

    assert calls == [
        "inject",
        "start",
        ("configure", "http://localhost:18741/v1"),
        "stop",
    ]
    assert os.environ["OPENAI_BASE_URL"] == "https://preexisting.invalid/v1"
    assert "OPENAI_API_KEY" not in os.environ


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", [None, "api_key", ""])
async def test_oauth_runtime_refuses_non_oauth_mode_without_starting_proxy(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, mode: str | None
) -> None:
    token_path = tmp_path / "chatgpt_auth.json"
    token_path.write_text("{}", encoding="utf-8")
    if mode is None:
        monkeypatch.delenv("PRISM_OPENAI_AUTH_MODE", raising=False)
    else:
        monkeypatch.setenv("PRISM_OPENAI_AUTH_MODE", mode)
    started = False

    async def start_proxy() -> bool:
        nonlocal started
        started = True
        return True

    runtime = ChatGPTOAuthRuntime(token_path=token_path, start_proxy=start_proxy)
    with pytest.raises(OAuthLLMUnavailable, match="chatgpt_oauth"):
        await runtime.__aenter__()
    assert started is False


@pytest.mark.asyncio
async def test_oauth_runtime_fails_closed_when_token_or_proxy_is_unavailable(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("PRISM_OPENAI_AUTH_MODE", "chatgpt_oauth")
    runtime = ChatGPTOAuthRuntime(token_path=tmp_path / "missing.json")
    with pytest.raises(OAuthLLMUnavailable, match="OAuth capability unavailable"):
        await runtime.__aenter__()

    token_path = tmp_path / "chatgpt_auth.json"
    token_path.write_text("{}", encoding="utf-8")
    cleared = False

    def clear_env() -> None:
        nonlocal cleared
        cleared = True

    async def failed_start() -> bool:
        return False

    runtime = ChatGPTOAuthRuntime(
        token_path=token_path,
        start_proxy=failed_start,
        clear_env=clear_env,
    )
    with pytest.raises(OAuthLLMUnavailable, match="OAuth capability unavailable"):
        await runtime.__aenter__()
    assert cleared is True


@pytest.mark.asyncio
async def test_oauth_runtime_rejects_concurrent_process_global_configuration(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    token_path = tmp_path / "chatgpt_auth.json"
    token_path.write_text("{}", encoding="utf-8")
    monkeypatch.setenv("PRISM_OPENAI_AUTH_MODE", "chatgpt_oauth")

    def inject_env() -> None:
        os.environ["OPENAI_BASE_URL"] = "http://localhost:18741/v1"
        os.environ["OPENAI_API_KEY"] = "internal-placeholder"

    async def start_proxy() -> bool:
        return True

    async def stop_proxy() -> None:
        return None

    first = ChatGPTOAuthRuntime(
        token_path=token_path,
        inject_env=inject_env,
        start_proxy=start_proxy,
        stop_proxy=stop_proxy,
        configure_backend=lambda _url: None,
    )
    second = ChatGPTOAuthRuntime(
        token_path=token_path,
        inject_env=inject_env,
        start_proxy=start_proxy,
        stop_proxy=stop_proxy,
        configure_backend=lambda _url: None,
    )

    await first.__aenter__()
    try:
        with pytest.raises(OAuthLLMUnavailable, match="already active"):
            await second.__aenter__()
    finally:
        await first.__aexit__(None, None, None)
