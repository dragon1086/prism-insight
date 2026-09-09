"""Harmless real process tests: no Codex/auth/network or production data."""
import asyncio
import os
import subprocess
import sys
import time

import pytest

from cores.llm import codex_oauth_fast_backend as backend


@pytest.fixture
def child_tree(monkeypatch, tmp_path):
    if os.name != "posix":
        pytest.skip("POSIX owned process group regression")
    pid_path = tmp_path / "pids"
    child_script = "import signal,time; signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(60)"
    parent_script = (
        "import os,signal,subprocess,sys,time; "
        "signal.signal(signal.SIGTERM, signal.SIG_IGN); "
        f"child=subprocess.Popen([sys.executable,'-c',{child_script!r}]); "
        f"open({str(pid_path)!r},'w').write(str(os.getpid())+' '+str(child.pid)); "
        "time.sleep(60)"
    )
    monkeypatch.setattr(backend, "_resolve_codex_executable", lambda _: sys.executable)
    monkeypatch.setattr(backend, "_command", lambda *args: [sys.executable, "-c", parent_script])
    return pid_path


def assert_tree_stopped(pid_path):
    pids = [int(pid) for pid in pid_path.read_text().split()]
    for pid in pids:
        deadline = time.monotonic() + 3
        while True:
            result = subprocess.run(["ps", "-o", "stat=", "-p", str(pid)], capture_output=True, text=True)
            # Orphaned grandchildren may briefly await system reaping as zombies.
            if not result.stdout.strip() or result.stdout.strip().startswith("Z"):
                break
            assert time.monotonic() < deadline, f"child {pid} remains running"
            time.sleep(0.02)
    with pytest.raises(ChildProcessError):
        os.waitpid(pids[0], os.WNOHANG)


def test_timeout_terminates_child_and_grandchild(child_tree, monkeypatch, caplog):
    signaled_groups = []
    real_killpg = os.killpg

    def record_killpg(group, sig):
        signaled_groups.append(group)
        assert group != os.getpgrp()
        real_killpg(group, sig)

    monkeypatch.setattr(os, "killpg", record_killpg)
    started = time.monotonic()
    with pytest.raises(backend.CodexFastError, match="timed out"):
        backend.generate_codex_fast(system_prompt="PRIVATE_PROMPT", user_prompt="PRIVATE_PROMPT", timeout=0.5)
    assert time.monotonic() - started < 3
    assert_tree_stopped(child_tree)
    leader = int(child_tree.read_text().split()[0])
    assert signaled_groups == [leader, leader]
    assert "category=timeout" in caplog.text
    assert "timeout_s=0.5" in caplog.text
    assert "PRIVATE_PROMPT" not in caplog.text


def test_async_cancellation_waits_for_process_tree_cleanup(child_tree):
    async def run():
        task = asyncio.create_task(backend.generate_codex_fast_async(system_prompt="s", user_prompt="u", timeout=30))
        deadline = time.monotonic() + 3
        while not child_tree.exists():
            assert time.monotonic() < deadline
            await asyncio.sleep(0.01)
        started = time.monotonic()
        task.cancel()
        await asyncio.sleep(0.02)
        task.cancel()  # repeated cancellation must not abandon the cleanup worker
        with pytest.raises(asyncio.CancelledError):
            await task
        assert time.monotonic() - started < 3
        assert_tree_stopped(child_tree)
    asyncio.run(run())
