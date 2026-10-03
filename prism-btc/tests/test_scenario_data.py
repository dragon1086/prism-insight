import sqlite3

import pandas as pd
import pytest

from backtest.scenario_data import HistoricalScenarioData, frame_inventory, load_market_data


def bars(periods=600, freq="5min"):
    return pd.DataFrame({"open": 100., "high": 102., "low": 98., "close": 101., "volume": 10.},
                        index=pd.date_range("2026-01-01", periods=periods, freq=freq, tz="UTC"))


def ms(timestamp):
    return int(timestamp.value//1_000_000)


def test_boundary_uses_only_last_known_close_not_next_open():
    source = bars()
    now = ms(source.index[480])
    source.iloc[480] = [999, 1000, 900, 990, 500]
    result = HistoricalScenarioData(source).snapshot(now)
    assert result["valid"]
    forming = result["timeframes"]["1h"]["forming"]
    assert forming["elapsed_ms"] == 0
    assert forming["ohlcv"] == dict(open=101., high=101., low=101., close=101., volume=0.)
    assert "1h" in result["historical_replay"]["zero_progress_synthetic_timeframes"]


def test_future_changes_and_appends_cannot_affect_snapshot():
    source = bars()
    now = ms(source.index[485])
    expected = HistoricalScenarioData(source.iloc[:485]).snapshot(now)
    source.iloc[485:] = [200, 220, 190, 210, 9999]
    assert HistoricalScenarioData(source).snapshot(now) == expected
    forming = expected["timeframes"]["1h"]["forming"]
    assert forming["elapsed_ms"] == 25*60_000
    assert forming["ohlcv"]["volume"] == 50.


def test_different_intrabar_paths_same_hour_totals_produce_different_inputs():
    left, right = bars(), bars()
    right.iloc[480] = [100, 110, 90, 105, 20]
    right.iloc[485] = [105, 110, 90, 101, 0]
    left.iloc[485] = [100, 110, 90, 101, 10]
    now = ms(left.index[485])
    a, b = HistoricalScenarioData(left).snapshot(now), HistoricalScenarioData(right).snapshot(now)
    assert a["timeframes"]["1h"]["forming"]["ohlcv"] != b["timeframes"]["1h"]["forming"]["ohlcv"]


@pytest.mark.parametrize("issue", ["gap", "duplicate", "naive", "bad_price", "off_grid"])
def test_bad_source_fails_closed(issue):
    source = bars()
    if issue == "gap":
        source = source.drop(source.index[10])
    elif issue == "duplicate":
        source = pd.concat([source, source.iloc[:1]])
    elif issue == "naive":
        source.index = source.index.tz_localize(None)
    elif issue == "bad_price":
        source.iloc[5, 0] = -1
    else:
        source.index = source.index + pd.Timedelta(seconds=1)
    with pytest.raises(ValueError):
        HistoricalScenarioData(source)


def test_warmup_only_closed_rows_and_missing_context_explicit():
    source = bars()
    warm = bars(50, "1d")
    warm.index = pd.date_range("2025-11-26", periods=50, freq="1d", tz="UTC")
    now = ms(source.index[485])
    result = HistoricalScenarioData(source, warmup={"1d": warm}).snapshot(now)
    assert result["timeframes"]["1d"]["status"] == "ok"
    assert result["timeframes"]["12h"]["status"] == "incomplete"
    warm.loc[warm.index >= pd.Timestamp("2026-01-02", tz="UTC")] = [900, 999, 800, 950, 999]
    changed = HistoricalScenarioData(source, warmup={"1d": warm}).snapshot(now)
    assert result == changed


def test_missing_primary_history_invalid_and_stale_source_rejected():
    source = bars(20)
    result = HistoricalScenarioData(source).snapshot(ms(source.index[-1])+300_000)
    assert not result["valid"]
    with pytest.raises(ValueError, match="complete_through"):
        HistoricalScenarioData(source).snapshot(ms(source.index[-1])+600_000)


def test_one_minute_source_has_causal_five_minute_volume():
    source = bars(3000, "1min")
    now = ms(source.index[2900])
    result = HistoricalScenarioData(source, source_interval_ms=60_000).snapshot(now)
    assert result["valid"]
    acceleration = result["timeframes"]["1h"]["forming"]["volume_acceleration"]
    assert acceleration["recent_15m_volume"] == 150.


def test_manifest_hash_gaps_and_missing_cost_inputs():
    source = bars()
    inventory = frame_inventory(source.drop(source.index[10]), 300_000)
    assert inventory["missing_bar_count"] == 1
    manifest = HistoricalScenarioData(source).manifest()
    assert len(manifest["source"]["sha256"]) == 64
    assert manifest["mark"]["status"] == manifest["funding"]["status"] == "missing"
    assert not manifest["funding"]["complete"]
    assert HistoricalScenarioData(source, mark=source).manifest()["mark"]["complete"]


def test_sqlite_market_only_readonly_and_filters_provisional(tmp_path):
    path = tmp_path / "market.db"
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE klines(timeframe,open_time,open,high,low,close,volume,confirmed)")
        conn.executemany("INSERT INTO klines VALUES(?,?,?,?,?,?,?,?)", [
            ("5m", 0, 100, 102, 98, 101, 10, 1), ("5m", 300000, 100, 102, 98, 101, 10, 0)])
    before = path.read_bytes()
    assert len(load_market_data(path)) == 1
    assert path.read_bytes() == before
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE trades(id)")
    with pytest.raises(ValueError, match="dedicated_market"):
        load_market_data(path)


def test_normalized_csv_and_jsonl_loaders(tmp_path):
    records = bars(5).reset_index(names="open_time")
    records["open_time"] = records.open_time.map(ms)
    csv, jsonl = tmp_path / "bars.csv", tmp_path / "bars.jsonl"
    records.to_csv(csv, index=False)
    records.to_json(jsonl, orient="records", lines=True)
    pd.testing.assert_frame_equal(load_market_data(csv), load_market_data(jsonl))
