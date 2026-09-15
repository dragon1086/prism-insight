import json
import subprocess
from copy import deepcopy
from datetime import date, timedelta

import pandas as pd
import pytest

from observability import oneil_watchlist as capture
from prism_core.oneil_watchlist import advance, completed


def test_collector_uses_fixed_argv_and_json_stdin(monkeypatch):
    seen = {}

    def run(argv, **kwargs):
        seen.update(argv=argv, **kwargs)
        return subprocess.CompletedProcess(argv, 0, stdout="{}")

    monkeypatch.setattr(capture.subprocess, "run", run)
    symbols = ["AAPL;not-a-command"]
    assert capture._collect(symbols, "20260915") == {}
    assert seen["argv"] == [capture.sys.executable, str(capture.ROOT / "tools/run_oneil_watchlist_shadow.py")]
    assert json.loads(seen["input"])["tickers"] == symbols
    assert seen["shell"] is False and seen["close_fds"] is True
    assert seen["timeout"] == 20


def frames(extra=0):
    days = pd.bdate_range("2026-01-01", periods=66 + extra)
    stock = [{"date": d.date().isoformat(), "close": 70 + i * .5,
              "high": 70.1 + i * .5} for i, d in enumerate(days)]
    bench = [{"date": r["date"], "close": 100., "high": 101.} for r in stock]
    return {"AAA": stock, "SPY": bench, "__expected_completed_date": bench[-1]["date"]}


def cutoff(data):
    return (date.fromisoformat(data["SPY"][-1]["date"]) + timedelta(days=1)).strftime("%Y%m%d")


SEEDS = [{"ticker": "AAA", "trigger": "Gap Up Momentum Top"}]


def test_ready_frozen_pivot_same_bar_and_expiry():
    data = frames()
    state, events = advance({}, SEEDS, data, cutoff(data), "batch1")
    row = events[0]
    assert row["status"] == "READY"
    assert row["pivot"] == data["AAA"][-2]["high"]
    repeated, again = advance(state, SEEDS, data, cutoff(data), "batch2")
    assert len(repeated["watches"]) == 1
    assert again[0]["ready_event_id"] == row["ready_event_id"]
    assert again[0]["elapsed_bars"] == 0
    later = frames(5)
    expired, ev = advance(repeated, [], later, cutoff(later), "batch3")
    assert ev[0]["status"] == "EXPIRED"
    assert ev[0]["pivot"] == row["pivot"]
    _, no_reseed = advance(expired, SEEDS, later, str(int(cutoff(later)) + 1), "batch4")
    assert no_reseed == []


def test_prior_candidate_revisited_empty_selection_and_strength():
    data = frames()
    data["AAA"][-1]["close"] = 101.9
    state, events = advance({}, SEEDS, data, cutoff(data), "b1")
    assert events[0]["status"] == "WATCHING"
    later = frames(1)
    state, events = advance(state, [], later, cutoff(later), "b2")
    assert events[0]["status"] == "READY"
    falling = frames(2)
    falling["AAA"][-2].update(close=106, high=107)
    falling["AAA"][-1].update(close=104, high=105)
    _, events = advance(state, [], falling, cutoff(falling), "b3")
    assert events[0]["status"] == "WATCHING"


@pytest.mark.parametrize("failure", ["short", "duplicate", "alignment", "nan"])
def test_bad_data_missing(failure):
    data = frames()
    if failure == "short": data["AAA"] = data["AAA"][-65:]
    if failure == "duplicate": data["AAA"].append(data["AAA"][-1])
    if failure == "alignment": data["SPY"][0]["date"] = "2025-12-31"
    if failure == "nan": data["AAA"][-1]["close"] = float("nan")
    _, events = advance({}, SEEDS, data, cutoff(data), "b")
    assert events[0]["status"] == "MISSING"


