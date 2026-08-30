from __future__ import annotations

import json
from datetime import datetime, timezone

from observability.journal_influence import (
    attach_deterministic_score_effect,
    build_journal_influence_context,
)


def test_build_context_hashes_prompt_input_without_copying_journal_text() -> None:
    journal_text = """### Past Trading Experience Reference

#### Trigger Performance Feedback
- Actual Gap performance: weak
#### Core Trading Principles (Applied to all trades)
- Never chase an extended breakout
#### Same Stock Past Trading History
- [2026-08-01] loss — wait for confirmation
#### Accumulated Trading Intuitions
- [entry] weak close -> wait
"""
    context = build_journal_influence_context(
        enabled=True,
        journal_context=journal_text,
        score_adjustment=-2,
        adjustment_reasons=(
            "Same stock past average loss -8.0%",
            "Recent stop-out 4.0h ago (-6.0%) — churn guard",
        ),
        as_of=datetime(2026, 8, 31, tzinfo=timezone.utc),
    )

    serialized = json.dumps(context, ensure_ascii=False, sort_keys=True)
    assert context["context_schema_version"] == 1
    assert context["status"] == "OK"
    assert context["input_hash"] and len(context["input_hash"]) == 24
    assert context["context_chars"] == len(journal_text)
    assert context["component_counts"] == {
        "trigger_feedback": 1,
        "universal_principles": 1,
        "same_ticker_history": 1,
        "accumulated_intuitions": 1,
    }
    assert context["score_adjustment_suggestion"] == {
        "value": -2,
        "reason_count": 2,
        "reason_codes": ["RECENT_RISK_EXIT", "SAME_TICKER_HISTORY"],
    }
    assert "Never chase" not in serialized
    assert "wait for confirmation" not in serialized
    assert "Same stock past average loss" not in serialized


def test_disabled_journal_is_explicit_missing_not_empty_success() -> None:
    context = build_journal_influence_context(
        enabled=False,
        journal_context="",
        score_adjustment=0,
        adjustment_reasons=(),
    )

    assert context["enabled"] is False
    assert context["status"] == "MISSING"
    assert context["reason_code"] == "JOURNAL_DISABLED"
    assert context["input_hash"] is None


def test_attach_score_effect_records_threshold_flip_without_mutating_input() -> None:
    original = build_journal_influence_context(
        enabled=True,
        journal_context="#### Same Stock Past Trading History\n- one trade",
        score_adjustment=-2,
        adjustment_reasons=("Recent stop-out 1.0h ago — churn guard",),
    )

    enriched = attach_deterministic_score_effect(
        original,
        score_before=6,
        score_after=4,
        min_score=5,
        applied_adjustment=-2,
        adjustment_reasons=("Recent stop-out 1.0h ago — churn guard",),
        application_mode="PROMPT_AND_DETERMINISTIC_SCORE",
    )

    assert "deterministic_effect" not in original
    assert enriched["deterministic_effect"] == {
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
    }
