"""Isolated Codex CLI backend using ChatGPT-managed OAuth Fast mode.

This is not a generic OpenAI SDK wrapper.  It invokes Codex non-interactively
with a dedicated CODEX_HOME, read-only sandbox, ephemeral thread, and no repo
rules.  Trading callers must retain an existing backend as fallback.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import signal
import stat
import sys
import threading

# Fixed argv, no shell, and a permission-checked executable.
import subprocess  # nosec B404
import tempfile
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Literal


from prism_core.codex_config import (
    CodexFastError as CodexFastError,
    SUPPORTED_MODELS as SUPPORTED_MODELS,
    SUPPORTED_REASONING_EFFORTS as SUPPORTED_REASONING_EFFORTS,
    validate_timeout as validate_timeout,
)


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


@dataclass
class CodexDiagnosticState:
    """Per-call host evidence; never infer cleanup from an exception message."""
    phase: str = "NO_PROCESS"
    returncode: int | None = None
    group_cleanup_confirmed: bool = False


McpProfile = Literal["kr_trading", "us_trading"]
SUPPORTED_MCP_PROFILES = frozenset({"kr_trading", "us_trading"})
logger = logging.getLogger(__name__)

# communicate() must continue draining both pipes. Check its cumulative buffers
# at each 100ms poll; this is a fail-fast budget, not a hard OS memory ceiling
# (a fast child may overshoot between polls). Telemetry retains no full stream.
_MAX_OUTPUT_BYTES = 32 * 1024 * 1024
_MAX_TELEMETRY_LINE = 64 * 1024
_MAX_STAGE_EVENTS = 256
_STDIN_WRITE_BUDGET = 64 * 1024
_SAFE_SERVERS = frozenset({"time", "sqlite", "perplexity", "kospi_kosdaq", "yahoo_finance"})
_SAFE_TOOLS = frozenset({
    "get_current_time", "list_tables", "describe_table", "read_query", "perplexity_ask",
    "get_stock_ohlcv", "get_stock_market_cap", "get_stock_trading_volume",
    "get_index_ohlcv", "get_ticker_name", "get_historical_stock_prices",
    "get_stock_info", "get_yahoo_finance_news", "get_stock_actions",
    "get_financial_statement", "get_holder_info", "get_option_expiration_dates",
    "get_option_chain", "get_recommendations",
})


class _StdinPump:
    """Own a detached POSIX stdin pipe; never retry/resend accepted bytes.

    communicate(input=None) drains stdout/stderr only. Retrying communicate with
    None cannot reliably finish partially written input on CPython POSIX, so the
    public stdin stream is detached before the first communicate call.
    """

    def __init__(self, stream, payload):
        self.stream = stream
        self.data = memoryview(payload)
        self.total = len(payload)
        self.sent = 0
        self.closed = False
        os.set_blocking(stream.fileno(), False)

    def advance(self):
        budget = _STDIN_WRITE_BUDGET
        while not self.closed and self.sent < self.total and budget:
            try:
                count = os.write(self.stream.fileno(), self.data[self.sent:self.sent + budget])
            except (BlockingIOError, InterruptedError):
                return
            if count <= 0:
                raise OSError("stdin write made no progress")
            self.sent += count
            budget -= count
        if self.sent == self.total:
            self.close()  # The child receives EOF only after the entire payload.

    def close(self):
        if not self.closed:
            self.stream.close()
            self.closed = True


class _StreamTelemetry:
    """Incremental cursor over communicate's cumulative snapshots; bounded state."""

    def __init__(self, emit):
        self.emit = emit
        self.offset = 0
        self.pending = b""
        self.dropping = False
        self.first_seen = False
        self.agent_message_seen = self.model_final_seen = False
        self.last_stage = "start"
        self.mcp_started = self.mcp_completed = self.mcp_errors = 0
        self.active = {}
        self.emitted = 0

    def consume(self, cumulative, *, final=False):
        if cumulative is None:
            return
        raw = cumulative.encode("utf-8") if isinstance(cumulative, str) else cumulative
        # Pipes remain binary through completion: no universal-newline offset
        # mismatch. Only scan the new suffix; CR and CRLF are line boundaries.
        data = raw[self.offset:].replace(b"\r", b"\n")
        self.offset = len(raw)
        start = 0
        while start < len(data):
            end = data.find(b"\n", start)
            stop = len(data) if end < 0 else end
            if not self.dropping:
                if len(self.pending) + stop - start <= _MAX_TELEMETRY_LINE:
                    self.pending += data[start:stop]
                else:
                    self.pending = b""
                    self.dropping = True
            if end < 0:
                break
            if not self.dropping:
                self._line(self.pending)
            self.pending = b""
            self.dropping = False
            start = end + 1
        if final and self.pending:
            self._line(self.pending)
            self.pending = b""

    def _stage(self, category, **metadata):
        self.last_stage = category
        if self.emitted < _MAX_STAGE_EVENTS:
            self.emit(category, **metadata)
            self.emitted += 1

    def _line(self, line):
        try:
            event = json.loads(line)
        except (ValueError, UnicodeError, RecursionError):
            return
        if not isinstance(event, dict):
            return
        if not self.first_seen:
            self.first_seen = True
            self._stage("first_event")
        kind = event.get("type")
        item = event.get("item")
        if isinstance(item, dict) and item.get("type") == "mcp_tool_call":
            # Raw IDs are internal only, length/count capped, never emitted.
            key = item.get("id")
            key = key if isinstance(key, str) and len(key) <= 256 else None
            server = item.get("server")
            tool = item.get("tool")
            metadata = dict(
                server=server if isinstance(server, str) and server in _SAFE_SERVERS else "other",
                tool=tool if isinstance(tool, str) and tool in _SAFE_TOOLS else "other",
            )
            if kind == "item.started":
                self.mcp_started += 1
                if key is not None and len(self.active) < _MAX_STAGE_EVENTS:
                    self.active.setdefault(key, time.monotonic())
                self._stage("mcp_started", **metadata)
            elif kind == "item.completed":
                self.mcp_completed += 1
                failed = bool(item.get("error")) or item.get("status") == "failed"
                self.mcp_errors += int(failed)
                began = self.active.pop(key, None)
                self._stage("mcp_error" if failed else "mcp_completed",
                            tool_s=-1 if began is None else time.monotonic() - began, **metadata)
        elif kind == "item.completed" and isinstance(item, dict) and item.get("type") == "agent_message":
            self.agent_message_seen = True
            self._stage("agent_message")
        elif kind == "turn.completed":
            if self.agent_message_seen and not self.model_final_seen:
                self.model_final_seen = True
                self._stage("model_final")
            self._stage("turn_completed")
        elif kind in ("turn.failed", "error"):
            self._stage("turn_error")