def test_missing_stock_expires_by_benchmark_and_no_reconstructed_seed_pivot():
    data = frames()
    data.pop("AAA")
    state, _ = advance({}, SEEDS, data, cutoff(data), "b1")
    later = frames(1)
    state, events = advance(state, [], later, cutoff(later), "b2")
    assert events[0]["status"] == "MISSING"
    later = frames(5)
    later.pop("AAA")
    _, events = advance(state, [], later, cutoff(later), "b3")
    assert events[0]["status"] == "EXPIRED"


def test_future_bars_excluded_cap_and_contrarian():
    data = frames()
    day = cutoff(data)
    data["AAA"].append({"date": date.fromisoformat(data["AAA"][-1]["date"]).isoformat(), "close": 0, "high": 0})
    with pytest.raises(ValueError): completed(data["AAA"], day)
    seeds = [{"ticker": str(i), "trigger": "Momentum"} for i in range(25)]
    state, _ = advance({}, [{"ticker": "VALUE", "trigger": "Contrarian Value Pick"}] + seeds, {}, day, "b")
    assert len(state["watches"]) == 20
    assert "VALUE" not in [w["ticker"] for w in state["watches"]]


def test_capture_restart_exact_batch_dedup_and_timeout(tmp_path, monkeypatch):
    monkeypatch.setattr(capture, "enabled", lambda: True)
    monkeypatch.setattr(capture, "STATE_PATH", tmp_path / "state.json")
    seen = []
    monkeypatch.setattr(capture, "emit_event", lambda event, **kw: seen.append((event, kw)) or kw)
    data = frames()
    selection = {"Gap Up Momentum Top": pd.DataFrame(index=["AAA"])}
    monkeypatch.setattr(capture, "_today", lambda: cutoff(data))
    original = deepcopy(selection)
    for _ in range(2):
        capture.observe_batch(selection, cutoff(data), "b1", collector=lambda *a: data)
    assert len(seen) == 3
    assert capture.read_ready_context("US", "AAA", "b1")
    assert capture.read_ready_context("US", "AAA", "other") is None
    assert capture.read_ready_context("KR", "AAA", "b1") is None
    pd.testing.assert_frame_equal(selection[next(iter(selection))], original[next(iter(original))])
    capture.observe_batch({}, cutoff(data), "b2", collector=lambda *a: data)
    assert capture.read_ready_context("US", "AAA", "b2")
    def timeout(*args): raise subprocess.TimeoutExpired("worker", 20)
    capture.observe_batch({}, cutoff(data), "b2", collector=timeout)
    assert capture.read_ready_context("US", "AAA", "b2") is None
    assert seen[-1][0] == "watchlist.shadow_unavailable"


def test_default_off_no_io(tmp_path, monkeypatch):
    monkeypatch.setattr(capture, "POLICY_PATH", tmp_path / "missing.json")
    assert capture.observe_batch({}, "20260916", "b", collector=lambda *a: pytest.fail("unexpected I/O")) is None


def test_real_spool_exact_ids_and_emitter_failure_retry(tmp_path, monkeypatch):
    monkeypatch.setattr(capture, "enabled", lambda: True)
    monkeypatch.setattr(capture, "STATE_PATH", tmp_path / "state.json")
    spool = tmp_path / "events.jsonl"
    monkeypatch.setenv("PRISM_OBSERVABILITY_SPOOL", str(spool))
    data = frames()
    monkeypatch.setattr(capture, "_today", lambda: cutoff(data))
    real_emit = capture.emit_event
    monkeypatch.setattr(capture, "emit_event", lambda *a, **k: None)
    selection = {"Momentum": pd.DataFrame(index=["AAA"])}
    capture.observe_batch(selection, cutoff(data), "b1", collector=lambda *a: data)
    assert capture.read_ready_context("US", "AAA", "b1") is None
    monkeypatch.setattr(capture, "emit_event", real_emit)
    capture.observe_batch({}, cutoff(data), "b2", collector=lambda *a: data)
    context = capture.read_ready_context("US", "AAA", "b2")
    assert context
    events = [json.loads(line) for line in spool.read_text().splitlines()]
    assert {e["event_id"] for e in events} == {context[k] for k in (
        "seed_event_id", "ready_event_id", "ready_observation_event_id")}
    assert events[0]["attributes"]["batch_ref"] == "b1"  # Immutable seed provenance.
    assert events[-1]["attributes"]["batch_ref"] == "b2"


