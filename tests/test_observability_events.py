from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime, timezone

import pytest

from observability.events import build_event, emit_event, suppress_events


def test_pytest_default_spool_is_discarded(tmp_path):
    assert os.environ['PRISM_OBSERVABILITY_SPOOL'] == os.devnull
    inherited = tmp_path / 'production-spool.jsonl'
    env = {**os.environ, 'PRISM_OBSERVABILITY_SPOOL': str(inherited)}
    code = ('import conftest, os; from observability.events import emit_event; '
            'emit_event("test.collection", service="fixture"); '
            'print(os.environ["PRISM_OBSERVABILITY_SPOOL"])')
    result = subprocess.run([sys.executable, '-c', code], env=env, capture_output=True, text=True, check=True)
    assert result.stdout.strip() == os.devnull
    assert not inherited.exists()


def test_suppression_is_scoped_and_restored_on_error(tmp_path):
    spool = tmp_path / 'events.jsonl'
    with pytest.raises(RuntimeError):
        with suppress_events():
            assert emit_event('test.replay', service='fixture', spool_path=spool) is None
            raise RuntimeError('failure')
    assert not spool.exists()
    assert emit_event('test.live', service='demo', spool_path=spool) is not None


def test_build_event_has_correlation_and_provenance(monkeypatch):
    monkeypatch.delenv("SCENARIO_SHADOW_CAPTURE_ENABLED", raising=False)
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


def test_capture_flag_changes_provenance_without_exporting_registry_path(monkeypatch):
    monkeypatch.setenv("SCENARIO_SHADOW_CAPTURE_ENABLED", "1")
    monkeypatch.setenv("SCENARIO_SHADOW_CAPTURE_DB", "/private/canary-capture.sqlite")
    enabled = build_event("test", service="test")
    monkeypatch.setenv("SCENARIO_SHADOW_CAPTURE_ENABLED", "0")
    disabled = build_event("test", service="test")
    assert enabled["config"]["SCENARIO_SHADOW_CAPTURE_ENABLED"] == "1"
    assert enabled["config_hash"] != disabled["config_hash"]
    assert "canary" not in json.dumps(enabled)


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
