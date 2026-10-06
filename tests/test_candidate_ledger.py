"""Candidate ledger: outcome math, descriptors, upsert, fail-open, selection invariance.

Offline: no network, no orders, no channel sends. KR selection runs in-process;
US selection and the real KR/US batches run in subprocesses because the KR and
US packages shadow one another.
"""
import importlib.util
import json
import os
import sqlite3
import subprocess
import sys
import types
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from observability import candidate_ledger as ledger  # noqa: E402

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


@pytest.fixture(autouse=True)
def _ledger_enabled(monkeypatch):
    monkeypatch.setenv("CANDIDATE_LEDGER_ENABLED", "true")
    monkeypatch.delenv("CANDIDATE_LEDGER_DB", raising=False)


def _bars(closes, *, highs=None, lows=None, volumes=None, end="2026-03-31"):
    dates = pd.bdate_range(end=end, periods=len(closes))
    return pd.DataFrame({
        "Open": closes,
        "High": highs if highs is not None else closes,
        "Low": lows if lows is not None else closes,
        "Close": closes,
        "Volume": volumes if volumes is not None else [1000.0] * len(closes),
    }, index=dates)


def _path(history, future_highs, future_lows, future_closes=None):
    """`history` flat bars ending at the anchor session, then the given future sessions."""
    future_closes = future_closes or [100.0] * len(future_highs)
    closes = [100.0] * history + list(future_closes)
    highs = [100.0] * history + list(future_highs)
    lows = [100.0] * history + list(future_lows)
    frame = ledger.normalize_bars(_bars(closes, highs=highs, lows=lows))
    return frame, frame.index[history - 1]


# --- outcomes ----------------------------------------------------------------

def test_mfe_mae_peak_and_session_returns():
    highs = [101, 104, 112, 108, 105, 103, 102, 101]
    lows = [99, 97, 101, 100, 99, 98, 96, 97]
    closes = [100, 103, 110, 106, 104, 102, 99, 100]
    bars, anchor = _path(5, highs, lows, closes)
    result = ledger.compute_outcomes(bars, anchor, 100.0)
    assert result["sessions_observed"] == 8
    assert result["mfe_pct"] == pytest.approx(12.0)
    assert result["mae_pct"] == pytest.approx(-4.0)
    assert result["peak_session"] == 3
    assert result["peak_date"] == bars.index[5 + 2]
    assert result["ret_7s"] == pytest.approx(-1.0)
    assert result["ret_14s"] is None and result["ret_30s"] is None
    assert result["evaluated_through"] == bars.index[-1]
    assert result["hit_20_before_stop_7"] is None
    assert result["hit_50"] == 0


def test_window_is_capped_at_thirty_sessions():
    highs = [101.0] * 34 + [300.0]
    lows = [99.0] * 35
    closes = [100.0] * 29 + [105.0] + [100.0] * 5
    bars, anchor = _path(3, highs, lows, closes)
    result = ledger.compute_outcomes(bars, anchor, 100.0)
    assert result["sessions_observed"] == 30
    assert result["mfe_pct"] == pytest.approx(1.0)
    assert result["ret_30s"] == pytest.approx(5.0)
    assert result["hit_50"] == 0


@pytest.mark.parametrize("highs,lows,expected", [
    ([105, 121, 100], [99, 95, 80], 1),     # +20 on session 2, -7 only later
    ([105, 110, 130], [99, 92, 95], 0),     # -7 on session 2 comes first
    ([105, 110, 115], [99, 95, 94], None),  # neither threshold yet
    ([125, 100, 100], [90, 99, 99], 0),     # same session: conservative stop-first
])
def test_first_touch_plus20_before_minus7(highs, lows, expected):
    bars, anchor = _path(3, highs, lows)
    assert ledger.compute_outcomes(bars, anchor, 100.0)["hit_20_before_stop_7"] == expected


def test_big_winner_flag_and_no_future_sessions():
    bars, anchor = _path(3, [120, 151], [99, 99])
    assert ledger.compute_outcomes(bars, anchor, 100.0)["hit_50"] == 1
    empty = ledger.compute_outcomes(bars, bars.index[-1], 100.0)
    assert empty["sessions_observed"] == 0 and empty["mfe_pct"] is None


