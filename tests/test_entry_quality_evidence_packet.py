from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

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


def _candidate(
    event_id: str,
    timestamp: str,
    decision_id: str,
    *,
    captured=True,
    journal: dict | None = None,
):
    attributes = {
        "trigger_type": "Volume Surge",
        "effective_entry_regime": "strong_bull",
        "decision_context": {"buy_score": 6, "min_score": 4},
        "security_context": {"risk_reward_ratio": 2.2},
    }
    if captured:
        attributes["entry_quality_context"] = _quality(as_of=timestamp)
    if journal is not None:
        attributes["policy_context"] = {
            "journal_influence_context": journal,
            "journal_reflection": {
                "referenced": True,
                "recent_exit_caution": "raw caution must not be copied",
                "applied_lessons": "raw lesson must not be copied",
            },
        }
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


def test_research_only_capture_is_included_without_inventing_setup_quality():
    candidate = _candidate("research", "2026-09-10T00:00:00Z", "decision", captured=False)
    candidate["market"] = "KR"
    candidate["attributes"]["research_context"] = {"status": "MISSING", "bars": []}
    packet = build_evidence_packet([candidate], market="KR")
    assert packet["prospective_cohort"]["candidate_count"] == 1
    assert packet["coverage"]["trend_research_captured_count"] == 1
    assert packet["coverage"]["entry_quality_captured_count"] == 0
    assert packet["analysis_rows"][0]["quality_status"] == "MISSING"
    assert packet["analysis_rows"][0]["capture_sources"] == ["TREND_RESEARCH"]


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
        "strategy_outcomes_linked": 1,
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
    assert "STRATEGY_CLOSED_TRADES_LT_30" in reason_codes
    assert "CONFIRMED_FILL_COVERAGE_LT_95_PCT" not in reason_codes
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


def test_packet_exposes_only_normalized_journal_influence_fields() -> None:
    packet = build_evidence_packet(
        [
            _candidate(
                "candidate",
                "2026-08-29T00:00:00Z",
                "decision-1",
                journal={
                    "context_schema_version": 1,
                    "status": "OK",
                    "enabled": True,
                    "as_of": "2026-08-29T00:00:00Z",
                    "input_hash": "b" * 24,
                    "context_chars": 321,
                    "component_counts": {
                        "trigger_feedback": 1,
                        "same_ticker_history": 2,
                    },
                    "score_adjustment_suggestion": {
                        "value": -2,
                        "reason_count": 1,
                        "reason_codes": ["RECENT_RISK_EXIT"],
                    },
                    "deterministic_effect": {
                        "application_mode": "PROMPT_AND_DETERMINISTIC_SCORE",
                        "applied_adjustment": -2,
                        "reason_count": 1,
                        "reason_codes": ["RECENT_RISK_EXIT"],
                        "score_before": 6,
                        "score_after": 4,
                        "min_score": 5,
                        "threshold_before": True,
                        "threshold_after": False,
                        "threshold_crossing": "ALLOW_TO_BLOCK",
                    },
                },
            )
        ]
    )

    journal = packet["analysis_rows"][0]["journal_influence"]
    assert packet["packet_schema_version"] == 3
    assert packet["analysis_contract_version"] == "entry-quality-harness-v2"
    assert packet["coverage"]["journal_influence"]["captured_count"] == 1
    assert packet["coverage"]["journal_influence"]["llm_referenced_count"] == 1
    assert packet["coverage"]["journal_influence"][
        "threshold_crossing_distribution"
    ] == {"ALLOW_TO_BLOCK": 1}
    assert journal["input_hash"] == "b" * 24
    assert journal["component_counts"]["same_ticker_history"] == 2
    assert journal["llm_reflection"] == {
        "referenced": True,
        "recent_exit_caution_present": True,
        "applied_lessons_present": True,
    }
    serialized = json.dumps(packet, ensure_ascii=False, sort_keys=True)
    assert "raw caution must not be copied" not in serialized
    assert "raw lesson must not be copied" not in serialized


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


def _closed_trade(status="REJECTED", index=0, profit=-3.51):
    start = datetime(2026, 7, 1, tzinfo=timezone.utc) + timedelta(days=index % 20)
    decision, position = f"decision-{index}", f"position-{index}"
    return [
        _candidate(f"candidate-{index}", start.isoformat(), decision),
        _event(
            f"entry-{index}",
            "entry.executed",
            (start + timedelta(minutes=1)).isoformat(),
            decision_id=decision,
            position_id=position,
        ),
        _event(
            f"fill-{index}",
            "entry.fill_reconciled",
            (start + timedelta(minutes=2)).isoformat(),
            decision_id=decision,
            position_id=position,
            attributes={"fill_provenance": {"status": status}},
        ),
        _event(
            f"outcome-{index}",
            "trade.outcome",
            (start + timedelta(hours=18, minutes=7)).isoformat(),
            position_id=position,
            attributes={"profit_rate_pct": profit, "exit_kind": "stop_loss"},
        ),
    ]


