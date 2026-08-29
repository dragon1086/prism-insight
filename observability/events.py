"""Immutable, fail-open event recording for PRISM trading observability.

The trading process only appends one JSON line to a local spool. Network I/O,
ClickStack availability, and dashboard queries are deliberately kept outside
the trading path.
"""

from __future__ import annotations

import hashlib
import json
import os
import socket
import subprocess
import uuid
from collections.abc import Mapping
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1
PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SPOOL_PATH = PROJECT_ROOT / "logs" / "prism_events.jsonl"
MAX_EVENT_BYTES = 256 * 1024

_SENSITIVE_KEY_PARTS = (
    "account",
    "api_key",
    "authorization",
    "cookie",
    "credential",
    "password",
    "private_key",
    "secret",
    "session",
    "token",
)

_CONFIG_KEYS = (
    "ENTRY_QUALITY_CAPTURE_ENABLED",
    "MARKET_PULSE_MODE",
    "REGIME_HIVOL_OVERRIDE",
    "REGIME_MIN_SCORE_FLOOR",
    "REGIME_WEAK_NO_TOPDOWN",
    "REENTRY_COOLDOWN_ENABLED",
    "REENTRY_COOLDOWN_LIVE",
    "REENTRY_COOLDOWN_RISK_EXIT_LIVE",
    "RS_RATING_ENABLED",
    "TRIGGER_PERFORMANCE_FEEDBACK",
    "REPORT_MODEL",
    "REPORT_EFFORT",
    "REPORT_AUX_MODEL",
    "REPORT_AUX_EFFORT",
)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _normalize_time(value: datetime | None) -> datetime:
    current = value or _utc_now()
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    return current.astimezone(timezone.utc)


def _normalize_hex_id(value: str | None, length: int) -> str:
    raw = str(value or "").strip().lower()
    if len(raw) == length and all(character in "0123456789abcdef" for character in raw):
        return raw
    if raw:
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:length]
    return uuid.uuid4().hex[:length]


def _redacted_key(key: str) -> bool:
    lowered = key.lower()
    return any(part in lowered for part in _SENSITIVE_KEY_PARTS)


def _sanitize(value: Any, *, key: str = "", depth: int = 0) -> Any:
    if _redacted_key(key):
        return "[REDACTED]"
    if depth >= 8:
        return "[MAX_DEPTH]"
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, Mapping):
        return {
            str(item_key): _sanitize(item_value, key=str(item_key), depth=depth + 1)
            for item_key, item_value in value.items()
        }
    if isinstance(value, (list, tuple, set)):
        return [_sanitize(item, depth=depth + 1) for item in value]
    return str(value)


@lru_cache(maxsize=1)
def _git_sha() -> str | None:
    configured = os.getenv("PRISM_GIT_SHA")
    if configured:
        return configured.strip() or None
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=PROJECT_ROOT,
            check=True,
            capture_output=True,
            text=True,
            timeout=1.0,
        )
        return result.stdout.strip() or None
    except (OSError, subprocess.SubprocessError):
        return None


def _config_snapshot() -> tuple[dict[str, str], str]:
    values = {key: os.environ[key] for key in _CONFIG_KEYS if key in os.environ}
    encoded = json.dumps(values, ensure_ascii=True, separators=(",", ":"), sort_keys=True)
    return values, hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:16]


def build_event(
    event_type: str,
    *,
    service: str,
    event_id: str | None = None,
    market: str | None = None,
    ticker: str | None = None,
    trace_id: str | None = None,
    span_id: str | None = None,
    parent_event_id: str | None = None,
    decision_id: str | None = None,
    position_id: str | None = None,
    attributes: Mapping[str, Any] | None = None,
    severity: str = "INFO",
    event_time: datetime | None = None,
) -> dict[str, Any]:
    """Build a versioned event without performing I/O."""
    normalized_type = str(event_type or "").strip()
    normalized_service = str(service or "").strip()
    if not normalized_type:
        raise ValueError("event_type is required")
    if not normalized_service:
        raise ValueError("service is required")

    timestamp = _normalize_time(event_time)
    config, config_hash = _config_snapshot()
    git_sha = _git_sha()
    event = {
        "schema_version": SCHEMA_VERSION,
        "event_id": _normalize_hex_id(event_id, 32),
        "event_type": normalized_type,
        "timestamp": timestamp.isoformat().replace("+00:00", "Z"),
        "time_unix_nano": str(int(timestamp.timestamp() * 1_000_000_000)),
        "severity": str(severity or "INFO").upper(),
        "service": normalized_service,
        "environment": os.getenv("PRISM_ENV", os.getenv("APP_ENV", "unknown")),
        "host": socket.gethostname(),
        "market": str(market).upper() if market else None,
        "ticker": str(ticker) if ticker else None,
        "trace_id": _normalize_hex_id(trace_id, 32),
        "span_id": _normalize_hex_id(span_id, 16),
        "parent_event_id": str(parent_event_id) if parent_event_id else None,
        "decision_id": str(decision_id) if decision_id else None,
        "position_id": str(position_id) if position_id else None,
        "git_sha": git_sha,
        "policy_version": os.getenv("PRISM_POLICY_VERSION") or (git_sha[:12] if git_sha else None),
        "config_hash": config_hash,
        "config": config,
        "attributes": _sanitize(dict(attributes or {})),
    }
    return event


def emit_event(
    event_type: str,
    *,
    service: str,
    event_id: str | None = None,
    market: str | None = None,
    ticker: str | None = None,
    trace_id: str | None = None,
    span_id: str | None = None,
    parent_event_id: str | None = None,
    decision_id: str | None = None,
    position_id: str | None = None,
    attributes: Mapping[str, Any] | None = None,
    severity: str = "INFO",
    event_time: datetime | None = None,
    spool_path: str | os.PathLike[str] | None = None,
) -> dict[str, Any] | None:
    """Append one event atomically; return ``None`` on every failure.

    Observability must never block or fail a trading decision. Callers should
    not retry synchronously.
    """
    try:
        event = build_event(
            event_type,
            service=service,
            event_id=event_id,
            market=market,
            ticker=ticker,
            trace_id=trace_id,
            span_id=span_id,
            parent_event_id=parent_event_id,
            decision_id=decision_id,
            position_id=position_id,
            attributes=attributes,
            severity=severity,
            event_time=event_time,
        )
        encoded = (
            json.dumps(event, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
            + "\n"
        ).encode("utf-8")
        if len(encoded) > MAX_EVENT_BYTES:
            return None

        path = Path(
            spool_path
            or os.getenv("PRISM_OBSERVABILITY_SPOOL", str(DEFAULT_SPOOL_PATH))
        )
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(path, os.O_APPEND | os.O_CREAT | os.O_WRONLY, 0o600)
        try:
            os.write(descriptor, encoded)
        finally:
            os.close(descriptor)
        return event
    except Exception:  # noqa: BLE001 - this boundary must never fail trading
        return None


__all__ = ["build_event", "emit_event"]