def test_prospective_guard_skips_past_and_future_without_io(tmp_path, monkeypatch):
    monkeypatch.setattr(capture, "enabled", lambda: True)
    monkeypatch.setattr(capture, "_today", lambda: "20260916")
    path = tmp_path / "state.json"
    for day in ("20260915", "20260917"):
        assert capture.observe_batch({}, day, "b", state_path=path,
                                     collector=lambda *a: pytest.fail("unexpected I/O")) is None
    assert not path.exists()


def test_atomic_failure_after_delivery_retries_identical_payload(tmp_path, monkeypatch):
    monkeypatch.setattr(capture, "enabled", lambda: True)
    monkeypatch.setattr(capture, "STATE_PATH", tmp_path / "state.json")
    spool = tmp_path / "events.jsonl"
    monkeypatch.setenv("PRISM_OBSERVABILITY_SPOOL", str(spool))
    data = frames()
    monkeypatch.setattr(capture, "_today", lambda: cutoff(data))
    atomic = capture._atomic
    calls = []
    def fail_final(path, state):
        calls.append(1)
        if len(calls) == 3:
            raise OSError("disk unavailable after spool append")
        return atomic(path, state)
    monkeypatch.setattr(capture, "_atomic", fail_final)
    selection = {"Momentum": pd.DataFrame(index=["AAA"])}
    capture.observe_batch(selection, cutoff(data), "b1", collector=lambda *a: data)
    assert capture.read_ready_context("US", "AAA", "b1") is None
    monkeypatch.setattr(capture, "_atomic", atomic)
    capture.observe_batch({}, cutoff(data), "b1", collector=lambda *a: data)
    assert capture.read_ready_context("US", "AAA", "b1")
    events = [json.loads(line) for line in spool.read_text().splitlines()]
    grouped = {}
    for event in events:
        grouped.setdefault(event["event_id"], []).append(event)
    repeated = [group for group in grouped.values() if len(group) > 1]
    assert len(repeated) == 3
    for first, second in repeated:
        assert first["attributes"] == second["attributes"]
        assert first["timestamp"] == second["timestamp"]


def test_bulk_collector_multiindex_and_timeout_budget(monkeypatch):
    import yfinance as yf

    from tools.run_oneil_watchlist_shadow import collect
    days = pd.bdate_range("2026-01-01", periods=66)
    frame = pd.DataFrame({"Close": range(100, 166), "High": range(101, 167)}, index=days)
    calls = []
    def download(symbols, **kwargs):
        calls.append((symbols, kwargs))
        return pd.concat({"AAA": frame, "SPY": frame}, axis=1)
    monkeypatch.setattr(yf, "download", download)
    result = collect(["AAA", "SPY"], "20260916")
    assert len(result["AAA"]) == 66
    assert calls[0][1]["auto_adjust"] is True
    assert calls[0][1]["end"] == "2026-09-16"
    assert calls[0][1]["timeout"] == 8
    def run(*args, **kwargs):
        assert kwargs["timeout"] == 20
        raise subprocess.TimeoutExpired("worker", 20)
    monkeypatch.setattr(capture.subprocess, "run", run)
    with pytest.raises(subprocess.TimeoutExpired):
        capture._collect(["AAA", "SPY"], "20260916")


def test_calendar_holiday_and_aligned_stale_data():
    from tools.run_oneil_watchlist_shadow import previous_session
    assert previous_session("20260908") == "2026-09-04"  # Labor Day Monday closed.
    data = frames()
    data["__expected_completed_date"] = "2026-09-15"
    _, events = advance({}, SEEDS, data, "20260916", "b")
    assert events[0]["status"] == "MISSING"
