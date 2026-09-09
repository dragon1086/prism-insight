import json
import logging
import re
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from pathlib import Path

import pytest

from cores.llm import codex_oauth_fast_backend as backend


def test_cumulative_partial_stream_is_bounded_and_safe():
    logs = []
    stream = backend._StreamTelemetry(lambda category, **kw: logs.append((category, kw)))
    events = [[], {"item": None}, {"type": "item.started", "item": {
        "type": "mcp_tool_call", "id": "SECRET", "server": "SECRET", "tool": "SECRET"}},
        {"type": "item.completed", "item": {"type": "mcp_tool_call", "id": "SECRET", "error": "SECRET"}},
        {"type": "item.completed", "item": {"type": "agent_message", "text": "SECRET"}},
        {"type": "turn.completed"}]
    data = ("garbage\n" + "\n".join(map(json.dumps, events))).encode()
    for end in range(1, len(data) + 1):
        stream.consume(data[:end])
    stream.consume(data.decode(), final=True)
    assert stream.mcp_started == stream.mcp_completed == stream.mcp_errors == 1
    assert stream.last_stage == "turn_completed"
    assert [c for c, _ in logs].count("first_event") == 1
    assert "SECRET" not in repr(logs)
    assert stream.offset == len(data)
    huge = data + b"\n" + b"x" * (backend._MAX_TELEMETRY_LINE * 3)
    stream.consume(huge)
    assert len(stream.pending) <= backend._MAX_TELEMETRY_LINE


def test_real_partial_mcp_timeout_drains_stderr_and_reports_stage(monkeypatch, caplog):
    event = json.dumps({"type": "item.started", "item": {
        "type": "mcp_tool_call", "id": "PRIVATE", "server": "time", "tool": "get_current_time"}})
    script = f"import sys,time; print({event!r},flush=True); sys.stderr.write('PRIVATE'*100000); sys.stderr.flush(); time.sleep(30)"
    monkeypatch.setattr(backend, "_resolve_codex_executable", lambda _: sys.executable)
    monkeypatch.setattr(backend, "_command", lambda *a: [sys.executable, "-c", script])
    caplog.set_level(logging.INFO)
    with pytest.raises(backend.CodexFastError, match="timed out"):
        backend.generate_codex_fast(system_prompt="PRIVATE", user_prompt="PRIVATE", timeout=.5)
    assert "category=mcp_started" in caplog.text
    assert "last_stage=mcp_started" in caplog.text
    assert any(f"category={category}" in caplog.text for category in ("cleanup_completed", "cleanup_unconfirmed"))
    assert "PRIVATE" not in caplog.text


def test_parser_ignores_non_objects():
    assert backend._parse_stream('[]\nnull\n{"item": null}\n{"type":"item.completed","item":[]}') == (None, None, ())


def test_allowlisted_profile_tools_are_named_and_unknown_tools_are_hidden():
    try:
        import tomllib
    except ModuleNotFoundError:
        import tomli as tomllib

    logs = []
    stream = backend._StreamTelemetry(lambda category, **kw: logs.append((category, kw)))
    root = Path(__file__).resolve().parents[1]
    for market in ("kr", "us"):
        profile = tomllib.loads((root / "deploy" / f"{market}_trading.config.toml").read_text())
        for server, config in profile["mcp_servers"].items():
            for tool in config["enabled_tools"]:
                stream._line(json.dumps({"type": "item.started", "item": {
                    "type": "mcp_tool_call", "server": server, "tool": tool}}))
                assert logs[-1][1]["tool"] == tool
    stream._line(json.dumps({"type": "item.started", "item": {
        "type": "mcp_tool_call", "server": "PRIVATE", "tool": "PRIVATE"}}))
    assert logs[-1][1] == {"server": "other", "tool": "other"}
    assert "PRIVATE" not in repr(logs)


@pytest.mark.parametrize("platform", ["darwin", "linux"])
def test_exited_group_permission_error_is_darwin_only_and_unconfirmed(monkeypatch, platform):
    if backend.os.name != "posix":
        pytest.skip("POSIX group signal regression")
    monkeypatch.setattr(backend.sys, "platform", platform)
    monkeypatch.setattr(backend.time, "sleep", lambda _: None)

    def signal_group(pid, sig):
        if sig == backend.signal.SIGKILL:
            raise PermissionError("PRIVATE")

    monkeypatch.setattr(backend.os, "killpg", signal_group)
    process = SimpleNamespace(pid=123, poll=lambda: 0, communicate=lambda **kw: ("", ""))
    if platform == "darwin":
        assert backend._terminate_owned_process(process) is False
    else:
        with pytest.raises(PermissionError):
            backend._terminate_owned_process(process)


