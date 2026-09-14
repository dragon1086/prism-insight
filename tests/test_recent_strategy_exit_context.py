import sqlite3
import ast
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from observability.recent_exit_context import build_recent_exit_facts


def database():
    connection = sqlite3.connect(":memory:")
    connection.execute("CREATE TABLE trading_history (ticker TEXT, account_key TEXT, sell_date TEXT, exit_kind TEXT)")
    return connection


def test_no_strategy_history_is_explicit_not_missing_input():
    connection = database()
    facts = build_recent_exit_facts(connection.cursor(), "161890", market="KR", account_key="strategy", now=datetime(2026, 9, 14, 10))
    assert facts["status"] == "NO_HISTORY"
    assert facts["stop_exit_day_count_14_calendar_days"] == 0
    assert facts["latest_exit_at"] is None


def test_same_scope_stop_episodes_not_pyramid_rows_or_loss_sign():
    connection = database()
    connection.executemany("INSERT INTO trading_history VALUES (?,?,?,?)", [
        ("161890", "strategy", "2026-09-12 10:00:00", "stop"),
        ("161890", "strategy", "2026-09-12 10:00:00", "stop"),
        ("161890", "strategy", "2026-09-10 10:00:00", "trend_exit"),
        ("161890", "strategy", "2026-09-02 10:00:00", "stop"),
        ("161890", "other", "2026-09-11 10:00:00", "stop"),
        ("161890", "strategy", "2026-08-01 10:00:00", "stop"),
    ])
    facts = build_recent_exit_facts(connection.cursor(), "161890", market="KR", account_key="strategy", now=datetime(2026, 9, 14, 10))
    assert facts["status"] == "OK"
    assert facts["stop_exit_day_count_14_calendar_days"] == 2
    assert facts["exit_day_count_14_calendar_days"] == 3
    assert "other" not in str(facts)
    assert facts["basis"] == "strategy_history_not_broker_fills"


def test_unavailable_scope_or_table_does_not_claim_zero():
    connection = database()
    for market, scope in [("KR", None), ("US", "strategy")]:
        facts = build_recent_exit_facts(connection.cursor(), "161890", market=market, account_key=scope)
        assert facts["status"] == "SOURCE_UNAVAILABLE"
        assert facts["stop_exit_day_count_14_calendar_days"] is None


def test_unknown_exit_kind_or_bad_clock_cannot_prove_zero_stops():
    connection = database()
    connection.execute("INSERT INTO trading_history VALUES ('161890','strategy','2026-09-12 10:00:00',NULL)")
    facts = build_recent_exit_facts(connection.cursor(), "161890", market="KR", account_key="strategy", now=datetime(2026, 9, 14, 10))
    assert facts["stop_exit_day_count_14_calendar_days"] is None
    assert facts["confirmed_stop_exit_day_count_14_calendar_days"] == 0
    connection.execute("INSERT INTO trading_history VALUES ('161890','strategy','invalid','stop')")
    facts = build_recent_exit_facts(connection.cursor(), "161890", market="KR", account_key="strategy", now=datetime(2026, 9, 14, 10))
    assert facts["status"] == "SOURCE_UNAVAILABLE"


def test_us_uses_us_strategy_history_and_old_exit_is_not_no_history():
    connection = database()
    connection.execute("CREATE TABLE us_trading_history (ticker TEXT, account_key TEXT, sell_date TEXT, exit_kind TEXT)")
    connection.execute("INSERT INTO us_trading_history VALUES ('HPE','strategy','2026-08-01 10:00:00','stop')")
    facts = build_recent_exit_facts(connection.cursor(), "HPE", market="US", account_key="strategy", now=datetime(2026, 9, 14, 10))
    assert facts["status"] == "OK"
    assert facts["source"] == "us_trading_history"
    assert facts["stop_exit_day_count_14_calendar_days"] == 0
    assert facts["latest_exit_at"].startswith("2026-08-01")


def test_future_exit_makes_history_unavailable_not_zero():
    connection = database()
    connection.execute("INSERT INTO trading_history VALUES ('161890','strategy','2026-09-15 10:00:00','stop')")
    facts = build_recent_exit_facts(connection.cursor(), "161890", market="KR", account_key="strategy", now=datetime(2026, 9, 14, 10))
    assert facts["status"] == "SOURCE_UNAVAILABLE"
    assert facts["latest_exit_at"] is None


@pytest.mark.parametrize("market,path,method", [
    ("KR", "stock_tracking_agent.py", "_get_relevant_journal_context"),
    ("US", "prism-us/us_stock_tracking_agent.py", "get_journal_context"),
])
def test_real_agent_journal_wrapper_injects_facts_and_preserves_isolation(market, path, method):
    root = Path(__file__).resolve().parents[1]
    tree = ast.parse((root / path).read_text())
    node = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == method)
    node.returns = None
    for arg in node.args.args:
        arg.annotation = None
    ns = {
        "effects_for": lambda *_: SimpleNamespace(journal=lambda _: {"context": "frozen isolated journal"}),
        "logger": SimpleNamespace(warning=lambda *a: None),
    }
    exec(compile(ast.Module(body=[node], type_ignores=[]), path, "exec"), ns)
    connection = database()
    if market == "US":
        connection.execute("CREATE TABLE us_trading_history (ticker TEXT, account_key TEXT, sell_date TEXT, exit_kind TEXT)")
    agent = SimpleNamespace(
        enable_journal=True, cursor=connection.cursor(), _account_scope=lambda: ("strategy", "private"),
        journal_manager=SimpleNamespace(get_context_for_ticker=lambda *a, **kw: "Narrative lessons exist"),
    )
    result = ns[method](agent, "161890")
    assert "SAME_TICKER_STRATEGY_EXIT_FACTS" in result
    assert '"status": "NO_HISTORY"' in result
    assert "Narrative lessons exist" in result
    def failing_lessons(*args, **kwargs):
        raise RuntimeError("optional journal unavailable")
    agent.journal_manager.get_context_for_ticker = failing_lessons
    result = ns[method](agent, "161890")
    assert '"status": "NO_HISTORY"' in result
    agent._no_order_effects = object()
    agent.cursor = None
    assert ns[method](agent, "161890") == "frozen isolated journal"


def test_disabled_journal_does_not_query_history():
    from observability.recent_exit_context import append_recent_exit_context
    assert append_recent_exit_context(SimpleNamespace(enable_journal=False), "161890", "", market="KR") == ""


def test_unverified_legacy_scope_is_unavailable_not_no_history():
    connection = sqlite3.connect(":memory:")
    connection.execute("CREATE TABLE trading_history (ticker TEXT, sell_date TEXT, exit_kind TEXT)")
    facts = build_recent_exit_facts(connection.cursor(), "161890", market="KR", account_key="strategy")
    assert facts["status"] == "SOURCE_UNAVAILABLE"
    assert facts["stop_exit_day_count_14_calendar_days"] is None


def test_two_different_stop_timestamps_same_day_are_only_one_stop_day():
    connection = database()
    connection.executemany("INSERT INTO trading_history VALUES (?,?,?,?)", [
        ("161890", "strategy", "2026-09-12 10:00:00", "stop"),
        ("161890", "strategy", "2026-09-12 14:00:00", "stop"),
    ])
    facts = build_recent_exit_facts(connection.cursor(), "161890", market="KR", account_key="strategy", now=datetime(2026, 9, 14, 10))
    assert facts["confirmed_stop_exit_day_count_14_calendar_days"] == 1
    assert "does NOT prove fewer than two stop episodes" in facts["limitation"]
