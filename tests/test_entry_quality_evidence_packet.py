from __future__ import annotations

import json
from datetime import datetime, timezone

from tools.build_entry_quality_evidence_packet import build_evidence_packet


def _event(
    event_id: str,
    event_type: str,
    timestamp: str,
    *,
    decision_id: str | None = None,
    position_id: str | None = None,
    attributes: dict | None = None,
) -> dict:
    return {
        "event_id": event_id,
        "event_type": event_type,
        "timestamp": timestamp,
        "market": "US",
        "ticker": "AAA",
        "decision_id": decision_id,
        "position_id": position_id,
        "policy_version": "policy-v1",
        "attributes": attributes or {},
    }


def _quality(*, status: str = "MISSING", as_of: str = "2026-08-29T00:00:00Z"):
    return {
        "status": status,
        "as_of": as_of,
        "missing_components": ["event_risk"],
        "setup_quality": {
            "status": "OK",
            "entry_position": {
                "distances_from_entry_pct": {
                    "primary_support_distance_pct": -3.0,
                    "primary_resistance_distance_pct": 8.0,
                }
            },
            "structured_checks": {"entry_checklist_passed": 4},
            "daily": {"status": "MISSING"},
            "weekly": {"status": "MISSING"},
        },
        "event_risk": {"status": "MISSING"},
        "trigger_prior": {"status": "OK"},
    }


def _candidate(event_id: str, timestamp: str, decision_id: str, *, captured=True):
    attributes = {
        "trigger_type": "Volume Surge",
        "effective_entry_regime": "strong_bull",
        "decision_context": {"buy_score": 6, "min_score": 4},
        "security_context": {"risk_reward_ratio": 2.2},
    }
    if captured:
        attributes["entry_quality_context"] = _quality(as_of=timestamp)
    return _event(
        event_id,
        "candidate.evaluated",
        timestamp,
        decision_id=decision_id,
        attributes=attributes,
    )


def test_excludes_legacy_candidates_before_first_captured_event() -> None:
    packet = build_evidence_packet(
        [
            _candidate(
                "legacy", "2026-08-28T23:59:59Z", "decision-old", captured=False
            ),
            _candidate("first", "2026-08-29T00:00:00Z", "decision-1"),
            _candidate("after", "2026-08-29T00:01:00Z", "decision-2", captured=False),
        ]
    )

    assert packet["prospective_cohort"]["capture_start_at"] == "2026-08-29T00:00:00Z"
    assert packet["prospective_cohort"]["legacy_excluded_count"] == 1
    assert packet["prospective_cohort"]["candidate_count"] == 2
    assert packet["coverage"]["captured_count"] == 1


def test_deduplicates_event_and_decision_ids_deterministically() -> None:
    first = _candidate("same-event", "2026-08-29T00:00:00Z", "decision-1")
    duplicate = json.loads(json.dumps(first))
    second_event_same_decision = _candidate(
        "second-event", "2026-08-29T00:01:00Z", "decision-1"
    )

    forward = build_evidence_packet([first, duplicate, second_event_same_decision])
    reverse = build_evidence_packet([second_event_same_decision, duplicate, first])

    assert forward["packet_id"] == reverse["packet_id"]
    assert forward["data_quality"]["duplicate_event_id_count"] == 1
    assert forward["data_quality"]["duplicate_candidate_decision_count"] == 1
    assert forward["prospective_cohort"]["candidate_count"] == 1


def test_missing_is_unknown_not_a_failed_quality_gate() -> None:
    packet = build_evidence_packet(
        [_candidate("candidate", "2026-08-29T00:00:00Z", "decision-1")]
    )

    assert packet["missingness"]["quality_status_distribution"] == {"MISSING": 1}
    assert "unknown evidence" in packet["missingness"]["interpretation"]
    row = packet["analysis_rows"][0]
    assert row["eligible_for_analysis"] is True
    assert "event_risk" in row["missing_components"]


def test_submitted_only_is_never_counted_as_confirmed_actual() -> None:
    candidate = _candidate("candidate", "2026-08-29T00:00:00Z", "decision-1")
    entry = _event(
        "entry",
        "entry.executed",
        "2026-08-29T00:01:00Z",
        decision_id="decision-1",
        position_id="position-1",
    )
    fill = _event(
        "fill",
        "entry.fill_reconciled",
        "2026-08-29T00:02:00Z",
        decision_id="decision-1",
        position_id="position-1",
        attributes={"fill_provenance": {"status": "SUBMITTED_ONLY"}},
    )
    outcome = _event(
        "outcome",
        "trade.outcome",
        "2026-09-01T00:00:00Z",
        position_id="position-1",
        attributes={"profit_rate_pct": 10.0},
    )

    packet = build_evidence_packet([candidate, entry, fill, outcome])

    assert packet["fill_provenance"]["status_distribution"] == {"SUBMITTED_ONLY": 1}
    assert packet["fill_provenance"]["confirmed_count"] == 0
    assert packet["outcome_linkage"]["confirmed_actual_outcomes_linked"] == 0
    row = packet["analysis_rows"][0]
    assert row["outcomes"]["confirmed_actual_return_pct"] is None
    assert row["outcomes"]["actual_exclusion_reason"] == "FILL_NOT_CONFIRMED"


