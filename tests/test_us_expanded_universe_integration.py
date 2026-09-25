"""Real expanded-universe collection -> eligibility -> morning/afternoon JSON."""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
RUN = r'''
import json
from pathlib import Path
import socket
import sys
from types import SimpleNamespace
from unittest.mock import patch
import numpy as np
import pandas as pd

root, mode, shape, output = sys.argv[1:]
sys.path[:0] = [str(Path(root) / "prism-us"), root]
def forbidden(*a, **kw):
    raise AssertionError("Network/broker/channel access forbidden")
with patch.object(socket.socket, "connect", forbidden), patch("dotenv.load_dotenv", return_value=False):
    import us_trigger_batch as batch
    from cores import us_surge_detector as provider
    from prism_core import us_stock_universe as universe
    from observability import oneil_watchlist
    symbols = ["AAA", "BBB", "CCC", "FUND", "TINY", "SHELL", "UNKNOWN"]
    calls = []
    def download(tickers, **kwargs):
        calls.append(tickers)
        if isinstance(tickers, list):
            if shape == "empty":
                return pd.DataFrame()
            frames = {}
            for i, symbol in enumerate(tickers):
                frames[symbol] = pd.DataFrame({
                    "Open": [100., 101.+i], "High": [102.,106.+i],
                    "Low": [98.,100.+i], "Close": [100.,105.+i],
                    "Volume": [1e6,4e6+i*1e6]},
                    index=pd.to_datetime(["2026-09-11","2026-09-14"]))
            if shape == "low_turnover":
                frames["CCC"]["Volume"] = 100.
            if shape == "ambiguous":
                return frames["AAA"]
            raw = pd.concat(frames, axis=1)
            return raw.swaplevel(axis=1) if shape == "price_first" else raw
        close = np.linspace(70.,104.+symbols.index(tickers),260)
        return pd.DataFrame({"Open":close-1,"High":close+2,"Low":close-2,
                             "Close":close,"Volume":np.full(260,2e6)},
                            index=pd.bdate_range(end="2026-09-14",periods=260))
    def ticker(symbol):
        info = {"quoteType":"EQUITY", "marketCap":1e11, "currency":"USD",
                "exchange":"NMS", "sector":"Technology", "industry":"Software",
                "shortName":symbol, "longName":symbol+" Inc."}
        if symbol == "FUND": info["quoteType"] = "ETF"
        if symbol == "TINY": info["marketCap"] = 1e6
        if symbol == "SHELL": info["industry"] = "Shell Companies"
        if symbol == "UNKNOWN": info.pop("marketCap")
        if shape == "metadata_empty": info = {}
        return SimpleNamespace(info=info,fast_info={"marketCap":info.get("marketCap",0)})
    records = [universe.UniverseRecord(s,s+" Common Stock","NASDAQ") for s in symbols]
    with patch.object(universe,"fetch_universe",return_value=universe.UniverseResult(records,{"fixture":7})), \
         patch.object(provider.yf,"download",side_effect=download), \
         patch.object(provider.yf,"Ticker",side_effect=ticker), \
         patch.object(oneil_watchlist,"enabled",return_value=False):
        result = batch.run_batch(mode,"ERROR",output,override_date="20260914")
        payload = json.loads(Path(output).read_text())
        selected = {s for frame in result.values() for s in frame.index}
        assert not selected.intersection({"FUND","TINY","SHELL","UNKNOWN"})
        if shape == "metadata_empty":
            assert not selected
            assert payload["metadata"]["universe_eligibility"]["metadata_status"] == "PARTIAL"
            assert payload["metadata"]["trigger_errors"]
            from prism_core.batch_run_status import batch_status_message
            message = batch_status_message("US",mode,"20260914","no_candidates",payload["metadata"])
            assert "오류" in message
        elif shape not in {"empty","ambiguous"}:
            assert selected, "Must exercise real candidate selection, not just empty output"
            expected_eligible = 2 if shape == "low_turnover" else 3
            assert payload["metadata"]["universe_eligibility"]["eligible_count"] == expected_eligible
            assert payload["metadata"]["universe_eligibility"]["liquidity_floor_usd"] == 50000000
            assert payload["metadata"]["snapshot_coverage"]["comparable_count"] == 7
            assert payload["metadata"]["universe_eligibility"]["metadata_status"] == "PARTIAL"
            assert {"trigger":"Universe Eligibility", "error_type":"IncompleteMetadata"} in payload["metadata"]["trigger_errors"]
        else:
            assert not selected
            assert payload["metadata"]["snapshot_coverage"]["comparable_count"] == 0
        assert isinstance(calls[0],list)
'''


@pytest.mark.parametrize('mode', ['morning', 'afternoon'])
@pytest.mark.parametrize('shape', ['price_first', 'ticker_first', 'empty', 'ambiguous', 'low_turnover', 'metadata_empty'])
def test_real_expanded_batch(tmp_path, mode, shape):
    output = tmp_path / 'result.json'
    env = dict(os.environ, US_SCREENING_UNIVERSE='listed_common',
               US_SCREENING_MIN_MARKET_CAP_USD='500000000',
               PRISM_DISABLE_SIGNAL_PUBLISH='1',
               PRISM_OBSERVABILITY_SPOOL=str(tmp_path / 'events.jsonl'),
               REGIME_WEAK_THIRD_SLOT_SHADOW_ENABLED='false')
    result = subprocess.run([sys.executable, '-c', RUN, str(ROOT), mode, shape, str(output)],
                            cwd=ROOT, env=env, capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr
    data = json.loads(output.read_text())
    assert data['metadata']['min_market_cap_usd'] == 500000000


def test_expanded_defaults_to_approved_one_billion_cap_before_network(tmp_path):
    code = '''
import sys, socket
from unittest.mock import patch
sys.path.insert(0, "prism-us")
with patch("dotenv.load_dotenv", return_value=False), patch.object(socket.socket,"connect",side_effect=AssertionError("network")):
 import us_trigger_batch as batch
 assert batch.DEFAULT_US_SCREENING_UNIVERSE == "listed_common"
 assert batch.DEFAULT_US_MIN_MARKET_CAP_USD == 1000000000
 try: batch._load_screening_inputs("20260914")
 except AssertionError as exc: assert "network" in str(exc)
 else: raise AssertionError("Default expanded mode must reach the directory boundary")
'''
    env = dict(os.environ, US_SCREENING_UNIVERSE='listed_common')
    env.pop('US_SCREENING_MIN_MARKET_CAP_USD', None)
    result = subprocess.run([sys.executable, '-c', code], cwd=ROOT, env=env,
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
