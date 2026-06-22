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
"""

from __future__ import annotations

import os

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
_DEFAULT_VISION_MODEL = "gpt-4o"
_PLACEHOLDER_KEY = "chatgpt-oauth-placeholder"


# ---------------------------------------------------------------------------
# Individual capability accessors (pure env reads — no caching needed;
# env is stable within a process and tests can monkeypatch freely)
# ---------------------------------------------------------------------------

def has_api_key() -> bool:
    """Return True if a real OpenAI API key is present (not a proxy placeholder).

    Vision requires a genuine API key because it uses base64 image inputs that
    the ChatGPT OAuth proxy does not support.
    """
    key = os.environ.get("OPENAI_API_KEY", "").strip()
    return bool(key) and key != _PLACEHOLDER_KEY


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