# --- descriptors -------------------------------------------------------------

def test_trend_descriptors_on_rising_series():
    closes = [50.0 + i * 0.5 for i in range(250)]
    bars = ledger.normalize_bars(_bars(closes))
    result = ledger.compute_descriptors(bars, bars.index[-1])
    assert result["above_ma50"] == 1 and result["ma50_rising"] == 1
    assert result["ma20_gt_ma50"] == 1 and result["above_ma200"] == 1


def test_insufficient_history_yields_null():
    bars = ledger.normalize_bars(_bars([100.0 + i for i in range(30)]))
    assert all(value is None for value in ledger.compute_descriptors(bars, bars.index[-1]).values())
    bars = ledger.normalize_bars(_bars([100.0 + i for i in range(60)]))
    result = ledger.compute_descriptors(bars, bars.index[-1])
    assert result["above_ma50"] == 1 and result["ma20_gt_ma50"] == 1
    assert result["ma50_rising"] is None and result["above_ma200"] is None
    assert result["vol_ratio_50"] == pytest.approx(1.0)  # 60 bars >= 51


def _pivot_bars(anchor_close, anchor_volume):
    # 60 rising sessions, then 10 sessions alternating down (vol 1000) / up (vol 400).
    closes = [80.0 + i * 0.3 for i in range(60)]
    volumes = [500.0] * 60
    for i in range(10):
        closes.append(closes[-1] - 0.5 if i % 2 == 0 else closes[-1] + 0.6)
        volumes.append(1000.0 if i % 2 == 0 else 400.0)
    closes.append(anchor_close)
    volumes.append(anchor_volume)
    return ledger.normalize_bars(_bars(closes, volumes=volumes))


def test_pocket_pivot_positive_and_negative():
    bars = _pivot_bars(anchor_close=99.0, anchor_volume=1500.0)
    anchor = bars.index[-1]
    assert ledger.compute_descriptors(bars, anchor)["pocket_pivot"] == 1
    # Volume does not exceed the largest down-day volume of the prior 10 sessions.
    assert ledger.compute_descriptors(_pivot_bars(99.0, 900.0), anchor)["pocket_pivot"] == 0
    # Not an up day.
    assert ledger.compute_descriptors(_pivot_bars(90.0, 5000.0), anchor)["pocket_pivot"] == 0


def test_volume_descriptors_exact_values():
    closes = [100.0 + (i % 2) for i in range(60)]           # alternate down/up
    volumes = [100.0 if i % 2 else 300.0 for i in range(59)] + [800.0]
    bars = ledger.normalize_bars(_bars(closes, volumes=volumes))
    result = ledger.compute_descriptors(bars, bars.index[-1])
    prior50 = volumes[-51:-1]
    assert result["vol_ratio_50"] == pytest.approx(800.0 / (sum(prior50) / 50))
    assert result["vol_dryup_10_50"] == pytest.approx((sum(volumes[-11:-1]) / 10) / (sum(prior50) / 50))
    up = sum(volumes[i] for i in range(10, 60) if closes[i] > closes[i - 1])
    down = sum(volumes[i] for i in range(10, 60) if closes[i] < closes[i - 1])
    assert result["updown_vol_ratio_50"] == pytest.approx(up / down)


def test_descriptors_use_captured_anchor_values_not_final_bar():
    closes = [100.0] * 70
    volumes = [1000.0] * 69 + [1e9]   # final anchor-day bar is not visible at capture time
    bars = ledger.normalize_bars(_bars(closes + [500.0] * 5, volumes=volumes + [1.0] * 5))
    anchor = bars.index[69]
    result = ledger.compute_descriptors(bars, anchor, anchor_close=101.0, anchor_volume=2000.0)
    assert result["vol_ratio_50"] == pytest.approx(2.0)
    assert result["above_ma50"] == 1


# --- capture / storage ---------------------------------------------------------

