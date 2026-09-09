"""Real harmless child processes; no Codex, auth, network or broker calls."""
import hashlib
import json
import asyncio
import logging
import os
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest

from cores.llm import codex_oauth_fast_backend as backend


READER = """
import hashlib,json,sys,time
time.sleep(.3)
data=sys.stdin.buffer.read()
answer=json.dumps({'bytes':len(data),'sha256':hashlib.sha256(data).hexdigest()})
print(json.dumps({'type':'item.completed','item':{'type':'agent_message','text':answer}}),flush=True)
print(json.dumps({'type':'turn.completed'}),flush=True)
"""


def child(monkeypatch, script=READER):
    monkeypatch.setattr(backend, "_resolve_codex_executable", lambda _: sys.executable)
    monkeypatch.setattr(backend, "_command", lambda *args: [sys.executable, "-u", "-c", script])


def verify(prompt, *, timeout=1.5):
    result = backend.generate_codex_fast(system_prompt="system", user_prompt=prompt, timeout=timeout)
    expected = backend._prompt("system", prompt, None).encode("utf-8")
    assert json.loads(result.text) == {"bytes": len(expected), "sha256": hashlib.sha256(expected).hexdigest()}


def test_delayed_reader_receives_large_prompt_exactly_once_and_eof(monkeypatch):
    child(monkeypatch)
    verify("x" * 200000)


def test_three_parallel_multibyte_prompts_and_stderr_backpressure(monkeypatch):
    child(monkeypatch, READER.replace("data=sys.stdin.buffer.read()", "sys.stderr.buffer.write(b'x'*1000000);sys.stderr.flush()\ndata=sys.stdin.buffer.read()"))
    with ThreadPoolExecutor(max_workers=3) as pool:
        list(pool.map(lambda i: verify(("한국어📈" + str(i)) * 20000, timeout=3), range(3)))


def track_children(monkeypatch):
    processes = []
    real = backend.subprocess.Popen

    def spawn(*args, **kwargs):
        process = real(*args, **kwargs)
        processes.append(process)
        return process

    monkeypatch.setattr(backend.subprocess, "Popen", spawn)
    return processes


def assert_reaped(process):
    assert process.returncode is not None
    with pytest.raises(ChildProcessError):
        os.waitpid(process.pid, os.WNOHANG)


def test_timeout_while_large_stdin_blocked_is_bounded_and_safe(monkeypatch, caplog):
    child(monkeypatch, "import sys,time;sys.stderr.buffer.write(b'x'*1000000);sys.stderr.flush();time.sleep(30)")
    processes = track_children(monkeypatch)
    caplog.set_level(logging.INFO)
    started = time.monotonic()
    with pytest.raises(backend.CodexFastError, match="timed out"):
        backend.generate_codex_fast(system_prompt="PRIVATE", user_prompt="PRIVATE" * 200000, timeout=.25)
    assert time.monotonic() - started < 3
    assert_reaped(processes[0])
    record = next(r.getMessage() for r in caplog.records if "category=timeout " in r.getMessage())
    total, sent, closed = map(int, re.search(r"stdin_total_bytes=(\d+) stdin_sent_bytes=(\d+) stdin_closed=(\d+)", record).groups())
    assert 0 < sent < total and closed == 0
    assert "PRIVATE" not in caplog.text


def test_repeated_async_cancellation_reaps_blocked_input_child(monkeypatch):
    child(monkeypatch, "import time;time.sleep(30)")
    processes = track_children(monkeypatch)

    async def run():
        task = asyncio.create_task(backend.generate_codex_fast_async(system_prompt="s", user_prompt="x" * 2000000, timeout=30))
        deadline = time.monotonic() + 3
        while not processes:
            assert time.monotonic() < deadline
            await asyncio.sleep(.01)
        await asyncio.sleep(.05)
        started = time.monotonic()
        task.cancel()
        await asyncio.sleep(.02)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert time.monotonic() - started < 3
        assert_reaped(processes[0])

    asyncio.run(run())


def test_success_reports_full_transmission_without_logging_prompt(monkeypatch, caplog):
    child(monkeypatch)
    caplog.set_level(logging.INFO)
    verify("PRIVATE_한국어" * 20000)
    record = next(r.getMessage() for r in caplog.records if "category=success " in r.getMessage())
    total, sent, closed = map(int, re.search(r"stdin_total_bytes=(\d+) stdin_sent_bytes=(\d+) stdin_closed=(\d+)", record).groups())
    assert sent == total and total > 200000 and closed == 1
    assert "PRIVATE" not in caplog.text


def test_reaped_early_success_with_incomplete_stdin_is_rejected_without_group_signal(monkeypatch):
    monkeypatch.setattr(backend, "_resolve_codex_executable", lambda _: sys.executable)
    monkeypatch.setattr(backend, "_STDIN_WRITE_BUDGET", 32)
    stream = json.dumps({"type": "item.completed", "item": {"type": "agent_message", "text": "{}"}})
    monkeypatch.setattr(backend.subprocess, "Popen", lambda *a, **kw: SimpleNamespace(
        stdin=open(os.devnull, "wb"), returncode=0, communicate=lambda **kw: (stream, "")))
    monkeypatch.setattr(backend, "_terminate_owned_process", lambda *a: pytest.fail("leader already reaped; never signal reusable group ID"))
    with pytest.raises(backend.CodexFastError, match="full prompt transmission"):
        backend.generate_codex_fast(system_prompt="s", user_prompt="x" * 200000)


def test_unsupported_platform_fails_before_launch_for_caller_fallback(monkeypatch):
    monkeypatch.setattr(backend, "os", SimpleNamespace(name="nt"))
    monkeypatch.setattr(backend.subprocess, "Popen", lambda *a, **kw: pytest.fail("unsupported platform must not launch"))
    with pytest.raises(backend.CodexFastError, match="POSIX.*fallback"):
        backend.generate_codex_fast(system_prompt="s", user_prompt="u")


def test_pump_setup_failure_still_closes_stdin_and_reaps(monkeypatch):
    child(monkeypatch, "import time;time.sleep(30)")
    processes = track_children(monkeypatch)
    streams = []

    def fail(stream, payload):
        streams.append(stream)
        raise OSError("PRIVATE_SETUP_ERROR")

    monkeypatch.setattr(backend, "_StdinPump", fail)
    with pytest.raises(backend.CodexFastError) as error:
        backend.generate_codex_fast(system_prompt="s", user_prompt="u")
    assert "PRIVATE_SETUP_ERROR" not in str(error.value)
    assert streams[0].closed
    assert_reaped(processes[0])
