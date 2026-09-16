"""Explicit, payload-free authentication failure classification.

The latch belongs to one adapter/session only, never persisted or shared across
accounts. A fresh tick creates a fresh adapter and can use replaced credentials.
"""


def auth_failure(value):
    code = value.get("retCode") if isinstance(value, dict) else getattr(value, "status_code", None)
    if code in (33004, "33004"):
        return "api_key_expired"
    return None