def _candidates():
    return {
        "Gap": pd.DataFrame({"composite_score": [0.9, 0.5, 0.7], "final_score": [0.4, 0.8, 0.6],
                             "stock_name": ["A", "B", "C"], "Close": [100.0, 50.0, 20.0],
                             "Volume": [10, 20, 30], "Amount": [1e3, 1e3, 6e2],
                             "prev_day_change_rate": [5.0, 3.0, 1.0], "gap_up_rate": [2.0, 1.0, 0.5]},
                            index=["A", "B", "C"]),
        "Vol": pd.DataFrame({"CompositeScore": [0.3], "CompanyName": ["D Inc"], "Close": [10.0],
                             "DailyChange": [2.5], "ExtensionScore": [0.7], "RSScore": [0.2]},
                            index=["D"]),
    }


def test_build_rows_ranks_and_selection_flags():
    candidates = _candidates()
    before = {k: v.copy() for k, v in candidates.items()}
    picks = {"Gap": candidates["Gap"].loc[["B"]].assign(SelectionChannel="top-down")}
    rows = {r["ticker"]: r for r in ledger.build_rows(candidates, picks, score_column="final_score")}
    assert [rows[t]["rank_in_trigger"] for t in "BCA"] == [1, 2, 3]
    assert rows["B"]["selected"] == 1 and rows["B"]["selection_channel"] == "top-down"
    assert rows["A"]["selected"] == 0 and rows["A"]["selection_channel"] is None
    assert rows["A"]["change_rate"] == 5.0 and rows["A"]["name"] == "A"
    assert json.loads(rows["A"]["features_json"])["gap_up_rate"] == 2.0
    assert rows["D"]["composite_score"] == 0.3 and rows["D"]["name"] == "D Inc"
    assert rows["D"]["change_rate"] == 2.5 and rows["D"]["extension_score"] == 0.7
    assert rows["D"]["final_score"] is None and rows["D"]["rank_in_trigger"] == 1
    for name, frame in candidates.items():
        pd.testing.assert_frame_equal(frame, before[name])


def _rows(db):
    connection = sqlite3.connect(db)
    connection.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in connection.execute("SELECT * FROM candidate_ledger ORDER BY id")]
    finally:
        connection.close()


def test_record_upsert_is_idempotent_and_resets_outcomes(tmp_path):
    db = tmp_path / "ledger.sqlite"
    rows = ledger.build_rows(_candidates(), {}, score_column="final_score")
    assert ledger.record(rows, market="KR", trade_date="20260302", mode="morning", db_path=db) == 4
    first = _rows(db)
    sqlite3.connect(db).execute("UPDATE candidate_ledger SET mfe_pct=9, outcome_status='PARTIAL'").connection.commit()
    rows[0]["close"] = 123.0
    assert ledger.record(rows, market="KR", trade_date="20260302", mode="morning", db_path=db) == 4
    second = _rows(db)
    assert len(second) == 4
    assert [r["id"] for r in second] == [r["id"] for r in first]
    assert [r["created_at"] for r in second] == [r["created_at"] for r in first]
    assert second[0]["close"] == 123.0 and second[0]["trade_date"] == "2026-03-02"
    assert all(r["mfe_pct"] is None and r["outcome_status"] == "PENDING" for r in second)
    ledger.record(rows, market="KR", trade_date="20260302", mode="afternoon", db_path=db)
    assert len(_rows(db)) == 8


def test_schema_migration_adds_missing_columns(tmp_path):
    db = tmp_path / "old.sqlite"
    connection = sqlite3.connect(db)
    connection.execute("CREATE TABLE candidate_ledger (id INTEGER PRIMARY KEY AUTOINCREMENT, market TEXT, "
                       "trade_date TEXT, mode TEXT, trigger_type TEXT, ticker TEXT, close REAL, "
                       "UNIQUE(market, trade_date, mode, trigger_type, ticker))")
    connection.commit()
    connection.close()
    rows = ledger.build_rows(_candidates(), {}, score_column="final_score")
    assert ledger.record(rows, market="US", trade_date="20260302", mode="morning", db_path=db) == 4
    assert _rows(db)[0]["pocket_pivot"] is None and _rows(db)[0]["outcome_status"] == "PENDING"


