"""No broker imports: exercise real KR/US helpers and actual validator AST."""
import ast
import importlib.util
import json
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]


def load(path):
    spec = importlib.util.spec_from_file_location("isolated_" + path.replace("/", "_"), ROOT / path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_kr_table_identifier_cannot_enter_query():
    module = load("tracking/helpers.py")
    class DenyCursor:
        def execute(self, *args):
            pytest.fail("unapproved table must not execute")
    result = module.get_existing_position_for_ticker(
        DenyCursor(), "TEST", table_name="stock_holdings; DROP TABLE stock_holdings")
    assert result["pyramid_ownership"] == "UNKNOWN"


@pytest.fixture(params=["KR", "US"])
def context(request):
    market = request.param
    module = load("tracking/helpers.py" if market == "KR" else "prism-us/tracking/db_schema.py")
    table = "stock_holdings" if market == "KR" else "us_stock_holdings"
    getter = module.get_existing_position_for_ticker if market == "KR" else module.get_us_existing_position_for_ticker
    gate = module.evaluate_pyramid_add_gate if market == "KR" else module.evaluate_us_pyramid_add_gate
    db = sqlite3.connect(":memory:")
    db.execute(f"CREATE TABLE {table} (ticker TEXT, account_key TEXT, buy_price REAL, scenario TEXT)")
    yield market, module, db, table, getter, gate
    db.close()


@pytest.mark.parametrize("scenario", [
    {"regime_entry_policy": {"mode": "rebound_pilot", "position_fraction": .5}},
    {"regime_entry_policy": {"position_fraction": "0.5"}},
    {"split_owner": "split-pilot-v1"}, {"pilot": {"owner": "split-pilot-v1"}},
])
def test_stored_pilot_or_owner_blocks_normal_pyramid(context, scenario):
    market, module, db, table, getter, gate = context
    db.execute(f"INSERT INTO {table} VALUES (?,?,?,?)", ("TEST", "mine", 100, json.dumps(scenario)))
    summary = getter(db.cursor(), "TEST", account_key="mine")
    assert summary["pyramid_ownership"] == "SPLIT_PILOT"
    assert gate("strong_bull", 100, 150, 1, ownership=summary["pyramid_ownership"]) == (False, "LEGACY_PYRAMID_BLOCKED_SPLIT_PILOT")
    if market == "KR":
        assert not module.pyramid_add_possible_ignoring_regime(100, 150, 1, ownership=summary["pyramid_ownership"])[0]


@pytest.mark.parametrize("scenario", [None, json.dumps({"regime_entry_policy": {"mode": "normal", "position_fraction": 1}})])
def test_account_scope_and_ordinary_legacy_remain_unchanged(context, scenario):
    _, _, db, table, getter, gate = context
    db.execute(f"INSERT INTO {table} VALUES (?,?,?,?)", ("TEST", "other", 100, json.dumps({"split_owner": "split-pilot-v1"})))
    db.execute(f"INSERT INTO {table} VALUES (?,?,?,?)", ("TEST", "mine", 100, scenario))
    summary = getter(db.cursor(), "TEST", account_key="mine")
    assert summary["row_count"] == 1 and summary["avg_buy_price"] == 100
    assert gate("strong_bull", 100, 110, 1, ownership=summary["pyramid_ownership"])[0]
    assert not gate("sideways", 100, 110, 1, ownership=summary["pyramid_ownership"])[0]


@pytest.mark.parametrize("raw", [
    "{malformed", json.dumps({"regime_entry_policy": []}), json.dumps({"pilot": False}),
    *[json.dumps({"regime_entry_policy": {"mode": "normal", "position_fraction": fraction}}) for fraction in [.25, 0, -1, 2, None, True]],
    json.dumps({"regime_entry_policy": {"mode": "unknown", "position_fraction": 1}}),
    json.dumps({"split_owner": "unknown"}), json.dumps({"split_owner": None}),
    json.dumps({"pilot": {"state": "WAIT"}}),
])
def test_malformed_present_metadata_fails_closed(context, raw):
    _, _, db, table, getter, gate = context
    db.execute(f"INSERT INTO {table} VALUES (?,?,?,?)", ("TEST", "mine", 100, raw))
    summary = getter(db.cursor(), "TEST", account_key="mine")
    assert summary["row_count"] == 1
    assert not gate("strong_bull", 100, 110, 1, ownership=summary["pyramid_ownership"])[0]


def test_actual_broker_validator_rechecks_latest_owner_and_bound_account(context):
    market, _, db, table, getter, _ = context
    path = "stock_tracking_agent.py" if market == "KR" else "prism-us/us_stock_tracking_agent.py"
    parsed = ast.parse((ROOT / path).read_text())
    names = {"_assert_legacy_pyramid_allowed", "_buy_quote_validator"}
    methods = [node for node in ast.walk(parsed) if isinstance(node, ast.FunctionDef) and node.name in names]
    namespace = {"apply_buy_scenario_contract": lambda scenario, **kw: scenario,
                 "get_existing_position_for_ticker" if market == "KR" else "get_us_existing_position_for_ticker": getter}
    exec(compile(ast.fix_missing_locations(ast.Module(body=methods, type_ignores=[])), path, "exec"), namespace)
    account = ["mine"]
    host = SimpleNamespace(conn=db, _account_scope=lambda: (account[0], "test"),
                           _evaluate_production_buy_gate=lambda *a, **kw: {"allowed": True})
    host._assert_legacy_pyramid_allowed = lambda *a, **kw: namespace["_assert_legacy_pyramid_allowed"](host, *a, **kw)
    db.execute(f"INSERT INTO {table} VALUES (?,?,?,?)", ("TEST", "mine", 100, None))
    # Simulate an ExecutionService __aenter__ await changing active scope before
    # the callback is even constructed. The immutable order account wins.
    account[0] = "other"
    validate = namespace["_buy_quote_validator"](host, {}, is_add=True, ticker="TEST", account_key="mine")
    validate(110)
    # Mutate stored ownership after preflight/validator creation; broker callback
    # must read again, and account switching must not redirect that read.
    db.execute(f"UPDATE {table} SET scenario=? WHERE account_key=?", (json.dumps({"split_owner": "split-pilot-v1"}), "mine"))
    account[0] = "other"
    with pytest.raises(ValueError, match="LEGACY_PYRAMID_BLOCKED_SPLIT_PILOT"):
        validate(110)


@pytest.mark.asyncio
async def test_insert_boundary_owner_guard_runs_under_existing_lock(context):
    import asyncio

    market, _, db, table, getter, _ = context
    path = "stock_tracking_agent.py" if market == "KR" else "prism-us/us_stock_tracking_agent.py"
    parsed = ast.parse((ROOT / path).read_text())
    method = next(n for n in ast.walk(parsed) if isinstance(n, ast.AsyncFunctionDef) and n.name == "_buy_stock_with_position")
    locked = next(n for n in ast.walk(method) if isinstance(n, ast.AsyncWith) and "_get_db_lock" in ast.unparse(n.items[0].context_expr))
    assert "_assert_legacy_pyramid_allowed" in ast.unparse(locked.body[0])
    assert not any(isinstance(n, ast.Await) for n in ast.walk(locked))
    lock = asyncio.Lock()
    calls = []

    def check(ticker, **kwargs):
        assert lock.locked()
        assert kwargs["account_key"] == "mine"
        calls.append(ticker)
        ownership = getter(db.cursor(), ticker, account_key="mine")["pyramid_ownership"]
        if ownership == "SPLIT_PILOT":
            raise ValueError("LEGACY_PYRAMID_BLOCKED_SPLIT_PILOT")

    host = SimpleNamespace(_get_db_lock=lambda: lock, _assert_legacy_pyramid_allowed=check)
    # Execute the actual guard statement inside its actual async lock boundary,
    # truncating before the real INSERT (no trading imports or fixture brokers).
    locked.body = locked.body[:1]
    harness = ast.AsyncFunctionDef(name="run", args=ast.arguments(posonlyargs=[], args=[], kwonlyargs=[], kw_defaults=[], defaults=[]), body=[locked], decorator_list=[])
    namespace = {"self": host, "ticker": "TEST", "is_add": True, "account_key": "mine"}
    exec(compile(ast.fix_missing_locations(ast.Module(body=[harness], type_ignores=[])), path, "exec"), namespace)
    db.execute(f"INSERT INTO {table} VALUES (?,?,?,?)", ("TEST", "mine", 100, json.dumps({"split_owner": "split-pilot-v1"})))
    with pytest.raises(ValueError, match="BLOCKED_SPLIT_PILOT"):
        await namespace["run"]()
    assert calls == ["TEST"] and not lock.locked()


def test_all_quote_validator_calls_and_pyramid_gates_wire_ownership():
    for path in ("stock_tracking_agent.py", "stock_tracking_enhanced_agent.py", "prism-us/us_stock_tracking_agent.py"):
        parsed = ast.parse((ROOT / path).read_text())
        for node in ast.walk(parsed):
            if isinstance(node, ast.Call):
                if isinstance(node.func, ast.Attribute) and node.func.attr == "_buy_quote_validator":
                    assert "ticker" in {keyword.arg for keyword in node.keywords}, path
                    bound = next(keyword.value for keyword in node.keywords if keyword.arg == "account_key")
                    assert ast.unparse(bound) in {"order_intent.account_id", "prepared.account_id"}, path
                if isinstance(node.func, ast.Name) and node.func.id in {"evaluate_pyramid_add_gate", "evaluate_us_pyramid_add_gate", "pyramid_add_possible_ignoring_regime"}:
                    assert "ownership" in {keyword.arg for keyword in node.keywords}, path