def test_joins_candidate_outcome_by_decision_and_actual_by_position() -> None:
    events = [
        _candidate("candidate", "2026-08-29T00:00:00Z", "decision-1"),
        _event(
            "candidate-outcome",
            "candidate.outcome",
            "2026-09-30T00:00:00Z",
            decision_id="decision-1",
            attributes={"return_30d_pct": 7.0},
        ),
        _event(
            "entry",
            "entry.executed",
            "2026-08-29T00:01:00Z",
            decision_id="decision-1",
            position_id="position-1",
        ),
        _event(
            "fill",
            "entry.fill_reconciled",
            "2026-08-29T00:02:00Z",
            decision_id="decision-1",
            position_id="position-1",
            attributes={"fill_provenance": {"status": "CONFIRMED"}},
        ),
        _event(
            "trade-outcome",
            "trade.outcome",
            "2026-09-10T00:00:00Z",
            position_id="position-1",
            attributes={"profit_rate_pct": 4.0, "exit_kind": "target"},
        ),
    ]

    packet = build_evidence_packet(events)

    assert packet["outcome_linkage"] == {
        "candidate_outcomes_linked": 1,
        "confirmed_actual_outcomes_linked": 1,
        "join_keys": {"decision": "decision_id", "position": "position_id"},
    }
    row = packet["analysis_rows"][0]
    assert row["outcomes"]["candidate"]["return_30d_pct"] == 7.0
    assert row["outcomes"]["confirmed_actual_return_pct"] == 4.0


def test_small_sample_emits_explicit_insufficiency_reasons() -> None:
    packet = build_evidence_packet(
        [_candidate("candidate", "2026-08-29T00:00:00Z", "decision-1")]
    )
    reason_codes = {
        reason["code"] for reason in packet["readiness"]["insufficiency_reasons"]
    }

    assert packet["readiness"]["data_sufficient"] is False
    assert "PROSPECTIVE_CANDIDATES_LT_100" in reason_codes
    assert "ACTUAL_ENTRIES_LT_30" in reason_codes
    assert "MATURED_OUTCOMES_LT_30" in reason_codes
    assert packet["readiness"]["automatic_live_forbidden"] is True


def test_packet_whitelist_does_not_copy_secrets_or_raw_attributes() -> None:
    candidate = _candidate("candidate", "2026-08-29T00:00:00Z", "decision-1")
    candidate["attributes"].update(
        {
            "account_number": "123-456-secret",
            "authorization": "Bearer do-not-copy",
            "nested": {"api_key": "do-not-copy-either"},
        }
    )

    serialized = json.dumps(build_evidence_packet([candidate]), sort_keys=True)

    assert "123-456-secret" not in serialized
    assert "Bearer do-not-copy" not in serialized
    assert "do-not-copy-either" not in serialized
    assert "account_number" not in serialized


def test_future_as_of_is_excluded_from_analysis() -> None:
    candidate = _candidate("candidate", "2026-08-29T00:00:00Z", "decision-1")
    candidate["attributes"]["entry_quality_context"] = _quality(
        as_of="2026-08-29T00:00:01Z"
    )
    outcome = _event(
        "future-contaminated-outcome",
        "candidate.outcome",
        "2026-09-30T00:00:00Z",
        decision_id="decision-1",
        attributes={"return_30d_pct": 99.0},
    )

    packet = build_evidence_packet(
        [candidate, outcome],
        prospective_start=datetime(2026, 8, 29, tzinfo=timezone.utc),
    )

    assert packet["data_quality"]["anti_leakage_exclusion_count"] == 1
    assert packet["analysis_rows"][0]["eligible_for_analysis"] is False
    assert "FUTURE_INFORMATION_LEAKAGE_DETECTED" in {
        reason["code"] for reason in packet["readiness"]["insufficiency_reasons"]
    }
    assert packet["cohorts"] == []
    assert packet["robustness_inputs"]["candidate_30d_ranked"] == []
