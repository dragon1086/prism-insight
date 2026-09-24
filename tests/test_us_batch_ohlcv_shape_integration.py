"""Real US batch regressions: provider column shape must not abort selection.

Subprocess isolation is intentional: KR and US both own a ``cores`` package.
Only market-data/network boundaries are mocked, never trigger/scoring functions.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
RUN_BATCH = r'''
import json
from pathlib import Path
import os
import socket
import sys
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path.cwd() / "prism-us"))
import us_trigger_batch as batch
from cores import us_surge_detector as provider
if os.getenv("TEST_QUALITY_CAPTURE_FAIL") == "true":
    import prism_core.screening_quality as quality
    def broken_capture(*a, **kw):
        raise ValueError("fixture capture failure")
    quality.build_screening_quality_context = broken_capture

mode, shape, output = sys.argv[1:4]
watch = len(sys.argv) > 4 and sys.argv[4] == "watch"
from observability import oneil_watchlist
from observability import watchlist_outcomes
watchlist_outcomes.ROOT = Path(output).parent / (Path(output).stem + "-outcomes")
oneil_watchlist.enabled = lambda: watch
oneil_watchlist._today = lambda: "20260914"
oneil_watchlist.STATE_PATH = Path(output).with_suffix(".watch-state.json")
def watch_collect(symbols, trade_date):
    days = pd.bdate_range(end="2026-09-11", periods=70)
    return {"__expected_completed_date": "2026-09-11",
        "__market_days": [day.date().isoformat() for day in days],
        "__benchmarks": {symbol: "SPY" for symbol in symbols}, **{
        symbol: [{"date": day.date().isoformat(), "close": 100 + i, "open": 100 + i,
                  "high": 100.5 + i, "low": 99.5 + i, "volume": 1000}
                 for i, day in enumerate(days)] for symbol in symbols}}
oneil_watchlist._collect = watch_collect
tickers = ["AAA", "BBB", "CCC"]
snapshot = pd.DataFrame({
    "Open": [101., 102., 103.], "High": [106., 108., 110.],
    "Low": [100., 101., 102.], "Close": [105., 107., 109.],
    "Volume": [4_000_000, 5_000_000, 6_000_000],
    "Amount": [420_000_000., 535_000_000., 654_000_000.],
}, index=tickers)
previous = snapshot.copy()
previous["Close"] = [100., 101., 102.]
previous["Volume"] = 1_000_000
download_calls = []

def download(ticker, **kwargs):
    download_calls.append(ticker)
    close = np.linspace(70., 104. + tickers.index(ticker), 260)
    frame = pd.DataFrame({
        "Open": close - 1, "High": close + 2, "Low": close - 2,
        "Close": close, "Volume": np.full(260, 2_000_000),
    }, index=pd.bdate_range(end="2026-09-14", periods=260))
    if shape == "flat":
        return frame
    if shape == "ambiguous":
        return pd.concat({"WRONG1": frame, "WRONG2": frame + 1000}, axis=1)
    if shape == "duplicate_high":
        return pd.concat([frame, frame[["High"]]], axis=1)
    if shape.startswith("mixed_"):
        # Requested symbol is deliberately not the first symbol in the response.
        frame = pd.concat({"WRONG": frame + 1000, ticker: frame}, axis=1)
        return frame.swaplevel(axis=1) if shape == "mixed_price_ticker" else frame
    frame = pd.concat({ticker: frame}, axis=1)
    frame.columns.names = ["Ticker", "Price"]
    return frame.swaplevel(axis=1) if shape == "price_ticker" else frame

def no_network(*args, **kwargs):
    raise AssertionError("Unexpected real network access in batch regression")

with patch.object(socket.socket, "connect", no_network), \
     patch.object(batch, "get_major_tickers", return_value=tickers), \
     patch.object(batch, "get_snapshot", return_value=snapshot), \
     patch.object(batch, "get_previous_snapshot", return_value=(previous, "20260911")), \
     patch.object(provider.yf, "download", side_effect=download), \
     patch.object(provider.yf, "Ticker", side_effect=lambda ticker: SimpleNamespace(
         info={"shortName": ticker, "sector": "Technology"}, fast_info={"marketCap": 1e11})):
    result = batch.run_batch(mode, "ERROR", output, override_date="20260914", watch_batch_ref="batch1" if watch else None)
    assert result, "Fixture must exercise nonempty final selection"
    assert download_calls, "Real get_multi_day_ohlcv must reach mocked provider"
    if watch:
        before = json.loads(oneil_watchlist.STATE_PATH.read_text())
        assert before["watches"]
        with patch.object(batch, "select_final_tickers", return_value={}):
            empty = batch.run_batch(mode, "ERROR", None, override_date="20260914", watch_batch_ref="batch2")
        assert empty == {}
        after = json.loads(oneil_watchlist.STATE_PATH.read_text())
        assert {w["watch_id"] for w in before["watches"]} == {w["watch_id"] for w in after["watches"]}
        assert all(w["batch_ref"] == "batch2" for w in after["watches"])
print(json.dumps(download_calls))
'''


def _run_batch(tmp_path, mode, shape, watch=False, quality=False, capture_fail=False, capture_requests=False):
    output = tmp_path / f"{mode}-{shape}-{watch}.json"
    env = dict(os.environ, PYTHONHASHSEED="0", PRISM_DISABLE_SIGNAL_PUBLISH="1",
               PRISM_OBSERVABILITY_SPOOL=str(tmp_path / "isolated-events.jsonl"),
               REGIME_WEAK_THIRD_SLOT_SHADOW_ENABLED="false")
    env.update(US_SCREENING_QUALITY_CAPTURE_ENABLED=str(quality).lower(),
               TEST_QUALITY_CAPTURE_FAIL=str(capture_fail).lower())
    result = subprocess.run(
        [sys.executable, "-c", RUN_BATCH, mode, shape, str(output)] + (["watch"] if watch else []),
        cwd=ROOT, env=env, text=True, capture_output=True, timeout=45, check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    payload = json.loads(output.read_text())
    payload["metadata"].pop("run_time")
    if capture_requests:
        payload["metadata"]["_test_download_calls"] = json.loads(result.stdout.splitlines()[-1])
    return payload


@pytest.mark.parametrize("mode", ["morning", "afternoon"])
@pytest.mark.parametrize("capture_fail", [False, True])
def test_quality_capture_only_adds_observations_not_scores_or_requests(tmp_path, mode, capture_fail):
    baseline = _run_batch(tmp_path, mode, "flat", capture_requests=True)
    observed = _run_batch(tmp_path, mode, "flat", quality=True, capture_fail=capture_fail, capture_requests=True)
    contexts = observed["metadata"].pop("screening_quality_candidates")
    assert set(contexts) == {"AAA", "BBB", "CCC"}
    assert all(c["scoring_applied"] is False for c in contexts.values())
    if capture_fail:
        assert all(c["status"] == "MISSING" for c in contexts.values())
        from prism_core.screening_quality import load_quality_candidates
        assert len(load_quality_candidates({'trade_date': '20260914',
                                           'screening_quality_candidates': contexts})) == 3
    else:
        assert all(c["last_completed_session"] == "2026-09-11" for c in contexts.values())
    assert observed == baseline


@pytest.mark.parametrize("mode", ["morning", "afternoon"])
def test_watch_shadow_on_off_exact_json_and_revisit_empty_selection(tmp_path, mode):
    assert _run_batch(tmp_path, mode, "flat", watch=True) == _run_batch(tmp_path, mode, "flat")


@pytest.mark.parametrize("mode", ["morning", "afternoon"])
@pytest.mark.parametrize("shape", [
    "price_ticker", "ticker_price", "mixed_price_ticker", "mixed_ticker_price",
])
def test_single_ticker_multiindex_preserves_flat_batch_selection(tmp_path, mode, shape):
    assert _run_batch(tmp_path, mode, shape) == _run_batch(tmp_path, mode, "flat")


@pytest.mark.parametrize("mode", ["morning", "afternoon"])
def test_batch_json_keeps_scenario_risk_reward_unknown(tmp_path, mode):
    payload = _run_batch(tmp_path, mode, "price_ticker")
    stocks = [stock for key, rows in payload.items() if key != "metadata" for stock in rows]
    assert all(stock["risk_reward_ratio"] is None for stock in stocks)


@pytest.mark.parametrize("mode", ["morning", "afternoon"])
@pytest.mark.parametrize("shape", ["ambiguous", "duplicate_high"])
def test_ambiguous_provider_history_keeps_evidence_missing_without_aborting_batch(tmp_path, mode, shape):
    payload = _run_batch(tmp_path, mode, shape)
    stocks = [stock for key, rows in payload.items() if key != "metadata" for stock in rows]
    assert all(stock["screening_price_evidence"]["status"] == "MISSING" for stock in stocks)