def _resolve_codex_executable(candidate: str) -> str:
    """Resolve an executable that cannot be replaced by another local user."""
    located = candidate if os.path.isabs(candidate) else shutil.which(candidate)
    if not located:
        raise CodexFastError("Codex executable not found")
    try:
        executable = Path(located).resolve(strict=True)
        mode = stat.S_IMODE(executable.stat().st_mode)
    except (OSError, ValueError):
        raise CodexFastError("Codex executable is unavailable") from None
    if not executable.is_file() or not os.access(executable, os.X_OK):
        raise CodexFastError("Codex executable is not runnable")
    if mode & (stat.S_IWGRP | stat.S_IWOTH):
        raise CodexFastError("Codex executable has unsafe permissions")
    return str(executable)


def _command(
    codex_bin: str,
    model: str,
    mcp_profile: McpProfile | None,
    reasoning_effort: str | None = None,
) -> list[str]:
    if model not in SUPPORTED_MODELS:
        raise CodexFastError("Unsupported Codex model")
    if mcp_profile is not None and mcp_profile not in SUPPORTED_MCP_PROFILES:
        raise CodexFastError("Unsupported Codex MCP profile")
    if reasoning_effort is not None and reasoning_effort not in SUPPORTED_REASONING_EFFORTS:
        raise CodexFastError("Unsupported Codex reasoning effort")
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
    if reasoning_effort is not None:
        command[2:2] = ["-c", f'model_reasoning_effort="{reasoning_effort}"']
    return command


