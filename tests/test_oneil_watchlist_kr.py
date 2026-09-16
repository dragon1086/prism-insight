import threading
from types import SimpleNamespace

import pandas as pd

from prism_core.oneil_watchlist import KR_POLICY_VERSION, POLICY_VERSION, advance, ref
from tools import run_oneil_watchlist_shadow as worker


def dataset():
    days = pd.bdate_range("2026-05-01", "2026-09-15")
    frame = pd.DataFrame({"Open": [100 + i for i in range(len(days))],
                          "Close": [100 + i for i in range(len(days))],
                          "High": [101 + i for i in range(len(days))],
                          "Low": [99 + i for i in range(len(days))], "Volume": 100}, index=days)
    return frame


def test_kr_official_market_benchmark_adjustment_and_cache(tmp_path):
    calls = []
    class Source:
        def price_history(self, ticker, start, end, *, adjusted):
            calls.append((ticker, adjusted, end))
            return dataset()

        def index_history(self, ticker, start, end):
            calls.append((ticker, None, end))
            return dataset()

    master = SimpleNamespace(observed_date="20260916", markets={"005930": "KOSPI", "035900": "KOSDAQ"})
    data = worker.collect_kr_bounded(["005930", "035900", "SPY", "123"], "20260916",
                                     source=Source(), master=master, cache_dir=tmp_path)
    assert data["__benchmarks"] == {"005930": "1001", "035900": "2001"}
    assert ("005930", True, "20260915") in calls
    assert set(data["005930"][0]) == {"date", "open", "high", "low", "close", "volume"}
    assert len(calls) == 4
    assert data["__attempted_symbols"] == ["005930", "035900"]
    worker.collect_kr_bounded(["005930", "035900"], "20260916", source=Source(), master=master, cache_dir=tmp_path)
    assert len(calls) == 4


def test_kr_policy_ids_currency_filter_and_us_legacy_identity():
    rows = worker._rows(dataset(), "2026-09-15")
    frames = {"005930": rows, "1001": rows, "SPY": rows,
              "__benchmarks": {"005930": "1001"}, "__expected_completed_date": "2026-09-15"}
    seeds = [{"ticker": "005930", "trigger": "모멘텀"}, {"ticker": "035900", "trigger": "역발상"},
             {"ticker": "ABC", "trigger": "Momentum"}]
    state, observations = advance({}, seeds, frames, "20260916", "batch", market="KR")
    assert len(observations) == 1
    assert state["policy_version"] == KR_POLICY_VERSION
    row = observations[0]
    assert row["currency"] == "KRW" and row["benchmark"] == "1001"
    assert row["watch_id"] == ref(KR_POLICY_VERSION, "KR", "005930", "20260916", "batch")
    _, us = advance({}, seeds[:1], frames, "20260916", "batch")
    assert us[0]["watch_id"] == ref(POLICY_VERSION, "US", "005930", "20260916", "batch")


def test_kr_missing_market_never_guesses_benchmark(tmp_path):
    class Source:
        def price_history(self, *args, **kwargs):
            raise AssertionError("No unknown-market query")
    data = worker.collect_kr_bounded(["005930"], "20260916", source=Source(), cache_dir=tmp_path,
        master=SimpleNamespace(observed_date="20260916", markets={}))
    assert "005930" not in data
    assert worker.previous_session("20260921", "KR") == "2026-09-18"


def test_kr_caller_budget_does_not_kill_inflight_provider(tmp_path, monkeypatch):
    monkeypatch.setattr(worker, "previous_session", lambda *args: "2026-09-15")
    monkeypatch.setattr(worker, "market_days", lambda *args: ["2026-09-15"])
    started, release, finished = threading.Event(), threading.Event(), threading.Event()
    class Source:
        def index_history(self, *args):
            started.set()
            release.wait(timeout=2)
            finished.set()
            return dataset()
    master = SimpleNamespace(observed_date="20260916", markets={"005930": "KOSPI"})
    try:
        data = worker.collect_kr_bounded(["005930"], "20260916", source=Source(), master=master,
                                         cache_dir=tmp_path, budget=.05)
        assert started.is_set() and not finished.is_set()
        assert "005930" not in data
        assert data["__attempted_symbols"] == []
    finally:
        release.set()
        assert finished.wait(timeout=2)
        # Await safe background cache cleanup; no thread termination.
        assert worker._KR_LOCK.acquire(timeout=2)
        worker._KR_LOCK.release()


