"""Secret-minimized snapshots of journal influence on trading decisions."""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from collections.abc import Iterable, Mapping
from datetime import datetime, timezone
from typing import Any

JOURNAL_INFLUENCE_CONTEXT_SCHEMA_VERSION = 1
JOURNAL_INFLUENCE_EXTRACTOR_VERSION = "journal-influence-v1"

_COMPONENT_HEADERS = (
    ("trigger performance feedback", "trigger_feedback"),
    ("core trading principles", "universal_principles"),
    ("same stock past trading history", "same_ticker_history"),
    ("same stock trade history", "same_ticker_history"),
    ("accumulated trading intuitions", "accumulated_intuitions"),
)
_COMPONENT_KEYS = (
    "trigger_feedback",
    "universal_principles",
    "same_ticker_history",
    "accumulated_intuitions",
)
_APPLICATION_MODES = {
    "PROMPT_ONLY",
    "PROMPT_AND_DETERMINISTIC_SCORE",
}


def _iso(value: datetime | None = None) -> str:
    observed = value or datetime.now(timezone.utc)
    if observed.tzinfo is None:
        observed = observed.replace(tzinfo=timezone.utc)
    return observed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _number(value: Any) -> float | int | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return value
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return None


def _reason_codes(reasons: Iterable[Any]) -> list[str]:
    codes = set()
    for value in reasons:
        reason = str(value or "").strip().lower()
        if not reason:
            continue
        if "recent stop-out" in reason or "churn guard" in reason:
            codes.add("RECENT_RISK_EXIT")
        elif "same stock" in reason:
            codes.add("SAME_TICKER_HISTORY")
        elif "trigger '" in reason or "trigger actual" in reason:
            codes.add("TRIGGER_ACTUAL_PERFORMANCE")
        elif "sector" in reason:
            codes.add("SECTOR_HISTORY")
        else:
            codes.add("OTHER")
    return sorted(codes)


def _component_counts(context: str) -> dict[str, int]:
    counts: Counter[str] = Counter()
    current: str | None = None
    for raw_line in context.splitlines():
        normalized = raw_line.strip().lower()
        if normalized.startswith("####"):
            current = next(
                (key for marker, key in _COMPONENT_HEADERS if marker in normalized),
                None,
            )
            continue
        if current and raw_line.startswith("- "):
            counts[current] += 1
    return {key: counts[key] for key in _COMPONENT_KEYS}


def build_journal_influence_context(
    *,
    enabled: bool,
    journal_context: str | None,
    score_adjustment: Any,
    adjustment_reasons: Iterable[Any],
    as_of: datetime | None = None,
) -> dict[str, Any]:
    """Describe the exact journal-related prompt input without copying its prose."""

    text = str(journal_context or "")
    reasons = tuple(adjustment_reasons or ())
    suggested = _number(score_adjustment) or 0
    has_input = bool(text.strip()) or suggested != 0 or bool(reasons)
    status = "OK" if enabled and has_input else "MISSING"
    reason_code = None
    if not enabled:
        reason_code = "JOURNAL_DISABLED"
    elif not has_input:
        reason_code = "JOURNAL_CONTEXT_EMPTY"

    hash_payload = {
        "enabled": bool(enabled),
        "journal_context": text,
        "score_adjustment": suggested,
        "adjustment_reasons": [str(value) for value in reasons],
    }
    encoded = json.dumps(
        hash_payload,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )
    snapshot = {
        "context_schema_version": JOURNAL_INFLUENCE_CONTEXT_SCHEMA_VERSION,
        "extractor_version": JOURNAL_INFLUENCE_EXTRACTOR_VERSION,
        "status": status,
        "enabled": bool(enabled),
        "reason_code": reason_code,
        "as_of": _iso(as_of),
        "source": "journal_manager.get_context_for_ticker",
        "input_hash": (
            hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:24]
            if has_input
            else None
        ),
        "context_chars": len(text),
        "context_lines": len(text.splitlines()),
        "component_counts": _component_counts(text),
        "score_adjustment_suggestion": {
            "value": suggested,
            "reason_count": len(reasons),
            "reason_codes": _reason_codes(reasons),
        },
    }
    return snapshot


def attach_deterministic_score_effect(
    context: Mapping[str, Any] | None,
    *,
    score_before: Any,
    score_after: Any,
    min_score: Any,
    applied_adjustment: Any,
    adjustment_reasons: Iterable[Any],
    application_mode: str,
) -> dict[str, Any]:
    """Attach the mechanical score effect while keeping the prompt snapshot immutable."""

    result = dict(context or {})
    before = _number(score_before)
    after = _number(score_after)
    minimum = _number(min_score)
    reasons = tuple(adjustment_reasons or ())
    mode = str(application_mode or "").strip().upper()
    if mode not in _APPLICATION_MODES:
        raise ValueError(f"unsupported journal application mode: {application_mode}")

    threshold_before = before >= minimum if before is not None and minimum is not None else None
    threshold_after = after >= minimum if after is not None and minimum is not None else None
    if threshold_before is None or threshold_after is None:
        crossing = "UNKNOWN"
    elif threshold_before and not threshold_after:
        crossing = "ALLOW_TO_BLOCK"
    elif not threshold_before and threshold_after:
        crossing = "BLOCK_TO_ALLOW"
    elif threshold_after:
        crossing = "UNCHANGED_ALLOW"
    else:
        crossing = "UNCHANGED_BLOCK"

    result["deterministic_effect"] = {
        "application_mode": mode,
        "applied_adjustment": _number(applied_adjustment) or 0,
        "reason_count": len(reasons),
        "reason_codes": _reason_codes(reasons),
        "score_before": before,
        "score_after": after,
        "min_score": minimum,
        "threshold_before": threshold_before,
        "threshold_after": threshold_after,
        "threshold_crossing": crossing,
    }
    return result


__all__ = [
    "JOURNAL_INFLUENCE_CONTEXT_SCHEMA_VERSION",
    "JOURNAL_INFLUENCE_EXTRACTOR_VERSION",
    "attach_deterministic_score_effect",
    "build_journal_influence_context",
]
