"""
Capability detection for PRISM feature flags — Phase 6 S1.

All reads are lazy (first call) and cheap. Importing this module has zero
side effects; the heavy OpenAI SDK is never imported here.

Environment variables (all off/safe by default):
    OPENAI_API_KEY          — real OpenAI API key (not a proxy placeholder)
    PRISM_FEATURE_VISION    — "on" to enable vision pipeline (default: off)
    PRISM_VISION_SHADOW     — "false" to disable shadow-only mode (default: true)
    PRISM_VISION_MODEL      — override vision model id (default: gpt-4o)
    PRISM_VISION_AUTH       — "api" or "oauth" for vision auth path (default: api)
    PRISM_FEATURE_INSIGHT_IMAGE — "on" to broadcast insight images to subscribers
                              (default: off; INDEPENDENT of the vision SHADOW flag)
"""

from __future__ import annotations

import os

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
# Default to the project's standard multimodal model (registered in models.py).
_DEFAULT_VISION_MODEL = "gpt-5.4-mini"
_PLACEHOLDER_KEY = "chatgpt-oauth-placeholder"


def _secrets_api_key() -> str:
    """Read ``openai.api_key`` from mcp_agent.secrets.yaml at the project root.

    Returns "" if the file/key is absent or not a real key. Never raises.
    Split out as a seam so tests can stub the filesystem dependency.
    """
    try:
        import pathlib

        import yaml

        # capabilities.py -> cores/llm/ -> cores/ -> <project root>
        root = pathlib.Path(__file__).resolve().parents[2]
        for path in (root / "mcp_agent.secrets.yaml",
                     pathlib.Path.cwd() / "mcp_agent.secrets.yaml"):
            if path.is_file():
                data = yaml.safe_load(path.read_text()) or {}
                key = str((data.get("openai") or {}).get("api_key", "")).strip()
                if key.startswith("sk-"):
                    return key
    except Exception:  # noqa: BLE001 — key resolution must never crash callers
        pass
    return ""


def resolve_openai_api_key() -> str:
    """Resolve a real OpenAI API key: env first, then mcp_agent.secrets.yaml.

    Under OAuth mode the key is not exported to OPENAI_API_KEY; the project's
    long-standing real key lives in ``mcp_agent.secrets.yaml`` (``openai.api_key``).
    Vision needs a genuine API key, so we fall back to that file. Returns "" when
    no real key is found. Never raises.
    """
    env_key = os.environ.get("OPENAI_API_KEY", "").strip()
    if env_key and env_key != _PLACEHOLDER_KEY:
        return env_key
    return _secrets_api_key()


# ---------------------------------------------------------------------------
# Individual capability accessors (pure env reads — no caching needed;
# env is stable within a process and tests can monkeypatch freely)
# ---------------------------------------------------------------------------

def has_api_key() -> bool:
    """Return True if a real OpenAI API key is resolvable (env or secrets file).

    Vision requires a genuine API key because it uses base64 image inputs that
    the ChatGPT OAuth proxy does not support.
    """
    return bool(resolve_openai_api_key())


def vision_enabled() -> bool:
    """Return True only when PRISM_FEATURE_VISION=on (default: off)."""
    return os.environ.get("PRISM_FEATURE_VISION", "off").strip().lower() == "on"


def vision_shadow() -> bool:
    """Return True when vision runs in shadow-only mode (default: True).

    Shadow mode means results are logged but never fed into trading decisions.
    Set PRISM_VISION_SHADOW=false to disable shadow mode (S3+ only).
    """
    return os.environ.get("PRISM_VISION_SHADOW", "true").strip().lower() != "false"


def vision_model() -> str:
    """Return the vision model id (default: gpt-4o)."""
    return os.environ.get("PRISM_VISION_MODEL", _DEFAULT_VISION_MODEL).strip() or _DEFAULT_VISION_MODEL


def vision_auth() -> str:
    """Return the vision auth mode: 'api' (default) or 'oauth'."""
    val = os.environ.get("PRISM_VISION_AUTH", "api").strip().lower()
    return val if val in ("api", "oauth") else "api"


def vision_available() -> bool:
    """Master gate: True iff vision is enabled AND a real API key exists.

    Callers MUST check this before encoding images or making vision calls.
    When False, skip entirely — no encoding, no client, no network.
    """
    return vision_enabled() and has_api_key()


def insight_image_enabled() -> bool:
    """Return True only when PRISM_FEATURE_INSIGHT_IMAGE=on (default: off).

    INDEPENDENT broadcast gate for the subscriber-facing insight image. This is
    deliberately SEPARATE from the vision SHADOW flag: deploying the broadcast
    code sends NOTHING to subscribers until this env is explicitly set to "on".
    Callers should require BOTH insight_image_enabled() AND vision_available()
    before producing/sending an image.
    """
    return os.environ.get("PRISM_FEATURE_INSIGHT_IMAGE", "off").strip().lower() == "on"
