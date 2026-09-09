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


SLOTS = {"US": {"max_slots": 10}}


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
    report = project_events([entry(pilot=True), entry("e2", "p2")], ledger, SLOTS)
    snapshots = snapshots_of(ledger)
    assert len(snapshots) == 2
    assert sorted(Decimal(s["remaining_allocation"]) for s in snapshots) == [Decimal(".5"), 1]
    assert report["counts"]["entries_missing_execution_profile"] == 2
    assert report["authoritative"] is False


def test_reverse_input_chronology_releases_cash_and_idempotent(tmp_path):
    ledger = StrategyLedger(tmp_path / "ledger.sqlite")
    events = [entry(profile="profile-A"), sell(), entry("e2", "p2", "profile-A", day=3)]
    project_events(list(reversed(events)), ledger, SLOTS)
    first = snapshots_of(ledger)
    assert len(first) == 2
    assert all(len(snapshot["campaigns"]) == 1 for snapshot in first)
    assert sum(Decimal(snapshot["realized_contribution"]) for snapshot in first) == Decimal(".1")
    assert all(snapshot["capacity_normalized_contribution"] is None for snapshot in first)
    project_events(events, ledger, SLOTS)
    assert snapshots_of(ledger) == first


def test_same_id_conflict_quarantines_before_sort_and_across_replays(tmp_path):
    ledger = StrategyLedger(tmp_path / "ledger.sqlite")
    original = entry()
    conflicting = entry(day=3)
    report = project_events([original, conflicting], ledger, SLOTS)
    assert report["counts"]["conflicting_source_ids"] == 1
    assert snapshots_of(ledger) == []
    project_events([original], ledger, SLOTS)
    before = snapshots_of(ledger)
    conflicting["irrelevant_source_field"] = "changed"
    report = project_events([conflicting], ledger, SLOTS)
    assert report["counts"]["rejected_events"] == 1
    assert snapshots_of(ledger) == before


def test_orphan_exit_retried_with_exact_entry_and_not_symbol(tmp_path):
    ledger = StrategyLedger(tmp_path / "ledger.sqlite")
    report = project_events([sell()], ledger, SLOTS)
    assert report["counts"]["deferred_or_rejected_events"] == 1
    project_events([entry("different", "different"), sell()], ledger, SLOTS)
    assert snapshots_of(ledger)[0]["campaigns"][0]["status"] == "OPEN"
    project_events([sell(), entry()], ledger, SLOTS)
    assert sorted(s["campaigns"][0]["status"] for s in snapshots_of(ledger)) == ["CLOSED", "OPEN"]


def test_selected_candidate_or_unrecorded_entry_not_projected(tmp_path):
    ledger = StrategyLedger(tmp_path / "ledger.sqlite")
    candidate = entry()
    candidate["event_type"] = "candidate.selected"
    unrecorded = entry("e2")
    unrecorded["attributes"]["execution_context"]["simulator_recorded"] = False
    project_events([candidate, unrecorded], ledger, SLOTS)
    assert snapshots_of(ledger) == []


def test_fill_without_profile_and_quantity_is_missing_not_confirmed(tmp_path):
    ledger = StrategyLedger(tmp_path / "ledger.sqlite")
    fill = sell()
    fill["event_type"] = "entry.fill_reconciled"
    fill["attributes"] = {"fill_provenance": {"status": "CONFIRMED"}}
    report = project_events([entry(), fill], ledger, SLOTS)
    assert report["counts"]["fill_evidence_missing"] == 1
    assert snapshots_of(ledger)[0]["executions"] == []


def test_explicit_rejection_overlay_does_not_cancel_strategy(tmp_path):
    ledger = StrategyLedger(tmp_path / "ledger.sqlite")
    fill = sell()
    fill["event_type"] = "entry.fill_reconciled"
    fill["intent_id"] = "intent-1"
    fill["attributes"] = {"fill_provenance": {"status": "REJECTED"},
                          "execution_context": {"execution_profile_ref": "profile-A"}}
    report = project_events([entry(), fill], ledger, SLOTS)
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
    report = project_events([entry(), fill], ledger, SLOTS)
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
    report = project_events([entry(), fill], ledger, SLOTS)
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
    report = project_events([entry(), fill], ledger, SLOTS)
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
    project_events([entry(), fill], ledger, SLOTS)
    overlay, = snapshots_of(ledger)[0]["executions"]
    assert overlay["status"] == "SUBMITTED"
    assert overlay["confirmed_quantity"] is None


def test_malformed_context_quarantined_and_ambiguous_fraction_rejected(tmp_path):
    ledger = StrategyLedger(tmp_path / "ledger.sqlite")
    malformed, fractional = entry(), entry("e2")
    malformed["attributes"] = ["bad"]
    fractional["attributes"]["policy_context"]["regime_entry_policy"]["position_fraction"] = .5
    report = project_events([malformed, fractional], ledger, SLOTS)
    assert report["counts"]["malformed_context_rows"] == 1
    assert report["counts"]["unsupported_legacy_fraction"] == 1
    assert snapshots_of(ledger) == []


