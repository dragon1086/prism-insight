"""Isolated fixture proof for the standalone, non-trading audit exporter."""
import importlib.util
import json
from pathlib import Path
import sqlite3

import pytest


SOURCE = Path(__file__).resolve().parents[2] / "tools/build_btc_scenario_forward_packet.py"
SPEC = importlib.util.spec_from_file_location("forward_packet", SOURCE)
packet = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(packet)


@pytest.fixture
def db(tmp_path):
    path = tmp_path / "ledger.sqlite"
    with sqlite3.connect(path) as conn:
        conn.executescript("""
          CREATE TABLE btc_meta(mode TEXT,key TEXT,value TEXT);
          CREATE TABLE llm_scenario_control(id INTEGER,body TEXT);
          CREATE TABLE llm_scenario_decisions(slot INTEGER,proposal TEXT,outcome TEXT,context TEXT);
          CREATE TABLE llm_scenario_intents(id TEXT,scenario_id TEXT,payload TEXT,status TEXT,evidence TEXT);
          CREATE TABLE llm_scenario_children(link_id TEXT,intent_id TEXT,scenario_id TEXT,kind TEXT,
            local_id TEXT,request TEXT,status TEXT,order_id TEXT,evidence TEXT,created_at REAL);
          CREATE TABLE llm_scenario_settlements(scenario_id TEXT,evidence TEXT);
          CREATE TABLE llm_scenario_broker_evidence(kind TEXT,captured_at REAL,body TEXT);
          CREATE TABLE llm_scenario_outbox(event_id TEXT,kind TEXT,body TEXT,status TEXT,message_id INTEGER);
        """)
        conn.execute("INSERT INTO btc_meta VALUES('demo','shared_entry_policy_v1',?)",
                     (json.dumps(dict(main_uid="123456789", swing_uid="987654321")),))
        conn.execute("INSERT INTO llm_scenario_control VALUES(1,?)",
                     (json.dumps(dict(version=1, state="active", main_uid="123456789")),))
        proposal = json.dumps(dict(action_id="private-action", scenario_id="private-scenario", action="OPEN", input_id="input-1"))
        conn.execute("INSERT INTO llm_scenario_decisions VALUES(1,?,?,?)", (proposal, '{"status":"wait"}', '{"input_id":"input-1"}'))
        conn.execute("INSERT INTO llm_scenario_intents VALUES('private-action','private-scenario',?,'DONE',NULL)",
                     (proposal,))
        conn.execute("INSERT INTO llm_scenario_broker_evidence VALUES('account',1000,?)",
                     (json.dumps(dict(raw_secret="DO_NOT_EXPORT")),))
    return path


def child(db, kind="entry", qty="1", price="100", timestamp="500000", fee=".05", duplicate=False):
    side = "Buy" if kind == "entry" else "Sell"
    oid, link, eid = (kind + suffix for suffix in ("-private-order", "-private-link", "-private-exec"))
    fill = dict(execId=eid, orderId=oid, symbol="BTCUSDT", side=side, execType="Trade",
                execQty=qty, execPrice=price, execTime=timestamp)
    if fee is not None:
        fill["execFee"] = fee
    order = dict(orderId=oid, orderLinkId=link, symbol="BTCUSDT", side=side,
                 qty=qty, cumExecQty=qty, orderStatus="Filled")
    proof = dict(order=order, executions=[fill, fill] if duplicate else [fill])
    request = dict(symbol="BTCUSDT", side=side, orderLinkId=link)
    with sqlite3.connect(db) as conn:
        conn.execute("INSERT INTO llm_scenario_children VALUES(?, 'private-action', 'private-scenario', ?, '', ?, 'TERMINAL', ?, ?, 400)",
                     (link, kind, json.dumps(request), oid, json.dumps(proof)))
    return eid


def settlement(db, ids, **changes):
    value = dict(scenario_id="private-scenario", flat_confirmed=True, orders_terminal=True,
                 executions_complete=True, fees_complete=True, funding_complete=True,
                 no_fills_confirmed=not bool(ids), execution_ids=ids,
                 gross_pnl=10 if ids else 0, fees=.1 if ids else 0,
                 funding_net=0, net_pnl=9.9 if ids else 0)
    value.update(changes)
    with sqlite3.connect(db) as conn:
        conn.execute("INSERT INTO llm_scenario_settlements VALUES('private-scenario',?)", (json.dumps(value),))


def scenario(db):
    result = packet.build_packet(db)
    assert result["status"] == "AVAILABLE", result
    return result["scenarios"][0]


def test_missing_database_never_created(tmp_path):
    path = tmp_path / "absent"
    result = packet.build_packet(path)
    assert result["status"] == "INPUT_UNAVAILABLE"
    assert result["scenarios"] is None
    assert not path.exists()


