"""Trigger-quality priority: shrinkage math, bounds, fail-open and KR selection."""
import json
import math
import os
import sqlite3
import sys
import types

import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from prism_core import trigger_quality as tq  # noqa: E402

_environment_before_import = dict(os.environ)
try:
    try:
        import trigger_batch  # noqa: E402
    except ModuleNotFoundError as error:
        if error.name != "krx_data_client":
            raise
        stub = types.ModuleType("krx_data_client")
        for name in ("_get_client", "get_market_ohlcv_by_ticker", "get_nearest_business_day_in_a_week",
                     "get_market_cap_by_ticker", "get_market_ticker_name"):
            setattr(stub, name, lambda *_a, **_k: None)
        sys.modules["krx_data_client"] = stub
        import trigger_batch  # noqa: E402
finally:
    os.environ.clear()
    os.environ.update(_environment_before_import)


KR_SCHEMA = """
CREATE TABLE analysis_performance_tracker (
  id INTEGER PRIMARY KEY, ticker TEXT, trigger_type TEXT, analyzed_date TEXT,
  tracked_7d_return REAL, tracked_14d_return REAL, tracked_30d_return REAL, was_traded INTEGER DEFAULT 0);
CREATE TABLE trading_history (
  id INTEGER PRIMARY KEY, ticker TEXT, trigger_type TEXT, sell_date TEXT,
  profit_rate REAL, scenario TEXT);
"""


def _make_db(path, candidates=(), trades=()):
    connection = sqlite3.connect(path)
    connection.executescript(KR_SCHEMA)
    connection.executemany(
        "INSERT INTO analysis_performance_tracker "
        "(ticker, trigger_type, analyzed_date, tracked_7d_return, tracked_14d_return, tracked_30d_return) "
        "VALUES ('T', ?, ?, ?, ?, ?)", candidates)
    connection.executemany(
        "INSERT INTO trading_history (ticker, trigger_type, sell_date, profit_rate, scenario) "
        "VALUES ('T', ?, ?, ?, ?)", trades)
    connection.commit()
    connection.close()
    return path


@pytest.fixture(autouse=True)
def _enabled(monkeypatch):
    monkeypatch.delenv("TRIGGER_QUALITY_PRIORITY", raising=False)
    tq._CACHE.clear()
    yield
    tq._CACHE.clear()


# --- pure math -------------------------------------------------------------

def test_shrinkage_matches_documented_formula():
    rows = [("Strong", 0.05, 0.10, 0.25)] * 10 + [("Strong", 0.0, 0.0, 0.0)] * 10
    rows += [("Weak", 0.0, 0.01, 0.02)] * 20
    weights, details, prior = tq.compute_weights(rows, [])
    assert prior["runner_rate"] == pytest.approx(10 / 40)
    shrunk = (10 + tq.PRIOR_STRENGTH * 0.25) / (20 + tq.PRIOR_STRENGTH)
    assert details["Strong"]["shrunk_runner_rate"] == pytest.approx(shrunk, abs=1e-4)
    quality = 0.5 * (shrunk - 0.25) / tq.RUNNER_SCALE
    assert details["Strong"]["weight"] == pytest.approx(1 + tq.WEIGHT_SPAN * math.tanh(quality), abs=1e-4)
    assert weights["Strong"] > 1.0 > weights["Weak"]


def test_small_sample_stays_near_prior_and_large_sample_moves():
    base = [("Base", 0.0, 0.0, 0.3 if i % 4 == 0 else 0.0) for i in range(400)]
    tiny = [("Tiny", 0.0, 0.0, 0.5)] * 2
    big = [("Big", 0.0, 0.0, 0.5)] * 60
    weights, _, _ = tq.compute_weights(base + tiny + big, [])
    assert abs(weights["Tiny"] - 1.0) < abs(weights["Big"] - 1.0)
    assert weights["Tiny"] < 1.1


def test_weights_are_bounded_for_extreme_history():
    rows = [("Moon", 1.0, 1.0, 1.0)] * 5000 + [("Dud", -0.5, -0.5, -0.5)] * 5000
    trades = [("Moon", 500.0, None)] * 5000 + [("Dud", -90.0, None)] * 5000
    weights, _, _ = tq.compute_weights(rows, trades)
    # Saturates at, never beyond, the documented closed range.
    assert 1 - tq.WEIGHT_SPAN <= weights["Dud"] < 1.0 < weights["Moon"] <= 1 + tq.WEIGHT_SPAN


