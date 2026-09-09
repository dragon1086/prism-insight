from concurrent.futures import ThreadPoolExecutor
import json
import sqlite3

import pytest

from prism_core.strategy_ledger import LedgerError, StrategyLedger
from prism_core.strategy_ledger_outbox import (
    StrategyLedgerOutbox, project_account_from_evidence,
    reconcile_account_evidence, record_account_evidence,
)

T0 = "2026-09-10T00:00:00Z"
T1 = "2026-09-10T01:00:00Z"
T2 = "2026-09-10T02:00:00Z"
T3 = "2026-09-10T03:00:00Z"


def ledger_at(tmp_path):
    ledger = StrategyLedger(tmp_path / "outbox.sqlite")
    ledger.create_book("book", "US", mode="SHADOW")
    return ledger


def buy(ledger):
    return ledger.apply_target("buy", "book", "c", "TEST", 50, 100, T0)


def test_notice_is_atomic_frozen_and_not_emitted_for_marks(tmp_path):
    ledger = ledger_at(tmp_path)
    buy(ledger)
    outbox = StrategyLedgerOutbox(ledger)
    notice, = outbox.pending()
    assert "100.00 USD" in notice["text"]
    assert "원천 관측 시각: 2026-09-10T00:00:00+00:00" in notice["text"]
    assert notice["delivery_scope"] == "TEST_CHAT_ONLY"
    ledger.mark("mark", "c", 200, T1)
    assert outbox.pending() == [notice]
    assert not buy(ledger)["event_applied"]
    assert outbox.pending() == [notice]


def test_notice_failure_rolls_back_strategy_leg(tmp_path, monkeypatch):
    ledger = ledger_at(tmp_path)
    before = ledger.snapshot("book")

    def fail(*args, **kwargs):
        raise RuntimeError("notice persistence failure")

    monkeypatch.setattr(ledger, "_notice", fail)
    with pytest.raises(RuntimeError):
        buy(ledger)
    assert ledger.snapshot("book") == before
    assert StrategyLedgerOutbox(ledger).pending() == []


def test_claim_crash_is_unknown_and_never_automatically_reauthorized(tmp_path):
    ledger = ledger_at(tmp_path)
    buy(ledger)
    outbox = StrategyLedgerOutbox(ledger)
    notice, = outbox.pending()
    claimed = outbox.claim(notice["notice_id"], "worker-1", T1)
    assert claimed["send_authorized"] is True
    assert claimed["status"] == "UNKNOWN"
    reopened = StrategyLedgerOutbox(StrategyLedger(ledger.path))
    assert reopened.pending() == []
    assert len(reopened.list_notices(status="UNKNOWN")) == 1
    assert reopened.claim(notice["notice_id"], "worker-1", T1)["send_authorized"] is False
    assert reopened.claim(notice["notice_id"], "worker-2", T1)["send_authorized"] is False


def test_parallel_claim_and_idempotent_ack(tmp_path):
    ledger = ledger_at(tmp_path)
    buy(ledger)
    notice, = StrategyLedgerOutbox(ledger).pending()

    def claim(worker):
        return StrategyLedgerOutbox(StrategyLedger(ledger.path)).claim(notice["notice_id"], worker, T1)

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(claim, ["one", "two"]))
    assert sum(result["send_authorized"] for result in results) == 1
    winner = next(result for result in results if result["send_authorized"])
    outbox = StrategyLedgerOutbox(ledger)
    first = outbox.ack(notice["notice_id"], winner["claim_id"], "telegram-receipt", T1)
    assert first["status"] == "SENT"
    assert outbox.ack(notice["notice_id"], winner["claim_id"], "telegram-receipt", T1) == first


def record(ledger, event, *, profile="account-A", intent="buy-intent", side="BUY", status="PARTIAL", at=T1,
           quantity=2, notional=200, reserved=300, target=50):
    return record_account_evidence(ledger, event, "c", execution_profile_ref=profile,
                                  intent_ref=intent, side=side, status=status, observed_at=at,
                                  evidence_source="broker-cumulative-snapshot",
                                  cumulative_fill_quantity=quantity, cumulative_fill_notional=notional,
                                  reserved_buy_notional=reserved, submitted_target_pct=target)


