"""Fixed binding tests; harmless children only, no model/provider launches."""
import asyncio
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile

import pytest

from cores.llm import codex_oauth_fast_backend as backend
from tools import isolated_codex_binding as binding
from tools import isolated_codex_invoker as rpc


@pytest.fixture
def fixture():
    with tempfile.TemporaryDirectory(prefix="bind-", dir="/tmp") as directory:
        base = Path(directory).resolve()
        model, agent, host = [base / name for name in ("model", "agent", "host")]
        for path in (model, agent, host):
            path.mkdir(mode=0o700)
        (model / ".isolated-codex-probe").write_text("fixture")
        wrapper = model / "wrapper.py"
        wrapper.write_text("# pinned fixture, never executed")
        wrapper.chmod(0o700)
        source = sqlite3.connect(agent / "state.db")
        source.execute("CREATE TABLE t(n)")
        source.commit()
        scope = rpc.InvocationScope("arm", "profile", "case", "rev", json.dumps({
            "model": "gpt-6-astra", "reasoning_effort": "xhigh", "mcp_profile": "us_trading", "require_mcp_calls": True,
        }), 240, source)
        snap = rpc.snapshot_sqlite(source, host, "request")
        config = binding.BindingConfig(model, agent, host, hashlib.sha256(wrapper.read_bytes()).hexdigest(),
                                       '{"PATH":"/usr/bin:/bin","PERPLEXITY_API_KEY":"HOST_CANARY"}')
        yield config, rpc.Invocation(scope, "system", "user", snap)
        source.close()


def test_fixed_binding_does_not_mutate_global_environment(fixture, monkeypatch):
    config, invocation = fixture
    seen = []
    before = dict(os.environ)
    async def fake(**kwargs):
        seen.append(kwargs)
        state = kwargs["_diagnostic_state"]
        state.phase, state.returncode = "REAPED", 0
        assert kwargs["_diagnostic_environment"]["PRISM_PROBE_SNAPSHOT_SHA256"] == invocation.snapshot.sha256
        assert kwargs["_diagnostic_environment"]["PERPLEXITY_API_KEY"] == "HOST_CANARY"
        return backend.CodexFastResult("{}", .1, None)
    monkeypatch.setattr(backend, "generate_codex_fast_async", fake)
    result = asyncio.run(binding.FixedCodexBinding(config)(invocation))
    assert result == rpc.BackendCompletion("{}")
    assert dict(os.environ) == before
    assert seen[0]["timeout"] == 240 and seen[0]["codex_bin"] == str(config.model_root / "wrapper.py")


@pytest.mark.parametrize("phase,rc,confirmed,allowed", [
    ("NO_PROCESS", None, False, True), ("LAUNCH_ATTEMPTED_UNKNOWN", None, False, False),
    ("REAPED", 0, False, True), ("REAPED", 1, False, False),
    ("TERMINATED", -9, True, True), ("TERMINATED", -9, False, False),
])
def test_only_typed_confirmed_cleanup_allows_model_error_fallback(fixture, monkeypatch, phase, rc, confirmed, allowed):
    config, invocation = fixture
    async def fake(**kwargs):
        state = kwargs["_diagnostic_state"]
        state.phase, state.returncode, state.group_cleanup_confirmed = phase, rc, confirmed
        raise backend.CodexFastError("SECRET_ERROR")
    monkeypatch.setattr(backend, "generate_codex_fast_async", fake)
    if allowed:
        assert asyncio.run(binding.FixedCodexBinding(config)(invocation)).error == "model_error"
    else:
        with pytest.raises(binding.CleanupUnconfirmed, match="cleanup_unconfirmed"):
            asyncio.run(binding.FixedCodexBinding(config)(invocation))


def test_unknown_cancellation_becomes_cleanup_failure_not_cancel_ack(fixture, monkeypatch):
    config, invocation = fixture
    async def fake(**kwargs):
        kwargs["_diagnostic_state"].phase = "LAUNCH_ATTEMPTED_UNKNOWN"
        raise asyncio.CancelledError()
    monkeypatch.setattr(backend, "generate_codex_fast_async", fake)
    with pytest.raises(binding.CleanupUnconfirmed):
        asyncio.run(binding.FixedCodexBinding(config)(invocation))


def test_snapshot_tampering_never_launches(fixture, monkeypatch):
    config, invocation = fixture
    invocation.snapshot.path.chmod(0o600)
    invocation.snapshot.path.write_bytes(b"tampered")
    invocation.snapshot.path.chmod(0o400)
    async def fake(**kwargs):
        pytest.fail("must reject snapshot")
    monkeypatch.setattr(backend, "generate_codex_fast_async", fake)
    with pytest.raises(ValueError):
        asyncio.run(binding.FixedCodexBinding(config)(invocation))


