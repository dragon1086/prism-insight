"""Runtime lifecycle policy for shadow features.

Shadow is not an eternal state.  Each feature has an explicit review deadline;
after that date the runtime treats it as OFF unless an operator explicitly
sets the state to LIVE.  LIVE is never granted automatically.

The db-server cron runs :mod:`tools.shadow_lifecycle` daily to materialize the
decision in a small JSON state file and append an audit record.  Runtime callers
also evaluate the deadline directly, so a missed cron cannot leave an expired
shadow active.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
import json
import os
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
STATE_PATH = Path(os.getenv("SHADOW_LIFECYCLE_STATE", str(ROOT / "shadow_lifecycle_state.json")))

# Dates are intentionally explicit and reviewable.  No entry here may silently
# remain shadow after review_by; the runtime returns OFF after that date.
POLICIES: dict[str, dict[str, Any]] = {
    "atr_stop_width": {
        "label": "ATR/ADR stop-width shadow",
        "started": "2026-08-19",
        "review_by": "2026-09-18",
        "default_mode": "shadow",
        "min_samples": 100,
        "promotion": "loss capture improves while winner removal <= 5%",
    },
    "fill_chaser": {
        "label": "fill-chaser amend/cancel shadow",
        "started": "2026-08-19",
        "review_by": "2026-09-18",
        "default_mode": "shadow",
        "min_samples": 100,
        "promotion": "KIS amend/cancel validation + 100 shadow actions + zero payload/duplicate errors",
    },
    "vision_buy_quality": {
        "label": "vision buy-quality shadow",
        "started": "2026-08-19",
        "review_by": "2026-09-02",
        "default_mode": "shadow",
        "min_samples": 100,
        "promotion": "holdout net effect positive; otherwise retire OFF",
    },
    "position_ledger": {
        "label": "parallel position-ledger shadow",
        "started": "2026-08-19",
        "review_by": "2026-09-18",
        "default_mode": "shadow",
        "min_samples": 5,
        "promotion": "five execution days with zero mismatch/unresolved mirror errors",
    },
}


def _today(now: date | datetime | None = None) -> date:
    if now is None:
        return datetime.now(timezone.utc).date()
    return now.date() if isinstance(now, datetime) else now


def _read_state(path: Path | None = None) -> dict[str, Any]:
    target = path or STATE_PATH
    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else {}
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}


def _write_state(state: dict[str, Any], path: Path | None = None) -> None:
    target = path or STATE_PATH
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(target.suffix + ".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(target)


def feature_mode(feature: str, *, now: date | datetime | None = None,
                 path: Path | None = None) -> str:
    """Return ``shadow``, ``live`` or ``off`` for a feature.

    An explicit state-file ``live`` is allowed, but only an operator can write
    it.  An explicit ``off`` always wins.  An expired default shadow becomes
    ``off`` even if the daily lifecycle cron did not run.
    """
    policy = POLICIES.get(feature)
    if policy is None:
        return "off"
    state = _read_state(path)
    configured = ((state.get("features") or {}).get(feature) or {}).get("mode")
    if configured == "live":
        return "live"
    if configured == "off":
        return "off"
    try:
        expired = _today(now) > date.fromisoformat(policy["review_by"])
    except (TypeError, ValueError):
        expired = True
    return "off" if expired else str(policy.get("default_mode", "off"))


def snapshot(*, now: date | datetime | None = None,
             path: Path | None = None) -> dict[str, Any]:
    today = _today(now).isoformat()
    return {
        "today": today,
        "state_path": str(path or STATE_PATH),
        "features": {
            name: {
                **policy,
                "mode": feature_mode(name, now=now, path=path),
                "expired": feature_mode(name, now=now, path=path) == "off"
                and policy.get("default_mode") == "shadow",
            }
            for name, policy in POLICIES.items()
        },
    }


def apply_expiry(*, now: date | datetime | None = None,
                 path: Path | None = None) -> dict[str, Any]:
    """Materialize expired shadows as OFF and return the resulting snapshot."""
    target = path or STATE_PATH
    state = _read_state(target)
    features = state.setdefault("features", {})
    today = _today(now)
    changed: list[str] = []
    for name, policy in POLICIES.items():
        current = features.get(name) or {}
        if current.get("mode") == "live":
            continue
        try:
            expired = today > date.fromisoformat(policy["review_by"])
        except (TypeError, ValueError):
            expired = True
        if expired and current.get("mode") != "off":
            features[name] = {
                "mode": "off",
                "changed_at": datetime.now(timezone.utc).isoformat(),
                "reason": "auto_expired_without_manual_live_approval",
            }
            changed.append(name)
    if changed:
        state["updated_at"] = datetime.now(timezone.utc).isoformat()
        _write_state(state, target)
    result = snapshot(now=now, path=target)
    result["changed"] = changed
    return result


__all__ = ["POLICIES", "STATE_PATH", "apply_expiry", "feature_mode", "snapshot"]
