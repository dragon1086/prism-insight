"""Explicit macro MCP configuration shared with the tracked report registry.

This controls configuration parity, not tool safety or execution isolation.
"""
import os
import sys
from pathlib import Path


def _report_configuration():
    # The US entrypoint has its own ``cores`` package. Resolve the root loader
    # and its lazy registry import without leaving that package shadowed.
    saved_path = list(sys.path)
    saved_modules = {
        name: module for name, module in list(sys.modules.items())
        if name == "cores" or name.startswith("cores.")
    }
    try:
        for name in saved_modules:
            sys.modules.pop(name, None)
        sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
        from cores.llm.config_loader import (
            load_report_mcp_registry,
            resolve_openai_api_key,
        )

        return load_report_mcp_registry(), resolve_openai_api_key()
    finally:
        sys.path[:] = saved_path
        for name in list(sys.modules):
            if name == "cores" or name.startswith("cores."):
                sys.modules.pop(name, None)
        sys.modules.update(saved_modules)


def create_macro_mcp_app(app_class, *, name):
    """Expose only Perplexity; retain the active OpenAI/OAuth proxy endpoint."""
    from prism_core.isolated_agent_runtime import configured_mcp_app

    registry, api_key = _report_configuration()
    base_url = os.environ.get("OPENAI_BASE_URL")
    if not api_key and not base_url:
        raise RuntimeError("Macro analysis requires OpenAI credentials or an active proxy")
    spec = registry.get("perplexity")
    server = {
        "command": spec.command,
        "args": list(spec.args),
        "env": dict(spec.env),
    }
    if spec.cwd is not None:
        server["cwd"] = spec.cwd
    if spec.read_timeout_seconds is not None:
        server["read_timeout_seconds"] = spec.read_timeout_seconds
    return configured_mcp_app(app_class, name, lambda: {
        "openai": {
            "api_key": api_key or "chatgpt-oauth-placeholder",
            "base_url": base_url or "https://api.openai.com/v1",
        },
        "mcp": {"servers": {"perplexity": server}},
    })
