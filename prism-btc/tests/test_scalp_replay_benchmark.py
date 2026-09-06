"""Causality and end-to-end research contracts, never operational returns."""
from dataclasses import replace
import sqlite3
from types import SimpleNamespace

import pandas as pd
import pytest

from analysis import scalp_replay_benchmark as bench
from analysis.replay_data import TIMEFRAME_MS
from backtest.execution_replay import EntryRequest, PriceEvent, ReplayConfig, run_replay


def frames_at(ts):
    result = {}
    for tf in bench.TFS:
        step = TIMEFRAME_MS[tf]
        rows = [dict(open_time=ts + (i - 60) * step, open=100 + i, high=101 + i,
                     low=99 + i, close=100 + i, volume=1, turnover=100)
                for i in range(61)]
        rows[-1].update(open=1, high=1, low=1, close=1)
        frame = pd.DataFrame(rows)
        frame.index = pd.to_datetime(frame.open_time, unit="ms", utc=True)
        result[tf] = bench.add_indicators(frame)
    return result


@pytest.mark.parametrize("tf", bench.TFS)
def test_closed_cutoff_excludes_forming_candle(tf):
    ts = bench.utc_ms("2024-01-01")
    frames = frames_at(ts)
    assert len(bench.closed_frame(frames, tf, ts)) == 60
    assert len(bench.closed_frame(frames, tf, ts - 1)) == 59
    assert bench.closed_frame(frames, tf, ts).iloc[-1].close == 159


def test_signal_price_and_background_never_use_current_candle(monkeypatch):
    ts = bench.utc_ms("2024-01-01")
    frames = frames_at(ts)
    monkeypatch.setattr(bench, "generate_signal", lambda snap: SimpleNamespace(side="long", strength=60))
    entry = bench.entry_context(frames, ts)
    assert entry["reference_price"] == 159
    assert entry["initial_stop"] < 159
    assert bench.background_permission(frames, "long", ts)
    assert not bench.background_permission(frames, "short", ts)


def test_no_signal_and_insufficient_warmup(monkeypatch):
    ts = bench.utc_ms("2024-01-01")
    frames = frames_at(ts)
    monkeypatch.setattr(bench, "generate_signal", lambda snap: SimpleNamespace(side="none"))
    assert bench.entry_context(frames, ts) is None
    frames["1w"] = frames["1w"].iloc[:30]
    assert bench.entry_context(frames, ts) is None


def test_week_grid_and_utc_conversion():
    assert bench.utc_ms("2024-01-01") == bench.utc_ms("2024-01-01T09:00:00+09:00")
    assert bench.grid_floor(bench.utc_ms("2024-01-04"), "1w") == bench.utc_ms("2024-01-01")


@pytest.mark.parametrize("path,expected", [("OHLC", [100, 110, 90, 105]), ("OLHC", [100, 90, 110, 105])])
def test_hypothetical_nodes_are_ordered_and_labelled(path, expected):
    rows = [dict(open_time=0, open=100, high=110, low=90, close=105)]
    events = bench.ohlc_path_events(rows, path, [False])
    assert [e.price for e in events] == expected
    assert [e.ts_ms for e in events] == [0, 100000, 200000, 299999]
    assert all(e.max_fill_lots is None for e in events)


def test_bad_path_and_missing_permission_rejected():
    with pytest.raises(ValueError):
        bench.ohlc_path_events([], "best", [])
    with pytest.raises(ValueError):
        bench.ohlc_path_events([], "OHLC", [True])


def summary_fixture():
    cfg = ReplayConfig(10000, .001, .0002, .00055, 0, 0, 0, 300000, 10000, 50, 50, 7200)
    result = run_replay([PriceEvent(0, 100), PriceEvent(1000, 111)],
                        EntryRequest(0, "long", 20, 90, 110, 300000), cfg)
    episode = dict(episode_id="e", ts_ms=0, side="long", risk_distance=10)
    return bench.campaign_summary(result, episode, "tight_remainder", "OHLC", 1)