def test_real_backend_private_environment_and_state_with_harmless_child(monkeypatch):
    monkeypatch.setenv("AMBIENT_MUST_NOT_INHERIT", "SECRET_AMBIENT")
    script = """
import json,os,sys
sys.stdin.buffer.read()
answer=json.dumps({'ambient': 'AMBIENT_MUST_NOT_INHERIT' in os.environ, 'private':os.environ.get('PRIVATE_INPUT')})
print(json.dumps({'type':'item.completed','item':{'type':'agent_message','text':answer}}))
print(json.dumps({'type':'turn.completed'}))
"""
    monkeypatch.setattr(backend, "_resolve_codex_executable", lambda _: sys.executable)
    monkeypatch.setattr(backend, "_command", lambda *a: [sys.executable, "-c", script])
    state = backend.CodexDiagnosticState()
    result = backend.generate_codex_fast(system_prompt="fixture", user_prompt="fixture", timeout=2,
                                         _diagnostic_environment={"PRIVATE_INPUT": "fixed"}, _diagnostic_state=state)
    assert json.loads(result.text) == {"ambient": False, "private": "fixed"}
    assert os.environ["AMBIENT_MUST_NOT_INHERIT"] == "SECRET_AMBIENT"
    assert state.phase == "REAPED" and state.returncode == 0


def test_launch_attempt_exception_never_claims_no_process(monkeypatch):
    monkeypatch.setattr(backend, "_resolve_codex_executable", lambda _: sys.executable)
    def fail(*a, **kw):
        raise OSError("PRIVATE_LAUNCH_ERROR")
    monkeypatch.setattr(backend.subprocess, "Popen", fail)
    state = backend.CodexDiagnosticState()
    with pytest.raises(backend.CodexFastError):
        backend.generate_codex_fast(system_prompt="fixture", user_prompt="fixture", _diagnostic_state=state)
    assert state.phase == "LAUNCH_ATTEMPTED_UNKNOWN"
    assert not binding._cleanup_confirmed(state)


def test_real_backend_cancellation_records_actual_group_cleanup(monkeypatch):
    monkeypatch.setattr(backend, "_resolve_codex_executable", lambda _: sys.executable)
    monkeypatch.setattr(backend, "_command", lambda *a: [sys.executable, "-c", "import time;time.sleep(30)"])
    async def run():
        state = backend.CodexDiagnosticState()
        task = asyncio.create_task(backend.generate_codex_fast_async(system_prompt="fixture", user_prompt="fixture", timeout=2,
                                                                    _diagnostic_state=state))
        while state.phase != "SPAWNED":
            await asyncio.sleep(.01)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert state.phase == "TERMINATED" and state.returncode is not None
        assert binding._cleanup_confirmed(state) is state.group_cleanup_confirmed
    asyncio.run(run())


def test_snapshot_mount_is_read_only_after_model_stage(fixture, monkeypatch):
    from tools import codex_probe_sandbox as sandbox
    config, invocation = fixture
    (config.model_root / "portfolio.sqlite").write_bytes(b"fixture-target-never-opened")
    command = sandbox.sandbox_command(config.model_root, Path("/fixed/codex"), Path("/fixed/socket"),
                                      Path("/fixed/wrapper.py"), ["exec"], snapshot=invocation.snapshot.path)
    index = command.index(str(invocation.snapshot.path))
    assert command[index - 1:index + 2] == ["--ro-bind", str(invocation.snapshot.path), str(config.model_root / "portfolio.sqlite")]
    assert index > command.index(str(config.model_root))
    assert "--bind" not in command and str(config.agent_root) not in command


@pytest.mark.parametrize("overlap", ["equal", "agent_inside_model", "model_inside_agent"])
def test_agent_and_model_registered_roots_must_not_overlap(fixture, overlap):
    from dataclasses import replace
    config, _ = fixture
    if overlap == "equal":
        config = replace(config, agent_root=config.model_root)
    elif overlap == "agent_inside_model":
        child = config.model_root / "writable-agent"
        child.mkdir(mode=0o700)
        config = replace(config, agent_root=child)
    else:
        child = config.agent_root / "nested-model"
        child.mkdir(mode=0o700)
        (child / ".isolated-codex-probe").write_text("fixture")
        config = replace(config, model_root=child)
    with pytest.raises(ValueError, match="separate_registered_roots"):
        binding.FixedCodexBinding(config)