def reconcile(ledger, profile="account-A", coverage=True):
    return reconcile_account_evidence(ledger.snapshot("book")["executions"], campaign_id="c",
                                      execution_profile_ref=profile, coverage_complete=coverage)


def strategy(snapshot):
    return {key: value for key, value in snapshot.items() if key not in {"executions", "event_applied"}}


def test_cumulative_fill_partial_cancel_late_fill_after_strategy_exit(tmp_path):
    ledger = ledger_at(tmp_path)
    before = strategy(buy(ledger))
    record(ledger, "partial")
    record(ledger, "repeat-poll", at="2026-09-10T01:30:00Z")
    assert strategy(ledger.snapshot("book")) == before
    assert len(StrategyLedgerOutbox(ledger).pending()) == 2  # BUY + changed execution only
    record(ledger, "cancel", status="CANCELLED", at=T2, reserved=0)
    evidence = reconcile(ledger)
    assert evidence["residual_quantity"] == "2"
    assert evidence["confirmed_buy_notional"] == "200"  # not 200 + 200 + 200
    assert evidence["reserved_buy_notional"] == "0"
    notice = StrategyLedgerOutbox(ledger).pending()[-1]
    assert "누적 확인 체결: 2주" in notice["text"] and "취소 전에 확인된 체결은 유지" in notice["text"]
    closed = strategy(ledger.sell("strategy-exit", "c", 110, T2))
    record(ledger, "late-fill", status="FILLED", at=T3, quantity=3, notional=300, reserved=0)
    assert strategy(ledger.snapshot("book")) == closed
    assert reconcile(ledger)["residual_quantity"] == "3"
    record(ledger, "account-reduction", intent="sell-intent", side="SELL", status="FILLED", at=T3,
           quantity=1, notional=110, reserved=0, target=0)
    evidence = reconcile(ledger)
    assert evidence["residual_quantity"] == "2"
    assert strategy(ledger.snapshot("book")) == closed
    plan = project_account_from_evidence(ledger.snapshot("book"), "c", account_evidence=evidence,
                                         account_unit_budget=1000, limit_price=110)
    assert plan["reason"] == "STRATEGY_NOT_ADDABLE"


def test_multiple_accounts_and_account_budget_do_not_change_strategy(tmp_path):
    ledger = ledger_at(tmp_path)
    before = strategy(buy(ledger))
    record(ledger, "account-a-fill", reserved=0, status="FILLED")
    record(ledger, "account-b-reject", profile="account-B", status="REJECTED", quantity=0, notional=0, reserved=0)
    assert reconcile(ledger)["residual_quantity"] == "2"
    assert reconcile(ledger, "account-B")["residual_quantity"] == "0"
    assert strategy(ledger.snapshot("book")) == before
    # Explicit caller-provided zero-order coverage, never derived from position.
    empty_evidence = {"campaign_id": "c", "execution_profile_ref": "account-C", "coverage_complete": True,
                      "unknown_execution": False, "confirmed_buy_notional": 0,
                      "reserved_buy_notional": 0, "previously_submitted_target_pct": 0}
    for budget in (0, 1000):
        result = project_account_from_evidence(ledger.snapshot("book"), "c", account_evidence=empty_evidence,
                                               account_unit_budget=budget, limit_price=100)
        assert result["quantity"] == (0 if budget == 0 else 5)
        assert result["no_order"] is True
        assert strategy(ledger.snapshot("book")) == before
    assert project_account_from_evidence(ledger.snapshot("book"), "c", account_evidence=empty_evidence,
                                          account_unit_budget=None, limit_price=100)["reason"] == "MISSING_ACCOUNT_BUDGET"
    assert strategy(ledger.snapshot("book")) == before


