from __future__ import annotations

import json
import sqlite3
import sys
import types
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from observability import entry_quality
from observability.entry_quality import (
    build_entry_quality_context,
    build_fill_provenance,
    capture_enabled,
    emit_fill_reconciliation,
    validate_completeness_status,
    validate_fill_provenance_status,
)
from observability.trading_context import emit_trading_context


def _feedback_cursor() -> sqlite3.Cursor:
    connection = sqlite3.connect(":memory:")
    cursor = connection.cursor()
    cursor.execute(
        """CREATE TABLE us_analysis_performance_tracker (
               trigger_type TEXT, was_traded INTEGER,
               return_7d REAL, return_14d REAL, return_30d REAL
           )"""
    )
    cursor.execute(
        """CREATE TABLE us_trading_history (
               id INTEGER, trigger_type TEXT, profit_rate REAL, sell_date TEXT
           )"""
    )
    cursor.executemany(
        "INSERT INTO us_analysis_performance_tracker VALUES (?, 0, ?, ?, ?)",
        (
            ("Volume Surge", 0.01, 0.02, 0.03),
            ("Volume Surge", -0.02, -0.01, -0.04),
        ),
    )
    cursor.executemany(
        "INSERT INTO us_trading_history VALUES (?, ?, ?, ?)",
        (
            (1, "Volume Surge", 5.0, "2026-08-01"),
            (2, "Volume Surge", -10.0, "2026-08-02"),
        ),
    )
    return cursor


def test_capture_is_on_by_default_and_has_one_explicit_kill_switch(monkeypatch) -> None:
    monkeypatch.delenv("ENTRY_QUALITY_CAPTURE_ENABLED", raising=False)
    assert capture_enabled() is True

    monkeypatch.setenv("ENTRY_QUALITY_CAPTURE_ENABLED", "0")
    assert capture_enabled() is False
    assert capture_enabled("false") is False
    assert capture_enabled("1") is True


def test_context_uses_only_structured_local_facts_and_keeps_missing_explicit() -> None:
    observed = datetime(2026, 8, 29, 1, 2, 3, tzinfo=timezone.utc)
    context = build_entry_quality_context(
        scenario={
            "entry_checklist_passed": 5,
            "momentum_signal_count": 3,
            "additional_confirmation_count": 2,
            "trading_scenarios": {
                "key_levels": {
                    "primary_support": 95,
                    "secondary_support": 90,
                    "primary_resistance": 115,
                    "secondary_resistance": 125,
                }
            },
        },
        current_price=100,
        cursor=_feedback_cursor(),
        trigger_type="Volume Surge",
        as_of=observed,
        captured_at=observed,
    )

    assert context["context_schema_version"] == 1
    assert context["status"] == "MISSING"
    assert context["missing_components"] == [
        "event_risk",
        "setup_quality.daily",
        "setup_quality.weekly",
    ]
    setup = context["setup_quality"]
    assert setup["status"] == "OK"
    assert setup["entry_position"]["distances_from_entry_pct"] == {
        "primary_support_distance_pct": -5.0,
        "secondary_support_distance_pct": -10.0,
        "primary_resistance_distance_pct": 15.0,
        "secondary_resistance_distance_pct": 25.0,
    }
    assert setup["daily"]["status"] == "MISSING"
    assert setup["weekly"]["status"] == "MISSING"
    assert context["event_risk"]["status"] == "MISSING"
    assert context["trigger_prior"]["status"] == "OK"
    assert context["trigger_prior"]["candidate"]["n"] == 2
    assert context["trigger_prior"]["actual"]["n"] == 2
    assert context["trigger_prior"]["actual"]["median_return_pct"] == -2.5
    assert context["trigger_prior"]["actual"]["profit_factor"] == 0.5


