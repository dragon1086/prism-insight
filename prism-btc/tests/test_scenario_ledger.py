import json
import sqlite3

import pytest

from live import scenario_ledger as ledger
from live import scenario_provenance as audit

RAW_ORDER_ID = "9beaf4a1-3aef-4965-9c20-935543830f8a"


@pytest.fixture
def spool(tmp_path, monkeypatch):
    path = tmp_path / "events.jsonl"
    monkeypatch.setenv("PRISM_OBSERVABILITY_SPOOL", str(path))
    return lambda: [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


def test_committed_audit_rows_reach_the_ledger_without_raw_ids(tmp_path, spool):
    conn = sqlite3.connect(tmp_path / "test.sqlite")
    with audit.run_capture(conn):
        audit.record("decision_input", {"context": {"day": "2026-10-08", "daily_net_pnl": -19.9,
                                                    "accounting_status": "confirmed", "current_plan": None},
                                        "snapshot": {"valid": True}, "context_hash": "c1"}, decision_slot=7)
        audit.exchange_call("cancel_order", {"category": "linear", "orderId": RAW_ORDER_ID,
                                             "orderLinkId": "sc_link", "symbol": "BTCUSDT"},
                            100.0, response={"retCode": 0, "result": {"orderId": RAW_ORDER_ID}})
        audit.record("accounting_observation", {"observation": {"equity": 9502.2}})
    events = spool()
    assert [e["event_type"] for e in events] == ["btc.scenario.decision_input", "btc.scenario.exchange_call"]
    decision, call = (e["attributes"] for e in events)
    assert decision["daily_net_pnl"] == -19.9 and decision["has_current_plan"] is False
    assert decision["decision_slot"] == 7
    assert call["method"] == "cancel_order" and call["status"] == "ACK_ONLY" and call["ret_code"] == 0
    assert call["order_id_hash"] and call["symbol"] == "BTCUSDT"
    text = json.dumps(events)
    assert RAW_ORDER_ID not in text and "sc_link" not in text
    rows = conn.execute("select count(*) from llm_scenario_audit_events").fetchone()[0]
    assert rows == 3                     # SQLite audit trail itself is unchanged


def test_intent_and_settlement_summaries_are_bounded():
    intent = ledger.summarize("intent_committed", {
        "payload": {"action": "OPEN", "confidence": 0.55, "leverage": 10, "hard_stop": 83030,
                    "entries": [{"price": 1}], "rationale": "x" * 900},
        "risk": {"budget": 47.5, "within_budget": True, "notes": {"nested": 1}}, "payload_hash": "p"})
    assert intent["action"] == "OPEN" and intent["entry_count"] == 1 and len(intent["rationale"]) == 300
    assert intent["risk"] == {"budget": 47.5, "within_budget": True}
    settled = ledger.summarize("settlement_recorded", {"settlement": {
        "net_pnl": -3.2, "flat_confirmed": True, "execution_ids": ["e1", "e2"], "scenario_id": "s_1"}})
    assert settled == {"net_pnl": -3.2, "flat_confirmed": True, "execution_count": 2}


def test_notice_and_runner_heartbeat(spool):
    ledger.forward_notice("halt-1", {"kind": "HALTED"}, "⛔ BTC 데모 신규 진입 중단")
    ledger.forward_run({"status": "blocked", "reason": "new_risk_halted", "verified_flat_halt": True},
                       loop="decision", started=0.0)
    ledger.forward_run({"status": "blocked", "reason": "scenario_runtime_unavailable",
                        "failure_stage": "run", "error_type": "TimeoutError"}, loop="decision", started=0.0)
    ledger.forward_run({"status": "lock_busy", "error_type": "LockBusy"}, loop="protection", started=0.0)
    notice, halted, failed, busy = spool()
    assert notice["event_type"] == "btc.scenario.notice" and notice["severity"] == "WARNING"
    assert notice["attributes"]["kind"] == "HALTED"
    assert (halted["attributes"]["status"], halted["severity"]) == ("BLOCKED", "INFO")
    assert halted["attributes"]["reason"] == "new_risk_halted"
    assert (failed["attributes"]["status"], failed["severity"]) == ("ERROR", "ERROR")
    assert busy["attributes"]["status"] == "LOCK_BUSY" and busy["attributes"]["mode"] == "protection"


def test_bridge_failures_never_escape(spool):
    ledger.forward_audit(("only", "three", "fields"))
    ledger.forward_notice("x", None, "body")
    ledger.forward_run(None, loop="decision", started=None)
    assert [e["attributes"]["status"] for e in spool()] == ["UNKNOWN"]
