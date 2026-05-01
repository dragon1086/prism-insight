"""Feature flags for Memory V2 rollout."""

import os
from typing import Set


def _bool_env(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


def _csv_int_set(name: str) -> Set[int]:
    raw = os.environ.get(name, "")
    out: Set[int] = set()
    for part in raw.split(","):
        p = part.strip()
        if not p:
            continue
        try:
            out.add(int(p))
        except ValueError:
            pass
    return out


def memory_v2_enabled_globally() -> bool:
    return _bool_env("MEMORY_V2_ENABLED", False)


def memory_v2_user_ids() -> Set[int]:
    return _csv_int_set("MEMORY_V2_USER_IDS")


def _v2_enabled_for(user_id: int) -> bool:
    """
    Returns True if Memory V2 is enabled for a user.

    Logic:
      - If MEMORY_V2_USER_IDS contains user_id, V2 is enabled (whitelist).
      - Else if MEMORY_V2_ENABLED is true, V2 is enabled globally.
      - Else V2 is disabled.
    """
    if user_id in memory_v2_user_ids():
        return True
    return memory_v2_enabled_globally()