@pytest.mark.parametrize("missing", ["confirmed_buy_notional", "reserved_buy_notional", "previously_submitted_target_pct", "unknown_execution"])
def test_account_projection_requires_every_evidence_field(tmp_path, missing):
    ledger = ledger_at(tmp_path)
    buy(ledger)
    record(ledger, "fill", reserved=0)
    evidence = reconcile(ledger)
    evidence.pop(missing)
    result = project_account_from_evidence(ledger.snapshot("book"), "c", account_evidence=evidence,
                                           account_unit_budget=1000, limit_price=100)
    assert result["quantity"] == 0 and result["reason"] == "INCOMPLETE_ACCOUNT_EVIDENCE"


def test_unknown_reservation_and_missing_cancel_fill_block_not_erase_strategy(tmp_path):
    ledger = ledger_at(tmp_path)
    before = strategy(buy(ledger))
    assert reconcile(ledger)["unknown_execution"] is True
    record(ledger, "partial")
    record(ledger, "cancel-missing", status="CANCELLED", at=T2, quantity=None, notional=None, reserved=None)
    evidence = reconcile(ledger)
    assert evidence["known_buy_quantity"] == "2"
    assert evidence["residual_quantity"] is None and evidence["unknown_execution"] is True
    result = project_account_from_evidence(ledger.snapshot("book"), "c", account_evidence=evidence,
                                           account_unit_budget=1000, limit_price=100)
    assert result["reason"] == "UNKNOWN_EXECUTION_RESERVATION"
    assert strategy(ledger.snapshot("book")) == before


def test_legacy_fill_without_cumulative_contract_cannot_size_account(tmp_path):
    ledger = ledger_at(tmp_path)
    buy(ledger)
    ledger.observe_execution("legacy", "c", "account-A", "FILLED", T1, intent_ref="i",
                             confirmed_quantity=2, confirmed_price=100, evidence_source="broker")
    assert reconcile(ledger)["unknown_execution"] is True


def test_incomplete_fill_status_is_unknown_not_confirmed(tmp_path):
    ledger = ledger_at(tmp_path)
    buy(ledger)
    record(ledger, "incomplete", status="FILLED", quantity=None, notional=None, reserved=None)
    evidence = ledger.snapshot("book")["executions"][0]
    assert evidence["status"] == "UNKNOWN" and evidence["source_status"] == "FILLED"
    assert evidence["confirmed_quantity"] is None
    assert reconcile(ledger)["unknown_execution"] is True


@pytest.mark.parametrize("kind", ["counter_decrease", "same_clock_conflict", "missing_coverage", "submitted_target_decrease"])
def test_ambiguous_cumulative_history_blocks_projection(tmp_path, kind):
    ledger = ledger_at(tmp_path)
    buy(ledger)
    record(ledger, "first")
    record(ledger, "second", at=T1 if kind == "same_clock_conflict" else T2,
           quantity=1 if kind == "counter_decrease" else 3, notional=100 if kind == "counter_decrease" else 300,
           target=10 if kind == "submitted_target_decrease" else 50)
    assert reconcile(ledger, coverage=kind != "missing_coverage")["unknown_execution"] is True


def test_execution_event_id_conflict_and_notice_failure_are_atomic(tmp_path, monkeypatch):
    ledger = ledger_at(tmp_path)
    buy(ledger)
    record(ledger, "same")
    assert record(ledger, "same")["event_applied"] is False
    with pytest.raises(LedgerError, match="conflict"):
        record(ledger, "same", quantity=3, notional=300)
    before = ledger.snapshot("book")
    monkeypatch.setattr(ledger, "_notice", lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("crash")))
    with pytest.raises(RuntimeError):
        record(ledger, "new", status="CANCELLED", reserved=0, at=T2)
    assert ledger.snapshot("book") == before


