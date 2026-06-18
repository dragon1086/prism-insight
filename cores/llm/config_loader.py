"""
Config loader for the LLM stack.

Provides env-interpolation, secret resolution, and MCP server registry loading
without depending on the mcp_agent framework's config files.

Search order for load_mcp_registry:
  (a) explicit config_path argument
  (b) PRISM_MCP_CONFIG environment variable
  (c) native cores/llm/mcp_servers.yaml
  (d) legacy mcp_agent.config.yaml (DEPRECATION warning — removed in Phase 5)

Secrets come from environment variables (.env); use resolve_secret() for
required credentials. YAML env values support ${VAR} / ${VAR:-default}
interpolation so no secrets are stored in config files.
"""

from __future__ import annotations

import logging
import os
import re
from pathlib import Path
from typing import Optional

import yaml

logger = logging.getLogger(__name__)

# Path of this file: cores/llm/config.py → parent.parent == project root
_HERE = Path(__file__).resolve().parent
_PROJECT_ROOT = _HERE.parent.parent

_NATIVE_CONFIG = _HERE / "mcp_servers.yaml"
_LEGACY_CONFIG = _PROJECT_ROOT / "mcp_agent.config.yaml"

_ENV_VAR_RE = re.compile(r"\$\{([^}:]+)(?::-([^}]*))?\}")


def interpolate_env(value: str) -> str:
    """Expand ``${VAR}`` and ``${VAR:-default}`` placeholders using ``os.environ``.

    - ``${VAR}`` → value of VAR; empty string if VAR is unset.
    - ``${VAR:-default}`` → value of VAR if set and non-empty, else ``default``.
    - Non-string input is returned unchanged.
    """
    if not isinstance(value, str):
        return value  # type: ignore[return-value]

    def _replace(m: re.Match) -> str:
        var_name = m.group(1)
        default = m.group(2)  # None if no ":-" clause
        env_val = os.environ.get(var_name, "")
        if env_val:
            return env_val
        return default if default is not None else ""

    return _ENV_VAR_RE.sub(_replace, value)


def _interpolate_obj(obj):
    """Recursively apply ``interpolate_env`` to every string in *obj*.

    Traverses dicts and lists in-place (returns a new structure for immutable
    input types). Non-container, non-string values are returned as-is.
    """
    if isinstance(obj, str):
        return interpolate_env(obj)
    if isinstance(obj, dict):
        return {k: _interpolate_obj(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_interpolate_obj(item) for item in obj]
    return obj


def resolve_secret(
    name: str,
    default: Optional[str] = None,
    *,
    required: bool = False,
) -> Optional[str]:
    """Read a secret from the environment.

    Args:
        name:     Environment variable name (e.g. ``"OPENAI_API_KEY"``).
        default:  Value to return when the variable is absent or empty.
        required: If ``True`` and the variable is absent/empty (after applying
                  *default*), raise ``RuntimeError`` naming the variable.

    Returns:
        The variable's value, *default*, or ``None``.

    Raises:
        RuntimeError: when ``required=True`` and no non-empty value is found.
    """
    value = os.environ.get(name) or default
    if required and not value:
        raise RuntimeError(
            f"Required secret '{name}' is not set. "
            f"Add {name}=<value> to your .env file or environment."
        )
    return value or None


def load_mcp_registry(config_path: "str | Path | None" = None):
    """Load and return a :class:`McpServerRegistry` from YAML config.

    Search order:
      (a) *config_path* argument (if provided)
      (b) ``PRISM_MCP_CONFIG`` environment variable
      (c) ``cores/llm/mcp_servers.yaml`` (native, no-secret config)
      (d) ``mcp_agent.config.yaml`` at project root — **LEGACY FALLBACK**
          (emits a DEPRECATION warning; will be removed in Phase 5)

    The loaded YAML is processed with :func:`_interpolate_obj` so that
    ``${ENV_VAR}`` references in env blocks are resolved from the process
    environment before building the registry.

    Auto-detects YAML shape:
      - Native: top-level ``servers:`` key
      - Legacy:  top-level ``mcp.servers:`` path (normalised before parsing)

    Raises:
        FileNotFoundError: if no config file is found via any search path.
    """
    # Import here to avoid circular imports; config.py must stay SDK-free.
    from cores.llm.mcp_registry import McpServerRegistry  # noqa: PLC0415

    resolved: Optional[Path] = None

    if config_path is not None:
        resolved = Path(config_path)
        if not resolved.exists():
            raise FileNotFoundError(
                f"MCP config not found at explicit path: {resolved}"
            )
    else:
        env_override = os.environ.get("PRISM_MCP_CONFIG")
        if env_override:
            resolved = Path(env_override)
            if not resolved.exists():
                raise FileNotFoundError(
                    f"MCP config from PRISM_MCP_CONFIG not found: {resolved}"
                )
        elif _NATIVE_CONFIG.exists():
            resolved = _NATIVE_CONFIG
        elif _LEGACY_CONFIG.exists():
            logger.warning(
                "DEPRECATION: loading MCP config from legacy mcp_agent.config.yaml. "
                "Migrate to cores/llm/mcp_servers.yaml or set PRISM_MCP_CONFIG. "
                "This fallback will be removed in Phase 5."
            )
            resolved = _LEGACY_CONFIG
        else:
            raise FileNotFoundError(
                "No MCP config found. Expected one of:\n"
                f"  - PRISM_MCP_CONFIG env var\n"
                f"  - {_NATIVE_CONFIG}\n"
                f"  - {_LEGACY_CONFIG}"
            )

    raw = yaml.safe_load(resolved.read_text())
    raw = _interpolate_obj(raw)

    # Auto-detect shape and normalise to {"mcp": {"servers": {...}}}
    if "servers" in raw and "mcp" not in raw:
        # Native shape: top-level `servers:` key
        normalised = {"mcp": {"servers": raw["servers"]}}
    else:
        # Legacy shape already has `mcp.servers`
        normalised = raw

    return McpServerRegistry.from_yaml_dict(normalised)