def test_fail_open_on_db_errors(tmp_path):
    rows = ledger.build_rows(_candidates(), {}, score_column="final_score")
    assert ledger.record(rows, market="KR", trade_date="20260302", mode="morning", db_path=tmp_path) == 0
    corrupt = tmp_path / "corrupt.sqlite"
    corrupt.write_bytes(b"not a sqlite database" * 200)
    assert ledger.record(rows, market="KR", trade_date="20260302", mode="morning", db_path=corrupt) == 0
    stats = ledger.update_candidate_outcomes("KR", db_path=corrupt, fetch_bars=lambda *a: None, sleep_sec=0)
    assert stats["rows"] == 0
    stats = ledger.update_candidate_outcomes("KR", db_path=tmp_path, fetch_bars=lambda *a: None, sleep_sec=0)
    assert stats["rows"] == 0


def test_disabled_switch_writes_nothing(tmp_path, monkeypatch):
    monkeypatch.setenv("CANDIDATE_LEDGER_ENABLED", "false")
    db = tmp_path / "ledger.sqlite"
    rows = ledger.build_rows(_candidates(), {}, score_column="final_score")
    assert ledger.record(rows, market="KR", trade_date="20260302", mode="morning", db_path=db) == 0
    assert ledger.update_candidate_outcomes("KR", db_path=db, fetch_bars=None)["rows"] == 0
    assert not db.exists()


# --- outcome pass ----------------------------------------------------------------

def _price_frame(anchor="2026-03-02", history=260, future=35, base=100.0, spike=None):
    dates = pd.bdate_range(end=anchor, periods=history).append(
        pd.bdate_range(start=pd.Timestamp(anchor) + pd.Timedelta(days=1), periods=future))
    closes = [base * (0.8 + 0.2 * (i + 1) / history) for i in range(history)] + [base] * future
    highs = list(closes)
    if spike:
        highs[history - 1 + spike[0]] = base * (1 + spike[1] / 100)
    return pd.DataFrame({"Open": closes, "High": highs, "Low": [c * 0.99 for c in closes],
                         "Close": closes, "Volume": [1000.0] * len(closes)}, index=dates)


def test_outcome_pass_fetches_once_per_ticker_and_completes(tmp_path):
    db = tmp_path / "ledger.sqlite"
    frame = pd.DataFrame({"composite_score": [0.9, 0.5], "Close": [100.0, 100.0], "Volume": [1000, 1000]},
                         index=["AAA", "BBB"])
    rows = ledger.build_rows({"T1": frame, "T2": frame}, {}, score_column="composite_score")
    ledger.record(rows, market="US", trade_date="20260302", mode="morning", db_path=db)
    ledger.record(rows, market="US", trade_date="20260302", mode="afternoon", db_path=db)
    calls = []

    def fetch(ticker, start, end):
        calls.append((ticker, start, end))
        if ticker == "BBB":
            raise RuntimeError("provider down")
        return _price_frame(spike=(4, 25.0))

    stats = ledger.update_candidate_outcomes("US", db_path=db, fetch_bars=fetch, today="2026-05-01", sleep_sec=0)
    assert sorted(c[0] for c in calls) == ["AAA", "BBB"]
    assert calls[0][1] <= "2025-02-01"  # descriptor lookback before the anchor
    assert stats["tickers"] == 2 and stats["fetch_errors"] == 1 and stats["complete"] == 4
    by_ticker = {}
    for row in _rows(db):
        by_ticker.setdefault(row["ticker"], []).append(row)
    for row in by_ticker["AAA"]:
        assert row["outcome_status"] == "COMPLETE" and row["sessions_observed"] == 30
        assert row["mfe_pct"] == pytest.approx(25.0) and row["peak_session"] == 4
        assert row["hit_20_before_stop_7"] == 1 and row["outcome_basis"] == "captured_close"
        assert row["above_ma50"] == 1 and row["descriptors_at"] and row["last_attempt_at"]
    for row in by_ticker["BBB"]:
        assert row["outcome_status"] == "PENDING" and row["mfe_pct"] is None and row["last_attempt_at"]
    # Completed rows are not fetched again; the failed ticker is retried.
    calls.clear()
    ledger.update_candidate_outcomes("US", db_path=db, fetch_bars=fetch, today="2026-05-02", sleep_sec=0)
    assert [c[0] for c in calls] == ["BBB"]