def test_event_and_active_metadata_budget():
    logs = []
    stream = backend._StreamTelemetry(lambda category, **kw: logs.append(category))
    data = b""
    for index in range(backend._MAX_STAGE_EVENTS * 2):
        data += (json.dumps({"type": "item.started", "item": {
            "type": "mcp_tool_call", "id": str(index)}}) + "\n").encode()
    stream.consume(data)
    assert len(logs) == len(stream.active) == backend._MAX_STAGE_EVENTS
    assert stream.mcp_started == backend._MAX_STAGE_EVENTS * 2


def test_parallel_calls_have_distinct_safe_correlation_ids(monkeypatch, caplog):
    monkeypatch.setattr(backend, "_resolve_codex_executable", lambda _: sys.executable)
    output = json.dumps({"type": "item.completed", "item": {"type": "agent_message", "text": "PRIVATE"}})
    monkeypatch.setattr(backend.subprocess, "Popen", lambda *a, **k: SimpleNamespace(
        returncode=0, communicate=lambda **kw: (output, "PRIVATE")))
    caplog.set_level(logging.INFO)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: backend.generate_codex_fast(system_prompt="PRIVATE", user_prompt="PRIVATE"), range(2)))
    assert len(results) == 2
    ids = re.findall(r"request_id=([0-9a-f]{32})", caplog.text)
    assert len(set(ids)) == 2
    assert all(ids.count(key) == 4 for key in set(ids))
    assert "PRIVATE" not in caplog.text


@pytest.mark.parametrize("completed", [False, True])
def test_output_budget_fails_closed_and_only_cleans_active_child(monkeypatch, caplog, completed):
    cleaned = []
    monkeypatch.setattr(backend, "_MAX_OUTPUT_BYTES", 10)
    monkeypatch.setattr(backend, "_resolve_codex_executable", lambda _: sys.executable)
    monkeypatch.setattr(backend, "_terminate_owned_process", lambda p: cleaned.append(p))
    def communicate(**kwargs):
        if completed:
            return b"PRIVATE" * 10, b""
        raise subprocess.TimeoutExpired("PRIVATE", .1, output=b"PRIVATE" * 10)

    monkeypatch.setattr(backend.subprocess, "Popen", lambda *a, **k: SimpleNamespace(
        returncode=0 if completed else None, communicate=communicate))
    with pytest.raises(backend.CodexFastError, match="output limit"):
        backend.generate_codex_fast(system_prompt="s", user_prompt="u")
    assert len(cleaned) == (0 if completed else 1)
    assert "category=output_limit" in caplog.text
    assert "PRIVATE" not in caplog.text


@pytest.mark.parametrize("separator", ["\r\n", "\r"])
def test_binary_partial_and_final_newlines_preserve_stages(monkeypatch, caplog, separator):
    events = [
        {"type": "item.started", "item": {"type": "mcp_tool_call", "id": "한국어", "tool": "get_stock_info"}},
        {"type": "item.completed", "item": {"type": "mcp_tool_call", "id": "한국어", "status": "completed"}},
        {"type": "item.completed", "item": {"type": "agent_message", "text": "완료"}},
        {"type": "turn.completed"},
    ]
    lines = [(json.dumps(event, ensure_ascii=False) + separator).encode() for event in events]
    script = (f"import sys,time; sys.stdout.buffer.write({lines[0]!r}); sys.stdout.buffer.flush(); "
              f"time.sleep(.25); sys.stdout.buffer.write({b''.join(lines[1:])!r}); sys.stdout.buffer.flush()")
    monkeypatch.setattr(backend, "_resolve_codex_executable", lambda _: sys.executable)
    monkeypatch.setattr(backend, "_command", lambda *a: [sys.executable, "-c", script])
    caplog.set_level(logging.INFO)
    result = backend.generate_codex_fast(system_prompt="한국어", user_prompt="한국어", timeout=3)
    assert result.text == "완료"
    for category in ("first_event", "mcp_started", "mcp_completed", "model_final", "turn_completed"):
        assert caplog.text.count(f"category={category} ") == 1
    assert "mcp_started=1 mcp_completed=1 mcp_errors=0 mcp_pending=0" in caplog.text
    assert "한국어" not in caplog.text
