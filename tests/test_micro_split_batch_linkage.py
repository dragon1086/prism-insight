from __future__ import annotations

import ast
import asyncio
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from observability import micro_split
from tools.build_micro_split_evidence_packet import build_micro_split_evidence_packet


def test_completion_requires_explicit_success_and_links_only_its_decisions(monkeypatch, tmp_path):
    monkeypatch.setenv("MICRO_SPLIT_SHADOW_ENABLED", "true")
    from observability.events import emit_event

    captured = []

    def record(kind, **kwargs):
        event = emit_event(kind, spool_path=tmp_path / "events.jsonl", **kwargs)
        captured.append(event)
        return event

    monkeypatch.setattr(micro_split, "emit_event", record)
    token = micro_split.begin_shadow_batch(market="US", trade_date="20260911", trigger_mode="afternoon")
    try:
        micro_split.emit_initial_shadow(market="US", ticker="HPE", decision_id="one", account_id="private", unit_amount=1000, current_price=100, regime="moderate_bull")
        micro_split.complete_shadow_batch(tracking_success=False, selected_count=1, report_count=1, pdf_count=1)
        assert len(captured) == 1
        micro_split.complete_shadow_batch(tracking_success=True, selected_count=2, report_count=1, pdf_count=1)
        assert len(captured) == 1
        micro_split.complete_shadow_batch(tracking_success=True, selected_count=1, report_count=1, pdf_count=1)
    finally:
        micro_split.end_shadow_batch(token)
    micro_split.emit_initial_shadow(market="US", ticker="HPE", decision_id="legacy", account_id="private", unit_amount=1000, current_price=100, regime="moderate_bull")
    packet = build_micro_split_evidence_packet(captured)
    observed = packet["observed_shadow"]
    assert observed["completed_batch_count"] == 1
    assert observed["completed_session_count"] == 1
    assert observed["completed_batch_decision_count"] == 1
    assert observed["unlinked_decision_count"] == 1
    assert captured[0]["attributes"]["batch_ref"] != "[REDACTED]"
    assert "batch_ref" not in captured[-1]["attributes"]


def test_no_completion_inferred_from_legacy_decision():
    packet = build_micro_split_evidence_packet([{
        "event_type": "micro_split.shadow_evaluated", "event_id": "old", "market": "US",
        "timestamp": "2026-09-11T00:00:00Z", "decision_id": "old", "attributes": {},
    }])
    assert packet["observed_shadow"]["completed_session_count"] == 0
    assert "COMPLETED_SESSIONS_LT_20" in {reason["code"] for reason in packet["readiness"]["reasons"]}


@pytest.mark.parametrize("date,mode", [("20260910", "morning"), ("20260911", "afternoon"), ("20269999", "afternoon")])
def test_completion_requires_matching_valid_metadata(date, mode):
    events = [{
        "event_type": "micro_split.shadow_batch_completed", "event_id": "complete", "market": "US",
        "timestamp": "2026-09-11T01:00:00Z", "attributes": {
            "batch_ref": "batch", "trade_date": date, "trigger_mode": mode,
            "status": "COMPLETED", "completion_scope": "analysis_and_tracking", "mode": "SHADOW",
        },
    }, {
        "event_type": "micro_split.shadow_evaluated", "event_id": "decision", "market": "US",
        "timestamp": "2026-09-11T00:00:00Z", "decision_id": "one", "attributes": {
            "batch_ref": "batch", "trade_date": "20260910", "trigger_mode": "afternoon",
        },
    }]
    observed = build_micro_split_evidence_packet(events)["observed_shadow"]
    assert observed["completed_batch_decision_count"] == 0
    assert observed["unlinked_decision_count"] == 1
    if date == "20269999":
        assert observed["completed_session_count"] == 0


def test_capture_context_fault_is_fail_open(monkeypatch):
    monkeypatch.setenv("MICRO_SPLIT_SHADOW_ENABLED", "true")

    def fail():
        raise RuntimeError("capture unavailable")

    monkeypatch.setattr(micro_split.uuid, "uuid4", fail)
    assert micro_split.begin_shadow_batch(market="US", trade_date="20260911", trigger_mode="morning") is None
    micro_split.end_shadow_batch(None)
    micro_split.end_shadow_batch("invalid token")


