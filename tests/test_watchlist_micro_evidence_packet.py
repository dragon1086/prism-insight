import copy
import json
from datetime import date, timedelta

import pandas as pd

from observability import micro_split
from tools.build_watchlist_micro_evidence_packet import (
    build_watchlist_micro_evidence_packet as build,
)


def fixture():
    common = {"watch_ref": "watch", "mode": "SHADOW", "policy_version": "oneil_watchlist_v1",
              "batch_ref": "batch", "status": "READY", "seed_event_id": "seed",
              "ready_event_id": "ready", "observation_price_ref": "price", "asof": "2026-09-15"}
    def event(id, kind, attrs):
        return {"event_id": id, "event_type": kind, "market": "US", "ticker": "AAPL",
                "timestamp": "2026-09-16T12:00:00Z", "attributes": attrs}
    rows = [event("seed", "watchlist.shadow_seeded", dict(common)),
            event("ready", "watchlist.shadow_ready", dict(common)),
            event("observed", "watchlist.shadow_evaluated", dict(common))]
    context = micro_split.build_initial_shadow_context(market="US", decision_id="decision",
               account_id="private-account", unit_amount=100, current_price=200, regime="moderate_bull")
    context.update(batch_ref="batch", trade_date="20260916", trigger_mode="morning")
    rows.append(event("micro", "micro_split.shadow_evaluated", context))
    rows.append(event("link", "watchlist_micro_split.shadow_linked", {
        "mode": "SHADOW", "link_schema_version": 1, "batch_ref": "batch", "watch_ref": "watch",
        "micro_event_id": "micro", "seed_event_id": "seed", "ready_event_id": "ready",
        "ready_observation_event_id": "observed", "observation_price_ref": "price",
        "source_decision_ref": context["decision_ref"], "execution_profile_ref": context["execution_profile_ref"],
        "watch_policy_version": "oneil_watchlist_v1", "micro_policy_version": context["policy_version"],
    }))
    rows.append(event("completed", "micro_split.shadow_batch_completed", {
        "batch_ref": "batch", "trade_date": "20260916", "trigger_mode": "morning",
        "status": "COMPLETED", "completion_scope": "analysis_and_tracking"}))
    return rows


def test_exact_link_zero_quantity_and_reordering():
    events = fixture()
    packet = build(events)
    assert packet == build(reversed(events))
    assert packet["counts"]["natural_pipeline_joint_links"] == 1
    assert packet["counts"]["zero_quantity_projections"] == 1
    assert packet["counts"]["completed_joint_sessions"] == 1
    assert packet["readiness"]["verdict"] == "CONTINUE_CAPTURE"
    assert packet["claims"]["confirmed_fills"] is False


def test_mismatch_or_orphan_rejected():
    for key, value in (("batch_ref", "other"), ("micro_event_id", "absent"),
                       ("execution_profile_ref", "other"), ("watch_policy_version", "other"),
                       ("source_decision_ref", "other"), ("ready_event_id", "seed")):
        events = fixture()
        events[4]["attributes"][key] = value
        assert build(events)["counts"]["natural_pipeline_joint_links"] == 0


def test_conflicting_duplicate_is_never_arbitrarily_chosen():
    events = fixture()
    other = copy.deepcopy(events[3])
    other["attributes"]["execution_profile_ref"] = "other"
    events.append(other)
    packet = build(events)
    assert packet == build(reversed(events))
    assert packet["counts"]["conflicting_event_ids"] == 1
    assert packet["counts"]["natural_pipeline_joint_links"] == 0


def test_temporal_future_and_superseded_rejected():
    events = fixture()
    events[2]["timestamp"] = "2026-09-17T00:00:00Z"
    assert build(events)["rejected_links"] == {"TEMPORAL_MISMATCH": 1}
    events = fixture()
    terminal = copy.deepcopy(events[2])
    terminal["event_id"] = "terminal"
    terminal["attributes"]["status"] = "EXPIRED"
    events.append(terminal)
    assert build(events)["rejected_links"] == {"SUPERSEDED_READY": 1}


