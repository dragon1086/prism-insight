from __future__ import annotations

import json
from datetime import datetime, timezone

from observability.events import build_event, emit_event


def test_build_event_has_correlation_and_provenance(monkeypatch):
    monkeypatch.setenv("PRISM_ENV", "test")
    monkeypatch.setenv("TRIGGER_PERFORMANCE_FEEDBACK", "shadow")
    event = build_event(
        "trade.decision",
        service="prism-kr-trading",
        market="kr",
        ticker="005930",
        trace_id="decision:005930",
        decision_id="decision-1",
        attributes={"score": 7},
        event_time=datetime(2026, 8, 26, 3, 0, tzinfo=timezone.utc),
    )

    assert event["schema_version"] == 1
    assert event["timestamp"] == "2026-08-26T03:00:00Z"
    assert event["environment"] == "test"
    assert event["market"] == "KR"
    assert event["ticker"] == "005930"
    assert event["decision_id"] == "decision-1"
    assert len(event["trace_id"]) == 32
    assert len(event["span_id"]) == 16
    assert event["config"] == {"TRIGGER_PERFORMANCE_FEEDBACK": "shadow"}
    assert len(event["config_hash"]) == 16


def test_sensitive_attributes_are_redacted():
    event = build_event(
        "deployment.applied",
        service="prism-operations",
        attributes={
            "token": "secret-token",
            "nested": {"api_key": "secret-key", "safe": "visible"},
            "account_key": "123-456",
        },
    )

    assert event["attributes"]["token"] == "[REDACTED]"
    assert event["attributes"]["nested"]["api_key"] == "[REDACTED]"
    assert event["attributes"]["nested"]["safe"] == "visible"
    assert event["attributes"]["account_key"] == "[REDACTED]"


def test_emit_event_appends_json_lines(tmp_path):
    spool = tmp_path / "events.jsonl"
    first = emit_event("pipeline.run", service="prism-test", spool_path=spool)
    second = emit_event("pipeline.done", service="prism-test", spool_path=spool)

    assert first is not None
    assert second is not None
    rows = [json.loads(line) for line in spool.read_text().splitlines()]
    assert [row["event_type"] for row in rows] == ["pipeline.run", "pipeline.done"]
    assert len({row["event_id"] for row in rows}) == 2


def test_emit_event_is_fail_open(tmp_path):
    parent_file = tmp_path / "not-a-directory"
    parent_file.write_text("occupied")
    result = emit_event(
        "pipeline.run",
        service="prism-test",
        spool_path=parent_file / "events.jsonl",
    )
    assert result is None