def test_file_replay_separate_destination_and_malformed_coverage(tmp_path):
    source, profiles, destination = (tmp_path / n for n in ("source.jsonl", "slots.json", "ledger.sqlite"))
    source.write_text(json.dumps(entry()) + "\nnot json\n")
    profiles.write_text(json.dumps(SLOTS))
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


def test_missing_slot_profile_never_defaults_to_actual_account(tmp_path):
    ledger = StrategyLedger(tmp_path / "ledger.sqlite")
    report = project_events([entry()], ledger, {})
    assert report["counts"]["isolated_legacy_entries"] == 1
    snapshot, = snapshots_of(ledger)
    assert snapshot["cohort"] is None
    assert snapshot["validation_only"] is True
    assert snapshot["capacity_normalized_contribution"] is None


@pytest.mark.parametrize("profiles", [{}, {"US": {"max_slots": 7, "cohort": "baseline-v1", "mode": "SHADOW"}}])
def test_strategy_identity_and_legs_independent_of_accounts_and_fills(tmp_path, profiles):
    strategies = []
    for index, (profile, status) in enumerate([(None, "UNKNOWN"), ("account-A", "REJECTED"), ("account-B", "CONFIRMED")]):
        ledger = StrategyLedger(tmp_path / f"ledger-{index}.sqlite")
        fill = sell("fill", day=2)
        fill["event_type"] = "entry.fill_reconciled"
        fill["intent_id"] = "intent-1"
        fill["attributes"] = {"execution_context": {"execution_profile_ref": profile},
                              "fill_provenance": {"status": status, "evidence_source": "broker-query",
                                                  "confirmed_quantity": 2 if status == "CONFIRMED" else None,
                                                  "confirmed_price": 100 if status == "CONFIRMED" else None}}
        project_events([entry(profile=profile, pilot=True), fill, sell(day=3)], ledger, profiles)
        snapshots = snapshots_of(ledger)
        strategies.append([{k: v for k, v in s.items() if k != "executions"} for s in snapshots])
    assert strategies[0] == strategies[1] == strategies[2]


def test_only_explicit_cohort_mapping_can_aggregate_legacy_campaigns(tmp_path):
    ledger = StrategyLedger(tmp_path / "ledger.sqlite")
    profiles = {"US": {"max_slots": 7, "cohort": "documented-policy-v1", "mode": "VALIDATION"}}
    project_events([entry(profile="first-account"), sell(), entry("e2", "p2", "other-account", day=3)], ledger, profiles)
    snapshot, = snapshots_of(ledger)
    assert len(snapshot["campaigns"]) == 2
    assert snapshot["cohort"] == "documented-policy-v1"
    assert snapshot["contribution_denominator_slots"] == 7
    assert abs(Decimal(snapshot["capacity_normalized_contribution"]) - Decimal(".1") / 7) < Decimal("1e-25")


def test_old_capital_profile_rejected_not_reinterpreted(tmp_path):
    ledger = StrategyLedger(tmp_path / "ledger.sqlite")
    with pytest.raises(ValueError, match="monetary"):
        project_events([entry()], ledger, {"US": {"currency": "USD", "unit_budget": 100}})
    assert snapshots_of(ledger) == []


@pytest.mark.parametrize("mode,fraction,category", [
    ("unknown", 1, "unsupported_legacy_policy_mode"),
    (None, 1, "unsupported_legacy_policy_mode"),
    ("normal", True, "ambiguous_legacy_fraction"),
    ("normal", None, "ambiguous_legacy_fraction"),
    ("normal", "NaN", "ambiguous_legacy_fraction"),
    ("normal", "Infinity", "ambiguous_legacy_fraction"),
    ("normal", "1.00000000000000000001", "unsupported_legacy_fraction"),
    ("rebound_pilot", "0.50000000000000000001", "invalid_pilot_fraction"),
])
def test_ambiguous_legacy_policy_never_invents_a_leg(tmp_path, mode, fraction, category):
    ledger = StrategyLedger(tmp_path / "ledger.sqlite")
    event = entry()
    policy = event["attributes"]["policy_context"]["regime_entry_policy"]
    policy.update(mode=mode, position_fraction=fraction)
    report = project_events([event], ledger)
    assert report["counts"][category] == 1
    assert snapshots_of(ledger) == []


def test_cli_replays_without_money_or_profile(tmp_path, capsys):
    from tools.replay_strategy_ledger import main

    source = tmp_path / "source.jsonl"
    source.write_text(json.dumps(entry(pilot=True)) + "\n")
    assert main(["--input", str(source), "--ledger", str(tmp_path / "ledger.sqlite"), "--message-preview"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["schema_version"] == 2
    assert report["snapshots"][0]["validation_only"] is True
    assert "합산 금지" in report["message_previews"][0]


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