def _terminate_owned_process(process: subprocess.Popen) -> bool:
    """Terminate only our new session, including MCP descendants, then reap."""
    group_confirmed = True

    def send(sig: int) -> None:
        nonlocal group_confirmed
        try:
            if os.name == "posix":
                os.killpg(process.pid, sig)
            elif process.poll() is None:
                if sig == signal.SIGTERM:
                    process.terminate()
                else:
                    process.kill()
        except ProcessLookupError:
            pass
        except PermissionError:
            # macOS can reject signaling an already exited child group. The
            # final signal was attempted before polling/reaping the leader.
            if sys.platform != "darwin" or sig != signal.SIGKILL or process.poll() is None:
                raise
            group_confirmed = False

    send(signal.SIGTERM)
    # Do not reap the leader until the final group signal: its PID cannot be
    # reused while it remains our unreaped child. Descendants may ignore TERM.
    time.sleep(0.2)
    send(signal.SIGKILL if os.name == "posix" else signal.SIGTERM)
    if os.name != "posix" and process.poll() is None:
        process.kill()
    try:
        process.communicate(timeout=1)
    except subprocess.TimeoutExpired:
        # A detached descendant might retain pipes; never wait on those forever.
        for pipe in (process.stdin, process.stdout, process.stderr):
            if pipe is not None:
                pipe.close()
        process.wait(timeout=1)
    return group_confirmed