def test_summary_counts_simulated_tp_quantity_and_open_nav():
    row = summary_fixture()
    assert row["tp1_complete"]
    assert row["remaining_lots"] == 10
    assert row["unrealized_pnl"] > 0
    assert row["nav_final"] == pytest.approx(row["cash"] + row["unrealized_pnl"])


def test_pairs_must_be_complete_and_unique():
    row = summary_fixture()
    with pytest.raises(ValueError, match="unpaired"):
        bench.aggregate([row])
    other = dict(row, profile="trend_runner", net_r=row["net_r"] + .1)
    summary = bench.aggregate([row, other])
    assert summary["paired_runner_minus_tight"][0]["mean_delta_net_r"] == pytest.approx(.1)
    with pytest.raises(ValueError, match="duplicate"):
        bench.aggregate([row, other, row])


def test_replay_cannot_manufacture_checks_between_observations():
    config = ReplayConfig(10000, .001, .0002, .00055, 0, 0, 0, 1000, 10000, 50, 50, 7200)
    events = [PriceEvent(0, 100), PriceEvent(300000, 111)]
    entry = EntryRequest(0, "long", 20, 90, 110, 300000)
    assert run_replay(events, entry, config) == run_replay(events, entry, replace(config, check_interval_ms=10000))


def test_full_loader_signal_paths_and_output_are_reproducible(tmp_path, monkeypatch):
    market, execution = tmp_path / "market.db", tmp_path / "execution.db"
    start, end = bench.utc_ms("2024-01-01"), bench.utc_ms("2024-01-02")
    schema = "CREATE TABLE klines(timeframe TEXT,open_time INTEGER,open REAL,high REAL,low REAL,close REAL,volume REAL,turnover REAL,confirmed INTEGER)"
    for path in (market, execution):
        with sqlite3.connect(path) as conn:
            conn.execute(schema)
    with sqlite3.connect(market) as conn:
        for tf in bench.TFS:
            step = TIMEFRAME_MS[tf]
            lo, hi = bench.grid_floor(start, tf) - 60 * step, bench.grid_floor(end, tf)
            conn.executemany("INSERT INTO klines VALUES(?,?,?,?,?,?,?,?,?)",
                             [(tf, t, 100, 101, 99, 100, 1, 100, 1) for t in range(lo, hi, step)])
        conn.execute("CREATE TABLE funding(funding_time INTEGER,rate REAL)")
        conn.executemany("INSERT INTO funding VALUES(?,?)", [(t, .0001) for t in range(start, end, 8 * 3600000)])
    with sqlite3.connect(execution) as conn:
        conn.executemany("INSERT INTO klines VALUES(?,?,?,?,?,?,?,?,?)",
                         [("5m", t, 100, 101, 99, 100, 1, 100, 1) for t in range(start, end, 300000)])
    monkeypatch.setattr(bench, "generate_signal", lambda snap: SimpleNamespace(side="long", strength=60))
    before = (market.read_bytes(), execution.read_bytes())
    packet = bench.run_benchmark(market, execution, start, end)
    again = bench.run_benchmark(market, execution, start, end)
    assert packet == again
    assert packet["episode_count"] == 6
    assert packet["scenario_count"] == 48
    assert packet["actual_execution_samples"] == 0
    assert packet["auto_activate"] is False
    assert packet["verdict"] == "RESEARCH_ONLY_INSUFFICIENT"
    compact = bench.compact_packet(packet)
    assert "results" not in compact
    assert compact["full_packet_sha256"] == packet["packet_sha256"]
    assert compact["result_rows_sha256"] == bench.canonical_hash(packet["results"])
    assert (market.read_bytes(), execution.read_bytes()) == before
    assert packet["funding_cashflow_audit"]["results"][0]["held_events"] == 2
    assert packet["funding_cashflow_audit"]["results"][0]["formula_funding"] == pytest.approx(.0004)
    assert packet["funding_cashflow_audit"]["results"][1]["formula_funding"] == pytest.approx(-.0004)
    assert all(row["held_funding_events"] == 0 for row in packet["results"])
