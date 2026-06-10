# tests/test_store.py — Offline tests for SQLite store (in-memory DB)
import pytest
import sqlite3

from collector.store import get_connection, upsert_rows, get_latest_open_time, get_row_count


@pytest.fixture
def conn():
    """In-memory SQLite connection for testing."""
    c = get_connection(":memory:")
    yield c
    c.close()


def make_rows(start_ms: int, count: int, interval_ms: int = 30 * 60 * 1000) -> list[list[str]]:
    """Generate synthetic Bybit-format kline rows (newest first)."""
    rows = []
    for i in range(count - 1, -1, -1):  # newest first
        ts = start_ms + i * interval_ms
        rows.append([
            str(ts),       # startTime
            "100.0",       # open
            "101.0",       # high
            "99.0",        # low
            "100.5",       # close
            "500.0",       # volume
            "50000.0",     # turnover
        ])
    return rows


class TestUpsertRows:
    def test_basic_insert(self, conn):
        rows = make_rows(1_000_000, 5)
        n = upsert_rows(conn, "30m", rows)
        assert n == 5
        assert get_row_count(conn, "30m") == 5

    def test_upsert_no_duplicate(self, conn):
        rows = make_rows(1_000_000, 5)
        upsert_rows(conn, "30m", rows)
        upsert_rows(conn, "30m", rows)  # same rows again
        assert get_row_count(conn, "30m") == 5

    def test_confirmed_default_is_1(self, conn):
        rows = make_rows(1_000_000, 3)
        upsert_rows(conn, "30m", rows)
        result = conn.execute(
            "SELECT confirmed FROM klines WHERE timeframe='30m'"
        ).fetchall()
        assert all(r[0] == 1 for r in result)

    def test_current_open_time_marked_unconfirmed(self, conn):
        rows = make_rows(1_000_000, 3)
        # rows[0] is the newest (current open_time)
        current_ts = int(rows[0][0])
        upsert_rows(conn, "30m", rows, current_open_time=current_ts)
        row = conn.execute(
            "SELECT confirmed FROM klines WHERE timeframe='30m' AND open_time=?",
            (current_ts,)
        ).fetchone()
        assert row[0] == 0  # unconfirmed

    def test_confirmed_candle_updates_to_confirmed(self, conn):
        rows = make_rows(1_000_000, 3)
        current_ts = int(rows[0][0])
        # First insert: newest is unconfirmed
        upsert_rows(conn, "30m", rows, current_open_time=current_ts)
        # Second insert: same rows but no current_open_time → all confirmed
        upsert_rows(conn, "30m", rows)
        row = conn.execute(
            "SELECT confirmed FROM klines WHERE timeframe='30m' AND open_time=?",
            (current_ts,)
        ).fetchone()
        assert row[0] == 1

    def test_multiple_timeframes_independent(self, conn):
        rows_30m = make_rows(1_000_000, 5)
        rows_1h = make_rows(2_000_000, 3, interval_ms=60 * 60 * 1000)
        upsert_rows(conn, "30m", rows_30m)
        upsert_rows(conn, "1h", rows_1h)
        assert get_row_count(conn, "30m") == 5
        assert get_row_count(conn, "1h") == 3


class TestGetLatestOpenTime:
    def test_returns_none_when_empty(self, conn):
        assert get_latest_open_time(conn, "30m") is None

    def test_returns_max_confirmed(self, conn):
        rows = make_rows(1_000_000, 5)
        current_ts = int(rows[0][0])  # newest = unconfirmed
        upsert_rows(conn, "30m", rows, current_open_time=current_ts)
        latest = get_latest_open_time(conn, "30m")
        # Should NOT return the unconfirmed one
        assert latest != current_ts
        # Should return the second-newest
        second_newest = int(rows[1][0])
        assert latest == second_newest

    def test_returns_latest_when_all_confirmed(self, conn):
        rows = make_rows(1_000_000, 5)
        upsert_rows(conn, "30m", rows)
        latest = get_latest_open_time(conn, "30m")
        assert latest == int(rows[0][0])
