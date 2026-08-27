"""Isolated Codex CLI backend using ChatGPT-managed OAuth Fast mode.

This is not a generic OpenAI SDK wrapper.  It invokes Codex non-interactively
with a dedicated CODEX_HOME, read-only sandbox, ephemeral thread, and no repo
rules.  Trading callers must retain an existing backend as fallback.
"""
from __future__ import annotations

import json
import os
import shutil
import stat

# Fixed argv, no shell, and a permission-checked executable.
import subprocess  # nosec B404
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Literal


class CodexFastError(RuntimeError):
    pass


@dataclass(frozen=True)
class CodexMcpCall:
    server: str
    tool: str
    arguments: dict
    status: str | None
    error: object | None


@dataclass(frozen=True)
class CodexFastResult:
    text: str
    latency_s: float
    usage: dict | None
    mcp_calls: tuple[CodexMcpCall, ...] = ()


McpProfile = Literal["kr_trading", "us_trading"]
SUPPORTED_MODELS = frozenset({"gpt-5.6-sol"})
SUPPORTED_MCP_PROFILES = frozenset({"kr_trading", "us_trading"})


def _resolve_codex_executable(candidate: str) -> str:
    """Resolve an executable that cannot be replaced by another local user."""
    located = candidate if os.path.isabs(candidate) else shutil.which(candidate)
    if not located:
        raise CodexFastError(f"Codex executable not found: {candidate}")
    try:
        executable = Path(located).resolve(strict=True)
        mode = stat.S_IMODE(executable.stat().st_mode)
    except OSError as exc:
        raise CodexFastError(f"Codex executable is unavailable: {candidate}") from exc
    if not executable.is_file() or not os.access(executable, os.X_OK):
        raise CodexFastError(f"Codex executable is not runnable: {executable}")
    if mode & (stat.S_IWGRP | stat.S_IWOTH):
        raise CodexFastError(f"Codex executable has unsafe permissions: {executable}")
    return str(executable)


def _command(
    codex_bin: str,
    model: str,
    mcp_profile: McpProfile | None,
) -> list[str]:
    if model not in SUPPORTED_MODELS:
        raise CodexFastError(f"Unsupported Codex model: {model}")
    if mcp_profile is not None and mcp_profile not in SUPPORTED_MCP_PROFILES:
        raise CodexFastError(f"Unsupported Codex MCP profile: {mcp_profile}")
    command = [
        codex_bin,
        "exec",
        "--ephemeral",
        "--sandbox", "read-only",
        "--skip-git-repo-check",
        "--ignore-rules",
        "--model", model,
        "-c", 'service_tier="fast"',
        "-c", "features.fast_mode=true",
        "--json",
        "-",
    ]
    if mcp_profile:
        command[2:2] = ["--profile", mcp_profile]
    return command


def _parse_stream(
    stream: str,
) -> tuple[str | None, dict | None, tuple[CodexMcpCall, ...]]:
    final = None
    usage = None
    mcp_calls: list[CodexMcpCall] = []
    for line in stream.splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if (
            event.get("type") == "item.completed"
            and event.get("item", {}).get("type") == "agent_message"
        ):
            final = event["item"].get("text")
        if event.get("type") == "turn.completed":
            usage = event.get("usage")
        item = event.get("item", {})
        if (
            event.get("type") == "item.completed"
            and item.get("type") == "mcp_tool_call"
        ):
            mcp_calls.append(
                CodexMcpCall(
                    server=str(item.get("server") or ""),
                    tool=str(item.get("tool") or ""),
                    arguments=item.get("arguments") or {},
                    status=item.get("status"),
                    error=item.get("error"),
                )
            )
    return final, usage, tuple(mcp_calls)


def _prompt(
    system_prompt: str,
    user_prompt: str,
    mcp_profile: McpProfile | None,
) -> str:
    if mcp_profile:
        execution_constraints = """- 설정된 MCP 조회 도구를 사용해 최신 데이터와 포트폴리오를 확인하세요.
- 셸, 파일 읽기/쓰기, 내장 웹 검색은 사용하지 말고 MCP 도구만 사용하세요.
- SQLite에는 조회 도구만 사용하고 어떤 데이터도 생성·수정·삭제하지 마세요.
- 도구 조회를 마친 뒤 시스템 프롬프트가 요구하는 JSON 객체 하나만 출력하세요."""
    else:
        execution_constraints = """- 도구, 셸, 파일, 웹 검색을 사용하지 마세요.
- 제공된 보고서·포트폴리오·결정적 사실만 사용하세요.
- 시스템 프롬프트가 요구하는 JSON 객체 하나만 출력하세요."""
    return f"""{system_prompt}

[실행 제약]
{execution_constraints}
- 코드펜스나 JSON 밖의 설명을 쓰지 마세요.

[사용자 메시지]
{user_prompt}
"""


def generate_codex_fast(
    *,
    system_prompt: str,
    user_prompt: str,
    model: str = "gpt-5.6-sol",
    timeout: int = 90,
    codex_bin: str | None = None,
    codex_home: str | None = None,
    mcp_profile: McpProfile | None = None,
    require_mcp_calls: bool = False,
) -> CodexFastResult:
    executable = _resolve_codex_executable(
        codex_bin or os.environ.get("PRISM_CODEX_BIN", "codex")
    )
    home = codex_home or os.environ.get("PRISM_CODEX_HOME")
    environment = os.environ.copy()
    if home:
        environment["CODEX_HOME"] = home
    started = time.monotonic()
    try:
        with tempfile.TemporaryDirectory(prefix="prism-codex-fast-") as run_dir:
            # The executable is resolved, permission-checked, and invoked with a
            # fixed argv list. No shell parsing or untrusted option expansion occurs.
            # nosemgrep: python.lang.security.audit.dangerous-subprocess-use-tainted-env-args.dangerous-subprocess-use-tainted-env-args, python.lang.security.audit.dangerous-subprocess-use-audit.dangerous-subprocess-use-audit
            process = subprocess.run(  # nosec B603
                _command(executable, model, mcp_profile),  # nosemgrep
                input=_prompt(system_prompt, user_prompt, mcp_profile),
                capture_output=True,
                text=True,
                cwd=run_dir,
                env=environment,
                timeout=timeout,
                check=False,
                # MCP stdio children are managed by Codex. Keep their shutdown
                # signals out of the long-lived PRISM orchestrator process group.
                start_new_session=True,
            )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise CodexFastError(f"Codex Fast unavailable: {exc}") from exc
    latency = time.monotonic() - started
    if process.returncode != 0:
        raise CodexFastError(
            f"Codex Fast rc={process.returncode}: {process.stderr[-300:]}"
        )
    text, usage, mcp_calls = _parse_stream(process.stdout)
    if not text:
        raise CodexFastError("Codex Fast returned no final agent message")
    if require_mcp_calls and not any(
        call.status == "completed" and not call.error for call in mcp_calls
    ):
        raise CodexFastError("Codex Fast returned no MCP tool calls")
    return CodexFastResult(
        text=text,
        latency_s=latency,
        usage=usage,
        mcp_calls=mcp_calls,
    )