def test_repeated_batch_does_not_inflate_unique_session_count():
    events = [{
        "event_type": "micro_split.shadow_batch_completed", "event_id": ref,
        "market": "US", "timestamp": "2026-09-11T00:00:00Z",
        "attributes": {
            "batch_ref": ref, "trade_date": "20260910", "trigger_mode": "afternoon",
            "status": "COMPLETED", "completion_scope": "analysis_and_tracking", "mode": "SHADOW",
        },
    } for ref in ("run-one", "run-two")]
    observed = build_micro_split_evidence_packet(events + [events[0]])["observed_shadow"]
    assert observed["completed_batch_count"] == 2
    assert observed["completed_session_count"] == 1


def test_disabled_capture_creates_no_completion(monkeypatch):
    monkeypatch.delenv("MICRO_SPLIT_SHADOW_ENABLED", raising=False)
    monkeypatch.setattr(micro_split, "emit_event", lambda *a, **kw: pytest.fail("disabled capture wrote event"))
    token = micro_split.begin_shadow_batch(market="US", trade_date="20260911", trigger_mode="morning")
    try:
        assert micro_split.complete_shadow_batch(tracking_success=True, selected_count=1, report_count=1, pdf_count=1) is None
    finally:
        micro_split.end_shadow_batch(token)


@pytest.mark.parametrize("tracking_ok,reports,pdfs,raises,expected", [
    (True, ["r"], ["p"], False, 1),
    (False, ["r"], ["p"], False, 0),
    (True, [], [], False, 0),
    (True, ["r"], [], False, 0),
    (True, ["r"], ["p"], True, 0),
])
def test_real_pipeline_completion_boundary(monkeypatch, tracking_ok, reports, pdfs, raises, expected):
    """Execute the actual pipeline method with I/O replaced, not a copied algorithm."""
    monkeypatch.setenv("MICRO_SPLIT_SHADOW_ENABLED", "true")
    captured = []
    monkeypatch.setattr(micro_split, "emit_event", lambda kind, **kw: captured.append((kind, kw)))
    source = Path(__file__).resolve().parents[1] / "prism-us/us_stock_analysis_orchestrator.py"
    tree = ast.parse(source.read_text())
    method = next(node for node in ast.walk(tree) if isinstance(node, ast.AsyncFunctionDef) and node.name == "run_full_pipeline")
    ns = {
        "logger": MagicMock(), "resolve_us_trade_date": lambda _: "20260911",
        "PRISM_US_DIR": source.parent, "os": SimpleNamespace(path=SimpleNamespace(exists=lambda _: False)),
        "asyncio": asyncio, "nullcontext": nullcontext, "COLLECTING": "COLLECTING",
        "_import_main_archive_ingest": lambda: SimpleNamespace(ingest_reports_async=AsyncMock()),
    }
    for name in ("publish_batch_campaign_best_effort", "publish_batch_reports_best_effort", "publish_batch_tracking_story_best_effort"):
        ns[name] = AsyncMock()
    exec(compile(ast.Module(body=[method], type_ignores=[]), str(source), "exec"), ns)
    tracker = SimpleNamespace(run=AsyncMock(return_value=tracking_ok), last_batch_messages=[])
    monkeypatch.setitem(__import__("sys").modules, "us_stock_tracking_agent", SimpleNamespace(
        USStockTrackingAgent=lambda **_: tracker, _us_codex_runtime_enabled=lambda: True, app=None,
    ))
    monkeypatch.setitem(__import__("sys").modules, "telegram_config", SimpleNamespace(
        is_openai_quota_error=lambda _: False, send_openai_quota_alert=AsyncMock(),
    ))
    instance = SimpleNamespace(
        run_macro_intelligence=AsyncMock(return_value={}),
        run_trigger_batch=AsyncMock(return_value=["HPE"]),
        generate_reports=AsyncMock(return_value=reports, side_effect=RuntimeError("test") if raises else None),
        convert_to_pdf=AsyncMock(return_value=pdfs),
        generate_telegram_messages=AsyncMock(return_value=[]),
        _campaign_messages={}, _broadcast_tasks=[],
        telegram_config=SimpleNamespace(use_telegram=False, log_status=lambda: None),
    )
    asyncio.run(ns["run_full_pipeline"](instance, "afternoon"))
    assert len(captured) == expected
    assert micro_split._BATCH.get() is None