def test_kr_adjusted_basis_revision_is_missing_not_new_signal():
    rows = worker._rows(dataset(), "2026-09-15")
    frames = {"005930": rows, "1001": rows, "__benchmarks": {"005930": "1001"},
              "__expected_completed_date": "2026-09-15"}
    state, events = advance({}, [{"ticker": "005930", "trigger": "모멘텀"}], frames,
                            "20260916", "batch1", market="KR")
    revised = [dict(r, close=r["close"] / 2, high=r["high"] / 2) for r in rows]
    _, events = advance(state, [], {**frames, "005930": revised}, "20260916", "batch2", market="KR")
    assert events[0]["status"] == "MISSING"
    assert events[0]["reason"] == "adjusted_price_basis_changed"


def test_us_legacy_seed_evidence_migrates_without_reconstruction():
    rows = worker._rows(dataset(), "2026-09-15")
    frames = {"AAA": rows, "SPY": rows, "__expected_completed_date": "2026-09-15"}
    state, events = advance({}, [{"ticker": "AAA", "trigger": "Momentum"}], frames, "20260916", "b1")
    original = events[0]
    watch = state["watches"][0]
    watch.pop("seed_close")
    _, missing = advance(state, [], frames, "20260916", "b2")
    assert missing[0]["reason"] == "historical_seed_basis_unknown"
    watch["delivery_seed_payload"] = {"asof": original["seed_asof"], "close": original["close"]}
    _, migrated = advance(state, [], frames, "20260916", "b2")
    assert migrated[0]["input_hash"] == original["input_hash"]
    assert migrated[0]["watch_id"] == original["watch_id"]
    assert migrated[0]["data_contract_version"] == 2


def test_slow_provider_attempts_outcome_before_twenty_active_watches(tmp_path, monkeypatch):
    from observability import oneil_watchlist as capture
    from observability import watchlist_outcomes
    tickers = [f"{i:06d}" for i in range(20)]
    pending = [f"{100+i:06d}" for i in range(10)]
    rows = worker._rows(dataset(), "2026-09-15")
    data = {t: rows for t in tickers}
    data.update({"1001": rows, "__benchmarks": {t: "1001" for t in tickers},
                 "__expected_completed_date": "2026-09-15"})
    state, _ = advance({}, [{"ticker": t, "trigger": "Momentum"} for t in tickers],
                       data, "20260916", "b0", market="KR")
    state_path = tmp_path / "watch.json"
    capture._atomic(state_path, state)
    monkeypatch.setattr(capture, "enabled", lambda *a: True)
    monkeypatch.setattr(capture, "_today", lambda *a: "20260916")
    monkeypatch.setattr(capture, "_emit", lambda *a, **k: k)
    monkeypatch.setattr(watchlist_outcomes, "pending_symbols", lambda *a: pending)
    seen = {}
    monkeypatch.setattr(watchlist_outcomes, "observe_outcomes", lambda market, obs, frames, *a:
                        seen.update(frames))
    monkeypatch.setattr(worker, "previous_session", lambda *a: "2026-09-15")
    monkeypatch.setattr(worker, "market_days", lambda *a: [r["date"] for r in rows])
    release = threading.Event()
    attempts = []
    class SlowSource:
        def index_history(self, *a):
            return dataset()

        def price_history(self, ticker, *a, **k):
            attempts.append(ticker)
            release.wait(timeout=2)
            return dataset()
    collect = worker.collect_kr_bounded
    master = SimpleNamespace(observed_date="20260916", markets={t: "KOSPI" for t in tickers+pending})
    monkeypatch.setattr(worker, "collect_kr_bounded", lambda symbols, date: collect(
        symbols, date, source=SlowSource(), master=master, cache_dir=tmp_path / "cache", budget=.1))
    try:
        capture.observe_batch({}, "20260916", "b1", market="KR", state_path=state_path)
        assert attempts and attempts[0] in pending
        assert attempts[0] in seen["__requested_symbols"]
        assert all(t not in seen["__requested_symbols"] for t in tickers)
    finally:
        release.set()
        assert worker._KR_LOCK.acquire(timeout=2)
        worker._KR_LOCK.release()
