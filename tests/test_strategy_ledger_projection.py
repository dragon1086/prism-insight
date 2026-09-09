import json
import sqlite3
from decimal import Decimal

import pytest

from observability.strategy_ledger_projection import (
    project_events,
    replay_files,
    validate_destination,
)
from prism_core.strategy_ledger import StrategyLedger
from prism_core.strategy_ledger_execution import project_account_target


def snapshots_of(ledger):
    return [ledger.snapshot(book_id) for book_id in ledger.list_book_ids()]


CAPITAL = {"US": {"currency": "USD", "initial_capital": "1000", "unit_budget": "1000"}}


def entry(event_id="e1", position="p1", profile=None, pilot=False, day=1):
    return {"event_id": event_id, "event_type": "entry.executed", "position_id": position,
            "market": "US", "ticker": "AAA", "timestamp": f"2026-09-{day:02}T12:00:00Z",
            "attributes": {"execution_context": {"simulator_recorded": True,
                "entry_price": 100, "execution_profile_ref": profile},
                "policy_context": {"regime_entry_policy": {
                    "mode": "rebound_pilot" if pilot else "normal",
                    "position_fraction": .5 if pilot else 1}}}}


def sell(event_id="x1", position="p1", day=2):
    return {"event_id": event_id, "event_type": "exit.executed", "position_id": position,
            "market": "US", "ticker": "AAA", "timestamp": f"2026-09-{day:02}T12:00:00Z",
            "attributes": {"decision_context": {"sell_price": 110}}}


def test_independent_legacy_books_and_pilot_capital(tmp_path):
    ledger = StrategyLedger(tmp_path / "ledger.sqlite")
    report = project_events([entry(pilot=True), entry("e2", "p2")], ledger, CAPITAL)
    snapshots = snapshots_of(ledger)
    assert len(snapshots) == 2
    assert sorted(Decimal(s["free_cash"]) for s in snapshots) == [0, 500]
    assert report["counts"]["entries_missing_execution_profile"] == 2
    assert report["authoritative"] is False


def test_reverse_input_chronology_releases_cash_and_idempotent(tmp_path):
    ledger = StrategyLedger(tmp_path / "ledger.sqlite")
    events = [entry(profile="profile-A"), sell(), entry("e2", "p2", "profile-A", day=3)]
    project_events(list(reversed(events)), ledger, CAPITAL)
    first = snapshots_of(ledger)
    assert len(first) == 1
    assert len(first[0]["campaigns"]) == 2
    assert Decimal(first[0]["free_cash"]) == 100
    project_events(events, ledger, CAPITAL)
    assert snapshots_of(ledger) == first


def test_same_id_conflict_quarantines_before_sort_and_across_replays(tmp_path):
    ledger = StrategyLedger(tmp_path / "ledger.sqlite")
    original = entry()
    conflicting = entry(day=3)
    report = project_events([original, conflicting], ledger, CAPITAL)
    assert report["counts"]["conflicting_source_ids"] == 1
    assert snapshots_of(ledger) == []
    project_events([original], ledger, CAPITAL)
    before = snapshots_of(ledger)
    conflicting["irrelevant_source_field"] = "changed"
    report = project_events([conflicting], ledger, CAPITAL)
    assert report["counts"]["rejected_events"] == 1
    assert snapshots_of(ledger) == before


def test_orphan_exit_retried_with_exact_entry_and_not_symbol(tmp_path):
    ledger = StrategyLedger(tmp_path / "ledger.sqlite")
    report = project_events([sell()], ledger, CAPITAL)
    assert report["counts"]["deferred_or_rejected_events"] == 1
    project_events([entry("different", "different"), sell()], ledger, CAPITAL)
    assert snapshots_of(ledger)[0]["campaigns"][0]["status"] == "OPEN"
    project_events([sell(), entry()], ledger, CAPITAL)
    assert sorted(s["campaigns"][0]["status"] for s in snapshots_of(ledger)) == ["CLOSED", "OPEN"]


def test_selected_candidate_or_unrecorded_entry_not_projected(tmp_path):
    ledger = StrategyLedger(tmp_path / "ledger.sqlite")
    candidate = entry()
    candidate["event_type"] = "candidate.selected"
    unrecorded = entry("e2")
    unrecorded["attributes"]["execution_context"]["simulator_recorded"] = False
    project_events([candidate, unrecorded], ledger, CAPITAL)
    assert snapshots_of(ledger) == []


