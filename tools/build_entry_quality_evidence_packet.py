"""Build a deterministic entry-quality evidence packet from sanitized JSONL.

This tool performs no network or database access.  It only reads explicitly
provided local observability JSONL files and emits a whitelisted analysis
artifact.  It never changes trading decisions or promotes a rule.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import statistics
from collections import Counter
from collections.abc import Iterable, Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

PACKET_SCHEMA_VERSION = 2
ANALYSIS_CONTRACT_VERSION = "entry-quality-harness-v1"
MIN_PROSPECTIVE_DATES = 20
MIN_PROSPECTIVE_CANDIDATES = 100
MIN_ACTUAL_ENTRIES = 30
MIN_MATURED_OUTCOMES = 30
MIN_CONFIRMED_FILL_COVERAGE = 0.95

_EVENT_TYPES = {
    "candidate.evaluated",
    "candidate.outcome",
    "entry.executed",
    "entry.fill_reconciled",
    "exit.executed",
    "trade.outcome",
}
_OUTCOME_HORIZONS = (1, 3, 5, 7, 14, 30)
_COMPONENT_PATHS = {
    "setup_quality": ("setup_quality",),
    "setup_quality.daily": ("setup_quality", "daily"),
    "setup_quality.weekly": ("setup_quality", "weekly"),
    "event_risk": ("event_risk",),
    "trigger_prior": ("trigger_prior",),
}
_FILL_STATUSES = {
    "SUBMITTED_ONLY",
    "PARTIAL",
    "CONFIRMED",
    "REJECTED",
    "CANCELLED",
    "UNKNOWN",
}
_INPUT_DIAGNOSTIC_KEYS = {
    "input_file_count",
    "input_line_count",
    "invalid_json_line_count",
    "non_object_line_count",
}
_JOURNAL_COMPONENT_KEYS = {
    "trigger_feedback",
    "universal_principles",
    "same_ticker_history",
    "accumulated_intuitions",
}
_JOURNAL_REASON_CODES = {
    "RECENT_RISK_EXIT",
    "SAME_TICKER_HISTORY",
    "TRIGGER_ACTUAL_PERFORMANCE",
    "SECTOR_HISTORY",
    "OTHER",
}
_JOURNAL_APPLICATION_MODES = {
    "PROMPT_ONLY",
    "PROMPT_AND_DETERMINISTIC_SCORE",
}
_THRESHOLD_CROSSINGS = {
    "ALLOW_TO_BLOCK",
    "BLOCK_TO_ALLOW",
    "UNCHANGED_ALLOW",
    "UNCHANGED_BLOCK",
    "UNKNOWN",
}


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _text(value: Any, default: str | None = None) -> str | None:
    if value is None:
        return default
    normalized = str(value).strip()
    return normalized or default


def _number(value: Any) -> float | int | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return value
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return None


def _parse_time(value: Any) -> datetime | None:
    text = _text(value)
    if text is None:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.isoformat().replace("+00:00", "Z")


def _canonical(value: Mapping[str, Any]) -> str:
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"))


def _ref(value: Any) -> str | None:
    normalized = _text(value)
    if normalized is None:
        return None
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16]


def _rounded(value: float | None, digits: int = 4) -> float | None:
    return round(value, digits) if value is not None else None


def _rate(numerator: int, denominator: int) -> float | None:
    return _rounded(numerator / denominator) if denominator else None


def _metrics(values: Iterable[Any]) -> dict[str, Any]:
    numbers = [float(value) for value in values if _number(value) is not None]
    wins = [value for value in numbers if value > 0]
    gross_loss = abs(sum(value for value in numbers if value < 0))
    profit_factor = sum(wins) / gross_loss if gross_loss else None
    return {
        "n": len(numbers),
        "win_rate": _rate(len(wins), len(numbers)),
        "median_return_pct": (
            _rounded(statistics.median(numbers)) if numbers else None
        ),
        "profit_factor": _rounded(profit_factor),
    }


def _latest(events: Iterable[Mapping[str, Any]]) -> Mapping[str, Any] | None:
    candidates = list(events)
    if not candidates:
        return None
    return max(
        candidates,
        key=lambda event: (str(event.get("timestamp") or ""), _canonical(event)),
    )


def _deduplicate(
    raw_events: Iterable[Mapping[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    by_id: dict[str, dict[str, Any]] = {}
    diagnostics = Counter()
    for raw in raw_events:
        diagnostics["input_event_count"] += 1
        event = dict(raw)
        event_id = _text(event.get("event_id"))
        if event_id is None:
            diagnostics["missing_event_id_count"] += 1
            continue
        if event.get("event_type") not in _EVENT_TYPES:
            diagnostics["unsupported_event_type_count"] += 1
            continue
        existing = by_id.get(event_id)
        if existing is not None:
            diagnostics["duplicate_event_id_count"] += 1
            if (str(event.get("timestamp") or ""), _canonical(event)) <= (
                str(existing.get("timestamp") or ""),
                _canonical(existing),
            ):
                continue
        by_id[event_id] = event
    events = sorted(
        by_id.values(),
        key=lambda event: (
            str(event.get("timestamp") or ""),
            str(event.get("event_id") or ""),
        ),
    )
    diagnostics["deduplicated_event_count"] = len(events)
    diagnostics["invalid_timestamp_count"] = sum(
        _parse_time(event.get("timestamp")) is None for event in events
    )
    return events, dict(diagnostics)


def _is_live(event: Mapping[str, Any]) -> bool:
    return str(_mapping(event.get("attributes")).get("ingestion_mode") or "live") != (
        "backfill"
    )


def _component_status(context: Mapping[str, Any], path: tuple[str, ...]) -> str:
    value: Any = context
    for part in path:
        value = _mapping(value).get(part)
    status = str(_mapping(value).get("status") or "MISSING").upper()
    return status if status in {"OK", "MISSING", "ERROR"} else "ERROR"


def _quality_features(context: Mapping[str, Any]) -> dict[str, Any]:
    setup = _mapping(context.get("setup_quality"))
    position = _mapping(setup.get("entry_position"))
    distances = _mapping(position.get("distances_from_entry_pct"))
    checks = _mapping(setup.get("structured_checks"))
    return {
        "primary_support_distance_pct": _number(
            distances.get("primary_support_distance_pct")
        ),
        "secondary_support_distance_pct": _number(
            distances.get("secondary_support_distance_pct")
        ),
        "primary_resistance_distance_pct": _number(
            distances.get("primary_resistance_distance_pct")
        ),
        "secondary_resistance_distance_pct": _number(
            distances.get("secondary_resistance_distance_pct")
        ),
        "entry_checklist_passed": _number(checks.get("entry_checklist_passed")),
        "momentum_signal_count": _number(checks.get("momentum_signal_count")),
        "additional_confirmation_count": _number(
            checks.get("additional_confirmation_count")
        ),
    }


def _safe_codes(value: Any, allowed: set[str]) -> list[str]:
    if not isinstance(value, list):
        return []
    return sorted(
        {
            normalized
            for item in value
            if (normalized := str(item or "").strip().upper()) in allowed
        }
    )


def _journal_influence(attributes: Mapping[str, Any]) -> dict[str, Any]:
    policy = _mapping(attributes.get("policy_context"))
    raw_value = policy.get("journal_influence_context")
    captured = isinstance(raw_value, Mapping)
    raw = _mapping(raw_value)
    reflection = _mapping(policy.get("journal_reflection"))
    status = str(raw.get("status") or "MISSING").upper()
    if status not in {"OK", "MISSING", "ERROR"}:
        status = "ERROR"
    components = _mapping(raw.get("component_counts"))
    suggestion = _mapping(raw.get("score_adjustment_suggestion"))
    effect = _mapping(raw.get("deterministic_effect"))
    application_mode = str(effect.get("application_mode") or "").upper()
    crossing = str(effect.get("threshold_crossing") or "").upper()
    return {
        "captured": captured,
        "context_schema_version": _number(raw.get("context_schema_version")),
        "status": status,
        "enabled": bool(raw.get("enabled")),
        "as_of": _text(raw.get("as_of")),
        "input_hash": _text(raw.get("input_hash")),
        "context_chars": _number(raw.get("context_chars")),
        "component_counts": {
            key: int(count)
            for key, raw_count in components.items()
            if key in _JOURNAL_COMPONENT_KEYS
            and (count := _number(raw_count)) is not None
            and count >= 0
        },
        "score_adjustment_suggestion": {
            "value": _number(suggestion.get("value")),
            "reason_count": _number(suggestion.get("reason_count")),
            "reason_codes": _safe_codes(
                suggestion.get("reason_codes"), _JOURNAL_REASON_CODES
            ),
        },
        "deterministic_effect": {
            "application_mode": (
                application_mode
                if application_mode in _JOURNAL_APPLICATION_MODES
                else None
            ),
            "applied_adjustment": _number(effect.get("applied_adjustment")),
            "reason_count": _number(effect.get("reason_count")),
            "reason_codes": _safe_codes(
                effect.get("reason_codes"), _JOURNAL_REASON_CODES
            ),
            "score_before": _number(effect.get("score_before")),
            "score_after": _number(effect.get("score_after")),
            "min_score": _number(effect.get("min_score")),
            "threshold_before": (
                effect.get("threshold_before")
                if isinstance(effect.get("threshold_before"), bool)
                else None
            ),
            "threshold_after": (
                effect.get("threshold_after")
                if isinstance(effect.get("threshold_after"), bool)
                else None
            ),
            "threshold_crossing": (
                crossing if crossing in _THRESHOLD_CROSSINGS else None
            ),
        },
        "llm_reflection": {
            "referenced": bool(reflection.get("referenced")),
            "recent_exit_caution_present": bool(
                reflection.get("recent_exit_caution")
            ),
            "applied_lessons_present": bool(reflection.get("applied_lessons")),
        },
    }


def _candidate_outcome(event: Mapping[str, Any] | None) -> dict[str, Any]:
    attributes = _mapping(event.get("attributes")) if event else {}
    return {
        f"return_{horizon}d_pct": _number(attributes.get(f"return_{horizon}d_pct"))
        for horizon in _OUTCOME_HORIZONS
    }


def _group_metrics(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
    for row in rows:
        if not row["eligible_for_analysis"]:
            continue
        key = (row["trigger_type"], row["regime"], row["policy_version"])
        grouped.setdefault(key, []).append(row)
    cohorts = []
    for (trigger_type, regime, policy_version), members in sorted(grouped.items()):
        candidate_metrics = {
            f"{horizon}d": _metrics(
                member["outcomes"]["candidate"].get(f"return_{horizon}d_pct")
                for member in members
            )
            for horizon in _OUTCOME_HORIZONS
        }
        actual_values = [
            member["outcomes"]["confirmed_actual_return_pct"] for member in members
        ]
        cohorts.append(
            {
                "trigger_type": trigger_type,
                "regime": regime,
                "policy_version": policy_version,
                "candidate_count": len(members),
                "quality_status_distribution": dict(
                    sorted(
                        Counter(member["quality_status"] for member in members).items()
                    )
                ),
                "confirmed_fill_count": sum(
                    member["entry"]["fill_status"] == "CONFIRMED" for member in members
                ),
                "candidate_outcomes": candidate_metrics,
                "confirmed_actual_outcomes": _metrics(actual_values),
            }
        )
    return cohorts


def _insufficiency_reasons(
    *,
    capture_start: datetime | None,
    decision_dates: int,
    candidates: int,
    captured_candidates: int,
    candidates_with_decision: int,
    entries: int,
    confirmed_fills: int,
    matured_outcomes: int,
    leakage_events: int,
    invalid_timestamps: int,
) -> list[dict[str, Any]]:
    checks = (
        ("CAPTURE_START_UNAVAILABLE", capture_start is None, None, "observed", None),
        (
            "PROSPECTIVE_DATES_LT_20",
            decision_dates < MIN_PROSPECTIVE_DATES,
            decision_dates,
            "minimum",
            MIN_PROSPECTIVE_DATES,
        ),
        (
            "PROSPECTIVE_CANDIDATES_LT_100",
            candidates < MIN_PROSPECTIVE_CANDIDATES,
            candidates,
            "minimum",
            MIN_PROSPECTIVE_CANDIDATES,
        ),
        (
            "CAPTURED_CANDIDATES_LT_100",
            captured_candidates < MIN_PROSPECTIVE_CANDIDATES,
            captured_candidates,
            "minimum",
            MIN_PROSPECTIVE_CANDIDATES,
        ),
        (
            "DECISION_ID_COVERAGE_LT_100_PCT",
            candidates_with_decision < candidates,
            _rate(candidates_with_decision, candidates),
            "minimum",
            1.0,
        ),
        (
            "ACTUAL_ENTRIES_LT_30",
            entries < MIN_ACTUAL_ENTRIES,
            entries,
            "minimum",
            MIN_ACTUAL_ENTRIES,
        ),
        (
            "CONFIRMED_FILL_COVERAGE_LT_95_PCT",
            entries == 0 or confirmed_fills / entries < MIN_CONFIRMED_FILL_COVERAGE,
            _rate(confirmed_fills, entries),
            "minimum",
            MIN_CONFIRMED_FILL_COVERAGE,
        ),
        (
            "MATURED_OUTCOMES_LT_30",
            matured_outcomes < MIN_MATURED_OUTCOMES,
            matured_outcomes,
            "minimum",
            MIN_MATURED_OUTCOMES,
        ),
        (
            "FUTURE_INFORMATION_LEAKAGE_DETECTED",
            leakage_events > 0,
            leakage_events,
            "maximum",
            0,
        ),
        (
            "INVALID_TIMESTAMPS_PRESENT",
            invalid_timestamps > 0,
            invalid_timestamps,
            "maximum",
            0,
        ),
    )
    return [
        {"code": code, "observed": observed, threshold_name: threshold}
        for code, failed, observed, threshold_name, threshold in checks
        if failed
    ]


def build_evidence_packet(
    raw_events: Iterable[Mapping[str, Any]],
    *,
    market: str = "US",
    prospective_start: datetime | str | None = None,
    input_diagnostics: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Return a deterministic, secret-minimized packet for offline analysis."""

    normalized_market = str(market or "US").upper()
    events, dedupe_diagnostics = _deduplicate(raw_events)
    market_events = [
        event
        for event in events
        if str(event.get("market") or "").upper() == normalized_market
    ]
    live_candidates = [
        event
        for event in market_events
        if event.get("event_type") == "candidate.evaluated" and _is_live(event)
    ]
    inferred_starts = [
        _parse_time(event.get("timestamp"))
        for event in live_candidates
        if isinstance(
            _mapping(event.get("attributes")).get("entry_quality_context"), Mapping
        )
    ]
    inferred_starts = [value for value in inferred_starts if value is not None]
    if isinstance(prospective_start, datetime):
        capture_start = prospective_start
        if capture_start.tzinfo is None:
            capture_start = capture_start.replace(tzinfo=timezone.utc)
        capture_start = capture_start.astimezone(timezone.utc)
    elif prospective_start:
        capture_start = _parse_time(prospective_start)
        if capture_start is None:
            raise ValueError("prospective_start must be an ISO-8601 timestamp")
    else:
        capture_start = min(inferred_starts, default=None)

    prospective_raw = [
        event
        for event in live_candidates
        if capture_start is not None
        and (timestamp := _parse_time(event.get("timestamp"))) is not None
        and timestamp >= capture_start
    ]
    candidates_by_identity: dict[str, dict[str, Any]] = {}
    duplicate_decisions = 0
    for event in prospective_raw:
        identity = _text(event.get("decision_id")) or f"event:{event['event_id']}"
        existing = candidates_by_identity.get(identity)
        if existing is not None:
            duplicate_decisions += 1
            event = dict(_latest((existing, event)) or event)
        candidates_by_identity[identity] = event
    candidates = sorted(
        candidates_by_identity.values(),
        key=lambda event: (str(event.get("timestamp") or ""), str(event["event_id"])),
    )
    candidate_outcomes: dict[str, list[dict[str, Any]]] = {}
    entries_by_decision: dict[str, list[dict[str, Any]]] = {}
    fills = []
    outcomes_by_position: dict[str, list[dict[str, Any]]] = {}
    for event in market_events:
        event_type = event.get("event_type")
        if event_type != "candidate.evaluated" and not _is_live(event):
            continue
        decision_id = _text(event.get("decision_id"))
        position_id = _text(event.get("position_id"))
        if event_type == "candidate.outcome" and decision_id:
            candidate_outcomes.setdefault(decision_id, []).append(event)
        elif event_type == "entry.executed" and decision_id:
            entries_by_decision.setdefault(decision_id, []).append(event)
        elif event_type == "entry.fill_reconciled":
            fills.append(event)
        elif event_type in {"trade.outcome", "exit.executed"} and position_id:
            outcomes_by_position.setdefault(position_id, []).append(event)

    leakage = Counter()
    missing_components = Counter()
    quality_statuses = Counter()
    component_statuses = {name: Counter() for name in _COMPONENT_PATHS}
    feature_non_null = Counter()
    fill_statuses = Counter()
    rows = []
    linked_entries = 0
    confirmed_fills = 0
    linked_candidate_outcomes = 0
    confirmed_actual_outcomes = 0
    journal_captured = 0
    journal_enabled = 0
    journal_input_present = 0
    journal_referenced = 0
    journal_adjustment_applied = 0
    journal_statuses = Counter()
    journal_crossings = Counter()
    journal_component_items = Counter()

    for candidate in candidates:
        attributes = _mapping(candidate.get("attributes"))
        context_raw = attributes.get("entry_quality_context")
        captured = isinstance(context_raw, Mapping)
        context = _mapping(context_raw)
        quality_status = str(context.get("status") or "MISSING").upper()
        if quality_status not in {"OK", "MISSING", "ERROR"}:
            quality_status = "ERROR"
        quality_statuses[quality_status] += 1
        component_values = {
            name: _component_status(context, path)
            for name, path in _COMPONENT_PATHS.items()
        }
        for name, status in component_values.items():
            component_statuses[name][status] += 1
        components = context.get("missing_components")
        if isinstance(components, list):
            missing = sorted({_text(value, "UNKNOWN") for value in components})
        else:
            missing = sorted(
                name for name, status in component_values.items() if status != "OK"
            )
        if not captured:
            missing = ["ENTRY_QUALITY_CONTEXT_ABSENT"]
        missing_components.update(missing)

        features = _quality_features(context)
        feature_non_null.update(
            key for key, value in features.items() if value is not None
        )
        candidate_at = _parse_time(candidate.get("timestamp"))
        context_as_of = _parse_time(context.get("as_of"))
        analysis_exclusions = []
        if context_as_of and candidate_at and context_as_of > candidate_at:
            leakage["future_context_as_of"] += 1
            analysis_exclusions.append("FUTURE_CONTEXT_AS_OF")

        decision_id = _text(candidate.get("decision_id"))
        outcome_event = (
            _latest(candidate_outcomes.get(decision_id, ())) if decision_id else None
        )
        if outcome_event and (
            candidate_at is not None
            and (outcome_at := _parse_time(outcome_event.get("timestamp"))) is not None
            and outcome_at < candidate_at
        ):
            leakage["candidate_outcome_before_decision"] += 1
            outcome_event = None
        if outcome_event:
            linked_candidate_outcomes += 1

        entry_event = (
            _latest(entries_by_decision.get(decision_id, ())) if decision_id else None
        )
        position_id = _text(entry_event.get("position_id")) if entry_event else None
        entry_at = _parse_time(entry_event.get("timestamp")) if entry_event else None
        if entry_event and candidate_at and entry_at and entry_at < candidate_at:
            leakage["entry_before_decision"] += 1
            entry_event = None
            position_id = None
            entry_at = None
        if entry_event:
            linked_entries += 1

        matching_fills = []
        for fill in fills:
            fill_position = _text(fill.get("position_id"))
            fill_decision = _text(fill.get("decision_id"))
            if position_id:
                matches = fill_position == position_id or (
                    fill_position is None
                    and decision_id
                    and fill_decision == decision_id
                )
            else:
                matches = bool(decision_id and fill_decision == decision_id)
            if matches:
                matching_fills.append(fill)
        valid_fills = []
        for fill in matching_fills:
            fill_at = _parse_time(fill.get("timestamp"))
            if entry_at and fill_at and fill_at < entry_at:
                leakage["fill_before_entry"] += 1
            else:
                valid_fills.append(fill)
        fill_event = _latest(valid_fills)
        fill_attributes = _mapping(fill_event.get("attributes")) if fill_event else {}
        fill_status = str(
            _mapping(fill_attributes.get("fill_provenance")).get("status") or "UNKNOWN"
        ).upper()
        if fill_status not in _FILL_STATUSES:
            fill_status = "UNKNOWN"
        if entry_event:
            fill_statuses[fill_status] += 1
            confirmed_fills += fill_status == "CONFIRMED"

        actual_event = (
            _latest(outcomes_by_position.get(position_id, ())) if position_id else None
        )
        if (
            actual_event
            and entry_at
            and (actual_at := _parse_time(actual_event.get("timestamp")))
            and actual_at < entry_at
        ):
            leakage["actual_outcome_before_entry"] += 1
            actual_event = None
        actual_return = None
        actual_exit_kind = None
        actual_exclusion = None
        if actual_event:
            actual_attributes = _mapping(actual_event.get("attributes"))
            if fill_status == "CONFIRMED":
                actual_return = _number(actual_attributes.get("profit_rate_pct"))
                actual_exit_kind = _text(actual_attributes.get("exit_kind"))
                confirmed_actual_outcomes += actual_return is not None
            else:
                actual_exclusion = "FILL_NOT_CONFIRMED"

        decision_context = _mapping(attributes.get("decision_context"))
        security_context = _mapping(attributes.get("security_context"))
        journal = _journal_influence(attributes)
        if journal["captured"]:
            journal_captured += 1
            journal_statuses[journal["status"]] += 1
            journal_enabled += journal["enabled"]
            journal_input_present += journal["input_hash"] is not None
            journal_referenced += journal["llm_reflection"]["referenced"]
            effect = journal["deterministic_effect"]
            journal_adjustment_applied += bool(effect["applied_adjustment"])
            if effect["threshold_crossing"]:
                journal_crossings[effect["threshold_crossing"]] += 1
            journal_component_items.update(journal["component_counts"])
        journal_as_of = _parse_time(journal.get("as_of"))
        if journal_as_of and candidate_at and journal_as_of > candidate_at:
            leakage["future_journal_context_as_of"] += 1
            analysis_exclusions.append("FUTURE_JOURNAL_CONTEXT_AS_OF")
        candidate_result = _candidate_outcome(outcome_event)
        rows.append(
            {
                "decision_ref": _ref(decision_id or candidate.get("event_id")),
                "ticker": _text(candidate.get("ticker"), "UNKNOWN"),
                "decided_at": _iso(candidate_at),
                "trigger_type": _text(attributes.get("trigger_type"), "UNKNOWN"),
                "regime": _text(
                    attributes.get("effective_entry_regime")
                    or attributes.get("regime"),
                    "UNKNOWN",
                ),
                "policy_version": _text(candidate.get("policy_version"), "UNKNOWN"),
                "quality_status": quality_status,
                "missing_components": missing,
                "eligible_for_analysis": not analysis_exclusions,
                "analysis_exclusions": analysis_exclusions,
                "decision": {
                    "decision": _text(attributes.get("decision")),
                    "gate_allowed": attributes.get("gate_allowed"),
                    "selected_for_entry": attributes.get("selected_for_entry"),
                    "buy_score": _number(decision_context.get("buy_score")),
                    "adjusted_score": _number(
                        decision_context.get("adjusted_score")
                    ),
                    "min_score": _number(decision_context.get("min_score")),
                    "risk_reward_ratio": _number(
                        security_context.get("risk_reward_ratio")
                    ),
                },
                "quality_features": features,
                "journal_influence": journal,
                "entry": {
                    "observed": entry_event is not None,
                    "position_ref": _ref(position_id),
                    "fill_status": fill_status if entry_event else None,
                },
                "outcomes": {
                    "candidate": candidate_result,
                    "confirmed_actual_return_pct": actual_return,
                    "actual_exit_kind": actual_exit_kind,
                    "actual_exclusion_reason": actual_exclusion,
                },
            }
        )

    dates = {
        timestamp.date().isoformat()
        for timestamp in (_parse_time(event.get("timestamp")) for event in candidates)
        if timestamp is not None
    }
    captured_count = sum(
        isinstance(
            _mapping(event.get("attributes")).get("entry_quality_context"), Mapping
        )
        for event in candidates
    )
    candidate_decision_count = sum(
        _text(event.get("decision_id")) is not None for event in candidates
    )
    matured_outcomes = sum(
        row["outcomes"]["candidate"]["return_30d_pct"] is not None for row in rows
    )
    leakage_count = sum(leakage.values())
    insufficiency = _insufficiency_reasons(
        capture_start=capture_start,
        decision_dates=len(dates),
        candidates=len(candidates),
        captured_candidates=captured_count,
        candidates_with_decision=candidate_decision_count,
        entries=linked_entries,
        confirmed_fills=confirmed_fills,
        matured_outcomes=matured_outcomes,
        leakage_events=leakage_count,
        invalid_timestamps=dedupe_diagnostics.get("invalid_timestamp_count", 0),
    )

    def robustness_rows(kind: str) -> list[dict[str, Any]]:
        extracted = []
        for row in rows:
            value = (
                row["outcomes"]["candidate"]["return_30d_pct"]
                if kind == "candidate_30d"
                else row["outcomes"]["confirmed_actual_return_pct"]
            )
            if value is None or not row["eligible_for_analysis"]:
                continue
            extracted.append(
                {
                    "decision_ref": row["decision_ref"],
                    "ticker": row["ticker"],
                    "trigger_type": row["trigger_type"],
                    "regime": row["regime"],
                    "policy_version": row["policy_version"],
                    "return_pct": value,
                }
            )
        return sorted(
            extracted,
            key=lambda item: (-float(item["return_pct"]), item["decision_ref"]),
        )

    event_times = [
        parsed
        for parsed in (_parse_time(event.get("timestamp")) for event in market_events)
        if parsed is not None
    ]
    safe_input_diagnostics = {
        key: int(value)
        for key, value in _mapping(input_diagnostics).items()
        if key in _INPUT_DIAGNOSTIC_KEYS and _number(value) is not None
    }
    packet: dict[str, Any] = {
        "packet_schema_version": PACKET_SCHEMA_VERSION,
        "analysis_contract_version": ANALYSIS_CONTRACT_VERSION,
        "market": normalized_market,
        "as_of": _iso(max(event_times, default=None)),
        "source_contract": {
            "kind": "local_sanitized_observability_jsonl",
            "network_access": False,
            "raw_attributes_copied": False,
        },
        "data_quality": {
            **dedupe_diagnostics,
            **safe_input_diagnostics,
            "market_event_count": len(market_events),
            "duplicate_candidate_decision_count": duplicate_decisions,
            "anti_leakage_exclusion_count": leakage_count,
            "anti_leakage_distribution": dict(sorted(leakage.items())),
        },
        "prospective_cohort": {
            "capture_start_at": _iso(capture_start),
            "capture_start_source": (
                "explicit"
                if prospective_start is not None
                else "first_live_captured_candidate"
            ),
            "legacy_excluded_count": sum(
                capture_start is not None
                and (timestamp := _parse_time(event.get("timestamp"))) is not None
                and timestamp < capture_start
                for event in live_candidates
            ),
            "decision_date_count": len(dates),
            "candidate_count": len(candidates),
            "candidate_with_decision_id_count": candidate_decision_count,
        },
        "coverage": {
            "captured_count": captured_count,
            "capture_rate": _rate(captured_count, len(candidates)),
            "decision_id_rate": _rate(candidate_decision_count, len(candidates)),
            "entry_link_count": linked_entries,
            "entry_link_rate": _rate(linked_entries, len(candidates)),
            "candidate_outcome_link_count": linked_candidate_outcomes,
            "candidate_outcome_link_rate": _rate(
                linked_candidate_outcomes, len(candidates)
            ),
            "matured_30d_candidate_count": matured_outcomes,
            "confirmed_actual_outcome_count": confirmed_actual_outcomes,
            "feature_non_null": {
                key: {
                    "count": feature_non_null[key],
                    "rate": _rate(feature_non_null[key], len(candidates)),
                }
                for key in sorted(_quality_features({}))
            },
            "journal_influence": {
                "captured_count": journal_captured,
                "capture_rate": _rate(journal_captured, len(candidates)),
                "enabled_count": journal_enabled,
                "input_present_count": journal_input_present,
                "llm_referenced_count": journal_referenced,
                "deterministic_adjustment_count": journal_adjustment_applied,
                "status_distribution": dict(sorted(journal_statuses.items())),
                "threshold_crossing_distribution": dict(
                    sorted(journal_crossings.items())
                ),
                "component_item_counts": dict(
                    sorted(journal_component_items.items())
                ),
                "causal_interpretation": (
                    "observational only; causal impact requires paired no-journal shadow"
                ),
            },
        },
        "missingness": {
            "quality_status_distribution": dict(sorted(quality_statuses.items())),
            "component_status_distribution": {
                name: dict(sorted(statuses.items()))
                for name, statuses in component_statuses.items()
            },
            "missing_component_distribution": dict(sorted(missing_components.items())),
            "interpretation": "MISSING is unknown evidence, not a failed quality gate.",
        },
        "fill_provenance": {
            "entry_count": linked_entries,
            "status_distribution": dict(sorted(fill_statuses.items())),
            "confirmed_count": confirmed_fills,
            "confirmed_coverage": _rate(confirmed_fills, linked_entries),
            "realized_sample_rule": "CONFIRMED only",
        },
        "outcome_linkage": {
            "candidate_outcomes_linked": linked_candidate_outcomes,
            "confirmed_actual_outcomes_linked": confirmed_actual_outcomes,
            "join_keys": {
                "decision": "decision_id",
                "position": "position_id",
            },
        },
        "cohorts": _group_metrics(rows),
        "analysis_rows": rows,
        "robustness_inputs": {
            "candidate_30d_ranked": robustness_rows("candidate_30d"),
            "confirmed_actual_ranked": robustness_rows("confirmed_actual"),
            "winner_removal_method": "recompute after removing the highest return",
            "counterexample_method": (
                "inspect blocked winners and allowed losers per preregistered rule"
            ),
        },
        "readiness": {
            "data_sufficient": not insufficiency,
            "insufficiency_reasons": insufficiency,
            "automatic_shadow_forbidden": True,
            "automatic_live_forbidden": True,
            "promotion_requires": (
                "preregistered rule, prospective holdout, review harness, user approval"
            ),
        },
    }
    encoded = json.dumps(
        packet, ensure_ascii=True, sort_keys=True, separators=(",", ":")
    )
    packet["packet_id"] = hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:24]
    return packet