def test_trigger_prior_ignores_prism_us_tracking_package_shadow(monkeypatch) -> None:
    shadow = types.ModuleType("tracking")
    shadow.__path__ = [
        str(Path(__file__).resolve().parents[1] / "prism-us" / "tracking")
    ]
    monkeypatch.setitem(sys.modules, "tracking", shadow)
    monkeypatch.delitem(sys.modules, "tracking.performance_feedback", raising=False)
    entry_quality._load_performance_feedback_module.cache_clear()
    monkeypatch.delitem(
        sys.modules, entry_quality._FEEDBACK_MODULE_NAME, raising=False
    )

    prior = entry_quality.trigger_prior_snapshot(
        _feedback_cursor(), "Volume Surge"
    )

    assert prior["status"] == "OK"
    assert prior["candidate"]["n"] == 2
    assert prior["actual"]["profit_factor"] == 0.5
    loaded = sys.modules[entry_quality._FEEDBACK_MODULE_NAME]
    assert Path(loaded.__file__).resolve() == (
        Path(__file__).resolve().parents[1]
        / "tracking"
        / "performance_feedback.py"
    ).resolve()


def test_context_rejects_future_as_of() -> None:
    captured = datetime(2026, 8, 29, tzinfo=timezone.utc)
    with pytest.raises(ValueError, match="future"):
        build_entry_quality_context(
            scenario={},
            current_price=100,
            as_of=captured + timedelta(seconds=1),
            captured_at=captured,
        )


def test_strict_status_enums_reject_unknown_values() -> None:
    assert validate_completeness_status("missing") == "MISSING"
    assert validate_fill_provenance_status("confirmed") == "CONFIRMED"
    with pytest.raises(ValueError):
        validate_completeness_status("PASS")
    with pytest.raises(ValueError):
        validate_fill_provenance_status("FILLED_LIKELY")


def test_fill_provenance_never_promotes_submission_to_confirmed() -> None:
    submitted = build_fill_provenance(
        {
            "success": True,
            "intent_status": "SUBMITTED",
            "intent_broker": "KIS",
            "order_no": "external-order-id",
        }
    )
    queued = build_fill_provenance({"success": True, "intent_status": "QUEUED"})
    rejected = build_fill_provenance({"success": False, "intent_status": "FAILED"})

    assert submitted["status"] == "SUBMITTED_ONLY"
    assert submitted["confirmed_fill_price"] is None
    assert queued["status"] == "UNKNOWN"
    assert rejected["status"] == "REJECTED"


def test_candidate_context_is_optional_and_fill_event_is_fail_open(
    monkeypatch, tmp_path
) -> None:
    spool = tmp_path / "events.jsonl"
    monkeypatch.setenv("PRISM_OBSERVABILITY_SPOOL", str(spool))
    monkeypatch.setenv("ENTRY_QUALITY_CAPTURE_ENABLED", "1")
    quality = build_entry_quality_context(
        scenario={}, current_price=100, trigger_type=None
    )
    candidate = emit_trading_context(
        "candidate.evaluated",
        market="US",
        ticker="AAA",
        decision_id="decision-1",
        entry_quality_context=quality,
    )
    fill = emit_fill_reconciliation(
        market="US",
        ticker="AAA",
        decision_id="decision-1",
        position_id="legacy:US:7",
        intent_id="intent-secret-value",
        result={
            "success": True,
            "intent_status": "SUBMITTED",
            "order_no": "external-order-id",
        },
    )

    assert candidate is not None and fill is not None
    assert candidate["attributes"]["entry_quality_context"]["status"] == "MISSING"
    assert fill["attributes"]["fill_provenance"]["status"] == "SUBMITTED_ONLY"
    assert fill["event_id"] == emit_fill_reconciliation(
        market="US",
        ticker="AAA",
        decision_id="decision-1",
        position_id="legacy:US:7",
        intent_id="intent-secret-value",
        result={"success": True, "intent_status": "SUBMITTED"},
    )["event_id"]
    raw = spool.read_text(encoding="utf-8")
    assert "external-order-id" not in raw
    assert "intent-secret-value" not in raw
    assert len([json.loads(line) for line in raw.splitlines()]) == 3

    monkeypatch.setenv("ENTRY_QUALITY_CAPTURE_ENABLED", "0")
    before = spool.read_text(encoding="utf-8")
    assert emit_fill_reconciliation(
        market="US",
        ticker="AAA",
        decision_id="decision-2",
        position_id=None,
        intent_id="intent-2",
        result={"success": True},
    ) is None
    assert spool.read_text(encoding="utf-8") == before