def test_ack_requires_claim_and_rejects_conflicting_receipt(tmp_path):
    ledger = ledger_at(tmp_path)
    buy(ledger)
    outbox = StrategyLedgerOutbox(ledger)
    notice, = outbox.pending()
    with pytest.raises(LedgerError, match="claim"):
        outbox.ack(notice["notice_id"], "worker", "receipt", T1)
    outbox.claim(notice["notice_id"], "worker", T1)
    with pytest.raises(LedgerError, match="claim"):
        outbox.ack(notice["notice_id"], "other", "receipt", T1)
    outbox.ack(notice["notice_id"], "worker", "receipt", T1)
    with pytest.raises(LedgerError, match="conflict"):
        outbox.ack(notice["notice_id"], "worker", "different-receipt", T1)
    with sqlite3.connect(ledger.path) as db, pytest.raises(sqlite3.IntegrityError):
        db.execute("DELETE FROM events WHERE id=?", (notice["notice_id"],))


def test_notice_snapshots_do_not_leak_account_references(tmp_path):
    ledger = ledger_at(tmp_path)
    buy(ledger)
    record(ledger, "execution", profile="SECRET_ACCOUNT", intent="SECRET_INTENT")
    text = StrategyLedgerOutbox(ledger).pending()[-1]["text"]
    assert "SECRET" not in text
    assert len(text) < 3500
    json.dumps(StrategyLedgerOutbox(ledger).pending())


def test_pilot_notices_follow_final_state_once_and_not_every_wait_tick(tmp_path):
    ledger = StrategyLedger(tmp_path / "pilot.sqlite")
    ledger.create_book("pilot", "US", cohort="split-pilot-v1", mode="SHADOW")
    dates = ["2026-09-04", "2026-09-08", "2026-09-09", "2026-09-10"]
    calendar = {"market": "US", "timezone": "America/New_York", "source_kind": "test_fixture",
                "source_ref": "outbox-fixture", "verification_status": "VERIFIED",
                "sessions": [{"date": d, "open_at": d + "T09:30:00-04:00", "close_at": d + "T16:00:00-04:00"} for d in dates]}
    ledger.open_pilot("open", "pilot", "c", "TEST", 100, "2026-09-04T10:00:00-04:00",
                      owner="split-pilot-v1", signal_bar=None, calendar=calendar, entry_eligible=True)
    outbox = StrategyLedgerOutbox(ledger)
    notice, = outbox.pending()
    assert "PILOT_50" in notice["text"]
    ledger.advance_pilot("wait", "c", expected_revision=0, evidence={}, occurred_at="2026-09-08T10:00:00-04:00")
    ledger.advance_pilot("wait-again", "c", expected_revision=1, evidence={}, occurred_at="2026-09-08T10:01:00-04:00")
    assert len(outbox.pending()) == 2
    ledger.advance_pilot("expire", "c", expected_revision=2, evidence={}, occurred_at="2026-09-10T16:00:00-04:00")
    assert len(outbox.pending()) == 3
    assert "ADD_EXPIRED" in outbox.pending()[-1]["text"]
    assert "PILOT_50" in outbox.pending()[0]["text"]  # frozen, not today's state


def test_strategy_sell_notice_and_legacy_execution_dedup(tmp_path):
    ledger = ledger_at(tmp_path)
    buy(ledger)
    ledger.observe_execution("reject", "c", "account", "REJECTED", T1, intent_ref="intent")
    ledger.observe_execution("same-status-poll", "c", "account", "REJECTED", T2, intent_ref="intent")
    assert len(StrategyLedgerOutbox(ledger).pending()) == 2
    ledger.sell("sell", "c", 110, T2)
    notice = StrategyLedgerOutbox(ledger).pending()[-1]
    assert notice["category"] == "SELL"
    assert "청산 완료" in notice["text"]


def test_no_new_tables_no_broker_or_transport_dependency(tmp_path):
    import ast
    from pathlib import Path

    ledger = ledger_at(tmp_path)
    buy(ledger)
    with sqlite3.connect(ledger.path) as db:
        assert {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")} == {
            "strategy_ledger_metadata", "books", "campaigns", "events", "legs", "executions"}
    source = Path(__file__).resolve().parents[1] / "prism_core" / "strategy_ledger_outbox.py"
    imports = [node.module for node in ast.walk(ast.parse(source.read_text())) if isinstance(node, ast.ImportFrom)]
    assert not any(module and any(name in module for name in ("trading.", "messaging", "telegram", "execution_service")) for module in imports)