def test_outcome_pass_bounds_tickers_and_expires_stale_rows(tmp_path):
    db = tmp_path / "ledger.sqlite"
    frame = pd.DataFrame({"composite_score": [3, 2, 1], "Close": [100.0] * 3}, index=["X1", "X2", "X3"])
    ledger.record(ledger.build_rows({"T": frame}, {}), market="KR", trade_date="20260302", mode="morning", db_path=db)
    calls = []

    def fetch(ticker, start, end):
        calls.append(ticker)
        return pd.DataFrame()

    ledger.update_candidate_outcomes("KR", db_path=db, fetch_bars=fetch, today="2026-03-20",
                                     max_tickers=2, sleep_sec=0)
    assert calls == ["X1", "X2"]
    calls.clear()
    # Least recently attempted first: the skipped ticker goes next.
    ledger.update_candidate_outcomes("KR", db_path=db, fetch_bars=fetch, today="2026-03-21",
                                     max_tickers=1, sleep_sec=0)
    assert calls == ["X3"]
    ledger.update_candidate_outcomes("KR", db_path=db, fetch_bars=fetch, today="2026-12-31", sleep_sec=0)
    assert {r["outcome_status"] for r in _rows(db)} == {"EXPIRED"}


def test_outcome_pass_skips_same_day_anchor(tmp_path):
    db = tmp_path / "ledger.sqlite"
    frame = pd.DataFrame({"composite_score": [1.0], "Close": [100.0]}, index=["Z"])
    ledger.record(ledger.build_rows({"T": frame}, {}), market="KR", trade_date="20260302", mode="morning", db_path=db)
    stats = ledger.update_candidate_outcomes("KR", db_path=db, fetch_bars=lambda *a: 1 / 0,
                                             today="2026-03-02", sleep_sec=0)
    assert stats["tickers"] == 0


def test_corporate_action_switches_to_provider_basis(tmp_path):
    db = tmp_path / "ledger.sqlite"
    frame = pd.DataFrame({"composite_score": [1.0], "Close": [200.0], "Volume": [1000]}, index=["S"])
    ledger.record(ledger.build_rows({"T": frame}, {}), market="KR", trade_date="20260302", mode="morning", db_path=db)
    ledger.update_candidate_outcomes("KR", db_path=db, fetch_bars=lambda *a: _price_frame(spike=(2, 10.0)),
                                     today="2026-05-01", sleep_sec=0)
    row = _rows(db)[0]
    assert row["outcome_basis"] == "history_close" and row["mfe_pct"] == pytest.approx(10.0)


# --- selection invariance ----------------------------------------------------------

def _kr_frame(rows):
    return pd.DataFrame({"composite_score": [s for _, s in rows], "stock_name": [t for t, _ in rows],
                         "Close": [100.0] * len(rows)}, index=[t for t, _ in rows])


def _kr_triggers():
    return {
        "거래량 급증 상위주": _kr_frame([("V1", 0.62), ("V2", 0.40), ("V3", 0.30)]),
        "갭 상승 모멘텀 상위주": _kr_frame([("G1", 0.70), ("G2", 0.60)]),
        "시총 대비 집중 자금 유입 상위주": _kr_frame([("C1", 0.55), ("V2", 0.20)]),
    }


def _kr_select(diagnostics):
    return trigger_batch.select_final_tickers(
        _kr_triggers(), use_hybrid=False, macro_context={"market_regime": "sideways", "leading_sectors": []},
        selection_diagnostics=diagnostics)


def _dump(result):
    return {name: frame.to_json(orient="split") for name, frame in result.items()}


def test_kr_selection_identical_with_and_without_ledger_hook(monkeypatch):
    baseline = _dump(_kr_select(None))
    diagnostics = {}
    assert _dump(_kr_select(diagnostics)) == baseline
    rows = diagnostics["candidate_ledger"]
    assert len(rows) == 7
    picked = {(name, t) for name, frame in _kr_select(None).items() for t in frame.index}
    assert {(r["selected_trigger"], r["ticker"]) for r in rows if r["selected"]} == picked

    def boom(*_a, **_k):
        raise RuntimeError("ledger broken")

    monkeypatch.setattr(ledger, "build_rows", boom)
    broken = {}
    assert _dump(_kr_select(broken)) == baseline
    assert "candidate_ledger" not in broken and "trigger_quality" in broken