def _parse_stream(
    stream: str,
) -> tuple[str | None, dict | None, tuple[CodexMcpCall, ...]]:
    final = None
    usage = None
    mcp_calls: list[CodexMcpCall] = []
    for line in stream.splitlines():
        try:
            event = json.loads(line)
        except (ValueError, RecursionError):
            continue
        if not isinstance(event, dict):
            continue
        item = event.get("item")
        item = item if isinstance(item, dict) else {}
        if (
            event.get("type") == "item.completed"
            and item.get("type") == "agent_message"
        ):
            final = event["item"].get("text")
        if event.get("type") == "turn.completed":
            usage = event.get("usage")
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
    timeout: float = 90,
    codex_bin: str | None = None,
    codex_home: str | None = None,
    mcp_profile: McpProfile | None = None,
    require_mcp_calls: bool = False,
    reasoning_effort: str | None = None,
    _cancel_event: threading.Event | None = None,
    _diagnostic_environment: dict[str, str] | None = None,
    _diagnostic_parent_fd: int | None = None,
    _diagnostic_state: CodexDiagnosticState | None = None,
) -> CodexFastResult:
    try:
        timeout = validate_timeout(timeout)
    except CodexFastError:
        raise CodexFastError("Codex timeout must be a finite number in (0, 600]") from None
    telemetry_started = time.monotonic()
    request_id = uuid.uuid4().hex
    pump = None

    def log_event(category: str, returncode: int | None = None, *,
                  server="none", tool="none", tool_s=-1) -> None:
        # Only allowlisted configuration and numeric process metadata. Never
        # include exception text, prompts, streams, paths, or MCP payloads.
        logger.log(
            logging.WARNING if category in {"timeout", "cancelled", "launch_error", "process_io_error", "nonzero_exit", "missing_final", "missing_successful_mcp", "output_limit", "cleanup_unconfirmed", "unsupported_platform", "incomplete_stdin"} else logging.INFO,
            "[CODEX_FAST] category=%s model=%s effort=%s profile=%s "
            "timeout_s=%g elapsed_s=%.3f rc=%s request_id=%s last_stage=%s "
            "mcp_started=%d mcp_completed=%d mcp_errors=%d mcp_pending=%d "
            "server=%s tool=%s tool_s=%.3f stdin_total_bytes=%d stdin_sent_bytes=%d stdin_closed=%d",
            category,
            model if model in SUPPORTED_MODELS else "invalid",
            reasoning_effort if reasoning_effort in SUPPORTED_REASONING_EFFORTS else (
                "default" if reasoning_effort is None else "invalid"
            ),
            mcp_profile if mcp_profile in SUPPORTED_MCP_PROFILES else (
                "none" if mcp_profile is None else "invalid"
            ),
            timeout, time.monotonic() - telemetry_started, returncode,
            request_id, telemetry.last_stage, telemetry.mcp_started,
            telemetry.mcp_completed, telemetry.mcp_errors, len(telemetry.active),
            server, tool, tool_s,
            pump.total if pump is not None else 0,
            pump.sent if pump is not None else 0,
            int(pump.closed) if pump is not None else 0,
        )

    telemetry = _StreamTelemetry(log_event)

    def check_output(stdout, stderr):
        # Real pipe snapshots are bytes; tolerate text in injected test adapters.
        size = sum(len(value.encode("utf-8") if isinstance(value, str) else value or b"")
                   for value in (stdout, stderr))
        if size > _MAX_OUTPUT_BYTES:
            log_event("output_limit")
            raise CodexFastError("Codex Fast output limit exceeded")

    log_event("start")
    if os.name != "posix":
        log_event("unsupported_platform")
        raise CodexFastError("Codex Fast requires POSIX nonblocking pipes; use fallback")
    try:
        executable = _resolve_codex_executable(
            codex_bin or os.environ.get("PRISM_CODEX_BIN", "codex")
        )
    except CodexFastError:
        log_event("launch_error")
        raise CodexFastError("Codex Fast executable unavailable") from None
    home = codex_home or os.environ.get("PRISM_CODEX_HOME")
    environment = os.environ.copy() if _diagnostic_environment is None else dict(_diagnostic_environment)
    if any(not isinstance(key, str) or not isinstance(value, str) or "\0" in key + value for key, value in environment.items()):
        raise CodexFastError("Invalid diagnostic environment")
    if _diagnostic_parent_fd is not None:
        import fcntl
        if (type(_diagnostic_parent_fd) is not int or _diagnostic_parent_fd < 3
                or not stat.S_ISFIFO(os.fstat(_diagnostic_parent_fd).st_mode)
                or fcntl.fcntl(_diagnostic_parent_fd, fcntl.F_GETFL) & os.O_ACCMODE != os.O_RDONLY):
            raise CodexFastError("Invalid diagnostic parent lease")
    if home:
        environment["CODEX_HOME"] = home
    started = time.monotonic()
    process = None
    try:
        with tempfile.TemporaryDirectory(prefix="prism-codex-fast-") as run_dir:
            # The executable is resolved, permission-checked, and invoked with a
            # fixed argv list. No shell parsing or untrusted option expansion occurs.
            # nosemgrep: python.lang.security.audit.dangerous-subprocess-use-tainted-env-args.dangerous-subprocess-use-tainted-env-args, python.lang.security.audit.dangerous-subprocess-use-audit.dangerous-subprocess-use-audit
            if _diagnostic_state is not None:
                _diagnostic_state.phase = "LAUNCH_ATTEMPTED_UNKNOWN"
            process = subprocess.Popen(  # nosec B603
                _command(executable, model, mcp_profile, reasoning_effort),  # nosemgrep
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=False,
                cwd=run_dir,
                env=environment,
                # MCP stdio children are managed by Codex. Keep their shutdown
                # signals out of the long-lived PRISM orchestrator process group.
                start_new_session=os.name == "posix",
                **({"pass_fds": (_diagnostic_parent_fd,)} if _diagnostic_parent_fd is not None else {}),
            )
            if _diagnostic_state is not None:
                _diagnostic_state.phase = "SPAWNED"
            stdin_stream = process.stdin
            process.stdin = None  # Public pipe ownership transfers to our pump.
            try:
                prompt = _prompt(system_prompt, user_prompt, mcp_profile).encode("utf-8")
                pump = _StdinPump(stdin_stream, prompt)
                while True:
                    if _cancel_event is not None and _cancel_event.is_set():
                        log_event("cancelled")
                        raise CodexFastError("Codex Fast cancelled")
                    remaining = timeout - (time.monotonic() - started)
                    if remaining <= 0:
                        log_event("timeout")
                        raise CodexFastError(f"Codex Fast timed out after {timeout:g}s")
                    pump.advance()
                    remaining = timeout - (time.monotonic() - started)
                    if remaining <= 0:
                        continue
                    try:
                        # A short drain poll while input remains prevents pipe-size
                        # dependent throttling; completed input retains 100ms polls.
                        poll = .01 if not pump.closed else .1
                        stdout, stderr = process.communicate(input=None, timeout=min(poll, remaining))
                        break
                    except subprocess.TimeoutExpired as exc:
                        check_output(exc.output, exc.stderr)
                        telemetry.consume(exc.output)
            except BaseException:
                try:
                    if pump is not None:
                        pump.close()
                    elif stdin_stream is not None:
                        stdin_stream.close()
                finally:
                    group_confirmed = _terminate_owned_process(process)
                    if _diagnostic_state is not None:
                        _diagnostic_state.phase = "TERMINATED"
                        _diagnostic_state.returncode = process.returncode
                        _diagnostic_state.group_cleanup_confirmed = group_confirmed is True and process.returncode is not None
                    log_event("cleanup_unconfirmed" if group_confirmed is False else "cleanup_completed")
                raise
            finally:
                if pump is not None:
                    pump.close()
                elif stdin_stream is not None:
                    stdin_stream.close()
            # communicate has reaped the leader. Do not signal its now-reusable
            # PID/group if completed-output validation fails.
            if _diagnostic_state is not None:
                _diagnostic_state.phase = "REAPED"
                _diagnostic_state.returncode = process.returncode
            check_output(stdout, stderr)
            telemetry.consume(stdout, final=True)
    except (OSError, subprocess.TimeoutExpired, UnicodeError, ValueError):
        log_event("launch_error" if process is None else "process_io_error")
        raise CodexFastError("Codex Fast unavailable") from None
    latency = time.monotonic() - started
    if process.returncode != 0:
        log_event("nonzero_exit", process.returncode)
        raise CodexFastError(
            f"Codex Fast rc={process.returncode}"
        )
    if pump.sent != pump.total:
        log_event("incomplete_stdin", process.returncode)
        raise CodexFastError("Codex Fast exited before full prompt transmission")
    try:
        stream = stdout.decode("utf-8") if isinstance(stdout, bytes) else stdout
    except UnicodeError:
        log_event("process_io_error", process.returncode)
        raise CodexFastError("Codex Fast invalid output encoding") from None
    text, usage, mcp_calls = _parse_stream(stream)
    if not text:
        log_event("missing_final", process.returncode)
        raise CodexFastError("Codex Fast returned no final agent message")
    if require_mcp_calls and not any(
        call.status == "completed" and not call.error for call in mcp_calls
    ):
        log_event("missing_successful_mcp", process.returncode)
        raise CodexFastError("Codex Fast returned no MCP tool calls")
    log_event("success", process.returncode)
    return CodexFastResult(
        text=text,
        latency_s=latency,
        usage=usage,
        mcp_calls=mcp_calls,
    )


async def generate_codex_fast_async(**kwargs) -> CodexFastResult:
    """Keep the loop responsive and finish child cleanup before cancellation."""
    cancel_event = threading.Event()
    worker = asyncio.create_task(asyncio.to_thread(
        generate_codex_fast, **kwargs, _cancel_event=cancel_event,
    ))
    try:
        return await asyncio.shield(worker)
    except asyncio.CancelledError:
        cancel_event.set()
        while not worker.done():
            try:
                await asyncio.shield(worker)
            except asyncio.CancelledError:
                continue
            except Exception:
                break
        if worker.done() and not worker.cancelled():
            worker.exception()
        raise
