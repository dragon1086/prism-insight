from __future__ import annotations

import importlib.util
import sqlite3
from pathlib import Path

import pytest

_SPEC = importlib.util.spec_from_file_location(
    "test_performance_feedback_module",
    Path(__file__).resolve().parents[1] / "tracking" / "performance_feedback.py",
)
_MODULE = importlib.util.module_from_spec(_SPEC)
assert _SPEC and _SPEC.loader
_SPEC.loader.exec_module(_MODULE)
feedback_log_payload = _MODULE.feedback_log_payload
format_trigger_feedback = _MODULE.format_trigger_feedback
get_trigger_feedback = _MODULE.get_trigger_feedback
resolve_actual_adjustment = _MODULE.resolve_actual_adjustment


@pytest.fixture
def db():
    connection = sqlite3.connect(":memory:")
    cursor = connection.cursor()
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
    yield connection, cursor
    connection.close()


def _seed(cursor, market: str):
    candidate_table = (
        "analysis_performance_tracker" if market == "KR" else "us_analysis_performance_tracker"
    )
    returns = (
        "tracked_7d_return, tracked_14d_return, tracked_30d_return"
        if market == "KR"
        else "return_7d, return_14d, return_30d"
    )
    actual_table = "trading_history" if market == "KR" else "us_trading_history"
    for value in (0.10, 0.05, -0.02, 0.07):
        cursor.execute(
            f"INSERT INTO {candidate_table} (trigger_type, was_traded, {returns}) "
            "VALUES ('Gap', 0, ?, ?, ?)",
            (value / 2, value * 0.75, value),
        )
    # This tracker row claims traded but must not contaminate Candidate or Actual.
    cursor.execute(
        f"INSERT INTO {candidate_table} (trigger_type, was_traded, {returns}) "
        "VALUES ('Gap', 1, 9, 9, 9)"
    )
    for row_id, value in enumerate((-6.0, -5.0, -4.0, -3.0, 2.0), 1):
        cursor.execute(
            f"INSERT INTO {actual_table} (id, trigger_type, profit_rate, sell_date) "
            "VALUES (?, 'Gap', ?, '2026-01-01')",
            (row_id, value),
        )


@pytest.mark.parametrize("market", ["KR", "US"])
def test_candidate_and_actual_stats_are_separate(db, market):
    connection, cursor = db
    _seed(cursor, market)
    connection.commit()
    feedback = get_trigger_feedback(cursor, market, "Gap")
    candidate = feedback["candidate_trigger"]
    actual = feedback["actual_trigger"]

    assert candidate["source"] == "candidate_tracker"
    assert candidate["n"] == 4
    assert candidate["positive_rate_30d"] == pytest.approx(0.75)
    assert candidate["avg_30d_pct"] == pytest.approx(5.0)
    assert actual["source"] == "trading_history"
    assert actual["n"] == 5
    assert actual["win_rate"] == pytest.approx(0.2)
    assert actual["avg_return_pct"] == pytest.approx(-3.2)


def test_shadow_logs_actual_penalty_without_applying_it(db):
    connection, cursor = db
    _seed(cursor, "US")
    connection.commit()
    feedback = get_trigger_feedback(cursor, "US", "Gap")
    adjustment = resolve_actual_adjustment(feedback, mode="shadow")
    payload = feedback_log_payload(
        feedback,
        adjustment,
        ticker="AAPL",
        sector="Technology",
    )

    assert payload["schema_version"] == 1
    assert payload["ticker"] == "AAPL"
    assert payload["sector"] == "Technology"
    assert adjustment["would_adjust"] == -1
    assert adjustment["applied_adjust"] == 0
    assert payload["candidate"]["n"] == 4
    assert payload["actual"]["n"] == 5


def test_actual_mode_applies_penalty_and_candidate_rows_do_not_change_it(db):
    connection, cursor = db
    _seed(cursor, "KR")
    cursor.execute(
        """INSERT INTO analysis_performance_tracker
           (trigger_type, was_traded, tracked_7d_return, tracked_14d_return, tracked_30d_return)
           VALUES ('Gap', 0, 100, 100, 100)"""
    )
    connection.commit()
    feedback = get_trigger_feedback(cursor, "KR", "Gap")
    adjustment = resolve_actual_adjustment(feedback, mode="actual")
    assert adjustment["would_adjust"] == -1
    assert adjustment["applied_adjust"] == -1


def test_insufficient_actual_samples_never_adjust(db):
    connection, cursor = db
    for row_id, value in enumerate((-6.0, -5.0, -4.0, -3.0), 1):
        cursor.execute(
            "INSERT INTO us_trading_history (id, trigger_type, profit_rate, sell_date) "
            "VALUES (?, 'Gap', ?, '2026-01-01')",
            (row_id, value),
        )
    connection.commit()
    feedback = get_trigger_feedback(cursor, "US", "Gap")
    adjustment = resolve_actual_adjustment(feedback, mode="actual")
    assert adjustment["actual_n"] == 4
    assert adjustment["applied_adjust"] == 0


def test_human_format_never_calls_candidates_trades(db):
    connection, cursor = db
    _seed(cursor, "US")
    connection.commit()
    feedback = get_trigger_feedback(cursor, "US", "Gap")
    ko = format_trigger_feedback(feedback, language="ko")
    en = format_trigger_feedback(feedback, language="en")
    assert ko == ["실제 매매: 5건, 승률 20%, PF 0.11", "관찰 후보: 30일 상승 비율 75% (n=4)"]
    assert en == [
        "Actual trades: n=5, win rate 20%, PF 0.11",
        "Watched candidates: 30d positive rate 75% (n=4)",
    ]
