"""BUY-only Codex settings; SELL callers deliberately do not use these."""
from __future__ import annotations

import math
import os
from collections.abc import Mapping
from dataclasses import dataclass


class CodexFastError(RuntimeError):
    pass


SUPPORTED_MODELS = frozenset({"gpt-5.6-sol", "gpt-6-astra"})
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
class BuyCodexSettings:
    model: str
    reasoning_effort: str | None
    timeout: float


def resolve_buy_codex_settings(environ: Mapping[str, str] | None = None) -> BuyCodexSettings:
    """Resolve explicit BUY overrides; timeouts are finite and at most 600s."""
    env = os.environ if environ is None else environ
    model = env.get("PRISM_BUY_CODEX_MODEL", "gpt-5.6-sol")
    effort = env.get("PRISM_BUY_CODEX_EFFORT")
    if model not in SUPPORTED_MODELS:
        raise CodexFastError(f"Unsupported Codex model: {model}")
    if effort is not None and effort not in SUPPORTED_REASONING_EFFORTS:
        raise CodexFastError(f"Unsupported Codex reasoning effort: {effort}")
    timeout = validate_timeout(env.get(
        "PRISM_BUY_CODEX_TIMEOUT", env.get("PRISM_CODEX_FAST_TIMEOUT", "90"),
    ))
    return BuyCodexSettings(model, effort, timeout)
