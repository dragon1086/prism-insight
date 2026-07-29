"""Fail-closed lifecycle for the repository's existing ChatGPT OAuth LLM path."""

from __future__ import annotations

import os
from pathlib import Path
from threading import Lock
from typing import Awaitable, Callable

from cores.chatgpt_proxy import clear_env as _clear_env
from cores.chatgpt_proxy import inject_env as _inject_env
from cores.chatgpt_proxy import start_proxy as _start_proxy
from cores.chatgpt_proxy import stop_proxy as _stop_proxy
from cores.llm.backends.openai_agents_backend import (
    OpenAIAgentsBackend,
    configure_openai_agents_for_proxy,
)
from cores.llm.mcp_registry import McpServerRegistry


CHATGPT_OAUTH_DEFAULT_MODEL = "gpt-5.4-mini"


class OAuthLLMUnavailable(RuntimeError):
    """Sanitized capability failure; token and provider details never escape."""


class ChatGPTOAuthRuntime:
    """Own one in-process OAuth proxy and restore process environment on exit.

    The returned backend has an empty MCP registry. Structured Phase 1 evaluators
    therefore remain tool-free even though they reuse the legacy OAuth transport.
    """

    _process_lock = Lock()

    def __init__(
        self,
        *,
        token_path: Path | None = None,
        inject_env: Callable[[], None] = _inject_env,
        clear_env: Callable[[], None] = _clear_env,
        start_proxy: Callable[[], Awaitable[bool]] = _start_proxy,
        stop_proxy: Callable[[], Awaitable[None]] = _stop_proxy,
        configure_backend: Callable[[str], None] = configure_openai_agents_for_proxy,
    ) -> None:
        self._token_path = token_path or (
            Path.home() / ".config" / "prism-insight" / "chatgpt_auth.json"
        )
        self._inject_env = inject_env
        self._clear_env = clear_env
        self._start_proxy = start_proxy
        self._stop_proxy = stop_proxy
        self._configure_backend = configure_backend
        self._started = False
        self._lock_held = False
        self._prior_env: dict[str, str | None] | None = None

    async def __aenter__(self) -> OpenAIAgentsBackend:
        if os.environ.get("PRISM_OPENAI_AUTH_MODE") != "chatgpt_oauth":
            raise OAuthLLMUnavailable(
                "Phase 1 LLM requires PRISM_OPENAI_AUTH_MODE=chatgpt_oauth"
            )
        if not self._token_path.is_file():
            raise OAuthLLMUnavailable("ChatGPT OAuth capability unavailable")
        if not self._process_lock.acquire(blocking=False):
            raise OAuthLLMUnavailable(
                "ChatGPT OAuth process-global configuration is already active"
            )
        self._lock_held = True

        self._prior_env = {
            name: os.environ.get(name) for name in ("OPENAI_BASE_URL", "OPENAI_API_KEY")
        }
        try:
            self._inject_env()
            if not await self._start_proxy():
                raise OAuthLLMUnavailable("ChatGPT OAuth capability unavailable")
            self._started = True
            base_url = os.environ.get("OPENAI_BASE_URL")
            if not base_url or not base_url.startswith("http://localhost:"):
                raise OAuthLLMUnavailable("ChatGPT OAuth capability unavailable")
            self._configure_backend(base_url)
            return OpenAIAgentsBackend(McpServerRegistry({}))
        except BaseException:
            await self._cleanup()
            raise

    async def __aexit__(self, exc_type, exc, traceback) -> None:
        await self._cleanup()

    async def _cleanup(self) -> None:
        try:
            if self._started:
                await self._stop_proxy()
        finally:
            self._started = False
            self._clear_env()
            if self._prior_env is not None:
                for name, value in self._prior_env.items():
                    if value is None:
                        os.environ.pop(name, None)
                    else:
                        os.environ[name] = value
                self._prior_env = None
            if self._lock_held:
                self._lock_held = False
                self._process_lock.release()
