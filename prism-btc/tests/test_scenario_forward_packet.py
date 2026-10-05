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


@pytest.mark.parametrize("slot", [0, 2])
def test_rejected_duplicate_proposal_does_not_poison_exact_intent(db, slot):
    child(db)
    with sqlite3.connect(db) as conn:
        raw = json.loads(conn.execute("SELECT proposal FROM llm_scenario_decisions").fetchone()[0])
        raw["input_id"] = "rejected-input"
        conn.execute("INSERT INTO llm_scenario_decisions VALUES(?,?,?,?)",
                     (slot, json.dumps(raw), '{"status":"blocked"}', '{"input_id":"rejected-input"}'))
    result = packet.build_packet(db, "1970-01-01T00:04:00Z")
    assert result["status"] == "AVAILABLE"
    assert result["scenarios"][0]["cohort"] == "PROSPECTIVE"
    assert result["scenarios"][0]["entry_quantity"] == "1"
    assert result["scenarios"][0]["open_decision_slot_start"] == 300
    assert result["scenarios"][0]["issues"] == []
    assert result["decision_outcomes"] == {"wait": 1, "blocked": 1}
    assert result["decision_scope"] == "GLOBAL_STORED_HISTORY_NOT_POST_BOUNDARY"


def test_ambiguous_exact_proposals_never_select_nearest_or_earliest(db):
    child(db)
    with sqlite3.connect(db) as conn:
        proposal, outcome, context = conn.execute(
            "SELECT proposal,outcome,context FROM llm_scenario_decisions").fetchone()
        conn.execute("INSERT INTO llm_scenario_decisions VALUES(2,?,?,?)", (proposal, outcome, context))
    result = packet.build_packet(db, "1970-01-01T00:04:00Z")
    assert result["status"] == "AVAILABLE"
    assert result["scenarios"][0]["cohort"] == "START_UNKNOWN"
    assert result["scenarios"][0]["open_decision_slot_start"] is None
    assert result["scenarios"][0]["issues"] == ["AMBIGUOUS_DECISION_ACTION_LINK"]
    assert result["scenarios"][0]["entry_quantity"] == "1"


def test_optional_notice_exact_link_and_no_raw_export(db):
    with sqlite3.connect(db) as conn:
        conn.execute("CREATE TABLE llm_scenario_broker_notices(event_id TEXT,scenario_id TEXT,body TEXT)")
        body = json.dumps(dict(event_id="private-notice", kind="FILLED", timestamp=500))
        conn.execute("INSERT INTO llm_scenario_broker_notices VALUES('private-notice','private-scenario',?)", (body,))
        conn.execute("INSERT INTO llm_scenario_outbox VALUES('private-notice','FILLED','secret','SENT',123)")
    item = scenario(db)
    assert item["notice_links"][0]["receipt_confirmed"] is True
    assert "private-notice" not in json.dumps(item)
    assert item["provenance_status"] == "MISSING"


def test_optional_tables_absent_keeps_historical_unknown(db):
    item = scenario(db)
    assert item["provenance_status"] == "MISSING"
    assert item["code_versions"] == []
    assert item["settlement_timestamp"] is None
    assert item["independent_accounting_complete"] is False


def test_notice_body_identity_conflict_not_nearest_join(db):
    with sqlite3.connect(db) as conn:
        conn.execute("CREATE TABLE llm_scenario_broker_notices(event_id TEXT,scenario_id TEXT,body TEXT)")
        conn.execute("INSERT INTO llm_scenario_broker_notices VALUES('one','private-scenario',?)",
                     (json.dumps(dict(event_id="other", kind="CLOSED", timestamp=500)),))
    assert scenario(db)["notice_link_status"] == "CONFLICT"


def audit_event(db, kind, body, *, event_id="event", sid="private-scenario"):
    manifest = dict(policy_hash="a"*64, execution_hash="b"*64, git_revision="c"*40,
                    source_hashes={"source.py": "d"*64}, loaded_code_status="UNKNOWN")
    manifest_id = packet.digest(manifest)
    with sqlite3.connect(db) as conn:
        if 'snapshot' not in {r[1] for r in conn.execute('PRAGMA table_info(llm_scenario_decisions)')}:
            conn.execute("ALTER TABLE llm_scenario_decisions ADD COLUMN snapshot TEXT DEFAULT '{}'")
        conn.execute("CREATE TABLE IF NOT EXISTS llm_scenario_audit_manifests(manifest_id TEXT,body TEXT)")
        conn.execute("CREATE TABLE IF NOT EXISTS llm_scenario_audit_events(event_id TEXT,run_id TEXT,kind TEXT,observed_at REAL,scenario_id TEXT,intent_id TEXT,decision_slot INTEGER,manifest_id TEXT,body TEXT)")
        if not conn.execute("SELECT 1 FROM llm_scenario_audit_manifests").fetchone():
            conn.execute("INSERT INTO llm_scenario_audit_manifests VALUES(?,?)", (manifest_id, json.dumps(manifest)))
        conn.execute("INSERT INTO llm_scenario_audit_events VALUES(?,'private-run',?,1000,?,NULL,1,?,?)",
                     (event_id, kind, sid, manifest_id, json.dumps(dict(schema_version=1, **body))))