def test_fill_without_profile_and_quantity_is_missing_not_confirmed(tmp_path):
    ledger = StrategyLedger(tmp_path / "ledger.sqlite")
    fill = sell()
    fill["event_type"] = "entry.fill_reconciled"
    fill["attributes"] = {"fill_provenance": {"status": "CONFIRMED"}}
    report = project_events([entry(), fill], ledger, CAPITAL)
    assert report["counts"]["fill_evidence_missing"] == 1
    assert snapshots_of(ledger)[0]["executions"] == []


def test_explicit_rejection_overlay_does_not_cancel_strategy(tmp_path):
    ledger = StrategyLedger(tmp_path / "ledger.sqlite")
    fill = sell()
    fill["event_type"] = "entry.fill_reconciled"
    fill["intent_id"] = "intent-1"
    fill["attributes"] = {"fill_provenance": {"status": "REJECTED"},
                          "execution_context": {"execution_profile_ref": "profile-A"}}
    report = project_events([entry(), fill], ledger, CAPITAL)
    assert report["counts"]["execution_events_accepted"] == 1
    snapshot = snapshots_of(ledger)[0]
    assert snapshot["campaigns"][0]["status"] == "OPEN"
    assert snapshot["executions"][0]["status"] == "REJECTED"


def test_real_capture_shape_keeps_profile_unknown_rejection(tmp_path):
    ledger = StrategyLedger(tmp_path / "ledger.sqlite")
    fill = sell()
    fill["event_type"] = "entry.fill_reconciled"
    fill["attributes"] = {"source": "us_order_intent_result", "intent_ref": "hashed-intent",
                          "fill_provenance": {"schema_version": 1, "status": "REJECTED",
                          "reason_code": "ORDER_NOT_ACCEPTED", "order_status": "FAILED",
                          "submission_scope": "SINGLE_ACCOUNT", "confirmed_fill_price": None,
                          "confirmed_fill_at": None}}
    report = project_events([entry(), fill], ledger, CAPITAL)
    overlay, = report["unresolved_execution_overlays"]
    assert overlay["intent_ref"] == "hashed-intent"
    assert overlay["profile_status"] == "UNKNOWN"
    assert overlay["status"] == "REJECTED"
    assert overlay["confirmed_quantity"] is None
    assert overlay["ledger_applied"] is False
    assert snapshots_of(ledger)[0]["campaigns"][0]["status"] == "OPEN"


def test_actual_fill_fields_and_top_level_attribute_profile(tmp_path):
    ledger = StrategyLedger(tmp_path / "ledger.sqlite")
    fill = sell()
    fill["event_type"] = "entry.fill_reconciled"
    fill["attributes"] = {"source": "broker_fill_reconciliation", "intent_ref": "hashed-intent",
                          "execution_profile_ref": "profile-A", "fill_provenance": {
                          "status": "CONFIRMED", "confirmed_quantity": 2,
                          "confirmed_fill_price": 100, "confirmed_fill_at": fill["timestamp"]}}
    report = project_events([entry(), fill], ledger, CAPITAL)
    assert report["counts"]["execution_events_accepted"] == 1
    overlay, = snapshots_of(ledger)[0]["executions"]
    assert overlay["status"] == "FILLED"
    assert overlay["confirmed_quantity"] == "2"
    assert overlay["confirmed_price"] == "100"
    assert overlay["intent_ref"] == "hashed-intent"


def test_confirmed_without_explicit_quantity_or_evidence_stays_unknown(tmp_path):
    ledger = StrategyLedger(tmp_path / "ledger.sqlite")
    fill = sell()
    fill["event_type"] = "entry.fill_reconciled"
    fill["attributes"] = {"intent_ref": "hashed-intent", "execution_profile_ref": "profile-A",
                          "fill_provenance": {"status": "CONFIRMED", "confirmed_fill_price": 100}}
    report = project_events([entry(), fill], ledger, CAPITAL)
    overlay, = report["unresolved_execution_overlays"]
    assert overlay["status"] == "UNKNOWN"
    assert overlay["source_status"] == "CONFIRMED"
    assert snapshots_of(ledger)[0]["executions"] == []