def test_pnl_component_uses_full_slot_units():
    pilot = json.dumps({"micro_split": {"contract": "micro-split-live-v1", "allocation": 0.25}})
    trades = [("A", -8.0, pilot)] * 30 + [("B", -8.0, None)] * 30 + [("C", 2.0, None)] * 30
    _, details, _ = tq.compute_weights([], trades)
    assert details["A"]["avg_pnl_pct"] == pytest.approx(-2.0)
    assert details["B"]["avg_pnl_pct"] == pytest.approx(-8.0)
    assert details["A"]["weight"] > details["B"]["weight"]


def test_weighted_score_respects_sign_and_bad_values():
    assert tq.weighted_score(0.5, 1.2) == pytest.approx(0.6)
    assert tq.weighted_score(-0.5, 1.25) == pytest.approx(-0.4)
    assert tq.weighted_score(float("nan"), 1.2) == 0.0
    assert tq.weighted_score(None, 1.2) == 0.0
    assert tq.weighted_score(0.7, 1.0) == 0.7


def test_guaranteed_pick_keeps_legacy_list_when_neutral():
    names = ["A", "B", "C"]
    assert tq.guaranteed_pick_triggers(names, None) == names
    assert tq.guaranteed_pick_triggers(names, {}) == names
    assert tq.guaranteed_pick_triggers(names, {"A": 1.1, "B": 0.96}) == names
    assert tq.guaranteed_pick_triggers(names, {"B": tq.GUARANTEE_FLOOR}) == ["A", "C"]


# --- loading / fail-open -----------------------------------------------------

def test_load_reads_only_the_window_before_as_of(tmp_path):
    candidates = [("Inside", "2026-08-01 09:00:00", 0.0, 0.0, 0.40)] * 30
    candidates += [("Inside", "2026-09-16 09:00:00", 0.0, 0.0, -0.4)] * 30  # as_of day: excluded
    candidates += [("Inside", "2025-12-01 09:00:00", 0.0, 0.0, -0.4)] * 30  # older than window
    candidates += [("Other", "2026-08-01 09:00:00", 0.0, 0.0, 0.0)] * 30
    db = _make_db(tmp_path / "db.sqlite", candidates)
    snapshot = tq.load_trigger_quality("KR", "20260916", db_path=db)
    assert snapshot.status == "ok"
    assert snapshot.details["Inside"]["candidate_n"] == 30
    assert snapshot.details["Inside"]["runners"] == 30
    assert snapshot.weight("Inside") > 1.0 > snapshot.weight("Other")
    assert snapshot.weight("Unknown trigger") == 1.0
    meta = snapshot.to_metadata()
    assert meta["weights"] == snapshot.weights and meta["window_days"] == tq.WINDOW_DAYS


def test_database_is_opened_read_only(tmp_path):
    db = _make_db(tmp_path / "db.sqlite", [("A", "2026-08-01", 0.0, 0.0, 0.3)] * 3)
    before = db.read_bytes()
    tq.load_trigger_quality("KR", "20260916", db_path=db)
    assert db.read_bytes() == before
    assert not (tmp_path / "db.sqlite-wal").exists()


@pytest.mark.parametrize("case", ["missing", "corrupt", "no_tables", "empty"])
def test_fail_open_returns_neutral_weights(tmp_path, case):
    db = tmp_path / "db.sqlite"
    if case == "corrupt":
        db.write_bytes(b"not a sqlite database at all" * 100)
    elif case == "no_tables":
        sqlite3.connect(db).close()
    elif case == "empty":
        _make_db(db)
    snapshot = tq.load_trigger_quality("KR", "20260916", db_path=db)
    assert snapshot.status == "unavailable"
    assert snapshot.weights == {}
    assert snapshot.weight("갭 상승 모멘텀 상위주") == 1.0


def test_kill_switch_disables_without_reading_db(tmp_path, monkeypatch):
    monkeypatch.setenv("TRIGGER_QUALITY_PRIORITY", "false")
    db = _make_db(tmp_path / "db.sqlite", [("A", "2026-08-01", 0.0, 0.0, 0.3)] * 30)
    snapshot = tq.load_trigger_quality("KR", "20260916", db_path=db)
    assert snapshot.status == "disabled" and snapshot.weights == {}


def test_snapshot_is_cached_per_day(tmp_path):
    db = _make_db(tmp_path / "db.sqlite", [("A", "2026-08-01", 0.0, 0.0, 0.3)] * 30
                  + [("B", "2026-08-01", 0.0, 0.0, 0.0)] * 30)
    first = tq.load_trigger_quality("KR", "20260916", db_path=db)
    db.unlink()
    assert tq.load_trigger_quality("KR", "20260916", db_path=db) is first
    assert tq.load_trigger_quality("KR", "20260917", db_path=db).status == "unavailable"