US_SELECT = r'''
import json, sys
from pathlib import Path
from unittest.mock import patch
import pandas as pd
sys.path.insert(0, str(Path.cwd() / "prism-us"))
import us_trigger_batch as batch
from observability import candidate_ledger

def frame(rows):
    return pd.DataFrame({"CompositeScore": [s for _, s in rows], "CompanyName": [t for t, _ in rows],
                         "Close": [10.0] * len(rows)}, index=[t for t, _ in rows])

def triggers():
    return {"Volume Surge Top": frame([("V1", 0.62), ("V2", 0.40)]),
            "Gap Up Momentum Top": frame([("G1", 0.70), ("G2", 0.60)]),
            "Closing Strength Top": frame([("C1", 0.55)])}

def run(diag):
    out = batch.select_final_tickers(triggers(), use_hybrid=False, selection_diagnostics=diag)
    return {k: v.to_json(orient="split") for k, v in out.items()}

with patch.object(batch, "get_us_sector_map", return_value={}):
    base = run(None)
    diag = {}
    assert run(diag) == base
    assert len(diag["candidate_ledger"]) == 5
    with patch.object(candidate_ledger, "build_rows", side_effect=RuntimeError("boom")):
        broken = {}
        assert run(broken) == base
        assert "candidate_ledger" not in broken
print("US_OK")
'''


def test_us_selection_identical_when_ledger_hook_raises():
    result = subprocess.run([sys.executable, "-c", US_SELECT], cwd=ROOT, capture_output=True, text=True,
                            timeout=120, env=dict(os.environ, PRISM_DISABLE_SIGNAL_PUBLISH="1"))
    assert result.returncode == 0 and "US_OK" in result.stdout, result.stdout[-3000:] + result.stderr[-3000:]


# --- real batch -> ledger ------------------------------------------------------------

def _integration_module():
    spec = importlib.util.spec_from_file_location(
        "trigger_quality_batch_integration", ROOT / "tests" / "test_trigger_quality_batch_integration.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _batch(tmp_path, market, enabled, integration):
    output = tmp_path / f"{market}-{enabled}.json"
    db = tmp_path / f"{market}-ledger.sqlite"
    env = dict(os.environ, PYTHONHASHSEED="0", PRISM_DISABLE_SIGNAL_PUBLISH="1",
               PRISM_OBSERVABILITY_SPOOL=str(tmp_path / "events.jsonl"),
               REGIME_WEAK_THIRD_SLOT_SHADOW_ENABLED="false", US_SCREENING_UNIVERSE="major_indices",
               US_SCREENING_QUALITY_CAPTURE_ENABLED="false", TRIGGER_QUALITY_PRIORITY="false",
               TRIGGER_QUALITY_DB=str(tmp_path / "missing.sqlite"),
               CANDIDATE_LEDGER_ENABLED="true" if enabled else "false", CANDIDATE_LEDGER_DB=str(db))
    script = integration.KR_RUN if market == "KR" else integration.US_RUN
    result = subprocess.run([sys.executable, "-c", script, "morning", str(output)], cwd=ROOT, env=env,
                            capture_output=True, text=True, timeout=120, check=False)
    assert result.returncode == 0, result.stdout[-4000:] + result.stderr[-4000:]
    payload = json.loads(output.read_text())
    payload["metadata"].pop("run_time")
    return payload, db


@pytest.mark.parametrize("market", ["KR", "US"])
def test_real_batch_records_every_candidate_without_changing_output(tmp_path, market):
    integration = _integration_module()
    disabled, db = _batch(tmp_path, market, False, integration)
    assert not db.exists()
    enabled, db = _batch(tmp_path, market, True, integration)
    assert enabled == disabled
    rows = _rows(db)
    picks = {(stock.get("code") or stock.get("ticker"), trigger)
             for trigger, stocks in enabled.items() if trigger != "metadata" for stock in stocks}
    assert len(rows) > len(picks) > 0
    assert {(r["ticker"], r["selected_trigger"]) for r in rows if r["selected"]} == picks
    assert {r["market"] for r in rows} == {market} and {r["mode"] for r in rows} == {"morning"}
    assert all(r["outcome_status"] == "PENDING" and r["close"] for r in rows)
    for trigger in {r["trigger_type"] for r in rows}:
        ranks = sorted(r["rank_in_trigger"] for r in rows if r["trigger_type"] == trigger)
        assert ranks == list(range(1, len(ranks) + 1))
