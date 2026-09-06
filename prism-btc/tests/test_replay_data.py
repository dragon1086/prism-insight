"""Small SQLite fixtures only; never open operational data through a writer."""
import hashlib
import sqlite3

import pytest

from analysis.replay_data import (
    MONDAY_ANCHOR_MS, TIMEFRAME_MS, load_bars, load_funding,
)


def fixture_db(tmp_path, *, bars=None, funding=None, name="input.db"):
    path = tmp_path / name
    with sqlite3.connect(path) as connection:
        # No uniqueness constraints: the reader must catch damaged imports too.
        connection.executescript(
            "CREATE TABLE klines (timeframe TEXT, open_time, open, high, low, "
            "close, volume, turnover, confirmed);"
            "CREATE TABLE funding (funding_time, rate);"
        )
        connection.executemany("INSERT INTO klines VALUES (?,?,?,?,?,?,?,?,?)",
                               bars if bars is not None else bar_rows())
        connection.executemany("INSERT INTO funding VALUES (?,?)",
                               funding if funding is not None else [(0, .001), (300_000, -.001)])
    return path


def bar_rows():
    return [("5m", timestamp, 100, 102, 98, 101, 0, 0, 1)
            for timestamp in (0, 300_000, 600_000)]


def read_bars(path):
    return load_bars(path, "5m", 0, 900_000)


def test_complete_bars_zero_volume_and_exclusive_end(tmp_path):
    rows = bar_rows() + [("5m", 900_000, 0, 0, 0, 0, 0, 0, 0)]
    result = read_bars(fixture_db(tmp_path, bars=rows))
    assert len(result.rows) == result.manifest["expected_count"] == 3
    assert result.rows[0]["volume"] == 0
    assert result.manifest["coverage"] == 1
    assert result.manifest["missing_count"] == result.manifest["gap_count"] == 0
    assert result.manifest["first_timestamp_ms"] == 0
    assert result.manifest["last_timestamp_ms"] == 600_000
    assert result.manifest["execution_observed"] is False
    assert result.manifest["assumptions"]["mark_price_available"] is False


@pytest.mark.parametrize("missing", [0, 1, 2])
def test_missing_edge_and_interior_bar_rejected(tmp_path, missing):
    rows = bar_rows()
    rows.pop(missing)
    with pytest.raises(ValueError, match="missing_count=1"):
        read_bars(fixture_db(tmp_path, bars=rows))


@pytest.mark.parametrize("field,value", [
    (8, 0), (8, 2), (8, "true"), (8, 1.0),
    (2, float("inf")), (2, float("nan")), (2, "100"), (2, None),
    (2, 0), (2, -1), (2, 103), (5, 97), (3, 97), (4, 103),
    (6, -1), (6, float("inf")), (7, -1), (7, "0"),
    (1, 0.0), (1, 1),
])
def test_invalid_bar_rejected(tmp_path, field, value):
    rows = bar_rows()
    row = list(rows[0])
    row[field] = value
    rows[0] = row
    with pytest.raises(ValueError):
        read_bars(fixture_db(tmp_path, bars=rows))


@pytest.mark.parametrize("conflicting", [False, True])
def test_duplicate_bar_rejected(tmp_path, conflicting):
    rows = bar_rows()
    extra = list(rows[0])
    if conflicting:
        extra[5] = 100
    with pytest.raises(ValueError, match="duplicate"):
        read_bars(fixture_db(tmp_path, bars=rows + [extra]))


@pytest.mark.parametrize("start,end", [(True, 900_000), (0, False), (0.0, 900_000),
                                        (0, 900_000.0), (-300_000, 900_000),
                                        (0, 0), (900_000, 0), (1, 900_000), (0, 899_999)])
def test_invalid_requested_bounds_rejected_without_open(tmp_path, start, end):
    with pytest.raises(ValueError):
        load_bars(tmp_path / "absent.db", "5m", start, end)
    assert list(tmp_path.iterdir()) == []


def test_weekly_monday_grid_and_all_timeframes(tmp_path):
    for timeframe, interval in TIMEFRAME_MS.items():
        start = MONDAY_ANCHOR_MS if timeframe == "1w" else 0
        rows = [(timeframe, start, 100, 102, 98, 101, 1, 100, 1)]
        path = fixture_db(tmp_path, bars=rows, name=timeframe + ".db")
        assert load_bars(path, timeframe, start, start + interval).manifest["count"] == 1
    with pytest.raises(ValueError, match="aligned"):
        load_bars(path, "1w", 0, TIMEFRAME_MS["1w"])