def test_missing_input_and_projection_stay_unknown():
    assert build([], input_available=False)["input_status"] == "INPUT_UNAVAILABLE"
    events = fixture()
    events[3]["attributes"].update(projection_status="INPUT_UNAVAILABLE", projected_whole_share_quantity=None)
    packet = build(events)
    assert packet["counts"]["input_unavailable_projections"] == 1
    assert packet["counts"]["zero_quantity_projections"] == 0


def test_wrong_completion_session_not_counted():
    events = fixture()
    events[-1]["attributes"]["trade_date"] = "20260917"
    assert build(events)["counts"]["completed_joint_sessions"] == 0


def test_callback_failure_preserves_original_micro(monkeypatch):
    monkeypatch.setenv("MICRO_SPLIT_SHADOW_ENABLED", "true")
    emitted = []
    def emit(kind, **kwargs):
        result = {"event_type": kind, **kwargs}
        emitted.append(result)
        return result
    monkeypatch.setattr(micro_split, "emit_event", emit)
    def broken(event):
        raise RuntimeError("optional callback")
    monkeypatch.setattr(micro_split, "_emit_watchlist_link", broken)
    result = micro_split.emit_initial_shadow(market="US", ticker="AAPL", decision_id="decision",
               account_id="secret", unit_amount=100, current_price=200, regime="moderate_bull")
    assert emitted == [result]
    assert result["attributes"]["reason_code"] == "ENTRY_ELIGIBLE_SCOUT"


def test_batch_accessor_returns_copy(monkeypatch):
    monkeypatch.setenv("MICRO_SPLIT_SHADOW_ENABLED", "true")
    token = micro_split.begin_shadow_batch(market="US", trade_date="20260916", trigger_mode="morning")
    try:
        original = micro_split.get_shadow_batch_context()
        mutated = micro_split.get_shadow_batch_context()
        mutated["batch_ref"] = "different"
        assert micro_split.get_shadow_batch_context() == original
    finally:
        micro_split.end_shadow_batch(token)


def test_real_runtime_events_join_without_schema_or_id_reconstruction(monkeypatch, tmp_path):
    from observability import oneil_watchlist as watcher

    monkeypatch.setenv("MICRO_SPLIT_SHADOW_ENABLED", "true")
    spool = tmp_path / "events.jsonl"
    monkeypatch.setenv("PRISM_OBSERVABILITY_SPOOL", str(spool))
    monkeypatch.setattr(watcher, "enabled", lambda: True)
    monkeypatch.setattr(watcher, "STATE_PATH", tmp_path / "state.json")
    days = pd.bdate_range("2026-01-01", periods=66)
    stock = [{"date": d.date().isoformat(), "close": 70 + i * .5, "high": 70.1 + i * .5}
             for i, d in enumerate(days)]
    data = {"AAA": stock, "SPY": [{"date": r["date"], "close": 100., "high": 101.} for r in stock]}
    data["__expected_completed_date"] = stock[-1]["date"]
    day = (date.fromisoformat(stock[-1]["date"]) + timedelta(days=1)).strftime("%Y%m%d")
    monkeypatch.setattr(watcher, "_today", lambda: day)
    token = micro_split.begin_shadow_batch(market="US", trade_date=day, trigger_mode="morning")
    try:
        batch = micro_split.get_shadow_batch_context()["batch_ref"]
        observed = watcher.observe_batch({"Gap Up Momentum Top": pd.DataFrame(index=["AAA"])},
                  day, batch, collector=lambda tickers, trade_date: data)
        assert observed and observed[0]["status"] == "READY"
        micro = micro_split.emit_initial_shadow(market="US", ticker="AAA", decision_id="actual-decision",
                  account_id="secret", unit_amount=100, current_price=200, regime="moderate_bull")
        micro_split.complete_shadow_batch(tracking_success=True, selected_count=1, report_count=1, pdf_count=1)
        events = [json.loads(line) for line in spool.read_text().splitlines()]
        packet = build(events)
        assert packet["rejected_links"] == {}
        assert packet["counts"]["natural_pipeline_joint_links"] == 1
        assert packet["counts"]["completed_joint_sessions"] == 1
        assert sum(e["event_type"] == "micro_split.shadow_evaluated" for e in events) == 1
        assert next(e for e in events if e["event_type"] == "micro_split.shadow_evaluated") == micro
    finally:
        micro_split.end_shadow_batch(token)
