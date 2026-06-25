"""Tests for the re-entry cooldown gate."""
import importlib
import sqlite3
from datetime import datetime, timedelta

import pytest


@pytest.fixture()
def rc(tmp_path, monkeypatch):
    db = tmp_path / "stock_tracking_db.sqlite"
    conn = sqlite3.connect(db)
    for t in ("trading_history", "us_trading_history"):
        conn.execute(
            f"CREATE TABLE {t} (ticker TEXT, account_key TEXT, sell_date TEXT, profit_rate REAL)"
        )
    conn.commit()
    conn.close()
    monkeypatch.setenv("REENTRY_COOLDOWN_DB", str(db))
    monkeypatch.setenv("REENTRY_COOLDOWN_ENABLED", "true")
    monkeypatch.setenv("REENTRY_COOLDOWN_HOURS", "6")
    monkeypatch.setenv("REENTRY_COOLDOWN_LOSS_HOURS", "24")
    import reentry_cooldown as m
    importlib.reload(m)
    return m, str(db)


def _seed(db, table, ticker, sell_dt, ret, account_key="acct1"):
    conn = sqlite3.connect(db)
    conn.execute(
        f"INSERT INTO {table}(ticker, account_key, sell_date, profit_rate) VALUES(?,?,?,?)",
        (ticker, account_key, sell_dt.strftime("%Y-%m-%d %H:%M:%S"), ret),
    )
    conn.commit()
    conn.close()


def test_no_prior_sell_allows(rc):
    m, db = rc
    assert m.reentry_block("US", "MU") is None


def test_recent_loss_blocks_within_24h(rc):
    m, db = rc
    now = datetime(2026, 6, 25, 12, 0, 0)
    _seed(db, "us_trading_history", "MU", now - timedelta(hours=2), -4.5)
    v = m.reentry_block("US", "MU", now=now)
    assert v and v["action"] == "WOULD_BLOCK" and v["after_loss"] is True
    assert v["window_hours"] == 24


def test_loss_beyond_24h_allows(rc):
    m, db = rc
    now = datetime(2026, 6, 25, 12, 0, 0)
    _seed(db, "us_trading_history", "MU", now - timedelta(hours=30), -4.5)
    assert m.reentry_block("US", "MU", now=now) is None


def test_win_uses_short_window(rc):
    m, db = rc
    now = datetime(2026, 6, 25, 12, 0, 0)
    # winning sell 8h ago: past the 6h normal window -> allow (loss window would've blocked)
    _seed(db, "us_trading_history", "MU", now - timedelta(hours=8), +5.0)
    assert m.reentry_block("US", "MU", now=now) is None
    # winning sell 3h ago: inside 6h normal window -> block
    _seed(db, "us_trading_history", "MU", now - timedelta(hours=3), +5.0)
    v = m.reentry_block("US", "MU", now=now)
    assert v and v["after_loss"] is False and v["window_hours"] == 6


def test_kr_table_and_most_recent_used(rc):
    m, db = rc
    now = datetime(2026, 6, 25, 12, 0, 0)
    _seed(db, "trading_history", "005930", now - timedelta(hours=50), -6.0)  # old
    _seed(db, "trading_history", "005930", now - timedelta(hours=1), -2.0)   # most recent
    v = m.reentry_block("KR", "005930", now=now)
    assert v and v["gap_hours"] < 2 and v["last_ret"] == -2.0


def test_account_key_filter(rc):
    m, db = rc
    now = datetime(2026, 6, 25, 12, 0, 0)
    _seed(db, "us_trading_history", "MU", now - timedelta(hours=1), -3.0, account_key="A")
    assert m.reentry_block("US", "MU", account_key="B", now=now) is None  # different account
    assert m.reentry_block("US", "MU", account_key="A", now=now) is not None


def test_default_win_cooldown_off_loss_still_blocks(rc, monkeypatch):
    m, db = rc
    now = datetime(2026, 6, 25, 12, 0, 0)
    monkeypatch.setenv("REENTRY_COOLDOWN_HOURS", "0")  # default: no cooldown after a win
    importlib.reload(m)
    # winning sell 0.2h ago -> NOT blocked (legit continuation)
    _seed(db, "us_trading_history", "WIN", now - timedelta(hours=0.2), +25.0)
    assert m.reentry_block("US", "WIN", now=now) is None
    # losing sell 0.2h ago -> still blocked by the 24h loss window
    _seed(db, "us_trading_history", "LOSS", now - timedelta(hours=0.2), -5.0)
    assert m.reentry_block("US", "LOSS", now=now) is not None


def test_disabled_returns_none(rc, monkeypatch):
    m, db = rc
    now = datetime(2026, 6, 25, 12, 0, 0)
    _seed(db, "us_trading_history", "MU", now - timedelta(hours=1), -3.0)
    monkeypatch.setenv("REENTRY_COOLDOWN_ENABLED", "false")
    importlib.reload(m)
    assert m.reentry_block("US", "MU", now=now) is None


def test_fail_open_on_bad_db(rc):
    m, _ = rc
    assert m.reentry_block("US", "MU", db_path="/proc/nope/x.sqlite") is None
