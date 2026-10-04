"""US session: runner hold rule in the trend-exit loop and the stop/guard hook (network-free).

Mirrors the KR cases in tests/test_runner_hold.py on us_stock_holdings and XNYS dates.
Run:  .venv/bin/python -m pytest prism-us/tests/test_runner_hold_us.py -q
"""
import asyncio
import json
import sqlite3
import sys
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas_market_calendars as mc
import pytest

_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import tools.trend_exit_seller as lb  # noqa: E402
from prism_core import runner_hold as R  # noqa: E402
from prism_core import runner_hold_live as L  # noqa: E402
from test_trend_exit_seller_us import FakeAgent, FakeTrader, _enable, _inflight, _patch  # noqa: E402

NY = ZoneInfo("America/New_York")


@lru_cache(maxsize=1)
def sessions():
    return [ts.date().isoformat() for ts in mc.get_calendar("XNYS").schedule("2026-01-02", "2026-12-30").index]


def build(path, entry_idx=60):
    days = sessions()
    bars = [{"date": days[i], "close": 100.0, "volume": 1000.0} for i in range(entry_idx + 1)]
    bars += [{"date": days[entry_idx + j], "close": float(c), "volume": 1000.0} for j, c in enumerate(path, 1)]
    return bars, days[entry_idx], days


def at(day):
    return datetime.fromisoformat(f"{day}T11:00:00").replace(tzinfo=NY).astimezone(timezone.utc)


@pytest.fixture(autouse=True)
def runner_on(monkeypatch):
    monkeypatch.setenv("RUNNER_HOLD_ENABLED", "true")
    monkeypatch.delenv("RUNNER_HOLD_MARKETS", raising=False)
    monkeypatch.setattr("observability.events.emit_event", lambda *a, **k: None)


@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    db = tmp_path / "t.sqlite"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE us_stock_holdings (id INTEGER PRIMARY KEY, ticker TEXT, company_name TEXT, "
                 "buy_price REAL, buy_date TEXT, scenario TEXT, target_price REAL, stop_loss REAL, "
                 "highest_price REAL, account_key TEXT, account_name TEXT)")
    conn.commit()
    conn.close()
    monkeypatch.setattr(lb, "DB_PATH", str(db))
    return str(db)


def _seed(db, scenario, start, stop=100.0):
    conn = sqlite3.connect(db)
    conn.execute("INSERT INTO us_stock_holdings VALUES (1, 'SYNT', 'S', 100.0, ?, ?, 0, ?, 0, 'acc1', 'primary')",
                 (f"{start} 23:40:00", json.dumps(scenario), stop))
    conn.commit()
    conn.close()


def test_us_trend_exit_skips_trailing_for_a_runner_and_sells_its_ma50_break_at_once(tmp_db, monkeypatch):
    bars, start, days = build([101, 103, 106, 110, 115, 121, 124, 130, 140, 150])
    block = R.new_record(R.detect(bars, entry_ref=100, entry_session=start), market="US", entry_ref=100,
                         entry_session=start, detected_at="t")
    assert block["status"] == R.RUNNER
    monkeypatch.setattr(L, "_now", lambda: at(days[days.index(bars[-1]["date"]) + 1]))
    monkeypatch.setattr(L, "fetch_daily_bars", lambda m, t, now=None: bars)
    _enable(monkeypatch, live=False, confirm=2)
    _seed(tmp_db, {"highest_price": 150, "runner": block}, start)
    calls = []
    _patch(monkeypatch, FakeTrader({"SYNT": 130.0}, calls=calls), agent_holder=FakeAgent(calls))
    summary = asyncio.run(lb.run_market("US", "run1"))
    assert summary["runner_held"] == 1 and summary["signaled"] == 0 and calls == []

    broken = bars + [{"date": days[days.index(bars[-1]["date"]) + 1], "close": 103.0, "volume": 1.0}]
    monkeypatch.setattr(L, "_now", lambda: at(days[days.index(broken[-1]["date"]) + 1]))
    monkeypatch.setattr(L, "fetch_daily_bars", lambda m, t, now=None: broken)
    summary = asyncio.run(lb.run_market("US", "run2"))
    assert summary["signaled"] == 1 and summary["acted"] == 1 and _inflight(tmp_db, "SHADOW") == 1


def test_us_trend_exit_unchanged_for_a_spike_excluded_holding(tmp_db, monkeypatch):
    bars, start, days = build([112, 118, 125, 130, 140, 150])
    block = R.new_record(R.detect(bars, entry_ref=100, entry_session=start), market="US", entry_ref=100,
                         entry_session=start, detected_at="t")
    assert block["status"] == R.EXCLUDED and block["reason"] == "SPIKE_FAST"
    monkeypatch.setattr(L, "_now", lambda: at(days[days.index(bars[-1]["date"]) + 1]))
    monkeypatch.setattr(L, "fetch_daily_bars", lambda m, t, now=None: bars)
    _enable(monkeypatch, live=False, confirm=1)
    _seed(tmp_db, {"highest_price": 150, "runner": block}, start)
    calls = []
    _patch(monkeypatch, FakeTrader({"SYNT": 130.0}, calls=calls), agent_holder=FakeAgent(calls))
    summary = asyncio.run(lb.run_market("US", "run1"))
    assert summary["runner_held"] == 0 and summary["signaled"] == 1


def test_us_detection_resets_an_ai_trailing_stop_to_the_entry_and_guards_sells(monkeypatch):
    bars, start, days = build([101, 103, 106, 110, 115, 121, 124])
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE us_stock_holdings (id INTEGER PRIMARY KEY, scenario TEXT, stop_loss REAL)")
    conn.execute("INSERT INTO us_stock_holdings VALUES (1, '{}', 118.0)")
    conn.commit()
    agent = type("Agent", (), {"conn": conn})()
    stock = {"id": 1, "ticker": "SYNT", "buy_price": 100.0, "buy_date": f"{start} 23:40:00", "current_price": 125.0,
             "stop_loss": 118.0, "scenario": "{}"}
    monkeypatch.setattr(L, "fetch_daily_bars", lambda m, t, now=None: bars)
    assert asyncio.run(L.review_holding(agent, "US", stock, now=at(days[days.index(bars[-1]["date"]) + 1]))) is None
    assert conn.execute("SELECT stop_loss FROM us_stock_holdings").fetchone()[0] == 100.0
    held, reason = L.guard_result(agent, "US", stock, True, "Target reached in a sideways regime", logger=None)
    assert held is False and reason.startswith("[RUNNER_HOLD] sell blocked (TARGET)")
