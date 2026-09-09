"""Replay sanitized, fail-open observations into a NON-authoritative ledger.

No broker, production database or messaging dependency is permitted here.
Legacy position IDs remain separate campaigns; symbol/date matching is unsafe.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from collections import Counter
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path

from prism_core.strategy_ledger import StrategyLedger


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                     ensure_ascii=False, allow_nan=False).encode()).hexdigest()


def _ref(value):
    return value if isinstance(value, str) and value.strip() and value not in {
        "[REDACTED]", "UNKNOWN", "MISSING"} else None


def _time(value):
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("source timestamp requires timezone")
    return parsed.astimezone(timezone.utc)


def validate_destination(destination, inputs=()):
    path = Path(destination).resolve()
    if path.name == "stock_tracking_db.sqlite" or path in {Path(p).resolve() for p in inputs}:
        raise ValueError("destination must be a separate strategy ledger")
    if path.exists() and path.stat().st_size:
        connection = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)
        try:
            tables = {row[0] for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'")}
        finally:
            connection.close()
        allowed = {"strategy_ledger_metadata", "books", "campaigns", "events", "legs", "executions"}
        if "strategy_ledger_metadata" not in tables or tables - allowed:
            raise ValueError("existing destination is not a strategy ledger")
    return path


def _apply_tail(ledger, event_id, row, digest, campaign, counts, unresolved):
    attrs = row.get("attributes") or {}
    execution = attrs.get("execution_context") or {}
    if row["event_type"] == "exit.executed":
        ledger.sell(event_id=event_id, campaign_id=campaign,
                    price=(attrs.get("decision_context") or {})["sell_price"],
                    occurred_at=row["timestamp"], source_hash=digest)
        counts["exit_events_accepted"] += 1
        return
    fill = attrs.get("fill_provenance") or {}
    profile = _ref(execution.get("execution_profile_ref")) or _ref(attrs.get("execution_profile_ref"))
    intent = _ref(attrs.get("intent_ref")) or _ref(row.get("intent_id")) or _ref(fill.get("intent_ref"))
    quantity = fill.get("confirmed_fill_quantity", fill.get("confirmed_quantity"))
    price = fill.get("confirmed_fill_price", fill.get("confirmed_price"))
    source_status = str(fill.get("status", "UNKNOWN")).upper()
    status = {"CONFIRMED": "FILLED", "SUBMITTED_ONLY": "SUBMITTED"}.get(source_status, source_status)
    if status not in {"UNKNOWN", "SUBMITTED", "ACCEPTED", "REJECTED", "PARTIAL", "FILLED", "CANCELLED"}:
        status = "UNKNOWN"
    evidence = _ref(fill.get("evidence_source")) or _ref(attrs.get("source"))
    fill_complete = quantity is not None and price is not None and evidence is not None
    if status in {"FILLED", "PARTIAL"} and not fill_complete:
        status = "UNKNOWN"
    if not profile or not intent or status == "UNKNOWN":
        known_campaign = any(c["campaign_id"] == campaign
                             for b in ledger.list_book_ids()
                             for c in ledger.snapshot(b)["campaigns"])
        if not known_campaign:
            raise ValueError("orphan execution observation")
        counts["fill_evidence_missing"] += 1
        unresolved.append({"event_ref": _digest(event_id), "source_hash": digest,
                           "campaign_id": campaign, "position_id": row["position_id"],
                           "intent_ref": intent, "execution_profile_ref": profile,
                           "profile_status": "KNOWN" if profile else "UNKNOWN",
                           "status": status, "source_status": source_status,
                           "confirmed_quantity": None, "confirmed_price": None,
                           "observed_at": row["timestamp"], "ledger_applied": False,
                           "evidence_status": "MISSING"})
        return
    ledger.observe_execution(event_id=event_id, campaign_id=campaign,
                             execution_profile_ref=profile, status=status,
                             observed_at=row["timestamp"], intent_ref=intent,
                             confirmed_quantity=quantity, confirmed_price=price,
                             evidence_source=evidence or "sanitized_observability", source_hash=digest)
    counts["execution_events_accepted"] += 1


def project_events(events, ledger, slot_profiles=None):
    """Apply exact-entry evidence, reporting omissions instead of inventing fills.

    Optional market-keyed slot profiles supply capacity and an explicit strategy
    cohort/mode mapping. Account profiles never establish strategy identity.
    Without an explicit cohort each legacy campaign remains isolated.
    """
    slot_profiles = {} if slot_profiles is None else slot_profiles
    if not isinstance(slot_profiles, dict):
        raise TypeError("slot profiles must be a market-keyed object")
    for market, config in slot_profiles.items():
        if market not in {"KR", "US"} or not isinstance(config, dict) or set(config) - {"max_slots", "cohort", "mode"}:
            raise ValueError("invalid slot profile; monetary configuration is not supported")
    counts = Counter()
    issues = []
    unresolved = []
    unique = {}
    conflicts = set()
    for row in events:
        counts["source_rows"] += 1
        event_id = _ref(row.get("event_id")) if isinstance(row, dict) else None
        if not event_id:
            counts["missing_event_id"] += 1
            continue
        try:
            digest = _digest(row)
        except (ValueError, TypeError):
            conflicts.add(event_id)
            continue
        if event_id in unique:
            if unique[event_id][1] != digest:
                conflicts.add(event_id)
            else:
                counts["duplicate_rows"] += 1
        else:
            unique[event_id] = (row, digest)
    ordered = []
    for event_id, (row, digest) in unique.items():
        if event_id in conflicts:
            continue
        try:
            ordered.append((_time(row.get("timestamp")), event_id, row, digest))
        except (ValueError, TypeError):
            counts["invalid_timestamp"] += 1
    counts["conflicting_source_ids"] = len(conflicts)
    deferred = []
    for _, event_id, row, digest in sorted(ordered):
        event_type = row.get("event_type")
        if event_type not in {"entry.executed", "exit.executed", "entry.fill_reconciled"}:
            counts["ignored_event_types"] += 1
            continue
        attrs = row.get("attributes") or {}
        if not isinstance(attrs, dict) or any(
            attrs.get(key) is not None and not isinstance(attrs[key], dict)
            for key in ("execution_context", "decision_context", "policy_context", "fill_provenance")
        ):
            counts["malformed_context_rows"] += 1
            continue
        position = _ref(row.get("position_id"))
        market = str(row.get("market", "")).upper()
        if not position or market not in {"KR", "US"}:
            counts["missing_exact_position_or_market"] += 1
            continue
        campaign = "legacy:" + _digest([market, position])
        attrs = row.get("attributes") or {}
        execution = attrs.get("execution_context") or {}
        profile = _ref(execution.get("execution_profile_ref")) or _ref(attrs.get("execution_profile_ref"))
        timestamp = row["timestamp"]
        try:
            if event_type == "entry.executed":
                if execution.get("simulator_recorded") is not True:
                    counts["entry_without_simulator_recorded"] += 1
                    continue
                config = slot_profiles.get(market, {})
                policy = (attrs.get("policy_context") or {}).get("regime_entry_policy") or {}
                if not isinstance(policy, dict):
                    raise ValueError("invalid policy")
                if policy.get("mode") not in {"normal", "rebound_pilot"}:
                    counts["unsupported_legacy_policy_mode"] += 1
                    continue
                pilot = policy["mode"] == "rebound_pilot"
                raw_fraction = policy.get("position_fraction")
                try:
                    fraction = Decimal(str(raw_fraction))
                except (InvalidOperation, ValueError):
                    fraction = Decimal("NaN")
                if isinstance(raw_fraction, bool) or not fraction.is_finite():
                    counts["ambiguous_legacy_fraction"] += 1
                    continue
                if pilot and fraction != Decimal(".5"):
                    counts["invalid_pilot_fraction"] += 1
                    continue
                if not pilot and fraction != 1:
                    counts["unsupported_legacy_fraction"] += 1
                    continue
                cohort = _ref(config.get("cohort"))
                mode = config.get("mode", "VALIDATION") if cohort else "VALIDATION_ONLY"
                book = "strategy:" + _digest([market, cohort, mode]) if cohort else "isolated:" + campaign
                if not profile:
                    counts["entries_missing_execution_profile"] += 1
                if not cohort:
                    counts["isolated_legacy_entries"] += 1
                ledger.create_book(book_id=book, market=market,
                                   max_slots=config.get("max_slots", 10), cohort=cohort, mode=mode)
                ledger.apply_target(event_id=event_id, book_id=book, campaign_id=campaign,
                                    symbol=row["ticker"], target_pct=50 if pilot else 100,
                                    price=execution["entry_price"], occurred_at=timestamp,
                                    policy_version=row.get("policy_version") or "legacy-observation-v1",
                                    reason="sanitized_observation_replay", source_hash=digest)
                counts["entry_events_accepted"] += 1
            else:
                try:
                    _apply_tail(ledger, event_id, row, digest, campaign, counts, unresolved)
                except (ValueError, KeyError, TypeError):
                    deferred.append((event_id, row, digest, campaign))
        except (ValueError, KeyError, TypeError) as error:
            counts["rejected_events"] += 1
            issues.append({"event_ref": _digest(event_id), "error_type": type(error).__name__})
    # Apply in source chronology. Orphans are never checkpointed; rerunning
    # the full input retries them without changing the original timestamps.
    for event_id, row, digest, campaign in deferred:
        try:
            _apply_tail(ledger, event_id, row, digest, campaign, counts, unresolved)
        except (ValueError, KeyError, TypeError) as error:
            counts["deferred_or_rejected_events"] += 1
            issues.append({"event_ref": _digest(event_id), "error_type": type(error).__name__})
    return {"schema_version": 2, "mode": "NO_ORDER_REPLAY", "authoritative": False,
            "coverage_status": "INCOMPLETE_FAIL_OPEN_SOURCE",
            "portfolio_aggregation": "EXPLICIT_STRATEGY_COHORT_ONLY; ISOLATED_BOOKS_MUST_NOT_BE_SUMMED",
            "counts": dict(counts), "issues": issues,
            "unresolved_execution_overlays": unresolved,
            "conflicting_event_refs": sorted(_digest(i) for i in conflicts)}


def replay_files(input_paths, destination, slot_profile_path=None):
    path = validate_destination(destination, [*input_paths, *([slot_profile_path] if slot_profile_path else [])])
    profiles = json.loads(Path(slot_profile_path).read_text()) if slot_profile_path else {}
    events = []
    malformed = 0
    for source in input_paths:
        with Path(source).open() as stream:
            for line in stream:
                if not line.strip():
                    continue
                try:
                    events.append(json.loads(line))
                except json.JSONDecodeError:
                    malformed += 1
    ledger = StrategyLedger(path)
    report = project_events(events, ledger, profiles)
    report["counts"]["malformed_json_rows"] = malformed
    report["snapshots"] = [ledger.snapshot(book_id) for book_id in ledger.list_book_ids()]
    return report