@pytest.mark.parametrize(
    "status", ["REJECTED", "PARTIAL", "UNKNOWN", "SUBMITTED_ONLY", "CANCELLED"]
)
def test_ledger_closed_trade_is_independent_of_broker_status(status):
    packet = build_evidence_packet(_closed_trade(status))
    outcome = packet["analysis_rows"][0]["outcomes"]
    assert outcome["strategy_return_pct"] == -3.51
    assert outcome["strategy_exit_kind"] == "stop_loss"
    assert outcome["strategy_holding_seconds"] == 18 * 3600 + 6 * 60
    assert outcome["confirmed_actual_return_pct"] is None
    assert packet["cohorts"][0]["strategy_outcomes"]["n"] == 1
    assert packet["coverage"]["strategy_closed_trade_count"] == 1
    assert len(packet["robustness_inputs"]["strategy_ranked"]) == 1


def test_strategy_metrics_and_sufficiency_invariant_under_fill_status():
    packets = [
        build_evidence_packet(
            [event for index in range(100) for event in _closed_trade(status, index)]
        )
        for status in ("REJECTED", "CONFIRMED", "PARTIAL", "UNKNOWN")
    ]
    for packet in packets:
        assert packet["readiness"] == packet["strategy_readiness"]
        assert packet["strategy_readiness"]["data_sufficient"] is True
        assert packet["strategy_readiness"] == packets[0]["strategy_readiness"]
        assert (
            packet["cohorts"][0]["strategy_outcomes"]
            == packets[0]["cohorts"][0]["strategy_outcomes"]
        )
        assert (
            packet["robustness_inputs"]["strategy_ranked"]
            == packets[0]["robustness_inputs"]["strategy_ranked"]
        )
        assert packet["source_contract"]["broker_realized_pnl_verified"] is False
    assert packets[0]["fill_provenance"]["confirmed_count"] == 0
    assert packets[1]["coverage"]["confirmed_actual_outcome_count"] == 100
    assert (
        "not broker realized PnL"
        in packets[1]["source_contract"]["confirmed_actual_compatibility"]
    )


@pytest.mark.parametrize(
    "damage",
    [
        "orphan",
        "ticker",
        "market",
        "before_entry",
        "invalid_time",
        "nan",
        "inf",
        "missing_entry",
    ],
)
def test_invalid_ledger_link_or_return_does_not_count(damage):
    events = _closed_trade()
    outcome = events[-1]
    if damage == "orphan":
        outcome["position_id"] = "unrelated"
    elif damage == "ticker":
        outcome["ticker"] = "OTHER"
    elif damage == "market":
        outcome["market"] = "KR"
    elif damage == "before_entry":
        outcome["timestamp"] = "2026-06-30T00:00:00Z"
    elif damage == "invalid_time":
        outcome["timestamp"] = "not-a-time"
    elif damage in {"nan", "inf"}:
        outcome["attributes"]["profit_rate_pct"] = float(damage)
    elif damage == "missing_entry":
        del events[1]
    packet = build_evidence_packet(events)
    assert packet["coverage"]["strategy_closed_trade_count"] == 0
    assert packet["analysis_rows"][0]["outcomes"]["strategy_return_pct"] is None
    assert packet["robustness_inputs"]["strategy_ranked"] == []
    json.dumps(packet, allow_nan=False)


def test_fill_before_entry_is_execution_diagnostic_not_strategy_leakage():
    events = _closed_trade("CONFIRMED")
    baseline = build_evidence_packet(events)
    events[2]["timestamp"] = "2026-06-30T00:00:00Z"
    packet = build_evidence_packet(events)
    assert packet["strategy_readiness"] == baseline["strategy_readiness"]
    assert packet["data_quality"]["anti_leakage_exclusion_count"] == 0
    assert packet["data_quality"]["broker_execution_diagnostics"] == {
        "fill_before_entry": 1
    }
    assert packet["analysis_rows"][0]["outcomes"]["strategy_return_pct"] == -3.51
    assert packet["analysis_rows"][0]["outcomes"]["confirmed_actual_return_pct"] is None


def test_strategy_deduplication_winner_removal_and_future_context_exclusion():
    events = _closed_trade(index=0, profit=99) + _closed_trade(index=1, profit=-3)
    packet = build_evidence_packet(events + [events[-1]])
    assert packet["coverage"]["strategy_closed_trade_count"] == 2
    ranked = packet["robustness_inputs"]["strategy_ranked"]
    assert [row["return_pct"] for row in ranked] == [99, -3]
    assert [row["return_pct"] for row in ranked[1:]] == [-3]
    events[0]["attributes"]["entry_quality_context"]["as_of"] = "2027-01-01T00:00:00Z"
    leaked = build_evidence_packet(events)
    assert leaked["coverage"]["strategy_closed_trade_count"] == 1
    assert [
        row["return_pct"] for row in leaked["robustness_inputs"]["strategy_ranked"]
    ] == [-3]


def test_exit_event_and_trade_outcome_are_one_ledger_close():
    events = _closed_trade()
    exit_event = dict(events[-1], event_id="exit", event_type="exit.executed")
    packet = build_evidence_packet(events + [exit_event])
    assert packet["coverage"]["strategy_closed_trade_count"] == 1
    assert packet["cohorts"][0]["strategy_outcomes"]["n"] == 1
