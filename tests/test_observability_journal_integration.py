from __future__ import annotations

import importlib.util
import json
import sqlite3
from pathlib import Path

import pytest

from tracking import journal as kr_journal

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _load_us_journal():
    spec = importlib.util.spec_from_file_location(
        "test_observability_us_journal",
        PROJECT_ROOT / "prism-us" / "tracking" / "journal.py",
    )
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def _database(market: str):
    connection = sqlite3.connect(":memory:")
    cursor = connection.cursor()
    cursor.execute(
        """CREATE TABLE trading_journal (
               ticker TEXT, market TEXT, trade_date TEXT,
               profit_rate REAL, buy_scenario TEXT
           )"""
    )
    if market == "KR":
        cursor.execute(
            """CREATE TABLE analysis_performance_tracker (
                   trigger_type TEXT, was_traded INTEGER,
                   tracked_7d_return REAL, tracked_14d_return REAL,
                   tracked_30d_return REAL
               )"""
        )
        cursor.execute(
            """CREATE TABLE trading_history (
                   id INTEGER PRIMARY KEY, trigger_type TEXT,
                   profit_rate REAL, sell_date TEXT
               )"""
        )
        candidate_table = "analysis_performance_tracker"
        history_table = "trading_history"
        return_columns = "tracked_7d_return, tracked_14d_return, tracked_30d_return"
    else:
        cursor.execute(
            """CREATE TABLE us_analysis_performance_tracker (
                   trigger_type TEXT, was_traded INTEGER,
                   return_7d REAL, return_14d REAL, return_30d REAL
               )"""
        )
        cursor.execute(
            """CREATE TABLE us_trading_history (
                   id INTEGER PRIMARY KEY, trigger_type TEXT,
                   profit_rate REAL, sell_date TEXT
               )"""
        )
        candidate_table = "us_analysis_performance_tracker"
        history_table = "us_trading_history"
        return_columns = "return_7d, return_14d, return_30d"

    cursor.execute(
        f"INSERT INTO {candidate_table} (trigger_type, was_traded, {return_columns}) "
        "VALUES ('Gap', 0, 0.01, 0.02, 0.03)"
    )
    for row_id, profit in enumerate((-6.0, -5.0, -4.0, -3.0, 2.0), 1):
        cursor.execute(
            f"INSERT INTO {history_table} (id, trigger_type, profit_rate, sell_date) "
            "VALUES (?, 'Gap', ?, '2026-08-01')",
            (row_id, profit),
        )
    connection.commit()
    return connection


@pytest.mark.parametrize("market", ["KR", "US"])
def test_journal_score_path_emits_fail_open_observability_event(
    tmp_path,
    monkeypatch,
    market,
):
    spool = tmp_path / f"{market.lower()}-events.jsonl"
    monkeypatch.setenv("PRISM_OBSERVABILITY_SPOOL", str(spool))
    monkeypatch.setenv("TRIGGER_PERFORMANCE_FEEDBACK", "shadow")
    connection = _database(market)
    try:
        if market == "KR":
            monkeypatch.setattr(kr_journal, "JOURNAL_RECENT_LOSS_PENALTY", 0)
            manager = kr_journal.JournalManager(
                connection.cursor(), connection, enable_journal=True
            )
        else:
            module = _load_us_journal()
            monkeypatch.setattr(module, "JOURNAL_RECENT_LOSS_PENALTY", 0)
            manager = module.USJournalManager(
                connection.cursor(), connection, enable_journal=True
            )

        adjustment, reasons = manager.get_score_adjustment("TEST", trigger_type="Gap")
        event = json.loads(spool.read_text(encoding="utf-8").strip())

        assert adjustment == 0
        assert reasons == []
        assert event["event_type"] == "trigger.performance_feedback"
        assert event["market"] == market
        assert event["ticker"] == "TEST"
        assert event["attributes"]["mode"] == "shadow"
        assert event["attributes"]["would_adjust"] == -1
        assert event["attributes"]["applied_adjust"] == 0
    finally:
        connection.close()
