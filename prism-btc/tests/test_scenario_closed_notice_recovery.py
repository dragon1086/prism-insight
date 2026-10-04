import importlib.util
import json
import sqlite3
from pathlib import Path

import pytest

SPEC = importlib.util.spec_from_file_location("closed_recovery", Path(__file__).resolve().parents[2]
                                            / "tools/recover_btc_closed_notice.py")
tool = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(tool)


def database(tmp_path):
    path = tmp_path / "evidence.sqlite"
    with sqlite3.connect(path) as conn:
        conn.executescript("""
        CREATE TABLE btc_meta(mode TEXT,key TEXT,value TEXT);
        CREATE TABLE llm_scenario_control(id INTEGER,body TEXT);
        CREATE TABLE llm_scenario_state(id INTEGER,body TEXT);
        CREATE TABLE llm_scenario_settlements(scenario_id TEXT PRIMARY KEY,evidence TEXT);
        CREATE TABLE llm_scenario_intents(id TEXT,scenario_id TEXT,payload TEXT);
        CREATE TABLE llm_scenario_children(link_id TEXT,intent_id TEXT,scenario_id TEXT,kind TEXT,
            request TEXT,status TEXT,order_id TEXT,evidence TEXT,created_at REAL);
        CREATE TABLE llm_scenario_broker_notices(event_id TEXT PRIMARY KEY,scenario_id TEXT,body TEXT);
        CREATE TABLE llm_scenario_outbox(event_id TEXT PRIMARY KEY,kind TEXT,body TEXT,status TEXT,message_id INTEGER);
        """)
        conn.execute("INSERT INTO btc_meta VALUES('demo','shared_entry_policy_v1',?)",
                     (json.dumps(dict(main_uid="123", swing_uid="456")),))
        conn.execute("INSERT INTO llm_scenario_control VALUES(1,?)",
                     (json.dumps(dict(version=1, state="active", main_uid="123")),))
        conn.execute("INSERT INTO llm_scenario_state VALUES(1,?)", (json.dumps(dict(active=dict(scenario_id="new-active"))),))
        conn.execute("INSERT INTO llm_scenario_intents VALUES('open','old',?)",
                     (json.dumps(dict(action_id="open", scenario_id="old", action="OPEN", side="LONG")),))
        for kind, side, price, at in (("entry", "Buy", "100", "10000"), ("tp", "Sell", "110", "20000")):
            request = dict(side=side, qty="1", symbol="BTCUSDT", orderLinkId=kind, reduceOnly=kind != "entry")
            order = dict(request, orderId=kind, cumExecQty="1", orderStatus="Filled")
            fill = dict(execId=kind, orderId=kind, side=side, symbol="BTCUSDT", execType="Trade",
                        execQty="1", execPrice=price, execTime=at, execFee=".1")
            proof = dict(order=order, executions=[fill])
            conn.execute("INSERT INTO llm_scenario_children VALUES(?,?,?,?,?,?,?,?,?)",
                         (kind, "open", "old", kind, json.dumps(request), "TERMINAL", kind, json.dumps(proof), 1))
        settlement = dict(scenario_id="old", flat_confirmed=True, orders_terminal=True,
                          executions_complete=True, fees_complete=True, funding_complete=True,
                          execution_ids=["entry", "tp"], no_fills_confirmed=False,
                          gross_pnl=10, fees=.2, funding_net=-.5, net_pnl=9.3)
        conn.execute("INSERT INTO llm_scenario_settlements VALUES('old',?)", (json.dumps(settlement),))
    return path


def mutate(path, table, column, transform):
    with sqlite3.connect(path) as conn:
        raw = conn.execute(f"SELECT {column} FROM {table} LIMIT 1").fetchone()[0]
        value = json.loads(raw)
        transform(value)
        conn.execute(f"UPDATE {table} SET {column}=? WHERE rowid=1", (json.dumps(value),))


def test_dry_run_unchanged_and_new_active_not_borrowed(tmp_path):
    path = database(tmp_path)
    before = path.read_bytes()
    result = tool.recover(path, "old", now=30)
    assert result["status"] == "DRY_RUN_ELIGIBLE" and not result["sent"]
    assert "지연 안내" in result["preview"] and "새 진입·추가 주문이 아닙니다" in result["preview"]
    assert "9.30" in result["preview"] and "new-active" not in str(result)
    assert path.read_bytes() == before
    assert not Path(str(path) + ".btc-execution.lock").exists()


