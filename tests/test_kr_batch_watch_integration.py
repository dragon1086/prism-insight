"""Real KR trigger/scoring/JSON path, with only data boundaries mocked."""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
RUN = r'''
import json
import socket
import sys
from pathlib import Path
from unittest.mock import patch
import numpy as np
import pandas as pd
import trigger_batch as batch
from cores import market_data
from observability import oneil_watchlist as watch, watchlist_outcomes as outcomes
from tools import run_oneil_watchlist_shadow as collector

mode, enabled, output = sys.argv[1:]
enabled = enabled == "on"
watch.enabled = lambda market="US": enabled
watch._today = lambda market="US": "20260916"
watch.KR_STATE_PATH = Path(output).with_suffix(".watch.json")
outcomes.ROOT = Path(output).parent / (Path(output).stem + "-outcomes")
tickers = ["005930", "000660", "035420"]
snapshot = pd.DataFrame({"Open": [10200., 10300., 10400.], "High": [10700., 10900., 11100.],
    "Low": [10100., 10200., 10300.], "Close": [10600., 10800., 11000.],
    "Volume": [4000000, 5000000, 6000000], "Amount": [42e9, 54e9, 66e9]}, index=tickers)
previous = snapshot.copy()
previous["Close"] = [10000., 10100., 10200.]
previous["Volume"] = 1000000
cap = pd.DataFrame({"시가총액": [1e12, 2e12, 3e12]}, index=tickers)
bundle = batch.MarketSnapshotBundle(snapshot, previous, cap, "20260915", "KIS_FIXTURE")
calls = []
def history(start, end, ticker, **kwargs):
    calls.append(ticker)
    close = np.linspace(7000., 10400. + tickers.index(ticker) * 100, 260)
    return pd.DataFrame({"Open": close - 10, "High": close + 20, "Low": close - 20,
        "Close": close, "Volume": 2000000, "Amount": close * 2000000},
        index=pd.bdate_range(end="2026-09-15", periods=260))
def collect(symbols, date):
    days = pd.bdate_range(end="2026-09-15", periods=70)
    return {"__expected_completed_date": "2026-09-15",
        "__market_days": [d.date().isoformat() for d in days],
        "__benchmarks": {s: "1001" for s in symbols}, **{
        s: [{"date": d.date().isoformat(), "open": 100+i, "close": 100+i,
            "high": 100.5+i, "low": 99.5+i, "volume": 1000} for i,d in enumerate(days)]
        for s in list(symbols) + ["1001"]}}
def no_network(*a, **k):
    raise AssertionError("Unexpected network/order/LLM in isolated KR batch")
with patch.object(socket.socket, "connect", no_network), \
     patch.object(batch, "_resolve_trade_date", return_value="20260916"), \
     patch.object(batch, "load_market_snapshot_bundle", return_value=bundle), \
     patch.object(batch, "_get_ticker_name_map", return_value={s:s for s in tickers}), \
     patch.object(market_data, "get_market_ohlcv_by_date", side_effect=history), \
     patch.object(collector, "collect_kr_bounded", side_effect=collect):
    result = batch.run_batch(mode, "ERROR", output, watch_batch_ref="kr-b1")
    assert result and calls, "Exercise real triggers/scoring and KIS history facade"
    if enabled:
        before = json.loads(watch.KR_STATE_PATH.read_text())
        assert before["watches"] and before["market"] == "KR"
        with patch.object(batch, "select_final_tickers", return_value={}):
            assert batch.run_batch(mode, "ERROR", None, watch_batch_ref="kr-b2") == {}
        after = json.loads(watch.KR_STATE_PATH.read_text())
        assert {w["watch_id"] for w in before["watches"]} == {w["watch_id"] for w in after["watches"]}
        assert all(w["batch_ref"] == "kr-b2" for w in after["watches"])
        with patch.object(collector, "collect_kr_bounded", side_effect=TimeoutError("fixture")):
            failed = batch.run_batch(mode, "ERROR", None, watch_batch_ref="kr-b3")
        assert {k:list(v.index) for k,v in failed.items()} == {k:list(v.index) for k,v in result.items()}
'''


@pytest.mark.parametrize("mode", ["morning", "afternoon"])
def test_real_kr_watch_preserves_selection_and_revisits_empty(tmp_path, mode):
    payloads = []
    for enabled in ("on", "off"):
        output = tmp_path / f"{mode}-{enabled}.json"
        env = dict(os.environ, PYTHONHASHSEED="0", PRISM_DISABLE_SIGNAL_PUBLISH="1",
                   PRISM_OBSERVABILITY_SPOOL=str(tmp_path / f"{enabled}-events.jsonl"),
                   REGIME_WEAK_THIRD_SLOT_SHADOW_ENABLED="false")
        result = subprocess.run([sys.executable, "-c", RUN, mode, enabled, str(output)],
                                cwd=ROOT, env=env, capture_output=True, text=True, timeout=45, check=False)
        assert result.returncode == 0, result.stdout + result.stderr
        payload = json.loads(output.read_text())
        payload["metadata"].pop("run_time")
        payloads.append(payload)
    assert payloads[0] == payloads[1]