@pytest.mark.parametrize("alias", [False, True])
def test_cli_cannot_create_missing_source_as_output(tmp_path, monkeypatch, alias):
    source = tmp_path / "missing.sqlite"
    target = source
    if alias:
        (tmp_path / "alias").symlink_to(tmp_path, target_is_directory=True)
        target = tmp_path / "alias" / source.name
    monkeypatch.setattr("sys.argv", ["audit", "--db", str(source), "--output", str(target)])
    with pytest.raises(SystemExit) as error:
        packet.main()
    assert error.value.code == 2
    assert not source.exists()


def test_missing_schema_and_binding(db):
    with sqlite3.connect(db) as conn:
        conn.execute("DROP TABLE llm_scenario_children")
    assert packet.build_packet(db)["status"] == "INPUT_UNAVAILABLE"


def test_wrong_main_account_fails_closed(db):
    with sqlite3.connect(db) as conn:
        conn.execute("UPDATE llm_scenario_control SET body=?", (json.dumps(dict(version=1, state="active", main_uid="777")),))
    assert packet.build_packet(db)["scenarios"] is None


def test_open_partial_and_no_zero_pnl(db):
    child(db)
    assert scenario(db)["entry_quantity"] == "1"
    assert scenario(db)["recorded_settlement_amounts"] is None
    child(db, "tp", ".6", "110", "600000")
    result = scenario(db)
    assert result["remaining_quantity"] == "0.4"
    assert result["lifecycle"] == "OPEN_OR_UNSETTLED"


def test_recorded_full_close_not_independent_profitability(db):
    ids = [child(db), child(db, "exit", "1", "110", "600000")]
    settlement(db, ids)
    result = scenario(db)
    assert result["lifecycle"] == "FLAT_SETTLEMENT_RECORDED"
    assert result["stored_settlement_checks_passed"] is True
    assert result["independent_accounting_complete"] is False
    assert result["recorded_settlement_amounts"]["net_pnl"] == "9.9"
    assert packet.build_packet(db)["profitability_proven"] is False


def test_cancel_no_fill_is_not_trade(db):
    settlement(db, [])
    assert scenario(db)["lifecycle"] == "CANCELLED_UNFILLED"


def test_identical_duplicate_counted_once(db):
    child(db, duplicate=True)
    assert scenario(db)["entry_quantity"] == "1"
    assert len(scenario(db)["execution_refs"]) == 1


@pytest.mark.parametrize("mutation", ["conflict", "foreign", "wrong_intent", "overexit"])
def test_invalid_exact_evidence_fails_closed(db, mutation):
    child(db)
    with sqlite3.connect(db) as conn:
        raw = json.loads(conn.execute("SELECT evidence FROM llm_scenario_children").fetchone()[0])
        if mutation == "conflict":
            other = dict(raw["executions"][0], execPrice="101")
            raw["executions"].append(other)
        elif mutation == "foreign":
            raw["executions"][0]["orderId"] = "foreign-order"
        elif mutation == "wrong_intent":
            conn.execute("UPDATE llm_scenario_children SET intent_id='unknown'")
        conn.execute("UPDATE llm_scenario_children SET evidence=?", (json.dumps(raw),))
    if mutation == "overexit":
        child(db, "exit", "2", "110", "600000")
    assert packet.build_packet(db)["status"] == "INPUT_UNAVAILABLE"
    assert packet.build_packet(db)["scenarios"] is None


@pytest.mark.parametrize("changes", [dict(net_pnl=99), dict(fees=None), dict(execution_ids=[]), dict(funding_complete=False),
                                     dict(gross_pnl=20, net_pnl=19.9), dict(fees=.2, net_pnl=9.8)])
def test_bad_settlement_not_reported(db, changes):
    ids = [child(db), child(db, "exit", "1", "110", "600000")]
    settlement(db, ids, **changes)
    assert packet.build_packet(db)["scenarios"] is None


def test_missing_cost_flagged_not_proven(db):
    child(db, fee=None)
    result = scenario(db)
    assert result["issues"] == ["MISSING_EXECUTION_FEE"]
    assert result["independent_accounting_complete"] is False


def test_future_fill_not_reported_as_confirmed(db):
    child(db, timestamp="1001000")
    result = packet.build_packet(db)
    assert result["status"] == "INPUT_UNAVAILABLE"
    assert result["scenarios"] is None


def test_privacy_determinism_read_only_and_cohort(db):
    child(db)
    before = db.read_bytes()
    result = packet.build_packet(db, "1970-01-01T00:08:20Z")
    assert result == packet.build_packet(db, "1970-01-01T00:08:20+00:00")
    assert db.read_bytes() == before
    assert result["scenarios"][0]["cohort"] == "CARRY_IN"
    serialized = json.dumps(result)
    for secret in ("123456789", "987654321", "DO_NOT_EXPORT", "private-action", "private-order", "private-exec"):
        assert secret not in serialized
    assert result["verdict"] == "CONTINUE_CAPTURE"
    assert result["auto_promotion"] is False


