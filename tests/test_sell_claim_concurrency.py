"""Dependency-light concurrency gate for the real KR/US sell_stock methods.

The full agent modules pull in LLM and broker dependencies that the lightweight
CI job intentionally does not install.  These tests compile the actual method
ASTs from both agent sources, then exercise them with two SQLite connections.
"""

from __future__ import annotations

import ast
import asyncio
import copy
import logging
import sqlite3
import threading
import traceback
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, Optional

import pytest


PROJECT_ROOT = Path(__file__).parent.parent


def _position_lookup(table):
    def lookup(cursor, ticker, account_key=None):
        cursor.execute(
            f"SELECT buy_price FROM {table} WHERE ticker = ? AND account_key = ?",
            (ticker, account_key),
        )
        prices = [float(row[0]) for row in cursor.fetchall()]
        return {
            "row_count": len(prices),
            "avg_buy_price": sum(prices) / len(prices) if prices else 0.0,
        }

    return lookup


@lru_cache
def _load_real_sell_method(market):
    if market == "KR":
        path = PROJECT_ROOT / "stock_tracking_agent.py"
        class_name = "StockTrackingAgent"
        lookup_name = "get_existing_position_for_ticker"
        lookup = _position_lookup("stock_holdings")
    else:
        path = PROJECT_ROOT / "prism-us" / "us_stock_tracking_agent.py"
        class_name = "USStockTrackingAgent"
        lookup_name = "get_us_existing_position_for_ticker"
        lookup = _position_lookup("us_stock_holdings")

    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(path))
    method = None
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == class_name:
            method = next(
                child
                for child in node.body
                if isinstance(child, ast.AsyncFunctionDef) and child.name == "sell_stock"
            )
            break
    assert method is not None

    method = copy.deepcopy(method)
    method.decorator_list = []
    module = ast.Module(body=[method], type_ignores=[])
    ast.fix_missing_locations(module)
    namespace = {
        "Any": Any,
        "Dict": Dict,
        "Optional": Optional,
        "datetime": datetime,
        "logger": logging.getLogger(f"sell-claim-{market.lower()}"),
        "traceback": traceback,
        lookup_name: lookup,
    }
    exec(compile(module, str(path), "exec"), namespace)
    return namespace["sell_stock"]


def _create_schema(conn, market):
    prefix = "us_" if market == "US" else ""
    conn.execute(
        f"""CREATE TABLE {prefix}stock_holdings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            account_key TEXT NOT NULL, account_name TEXT,
            ticker TEXT NOT NULL, company_name TEXT NOT NULL,
            buy_price REAL NOT NULL, buy_date TEXT NOT NULL,
            current_price REAL, scenario TEXT, trigger_type TEXT,
            trigger_mode TEXT, sector TEXT
        )"""
    )
    conn.execute(
        f"""CREATE TABLE {prefix}trading_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            account_key TEXT NOT NULL, account_name TEXT,
            ticker TEXT NOT NULL, company_name TEXT NOT NULL,
            buy_price REAL NOT NULL, buy_date TEXT NOT NULL,
            sell_price REAL NOT NULL, sell_date TEXT NOT NULL,
            profit_rate REAL NOT NULL, holding_days INTEGER NOT NULL,
            scenario TEXT, trigger_type TEXT, trigger_mode TEXT,
            sector TEXT, exit_kind TEXT
        )"""
    )


@pytest.mark.parametrize("market", ["KR", "US"])
@pytest.mark.parametrize("journal_mode", ["DELETE", "WAL"])
@pytest.mark.parametrize("pyramiding", [False, True], ids=["single", "pyramiding"])
def test_real_sell_stock_claim_is_atomic(tmp_path, market, journal_mode, pyramiding):
    db_path = tmp_path / f"{market.lower()}-{journal_mode.lower()}-claim.sqlite"
    setup = sqlite3.connect(db_path)
    actual_mode = setup.execute(f"PRAGMA journal_mode={journal_mode}").fetchone()[0]
    assert actual_mode.upper() == journal_mode
    _create_schema(setup, market)

    holdings = "us_stock_holdings" if market == "US" else "stock_holdings"
    history = "us_trading_history" if market == "US" else "trading_history"
    ticker = "AAPL" if market == "US" else "005930"
    company = "Apple" if market == "US" else "Samsung"
    target_price = 180.0 if market == "US" else 70000.0
    remaining_price = 175.0 if market == "US" else 68000.0
    target_row_id = setup.execute(
        f"""INSERT INTO {holdings}
            (account_key, account_name, ticker, company_name, buy_price, buy_date)
            VALUES (?, ?, ?, ?, ?, '2026-07-01 09:00:00')""",
        ("ACC1", "primary", ticker, company, target_price),
    ).lastrowid
    if pyramiding:
        setup.execute(
            f"""INSERT INTO {holdings}
                (account_key, account_name, ticker, company_name, buy_price, buy_date)
                VALUES (?, ?, ?, ?, ?, '2026-06-15 09:00:00')""",
            ("ACC1", "primary", ticker, company, remaining_price),
        )
    setup.commit()
    setup.close()

    sell_stock = _load_real_sell_method(market)
    barrier = threading.Barrier(2)

    stock = {
        "id": target_row_id,
        "ticker": ticker,
        "company_name": company,
        "buy_price": target_price,
        "buy_date": "2026-07-01 09:00:00",
        "current_price": target_price + 5,
        "account_key": "ACC1",
        "account_name": "primary",
        "sector": "Technology",
    }

    def run_competitor():
        conn = sqlite3.connect(db_path, timeout=1, check_same_thread=False)
        conn.execute("PRAGMA busy_timeout=1000")
        agent = type("SellClaimAgent", (), {})()
        agent.conn = conn
        agent.cursor = conn.cursor()
        agent.message_queue = []
        agent._msg_types = []
        agent._account_scope = lambda: ("ACC1", "primary")
        agent._get_trigger_win_rate = lambda _trigger: ""
        # This dependency-light fixture compiles only sell_stock's AST. The real
        # agents provide this helper; shadow-ledger behavior is covered by the
        # full caller tests in the KR/US process-report suites.
        agent._mirror_position_closed = lambda **_kwargs: True
        agent.enable_journal = False
        agent.journal_manager = None

        async def create_journal(**_kwargs):
            return True

        agent._create_journal_entry = create_journal

        async def exercise():
            barrier.wait(timeout=5)
            sold = await sell_stock(agent, dict(stock), "concurrency regression")
            return sold, len(agent.message_queue)

        try:
            return asyncio.run(exercise())
        finally:
            conn.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _index: run_competitor(), range(2)))

    verify = sqlite3.connect(db_path)
    history_count = verify.execute(f"SELECT COUNT(*) FROM {history}").fetchone()[0]
    remaining_prices = verify.execute(
        f"SELECT buy_price FROM {holdings} ORDER BY id"
    ).fetchall()
    verify.close()

    assert sorted(sold for sold, _messages in results) == [False, True]
    assert sum(messages for _sold, messages in results) == 1
    assert history_count == 1
    assert remaining_prices == ([(remaining_price,)] if pyramiding else [])