def test_apply_only_notice_tables_then_skip(tmp_path):
    path = database(tmp_path)
    with sqlite3.connect(path) as conn:
        protected = {table: conn.execute(f"SELECT * FROM {table}").fetchall() for table in
                     ("llm_scenario_state", "llm_scenario_settlements", "llm_scenario_children", "llm_scenario_intents")}
    assert tool.recover(path, "old", apply=True, now=30)["status"] == "QUEUED_NOT_SENT"
    with sqlite3.connect(path) as conn:
        assert conn.execute("SELECT status FROM llm_scenario_outbox").fetchone() == ("QUEUED",)
        for table, rows in protected.items():
            assert conn.execute(f"SELECT * FROM {table}").fetchall() == rows
    assert tool.recover(path, "old", apply=True, now=30)["status"] == "SKIPPED_EXISTING_NOTICE"


@pytest.mark.parametrize("status", ["QUEUED", "SENDING", "UNKNOWN", "SENT"])
def test_outbox_any_state_never_resends(tmp_path, status):
    path = database(tmp_path)
    with sqlite3.connect(path) as conn:
        conn.execute("INSERT INTO llm_scenario_outbox VALUES('scenario-closed-old','CLOSED','prior',?,NULL)", (status,))
    before = path.read_bytes()
    assert tool.recover(path, "old", now=30)["status"] == "SKIPPED_EXISTING_NOTICE"
    assert path.read_bytes() == before


@pytest.mark.parametrize("mutation", ["unfilled", "missingfees", "net", "execution_id", "duplicate", "time", "quantity", "binding", "terminal"])
def test_corrupt_or_insufficient_proof_rejected(tmp_path, mutation):
    path = database(tmp_path)
    if mutation in {"unfilled", "net", "execution_id"}:
        key, value = {"unfilled": ("no_fills_confirmed", True), "net": ("net_pnl", 100),
                      "execution_id": ("execution_ids", ["entry", "other"])}[mutation]
        mutate(path, "llm_scenario_settlements", "evidence", lambda row: row.update({key: value}))
    elif mutation == "binding":
        mutate(path, "llm_scenario_control", "body", lambda row: row.update(main_uid="456"))
    else:
        def change(row):
            if mutation == "missingfees":
                row["executions"][0].pop("execFee")
            elif mutation == "duplicate":
                row["executions"].append(dict(row["executions"][0]))
            elif mutation == "time":
                row["executions"][0]["execTime"] = "99000"
            elif mutation == "quantity":
                row["order"]["cumExecQty"] = "2"
            else:
                row["order"]["orderStatus"] = "New"
        mutate(path, "llm_scenario_children", "evidence", change)
    before = path.read_bytes()
    with pytest.raises((ValueError, KeyError)):
        tool.recover(path, "old", apply=True, now=30)
    assert path.read_bytes() == before


def test_second_insert_failure_rolls_back_first(tmp_path):
    path = database(tmp_path)
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TRIGGER reject_notice BEFORE INSERT ON llm_scenario_outbox BEGIN SELECT RAISE(ABORT,'fixture'); END")
    with pytest.raises(sqlite3.IntegrityError):
        tool.recover(path, "old", apply=True, now=30)
    with sqlite3.connect(path) as conn:
        assert conn.execute("SELECT count(*) FROM llm_scenario_broker_notices").fetchone()[0] == 0


def test_explicit_scenario_no_all_history_recovery(tmp_path):
    path = database(tmp_path)
    with pytest.raises(ValueError):
        tool.recover(path, "", now=30)
    with pytest.raises(ValueError, match="persisted_settlement"):
        tool.recover(path, "missing", now=30)


def test_native_retained_after_execution_is_not_creation_time(tmp_path):
    path = database(tmp_path)
    with sqlite3.connect(path) as conn:
        conn.execute("UPDATE llm_scenario_children SET kind='native_sl',created_at=25 WHERE kind='tp'")
    assert tool.recover(path, "old", now=30)["status"] == "DRY_RUN_ELIGIBLE"


def test_broker_only_existing_notice_skips(tmp_path):
    path = database(tmp_path)
    with sqlite3.connect(path) as conn:
        conn.execute("INSERT INTO llm_scenario_broker_notices VALUES('scenario-closed-old','old','{}')")
    assert tool.recover(path, "old", apply=True, now=30)["status"] == "SKIPPED_EXISTING_NOTICE"