def accounting_body(db):
    children, owned, trades = [], [], []
    with sqlite3.connect(db) as conn:
        conn.row_factory = sqlite3.Row
        for row in conn.execute("SELECT * FROM llm_scenario_children"):
            item = dict(row)
            item["evidence"] = json.loads(item["evidence"])
            item["request"] = json.loads(item["request"])
            children.append(item)
            owned.append(dict(order_id=item["order_id"], role="entry" if item["kind"] == "entry" else "exit",
                              side=item["request"]["side"], cumulative_qty=1, terminal=True))
            raw = item["evidence"]["executions"][0]
            pnl = 0 if item["kind"] == "entry" else 10
            txn = dict(tradeId=raw["execId"], orderId=raw["orderId"], type="TRADE", symbol="BTCUSDT",
                       currency="USDT", fee=raw["execFee"], cashFlow=pnl, funding=0)
            trades.append(dict(order_id=raw["orderId"], execution_id=raw["execId"], raw_execution=raw,
                               raw_transaction=txn, quantity=raw["execQty"], price=raw["execPrice"],
                               timestamp=raw["execTime"], fee=raw["execFee"], gross_pnl=pnl))
    evidence = dict(response_pages_complete=True, start_ms=400000, end_ms=1000000,
                    unmatched_ids=[], trades=trades, funding=[])
    schedule = dict(complete=True, start_ms=400000, end_ms=1000000, events=[])
    observation = dict(position=dict(symbol="BTCUSDT", positionIdx=0, size=0), open_orders=[])
    spec = importlib.util.spec_from_file_location("accounting_fixture", SOURCE.parents[1] / "prism-btc/live/scenario_accounting.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    result = module.reconcile_scenario("private-scenario", evidence, owned, observation, funding_schedule=schedule)
    assert result["accounting_complete"]
    return dict(financial_evidence=evidence, owned_orders=owned, children=children, observation=observation,
                funding_schedule=schedule, funding_source=dict(raw_rows=[], instruments=[dict(symbol="BTCUSDT", fundingInterval=480)],
                next_funding_time=28800000), result=result)


def test_raw_source_rereconciliation_and_detection_not_execution_time(db):
    ids = [child(db), child(db, "exit", "1", "110", "600000")]
    settlement(db, ids)
    body = accounting_body(db)
    audit_event(db, "accounting_observation", body)
    audit_event(db, "settlement_recorded", dict(settlement=body["result"]["settlement"], detected_at=1000), event_id="settle")
    item = scenario(db)
    assert item["accounting_source_check"]["status"] == "SOURCE_RECONCILIATION_VERIFIED"
    assert item["settlement_timestamp"] == 1000
    assert item["last_execution_timestamp"] == 600
    assert item["code_versions"] == ["c"*40]
    assert item["provenance_status"] == "PARTIAL"
    assert item["independent_accounting_complete"] is False
    assert "private-" not in json.dumps(item)


@pytest.mark.parametrize("fault", ["source_missing", "foreign_child", "duplicate_exec", "fee", "source_rate", "missing_trade"])
def test_raw_evidence_faults_never_complete(db, fault):
    ids = [child(db), child(db, "exit", "1", "110", "600000")]
    settlement(db, ids)
    body = accounting_body(db)
    if fault == "source_missing":
        del body["funding_source"]
    elif fault == "foreign_child":
        body["children"][0]["scenario_id"] = "other"
    elif fault == "duplicate_exec":
        body["financial_evidence"]["trades"].append(body["financial_evidence"]["trades"][0])
    elif fault == "fee":
        body["financial_evidence"]["trades"][0]["raw_transaction"]["fee"] = 100
    elif fault == "source_rate":
        body["funding_source"]["raw_rows"] = [dict(symbol="BTCUSDT", fundingRateTimestamp=500000, fundingRate=.001)]
    else:
        body["financial_evidence"]["trades"].pop()
    audit_event(db, "accounting_observation", body)
    item = scenario(db)
    assert item["accounting_source_check"]["status"] != "SOURCE_RECONCILIATION_VERIFIED"
    assert item["independent_accounting_complete"] is False


def test_foreign_sid_and_duplicate_event_do_not_link(db):
    audit_event(db, "intent_committed", {}, sid="other")
    assert scenario(db)["provenance_status"] == "MISSING"
    audit_event(db, "intent_committed", {})
    assert scenario(db)["provenance_status"] == "CONFLICT"


def hashed_body(**fields):
    return dict(fields, **{name + "_hash": packet.digest(value) for name, value in fields.items()})


def test_pre_scenario_decision_and_ack_use_exact_ids_only(db):
    child(db)
    audit_event(db, "decision_input", hashed_body(snapshot={}, context={"input_id": "input-1"}, input_id="input-1"), sid=None)
    audit_event(db, "exchange_call", hashed_body(request=dict(orderLinkId="entry-private-link"), status="ACK_ONLY"),
                event_id="ack", sid=None)
    item = scenario(db)
    assert item["judgement_policy_hashes"] == ["a"*64]
    assert item["execution_policy_hashes"] == ["b"*64]
    assert item["audit_links"][0]["kind"] == "exchange_call"
    assert item["audit_links"][0]["status"] == "ACK_ONLY"
    assert item["independent_accounting_complete"] is False
    with sqlite3.connect(db) as conn:
        conn.execute("UPDATE llm_scenario_audit_events SET decision_slot=99 WHERE kind='decision_input'")
    assert scenario(db)["judgement_policy_hashes"] == []


def test_intent_risk_and_reservations_link_without_raw_payload_export(db):
    with sqlite3.connect(db) as conn:
        payload = json.loads(conn.execute("SELECT payload FROM llm_scenario_intents").fetchone()[0])
    audit_event(db, "intent_committed", hashed_body(payload=payload, risk={"budget": 20}, initial_equity=1000, pending_entries=[]))
    with sqlite3.connect(db) as conn:
        conn.execute("UPDATE llm_scenario_audit_events SET intent_id='private-action'")
    item = scenario(db)
    assert item["audit_links"][0]["risk_hash"] == packet.digest({"budget": 20})
    assert "private-action" not in json.dumps(item)
    assert item["provenance_status"] == "PARTIAL"


def test_accounting_envelope_cannot_change_same_id_ledger_fill(db):
    ids = [child(db), child(db, "exit", "1", "110", "600000")]
    settlement(db, ids)
    body = accounting_body(db)
    body["children"][0]["evidence"]["executions"][0]["execPrice"] = "101"
    audit_event(db, "accounting_observation", body)
    assert scenario(db)["provenance_status"] == "CONFLICT"


@pytest.mark.parametrize("fault", ["slot", "context", "snapshot", "foreign"])
def test_bound_decision_requires_exact_durable_input_not_self_hash(db, fault):
    body = hashed_body(snapshot={}, context={"input_id": "input-1"}, input_id="input-1")
    if fault == "context":
        body = hashed_body(snapshot={}, context={"input_id": "input-1", "forged": True}, input_id="input-1")
    if fault == "snapshot":
        body = hashed_body(snapshot={"forged": True}, context={"input_id": "input-1"}, input_id="input-1")
    audit_event(db, "decision_input", body)
    with sqlite3.connect(db) as conn:
        if fault == "slot":
            conn.execute("UPDATE llm_scenario_audit_events SET decision_slot=2")
        if fault == "foreign":
            context = dict(input_id="input-1", scenario_id="foreign")
            conn.execute("UPDATE llm_scenario_decisions SET context=?", (json.dumps(context),))
            changed = dict(schema_version=1, **hashed_body(snapshot={}, context=context, input_id="input-1"))
            conn.execute("UPDATE llm_scenario_audit_events SET body=?", (json.dumps(changed),))
    assert scenario(db)["provenance_status"] == "CONFLICT"


def test_active_wait_decision_exact_durable_link(db):
    context = dict(input_id="wait-input", scenario_id="private-scenario")
    audit_event(db, "decision_input", hashed_body(snapshot={}, context=context, input_id="wait-input"))
    with sqlite3.connect(db) as conn:
        conn.execute("UPDATE llm_scenario_audit_events SET decision_slot=2")
        conn.execute("INSERT INTO llm_scenario_decisions(slot,proposal,outcome,context,snapshot) VALUES(2,?,'{}',?,'{}')",
                     (json.dumps(dict(action="WAIT", scenario_id="private-scenario", input_id="wait-input")), json.dumps(context)))
    assert scenario(db)["judgement_policy_hashes"] == ["a"*64]
    assert scenario(db)["provenance_status"] == "PARTIAL"


@pytest.mark.parametrize("earlier", ["nonterminal", "before_replacement"])
def test_valid_historical_accounting_prefix_does_not_poison_final(db, earlier):
    ids = [child(db), child(db, "exit", "1", "110", "600000")]
    settlement(db, ids)
    old = accounting_body(db)
    if earlier == "nonterminal":
        old["owned_orders"][0]["terminal"] = False
        old["result"]["settlement"] = None
    else:
        req = dict(symbol="BTCUSDT", side="Sell", orderLinkId="replacement")
        proof = dict(order=dict(orderId="replacement-order", orderLinkId="replacement", symbol="BTCUSDT", side="Sell",
                               qty="1", cumExecQty="0", orderStatus="Cancelled"), executions=[])
        with sqlite3.connect(db) as conn:
            conn.execute("INSERT INTO llm_scenario_children VALUES('replacement','private-action','private-scenario','tp','',?,'TERMINAL','replacement-order',?,650)",
                         (json.dumps(req), json.dumps(proof)))
    final = accounting_body(db) if earlier == "nonterminal" else json.loads(json.dumps(old))
    if earlier == "before_replacement":
        with sqlite3.connect(db) as conn:
            conn.row_factory = sqlite3.Row
            extra = dict(conn.execute("SELECT * FROM llm_scenario_children WHERE link_id='replacement'").fetchone())
        extra["evidence"] = json.loads(extra["evidence"])
        extra["request"] = json.loads(extra["request"])
        final["children"].append(extra)
        final["owned_orders"].append(dict(order_id="replacement-order", role="exit", side="Sell", cumulative_qty=0, terminal=True))
    audit_event(db, "accounting_observation", old, event_id="earlier")
    audit_event(db, "accounting_observation", final, event_id="later")
    with sqlite3.connect(db) as conn:
        conn.execute("UPDATE llm_scenario_audit_events SET observed_at=999 WHERE event_id='earlier'")
    item = scenario(db)
    assert item["provenance_status"] == "PARTIAL"
    assert item["accounting_source_check"]["status"] == "SOURCE_RECONCILIATION_VERIFIED"
    assert any(link.get("accounting_scope") == "HISTORICAL_PREFIX_NOT_CURRENT_PROOF" for link in item["audit_links"])


def test_environment_change_separates_policy_groups(db):
    audit_event(db, "other", {}, event_id="a")
    audit_event(db, "other", {}, event_id="b")
    with sqlite3.connect(db) as conn:
        manifest = json.loads(conn.execute("SELECT body FROM llm_scenario_audit_manifests").fetchone()[0])
        manifest.update(python="3.12.12", libraries={"pandas": "2.2.3"})
        ident = packet.digest(manifest)
        conn.execute("INSERT INTO llm_scenario_audit_manifests VALUES(?,?)", (ident, json.dumps(manifest)))
        conn.execute("UPDATE llm_scenario_audit_events SET manifest_id=? WHERE event_id='b'", (ident,))
    item = scenario(db)
    assert len(item["policy_hashes"]) == 1
    assert len(item["environment_hashes"]) == len(item["policy_groups"]) == 2


def test_pending_accounting_same_fills_is_not_conflict(db):
    child(db)
    child(db, "exit", "1", "110", "600000")
    body = accounting_body(db)
    body["result"] = dict(status="pending", accounting_complete=False, settlement=None, reasons=["fees_missing"])
    audit_event(db, "accounting_observation", body)
    item = scenario(db)
    assert item["provenance_status"] == "PARTIAL"
    assert item["accounting_source_check"]["status"] == "MISSING"


def test_pending_missing_transaction_trade_then_full_proof(db):
    child(db)
    child(db, "exit", "1", "110", "600000")
    complete = accounting_body(db)
    pending = json.loads(json.dumps(complete))
    removed = pending["financial_evidence"]["trades"].pop()
    pending["financial_evidence"]["unmatched_ids"] = [removed["execution_id"]]
    pending["result"] = dict(status="pending", accounting_complete=False, settlement=None, reasons=["financial_rows_unmatched"])
    audit_event(db, "accounting_observation", pending, event_id="pending")
    audit_event(db, "accounting_observation", complete, event_id="complete")
    item = scenario(db)
    assert item["provenance_status"] == "PARTIAL"
    assert item["accounting_source_check"]["status"] == "SOURCE_RECONCILIATION_VERIFIED"
    assert len(item["accounting_source_check"]["calculator_source_sha256"]) == 64


def update_manifest(db, **changes):
    with sqlite3.connect(db) as conn:
        old_id, raw = conn.execute("SELECT manifest_id,body FROM llm_scenario_audit_manifests").fetchone()
        body = dict(json.loads(raw), **changes)
        ident = packet.digest(body)
        conn.execute("UPDATE llm_scenario_audit_manifests SET manifest_id=?,body=? WHERE manifest_id=?",
                     (ident, json.dumps(body), old_id))
        conn.execute("UPDATE llm_scenario_audit_events SET manifest_id=? WHERE manifest_id=?", (ident, old_id))


@pytest.mark.parametrize("execution_status", ["VERIFIED", "UNKNOWN", "MIXED"])
def test_protection_uses_execution_domain_not_unused_judgement(db, execution_status):
    child(db)
    child(db, "exit", "1", "110", "600000")
    audit_event(db, "accounting_observation", accounting_body(db))
    update_manifest(db, loaded_code_status="UNKNOWN", judgment_loaded_code_status="UNKNOWN",
                    execution_loaded_code_status=execution_status)
    item = scenario(db)
    assert item["loaded_code_status"] == execution_status
    assert item["audit_links"][0]["loaded_code_status"] == execution_status
    assert item["provenance_status"] == "PARTIAL"
    assert item["independent_accounting_complete"] is False
    assert packet.build_packet(db)["auto_promotion"] is False


def test_decision_only_uses_judgement_domain(db):
    audit_event(db, "decision_input", hashed_body(snapshot={}, context={"input_id": "input-1"}, input_id="input-1"))
    update_manifest(db, loaded_code_status="UNKNOWN", judgment_loaded_code_status="VERIFIED",
                    execution_loaded_code_status="UNKNOWN")
    assert scenario(db)["loaded_code_status"] == "VERIFIED"


def test_intent_requires_both_domains(db):
    with sqlite3.connect(db) as conn:
        payload = json.loads(conn.execute("SELECT payload FROM llm_scenario_intents").fetchone()[0])
    audit_event(db, "intent_committed", hashed_body(payload=payload, risk={}, initial_equity=1000, pending_entries=[]))
    with sqlite3.connect(db) as conn:
        conn.execute("UPDATE llm_scenario_audit_events SET intent_id='private-action'")
    update_manifest(db, loaded_code_status="UNKNOWN", judgment_loaded_code_status="VERIFIED",
                    execution_loaded_code_status="UNKNOWN")
    assert scenario(db)["loaded_code_status"] == "UNKNOWN"


@pytest.mark.parametrize("legacy", ["UNKNOWN", "MIXED", "VERIFIED"])
def test_old_manifest_without_domain_proof_remains_unknown(db, legacy):
    audit_event(db, "decision_input", hashed_body(snapshot={}, context={"input_id": "input-1"}, input_id="input-1"))
    update_manifest(db, loaded_code_status=legacy)
    assert scenario(db)["loaded_code_status"] == "UNKNOWN"
    assert scenario(db)["manifest_loaded_code_statuses"] == [legacy]


def test_five_minute_judgement_plus_one_minute_protection_domains(db):
    child(db)
    child(db, "exit", "1", "110", "600000")
    audit_event(db, "decision_input", hashed_body(snapshot={}, context={"input_id": "input-1"}, input_id="input-1"), event_id="judgement")
    audit_event(db, "accounting_observation", accounting_body(db), event_id="protection")
    update_manifest(db, loaded_code_status="VERIFIED", judgment_loaded_code_status="VERIFIED", execution_loaded_code_status="VERIFIED")
    with sqlite3.connect(db) as conn:
        body = json.loads(conn.execute("SELECT body FROM llm_scenario_audit_manifests").fetchone()[0])
        body.update(loaded_code_status="UNKNOWN", judgment_loaded_code_status="UNKNOWN")
        ident = packet.digest(body)
        conn.execute("INSERT INTO llm_scenario_audit_manifests VALUES(?,?)", (ident, json.dumps(body)))
        conn.execute("UPDATE llm_scenario_audit_events SET manifest_id=? WHERE event_id='protection'", (ident,))
    item = scenario(db)
    assert item["loaded_code_status"] == "VERIFIED"
    assert item["manifest_loaded_code_statuses"] == ["UNKNOWN", "VERIFIED"]
    assert item["judgement_policy_hashes"] == ["a"*64]
    assert item["provenance_status"] == "PARTIAL"
    assert item["independent_accounting_complete"] is False