def read_jsonl(paths: Iterable[Path]) -> tuple[list[dict[str, Any]], dict[str, int]]:
    events = []
    diagnostics = Counter()
    for path in paths:
        diagnostics["input_file_count"] += 1
        with path.open(encoding="utf-8") as handle:
            for line in handle:
                diagnostics["input_line_count"] += 1
                try:
                    value = json.loads(line)
                except json.JSONDecodeError:
                    diagnostics["invalid_json_line_count"] += 1
                    continue
                if not isinstance(value, Mapping):
                    diagnostics["non_object_line_count"] += 1
                    continue
                events.append(dict(value))
    return events, dict(diagnostics)


def write_packet(path: Path, packet: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(packet, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", action="append", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--market", default="US")
    parser.add_argument("--prospective-start")
    args = parser.parse_args(argv)

    events, diagnostics = read_jsonl(args.input)
    packet = build_evidence_packet(
        events,
        market=args.market,
        prospective_start=args.prospective_start,
        input_diagnostics=diagnostics,
    )
    write_packet(args.output, packet)
    print(
        json.dumps(
            {
                "status": "ok",
                "output": str(args.output),
                "packet_id": packet["packet_id"],
                "candidate_count": packet["prospective_cohort"]["candidate_count"],
                "data_sufficient": packet["readiness"]["data_sufficient"],
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