@pytest.mark.parametrize("status", ["SUBMITTED", "ACCEPTED", "PARTIAL"])
@pytest.mark.parametrize("target,reserved", [(0, 0), (50, 0), (0, 300), (50, None)])
def test_active_buy_requires_coherent_submitted_target_and_reservation(tmp_path, status, target, reserved):
    ledger = ledger_at(tmp_path)
    buy(ledger)
    record(ledger, "active", status=status, target=target, reserved=reserved,
           quantity=1 if status == "PARTIAL" else 0, notional=100 if status == "PARTIAL" else 0)
    stored = ledger.snapshot("book")["executions"][0]
    assert stored["status"] == "UNKNOWN" and stored["source_status"] == status
    assert reconcile(ledger)["unknown_execution"] is True
    # The pure reconciler must also reject contradictory raw poll evidence,
    # independent of the writer's defensive normalization.
    raw = {**stored, "status": status}
    account = reconcile_account_evidence([raw], campaign_id="c", execution_profile_ref="account-A", coverage_complete=True)
    assert account["unknown_execution"] is True
    result = project_account_from_evidence(ledger.snapshot("book"), "c", account_evidence=account,
                                           account_unit_budget=1000, limit_price=100)
    assert result["quantity"] == 0 and result["reason"] == "UNKNOWN_EXECUTION_RESERVATION"


def test_late_older_execution_is_preserved_without_stale_or_repeated_notice(tmp_path):
    ledger = ledger_at(tmp_path)
    buy(ledger)
    record(ledger, "final", status="FILLED", at=T2, quantity=2, notional=200, reserved=0)
    outbox = StrategyLedgerOutbox(ledger)
    original = outbox.pending()
    record(ledger, "late-old", status="PARTIAL", at=T1, quantity=1, notional=100, reserved=400)
    assert outbox.pending() == original
    record(ledger, "new-repeat-final", status="FILLED", at=T3, quantity=2, notional=200, reserved=0)
    assert outbox.pending() == original
    assert len(ledger.snapshot("book")["executions"]) == 3
    assert reconcile(ledger)["residual_quantity"] == "2"


def test_late_first_notice_separates_source_clock_from_frozen_snapshot_clock(tmp_path):
    ledger = ledger_at(tmp_path)
    buy(ledger)
    ledger.sell("exit", "c", 110, T2)
    record(ledger, "old-first-fill", status="FILLED", at=T1, reserved=0)
    notice = StrategyLedgerOutbox(ledger).pending()[-1]
    assert notice["source_observed_at"] == "2026-09-10T01:00:00+00:00"
    assert notice["snapshot_evidence_at"] == "2026-09-10T02:00:00+00:00"
    assert notice["clock_basis"] == "EVIDENCE_CLOCK_NOT_WALL_CLOCK"
    assert "원천 관측 시각: 2026-09-10T01:00:00+00:00" in notice["text"]
    assert "고정 원장 증거 기준 시각: 2026-09-10T02:00:00+00:00" in notice["text"]
    assert "청산 완료" in notice["text"]


@pytest.mark.parametrize("receipt", ["UNKNOWN", "MISSING", "[REDACTED]", " unknown "])
def test_placeholder_receipt_cannot_mark_sent(tmp_path, receipt):
    ledger = ledger_at(tmp_path)
    buy(ledger)
    outbox = StrategyLedgerOutbox(ledger)
    notice, = outbox.pending()
    outbox.claim(notice["notice_id"], "worker", T1)
    with pytest.raises(LedgerError, match="known receipt"):
        outbox.ack(notice["notice_id"], "worker", receipt, T1)
    assert outbox.status(notice["notice_id"])["status"] == "UNKNOWN"