# --- KR selection ------------------------------------------------------------

def _frame(rows):
    return pd.DataFrame({"composite_score": [s for _, s in rows],
                         "stock_name": [t for t, _ in rows]}, index=[t for t, _ in rows])


def _select(triggers, weights, regime="sideways"):
    result = trigger_batch.select_final_tickers(
        triggers, use_hybrid=False, trigger_weights=weights,
        macro_context={"market_regime": regime, "leading_sectors": []})
    return [(ticker, name) for name, frame in result.items() for ticker in frame.index]


def _morning():
    # Registration order mirrors production: weak volume-surge trigger first.
    return {
        "거래량 급증 상위주": _frame([("V1", 0.62), ("V2", 0.40)]),
        "갭 상승 모멘텀 상위주": _frame([("G1", 0.70), ("G2", 0.60)]),
        "시총 대비 집중 자금 유입 상위주": _frame([("C1", 0.55)]),
    }


WEIGHTS = {"거래량 급증 상위주": 0.88, "갭 상승 모멘텀 상위주": 1.05, "시총 대비 집중 자금 유입 상위주": 0.90}


def test_kr_weak_trigger_loses_guarantee_and_strong_wins_close_call(monkeypatch):
    monkeypatch.setattr(trigger_batch, "_get_regime_slots", lambda _r: (0, 2))
    legacy = _select(_morning(), None)
    weighted = _select(_morning(), WEIGHTS)
    assert sorted(legacy) == [("G1", "갭 상승 모멘텀 상위주"), ("V1", "거래량 급증 상위주")]
    # V1 0.62*0.88=0.546 < G2 0.60*1.05=0.63: strong trigger wins the close call.
    assert sorted(weighted) == [("G1", "갭 상승 모멘텀 상위주"), ("G2", "갭 상승 모멘텀 상위주")]


def test_kr_weak_trigger_still_picked_when_best_available(monkeypatch):
    monkeypatch.setattr(trigger_batch, "_get_regime_slots", lambda _r: (0, 2))
    triggers = _morning()
    triggers["거래량 급증 상위주"] = _frame([("V1", 0.95)])
    weighted = _select(triggers, WEIGHTS)
    assert ("V1", "거래량 급증 상위주") in weighted


def test_kr_all_weak_triggers_still_fill_every_slot(monkeypatch):
    monkeypatch.setattr(trigger_batch, "_get_regime_slots", lambda _r: (0, 2))
    weights = {name: 0.8 for name in _morning()}
    assert len(_select(_morning(), weights)) == 2


def test_kr_neutral_or_disabled_weights_are_byte_identical(monkeypatch):
    monkeypatch.setattr(trigger_batch, "_get_regime_slots", lambda _r: (0, 2))
    legacy = _select(_morning(), None)
    assert _select(_morning(), {}) == legacy
    assert _select(_morning(), {name: 1.0 for name in _morning()}) == legacy


def test_kr_topdown_pool_scales_by_trigger_weight():
    triggers = {
        "매크로 섹터 리더": pd.DataFrame({"final_score": [0.80]}, index=["M1"]),
        "일중 상승률 상위주": pd.DataFrame({"final_score": [0.74]}, index=["I1"]),
    }
    macro = {"leading_sectors": [{"sector": "반도체", "confidence": 0.5}],
             "sector_map": {"M1": "반도체", "I1": "반도체"}}
    assert trigger_batch._build_topdown_pool(triggers, macro, "final_score")[0][0] == "M1"
    pool = trigger_batch._build_topdown_pool(triggers, macro, "final_score",
                                             {"매크로 섹터 리더": 0.89, "일중 상승률 상위주": 1.10})
    assert pool[0][0] == "I1"


def test_kr_third_slot_mirror_matches_live_weighted_selection(monkeypatch):
    monkeypatch.setattr(trigger_batch, "_get_regime_slots", lambda _r: (0, 2))
    live = _select(_morning(), WEIGHTS)
    mirror = trigger_batch._counterfactual_bottomup_order(
        _morning(), "composite_score", limit=3, trigger_weights=WEIGHTS)
    assert {row["ticker"] for row in mirror[:2]} == {ticker for ticker, _ in live}
    legacy_mirror = trigger_batch._counterfactual_bottomup_order(_morning(), "composite_score", limit=3)
    assert {row["ticker"] for row in legacy_mirror[:2]} == {t for t, _ in _select(_morning(), None)}
