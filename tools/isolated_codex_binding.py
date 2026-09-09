"""Trusted fixed backend binding; no CLI, agent launcher or runtime enablement.

The supervisor registers all paths/settings, authorizes source connections and
serializes the WHOLE primary/fallback chain. Only immutable snapshot bytes enter
the model namespace. Rebuild/pin the wrapper containing ParentLease before use.
The global invoker callback limit alone is not a fallback concurrency gate.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
import hashlib
import json
import os
from pathlib import Path

from cores.llm import codex_oauth_fast_backend as backend
from tools.codex_probe_sandbox import TRUSTED_CODEX_BINARY, validate_snapshot, validate_stage
from tools.isolated_codex_invoker import BackendCompletion


class CleanupUnconfirmed(RuntimeError):
    pass


@dataclass(frozen=True)
class BindingConfig:
    model_root: Path
    agent_root: Path
    host_root: Path
    wrapper_sha256: str
    environment_json: str = field(repr=False)


def _cleanup_confirmed(state):
    return (state.phase == "NO_PROCESS"
            or state.phase == "REAPED" and state.returncode == 0
            or state.phase == "TERMINATED" and state.group_cleanup_confirmed and state.returncode is not None)


class FixedCodexBinding:
    """Callable for CodexInvoker; configuration is supervisor-owned, not RPC."""
    def __init__(self, config):
        self.config = config
        validate_stage(config.model_root)
        for path in (config.agent_root, config.host_root):
            if path.resolve() != path or not path.is_dir():
                raise ValueError("invalid_registered_root")
        if (config.host_root.is_relative_to(config.agent_root) or config.host_root.is_relative_to(config.model_root)
                or config.agent_root.is_relative_to(config.host_root) or config.model_root.is_relative_to(config.host_root)
                or config.agent_root.is_relative_to(config.model_root) or config.model_root.is_relative_to(config.agent_root)):
            raise ValueError("separate_registered_roots_required")
        self.environment = json.loads(config.environment_json)
        if (not isinstance(self.environment, dict) or any(not isinstance(k, str) or not isinstance(v, str) or "\0" in k + v
                                                       for k, v in self.environment.items())):
            raise ValueError("invalid_registered_environment")

    async def __call__(self, invocation):
        config = self.config
        wrapper = config.model_root / "wrapper.py"
        if wrapper.is_symlink() or hashlib.sha256(wrapper.read_bytes()).hexdigest() != config.wrapper_sha256:
            raise ValueError("wrapper_pin_mismatch")
        validate_snapshot(invocation.snapshot.path, invocation.snapshot.sha256, config.model_root, config.agent_root, config.host_root)
        settings = json.loads(invocation.scope.fixed_settings_json)
        if (set(settings) != {"model", "reasoning_effort", "mcp_profile", "require_mcp_calls"}
                or settings["model"] not in backend.SUPPORTED_MODELS
                or settings["reasoning_effort"] not in backend.SUPPORTED_REASONING_EFFORTS
                or settings["mcp_profile"] not in backend.SUPPORTED_MCP_PROFILES or settings["require_mcp_calls"] is not True):
            raise ValueError("invalid_registered_settings")
        reader, writer = os.pipe()  # writer never passed to the backend/model.
        state = backend.CodexDiagnosticState()
        env = dict(self.environment, PRISM_PROBE_ROOT=str(config.model_root),
                   PRISM_PROBE_CODEX_BINARY=TRUSTED_CODEX_BINARY,
                   PRISM_PROBE_PARENT_FD=str(reader), PRISM_PROBE_PARENT_PID=str(os.getpid()),
                   PRISM_PROBE_SNAPSHOT=str(invocation.snapshot.path), PRISM_PROBE_SNAPSHOT_SHA256=invocation.snapshot.sha256,
                   PRISM_PROBE_AGENT_ROOT=str(config.agent_root), PRISM_PROBE_SNAPSHOT_ROOT=str(config.host_root))
        try:
            try:
                result = await backend.generate_codex_fast_async(
                    system_prompt=invocation.system_prompt, user_prompt=invocation.user_prompt,
                    timeout=invocation.scope.model_deadline, codex_bin=str(wrapper), codex_home=str(config.model_root / "home"),
                    **settings, _diagnostic_environment=env, _diagnostic_parent_fd=reader, _diagnostic_state=state)
            except asyncio.CancelledError:
                if not _cleanup_confirmed(state):
                    raise CleanupUnconfirmed("cleanup_unconfirmed") from None
                raise
            except backend.CodexFastError:
                if not _cleanup_confirmed(state):
                    raise CleanupUnconfirmed("cleanup_unconfirmed") from None
                return BackendCompletion(None, error="model_error")
            if not _cleanup_confirmed(state):
                raise CleanupUnconfirmed("cleanup_unconfirmed")
            return BackendCompletion(result.text)
        finally:
            os.close(reader)
            os.close(writer)