@pytest.mark.parametrize("timeframe", ["1m", "", None, True, []])
def test_bad_timeframe(tmp_path, timeframe):
    with pytest.raises(ValueError, match="timeframe"):
        load_bars(tmp_path / "absent.db", timeframe, 0, 300_000)


def test_actual_signed_funding_and_zero_are_retained(tmp_path):
    path = fixture_db(tmp_path, funding=[(0, .001), (300_000, -.001), (600_000, 0)])
    result = load_funding(path, 0, 900_000, 300_000)
    assert [row["rate"] for row in result.rows] == [.001, -.001, 0]
    assert result.manifest["source_kind"] == "ACTUAL_FUNDING_TIMESTAMP_RATE"
    assert result.manifest["assumptions"]["interval_contract"] == "CALLER_SUPPLIED"


@pytest.mark.parametrize("missing", [0, 1, 2])
def test_missing_funding_is_not_zero(tmp_path, missing):
    rows = [(0, .001), (300_000, -.001), (600_000, 0)]
    rows.pop(missing)
    with pytest.raises(ValueError, match="missing_count=1"):
        load_funding(fixture_db(tmp_path, funding=rows), 0, 900_000, 300_000)


@pytest.mark.parametrize("rate", [1, -1, float("inf"), float("nan"), "0", None])
def test_invalid_funding_rate(tmp_path, rate):
    with pytest.raises(ValueError):
        load_funding(fixture_db(tmp_path, funding=[(0, rate)]), 0, 300_000, 300_000)


@pytest.mark.parametrize("interval", [0, -1, True, 300_000.0])
def test_invalid_funding_interval(tmp_path, interval):
    with pytest.raises(ValueError):
        load_funding(tmp_path / "absent.db", 0, 600_000, interval)


def test_duplicate_funding_rejected(tmp_path):
    with pytest.raises(ValueError, match="duplicate"):
        load_funding(fixture_db(tmp_path, funding=[(0, .001), (0, -.001)]),
                     0, 300_000, 300_000)


@pytest.mark.parametrize("timestamp", [1, 0.0, None, "0"])
def test_invalid_funding_timestamp_rejected(tmp_path, timestamp):
    with pytest.raises(ValueError):
        load_funding(fixture_db(tmp_path, funding=[(timestamp, .001)]),
                     0, 300_000, 300_000)


def test_empty_ranges_report_all_missing(tmp_path):
    path = fixture_db(tmp_path, bars=[], funding=[])
    with pytest.raises(ValueError, match="missing_count=3"):
        read_bars(path)
    with pytest.raises(ValueError, match="missing_count=2"):
        load_funding(path, 0, 600_000, 300_000)


def test_read_only_no_creation_or_mutation_and_path_independent_hash(tmp_path):
    first = fixture_db(tmp_path, name="a #?.db")
    second = fixture_db(tmp_path, bars=list(reversed(bar_rows())), name="other.db")
    before = {path.name: hashlib.sha256(path.read_bytes()).hexdigest()
              for path in tmp_path.iterdir()}
    assert read_bars(first).manifest == read_bars(second).manifest
    assert load_funding(first, 0, 600_000, 300_000).manifest == load_funding(
        second, 0, 600_000, 300_000).manifest
    for loader in (lambda p: read_bars(p), lambda p: load_funding(p, 0, 600_000, 300_000)):
        with pytest.raises(sqlite3.OperationalError):
            loader(tmp_path / "does-not-exist.db")
    assert before == {path.name: hashlib.sha256(path.read_bytes()).hexdigest()
                      for path in tmp_path.iterdir()}


def test_content_and_bounds_change_identity_but_outside_rows_do_not(tmp_path):
    first = fixture_db(tmp_path, name="first.db")
    rows = bar_rows()
    rows[0] = ("5m", 0, 100, 102, 98, 100, 0, 0, 1)
    second = fixture_db(tmp_path, bars=rows, name="second.db")
    third = fixture_db(tmp_path, bars=bar_rows() + [("1h", 0, 1, 1, 1, 1, 0, 0, 1)],
                       name="third.db")
    original = read_bars(first).manifest["content_sha256"]
    assert original != read_bars(second).manifest["content_sha256"]
    assert original == read_bars(third).manifest["content_sha256"]
    assert original != load_bars(first, "5m", 0, 600_000).manifest["content_sha256"]
