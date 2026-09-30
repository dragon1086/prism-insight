"""Independent BUY and SELL Codex settings with shared pure validation."""
from __future__ import annotations

import base64
import json
import math
import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path


class CodexFastError(RuntimeError):
    pass


SUPPORTED_MODELS = frozenset({"gpt-5.6-sol", "gpt-6-astra", "gpt-6.1-sol"})
SUPPORTED_REASONING_EFFORTS = frozenset({"low", "medium", "high", "xhigh", "max", "ultra"})
MAX_TIMEOUT_SECONDS = 600


def validate_timeout(value: object) -> float:
    """Bound runtime to (0, 600] seconds; reject non-finite configuration."""
    try:
        timeout = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise CodexFastError("Codex timeout must be a finite number in (0, 600]") from exc
    if isinstance(value, bool) or not math.isfinite(timeout) or not 0 < timeout <= MAX_TIMEOUT_SECONDS:
        raise CodexFastError("Codex timeout must be a finite number in (0, 600]")
    return timeout


@dataclass(frozen=True)
class CodexSettings:
    model: str
    reasoning_effort: str | None
    timeout: float


# Preserve the public BUY settings import and immutable value semantics.
BuyCodexSettings = CodexSettings


# The single ChatGPT OAuth login shared by reports and BUY/SELL (via the proxy).
# Same path as cores.chatgpt_proxy.constants.AUTH_FILE, without importing `cores`
# (prism-us shadows that package).
DEFAULT_AUTH_FILE = Path.home() / ".config" / "prism-insight" / "chatgpt_auth.json"


def active_oauth_email(auth_file: str | os.PathLike | None = None) -> str | None:
    """Email of the active OAuth login, from its access-token claims; None if unknown."""
    try:
        data = json.loads(Path(auth_file or DEFAULT_AUTH_FILE).read_text())
        payload = str(data["access_token"]).split(".")[1]
        claims = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
        email = (claims.get("https://api.openai.com/profile") or {}).get("email") or claims.get("email")
        return str(email).strip().lower() if email else None
    except Exception:  # noqa: BLE001 - unknown account keeps the configured effort
        return None


def effort_for_account(mapping: str | None, email: str | None) -> str | None:
    """Parse ``a@x=medium,b@y=xhigh`` and return the entry for ``email``."""
    if not mapping or not email:
        return None
    for item in mapping.split(","):
        account, sep, effort = item.partition("=")
        if sep and account.strip().lower() == email and effort.strip():
            return effort.strip()
    return None


def _resolve_codex_settings(side: str, environ: Mapping[str, str] | None) -> CodexSettings:
    env = os.environ if environ is None else environ
    model = env.get(f"PRISM_{side}_CODEX_MODEL", "gpt-5.6-sol")
    effort = env.get(f"PRISM_{side}_CODEX_EFFORT")
    # Account-aware effort (2026-09-30): a small plan runs out of quota at high
    # effort, a large one can afford more. The active login decides.
    by_account = effort_for_account(
        env.get("PRISM_CODEX_EFFORT_BY_ACCOUNT"),
        active_oauth_email(env.get("PRISM_CODEX_AUTH_FILE")) if env.get("PRISM_CODEX_EFFORT_BY_ACCOUNT") else None,
    )
    if by_account is not None:
        effort = by_account
    if model not in SUPPORTED_MODELS:
        raise CodexFastError(f"Unsupported Codex model: {model}")
    if effort is not None and effort not in SUPPORTED_REASONING_EFFORTS:
        raise CodexFastError(f"Unsupported Codex reasoning effort: {effort}")
    timeout = validate_timeout(env.get(
        f"PRISM_{side}_CODEX_TIMEOUT", env.get("PRISM_CODEX_FAST_TIMEOUT", "90"),
    ))
    return CodexSettings(model, effort, timeout)


def resolve_buy_codex_settings(environ: Mapping[str, str] | None = None) -> BuyCodexSettings:
    """Resolve explicit BUY overrides; timeouts are finite and at most 600s."""
    return _resolve_codex_settings("BUY", environ)


def resolve_sell_codex_settings(environ: Mapping[str, str] | None = None) -> CodexSettings:
    """Resolve SELL overrides independently of BUY, preserving unset defaults."""
    return _resolve_codex_settings("SELL", environ)