def test_exact_notice_status_and_receipts(db):
    with sqlite3.connect(db) as conn:
        for index, (status, receipt) in enumerate([("SENT", 1), ("UNKNOWN", 2), ("SENDING", 3), ("SENT", None)]):
            conn.execute("INSERT INTO llm_scenario_outbox VALUES(?,'FILLED','secret',?,?)", (str(index), status, receipt))
    result = packet.build_packet(db)
    assert result["notice_receipts"] == 1
    assert result["notice_statuses"] == dict(SENT=2, UNKNOWN=1, SENDING=1)


def test_cli_refuses_existing_output_and_naive_boundary(db, tmp_path, monkeypatch):
    output = tmp_path / "packet.json"
    monkeypatch.setattr("sys.argv", [str(SOURCE), "--db", str(db), "--output", str(output)])
    assert packet.main() == 0
    first = output.read_bytes()
    with pytest.raises(FileExistsError):
        packet.main()
    assert output.read_bytes() == first
    with pytest.raises(ValueError):
        packet.build_packet(db, "2026-10-04T00:00:00")


def test_missing_proposal_link_never_invents_join(db):
    child(db)
    with sqlite3.connect(db) as conn:
        conn.execute("UPDATE llm_scenario_decisions SET proposal=NULL")
    assert "MISSING_DECISION_ACTION_LINK" in scenario(db)["issues"]


def test_entry_after_boundary_still_keeps_carry_in_plan(db):
    child(db)
    result = packet.build_packet(db, "1970-01-01T00:05:50Z")
    assert result["scenarios"][0]["cohort"] == "CARRY_IN"


def test_real_filled_settlement_without_no_fill_flag(db):
    ids = [child(db), child(db, "exit", "1", "110", "600000")]
    settlement(db, ids)
    with sqlite3.connect(db) as conn:
        record = json.loads(conn.execute("SELECT evidence FROM llm_scenario_settlements").fetchone()[0])
        del record["no_fills_confirmed"]
        conn.execute("UPDATE llm_scenario_settlements SET evidence=?", (json.dumps(record),))
    assert scenario(db)["lifecycle"] == "FLAT_SETTLEMENT_RECORDED"


def test_no_fill_settlement_requires_explicit_flag(db):
    settlement(db, [], no_fills_confirmed=None)
    result = packet.build_packet(db)
    assert result["status"] == "INPUT_UNAVAILABLE"
    assert result["input_stage"] == "scenario_validation"


def test_same_timestamp_mixed_roles_never_ordered_by_execution_id(db):
    ids = [child(db), child(db, "exit", "1", "110", "500000")]
    settlement(db, ids)
    result = packet.build_packet(db)
    assert result["status"] == "INPUT_UNAVAILABLE"
    assert result["scenarios"] is None
    assert result["input_stage"] == "scenario_validation"


@pytest.mark.parametrize("outcome", ["intent_pending", "duplicate_slot", "execution_disabled", "fenced"])
def test_actual_outcomes_not_collapsed_to_other(db, outcome):
    with sqlite3.connect(db) as conn:
        conn.execute("UPDATE llm_scenario_decisions SET outcome=?", (json.dumps(dict(status=outcome)),))
    assert packet.build_packet(db)["decision_outcomes"] == {outcome: 1}


@pytest.mark.parametrize("change", ["missing", "adjust", "input_mismatch"])
def test_prospective_requires_exact_open_link(db, change):
    child(db)
    with sqlite3.connect(db) as conn:
        if change == "missing":
            conn.execute("UPDATE llm_scenario_decisions SET proposal=NULL")
        elif change == "input_mismatch":
            conn.execute("UPDATE llm_scenario_decisions SET context='{}'")
        else:
            raw = json.loads(conn.execute("SELECT proposal FROM llm_scenario_decisions").fetchone()[0])
            raw["action"] = "ADJUST"
            conn.execute("UPDATE llm_scenario_decisions SET proposal=?", (json.dumps(raw),))
            conn.execute("UPDATE llm_scenario_intents SET payload=?", (json.dumps(raw),))
    result = packet.build_packet(db, "1970-01-01T00:04:00Z")
    assert result["scenarios"][0]["cohort"] == "START_UNKNOWN"


def test_prospective_exact_open_preserved(db):
    child(db)
    result = packet.build_packet(db, "1970-01-01T00:04:00Z")
    assert result["scenarios"][0]["cohort"] == "PROSPECTIVE"
