# test_forecast_stats.py
"""Unit tests for cores.llm.features.forecast_stats (pure DB logic).

Builds a tiny in-memory-style sqlite with the two tracker tables and points the
module's DB locator at it, then checks the scenario lookup, the tiered up/side/
down distribution (with percentiles), and the target-reach rate. No network, no
matplotlib — just the SQL/aggregation logic.
"""

import os
import sqlite3
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from cores.llm.features import forecast_stats as fs  # noqa: E402


def _build_db(path):
    conn = sqlite3.connect(path)
    c = conn.cursor()
    c.execute(
        """CREATE TABLE analysis_performance_tracker (
            id INTEGER PRIMARY KEY, ticker TEXT, company_name TEXT,
            trigger_type TEXT, analyzed_date TEXT, analyzed_price REAL,
            buy_score REAL, target_price REAL, stop_loss REAL,
            tracked_30d_return REAL, tracking_status TEXT)"""
    )
    # 60 completed high-band rows: 40 up(+20%), 10 sideways(0), 10 down(-20%)
    rows = []
    for i in range(60):
        r = 0.20 if i < 40 else (0.0 if i < 50 else -0.20)
        rows.append(("000660", "trigA", "20260101", 100000.0, 7.0,
                     120000.0, 95000.0, r, "completed"))
    # a recent row for scenario lookup (latest by date)
    rows.append(("000660", "trigA", "20260601", 100000.0, 7.0,
                 130000.0, 90000.0, None, "in_progress"))
    c.executemany(
        """INSERT INTO analysis_performance_tracker
           (ticker, trigger_type, analyzed_date, analyzed_price, buy_score,
            target_price, stop_loss, tracked_30d_return, tracking_status)
           VALUES (?,?,?,?,?,?,?,?,?)""",
        rows,
    )
    c.execute(
        """CREATE TABLE us_analysis_performance_tracker (
            id INTEGER PRIMARY KEY, ticker TEXT, company_name TEXT,
            trigger_type TEXT, analysis_date TEXT, analysis_price REAL,
            buy_score REAL, target_price REAL, stop_loss REAL,
            return_30d REAL, hit_target INTEGER, tracking_status TEXT)"""
    )
    us = []
    for i in range(40):
        r = 0.15 if i < 20 else -0.05
        us.append(("AMD", "trigB", "20260101", 200.0, 7.0, 230.0, 190.0,
                   r, 1 if i < 20 else 0, "completed"))
    c.executemany(
        """INSERT INTO us_analysis_performance_tracker
           (ticker, trigger_type, analysis_date, analysis_price, buy_score,
            target_price, stop_loss, return_30d, hit_target, tracking_status)
           VALUES (?,?,?,?,?,?,?,?,?,?)""",
        us,
    )
    conn.commit()
    conn.close()


@pytest.fixture()
def db(tmp_path, monkeypatch):
    p = tmp_path / "stock_tracking_db.sqlite"
    _build_db(str(p))
    monkeypatch.setattr(fs, "_db_path", lambda: p)
    return p


def test_score_band():
    assert fs.score_band(3) == "low"
    assert fs.score_band(5) == "mid"
    assert fs.score_band(7) == "high"
    assert fs.score_band(None) is None


def test_stock_scenario_latest(db):
    sc = fs.get_stock_scenario("000660", market="kr")
    assert sc is not None
    # latest row (20260601) wins
    assert sc["target_price"] == 130000.0
    assert sc["buy_score"] == 7.0


def test_distribution_kr(db):
    d = fs.get_forecast_distribution("kr", 7, "trigA", threshold=0.10)
    assert d is not None
    assert d["n"] == 60
    assert d["up"] == 67 and d["down"] == 17 and d["side"] == 17  # 40/10/10
    assert d["tier"] == "band+trigger"
    # percentiles present and ordered
    p = d["pcts"]
    assert p["p10"] <= p["p50"] <= p["p90"]


def test_distribution_fallback_global(db):
    # unknown trigger + a band with too few rows -> falls back broader
    d = fs.get_forecast_distribution("kr", 7, "no_such_trigger")
    assert d is not None and d["n"] == 60  # band tier still satisfies


def test_target_reach_us_uses_hit_flag(db):
    r = fs.get_target_reach_rate("us", 7)
    assert r is not None
    assert r["proxy"] is False  # US uses explicit hit_target
    assert r["rate"] == 50  # 20/40