def test_submitted_only_maps_to_submission_not_fill(tmp_path):
    ledger = StrategyLedger(tmp_path / "ledger.sqlite")
    fill = sell()
    fill["event_type"] = "entry.fill_reconciled"
    fill["attributes"] = {"intent_ref": "hashed-intent", "execution_profile_ref": "profile-A",
                          "fill_provenance": {"status": "SUBMITTED_ONLY", "confirmed_fill_price": None}}
    project_events([entry(), fill], ledger, CAPITAL)
    overlay, = snapshots_of(ledger)[0]["executions"]
    assert overlay["status"] == "SUBMITTED"
    assert overlay["confirmed_quantity"] is None


def test_malformed_context_quarantined_and_ambiguous_fraction_rejected(tmp_path):
    ledger = StrategyLedger(tmp_path / "ledger.sqlite")
    malformed, fractional = entry(), entry("e2")
    malformed["attributes"] = ["bad"]
    fractional["attributes"]["policy_context"]["regime_entry_policy"]["position_fraction"] = .5
    report = project_events([malformed, fractional], ledger, CAPITAL)
    assert report["counts"]["malformed_context_rows"] == 1
    assert report["counts"]["unsupported_legacy_fraction"] == 1
    assert snapshots_of(ledger) == []


def test_file_replay_separate_destination_and_malformed_coverage(tmp_path):
    source, profiles, destination = (tmp_path / n for n in ("source.jsonl", "capital.json", "ledger.sqlite"))
    source.write_text(json.dumps(entry()) + "\nnot json\n")
    profiles.write_text(json.dumps(CAPITAL))
    report = replay_files([source], destination, profiles)
    assert report["counts"]["malformed_json_rows"] == 1
    assert len(report["snapshots"]) == 1
    replay_files([source], destination, profiles)
    for forbidden in (source, profiles, tmp_path / "stock_tracking_db.sqlite"):
        with pytest.raises(ValueError):
            replay_files([source], forbidden, profiles)
    other = tmp_path / "production.sqlite"
    with sqlite3.connect(other) as db:
        db.execute("CREATE TABLE holdings (ticker TEXT)")
    with pytest.raises(ValueError):
        validate_destination(other)


def test_missing_capital_never_defaults_to_actual_account(tmp_path):
    ledger = StrategyLedger(tmp_path / "ledger.sqlite")
    report = project_events([entry()], ledger, {})
    assert report["counts"]["missing_capital_profile"] == 1
    assert snapshots_of(ledger) == []


def plan(**updates):
    args = {"execution_profile_ref": "profile-A", "account_unit_budget": 1000000,
            "target_pct": 50, "limit_price": 300000, "confirmed_buy_notional": 0,
            "reserved_buy_notional": 0, "previously_submitted_target_pct": 0}
    args.update(updates)
    return project_account_target(**args)


def test_account_cap_rounding_and_unknown_reservation():
    result = plan()
    assert result["quantity"] == 1
    assert Decimal(result["unused_budget"]) == 200000
    assert result["no_order"] is True
    assert plan(unknown_execution=True)["quantity"] == 0
    assert plan(reserved_buy_notional=None)["quantity"] == 0
    assert plan(execution_profile_ref=None)["quantity"] == 0
    assert plan(previously_submitted_target_pct=None)["reason"] == "PREVIOUS_TARGET_REQUIRED"


def test_cumulative_pending_and_same_target_lower_price_no_add():
    assert plan(confirmed_buy_notional=300000, reserved_buy_notional=200000)["quantity"] == 0
    result = plan(confirmed_buy_notional=300000, limit_price=100000,
                  previously_submitted_target_pct=50)
    assert result["reason"] == "TARGET_ALREADY_SUBMITTED"
    assert result["quantity"] == 0
    assert plan(target_pct=100, confirmed_buy_notional=300000,
                reserved_buy_notional=300000, previously_submitted_target_pct=50)["quantity"] == 1


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -1, True])
def test_invalid_account_amounts_fail(value):
    with pytest.raises((ValueError, TypeError)):
        plan(account_unit_budget=value)
