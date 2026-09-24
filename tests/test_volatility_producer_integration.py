"""Actual KR/US trend-facts producers feed the actual deterministic buy gate.

Each market imports in its own subprocess to preserve its real cores namespace.
Only provider and optional market-pulse boundaries are replaced; agent __init__,
network, accounts, orders, model inference and channel delivery never run.
"""

import os
from pathlib import Path
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]

RUN = r'''
from contextlib import ExitStack
from pathlib import Path
import socket
import sqlite3
import sys
from types import SimpleNamespace
from unittest.mock import patch

root, market = Path(sys.argv[1]), sys.argv[2]
sys.path[:0] = ([str(root / "prism-us"), str(root)] if market == "US" else [str(root)])
forbidden = []
provider_calls = []

def deny(*args, **kwargs):
    forbidden.append(repr(args))
    raise AssertionError("Network/accounts/orders/channels forbidden")

with ExitStack() as stack:
    stack.enter_context(patch.object(socket.socket, "connect", deny))
    stack.enter_context(patch.object(socket, "create_connection", deny))
    stack.enter_context(patch.object(sqlite3, "connect", deny))
    stack.enter_context(patch("dotenv.load_dotenv", return_value=False))
    import pandas as pd
    import cores
    closes = [100. + i * .1 for i in range(260)]
    frame = pd.DataFrame({
        "Open": closes, "High": [c * 1.02 for c in closes],
        "Low": [c * .98 for c in closes], "Close": closes, "Volume": [1000000] * 260,
    }, index=pd.bdate_range(end="2026-09-23", periods=260))

    if market == "KR":
        import stock_tracking_agent as tracking
        from cores import stock_chart, regime_policy, buy_gate, shadow_lifecycle
        assert Path(tracking.__file__).resolve() == root / "stock_tracking_agent.py"

        def stock_bars(start, end, ticker, adjusted):
            assert ticker == "005930" and adjusted is True
            provider_calls.append("stock")
            return frame.copy()

        def index_bars(start, end, ticker):
            provider_calls.append("index")
            return frame.copy()

        stack.enter_context(patch.object(stock_chart, "get_market_ohlcv_by_date", stock_bars))
        stack.enter_context(patch.object(stock_chart, "get_index_ohlcv_by_date", index_bars))
        stack.enter_context(patch.object(stock_chart, "_detect_index_ticker", return_value="1001"))
        stack.enter_context(patch.object(regime_policy, "get_market_pulse_detail", return_value=None))
        agent = object.__new__(tracking.StockTrackingAgent)
        ticker = "005930"
    else:
        import us_stock_tracking_agent as tracking
        from cores import us_data_client
        assert Path(tracking.__file__).resolve() == root / "prism-us/us_stock_tracking_agent.py"
        assert Path(us_data_client.__file__).resolve() == root / "prism-us/cores/us_data_client.py"
        us_frame = frame.rename(columns=str.lower)

        def stock_bars(ticker, period):
            assert ticker == "AAPL" and period == "2y"
            provider_calls.append("stock")
            return us_frame.copy()

        def index_bars(ticker, period):
            assert ticker == "^GSPC" and period == "6mo"
            provider_calls.append("index")
            return us_frame.copy()

        stack.enter_context(patch.object(us_data_client, "get_us_data_client", return_value=SimpleNamespace(
            get_ohlcv=stock_bars, get_index_data=index_bars,
        )))
        original_import = tracking._import_from_main_cores
        regime_policy = original_import("prism_root_regime_policy", "cores/regime_policy.py")
        stack.enter_context(patch.object(regime_policy, "get_market_pulse_detail", return_value=None))

        def main_import(name, path):
            return regime_policy if path == "cores/regime_policy.py" else original_import(name, path)

        stack.enter_context(patch.object(tracking, "_import_from_main_cores", main_import))
        buy_gate = original_import("prism_root_buy_gate", "cores/buy_gate.py")
        # Explicit policy-mode tests load the REAL shared lifecycle module under
        # the name consumed by the gate; this does not test US lifecycle routing.
        shadow_lifecycle = original_import("cores.shadow_lifecycle", "cores/shadow_lifecycle.py")
        agent = object.__new__(tracking.USStockTrackingAgent)
        ticker = "AAPL"

    facts = agent._get_trend_facts(ticker)
    assert provider_calls == ["stock", "index"], provider_calls
    assert "T1_hit" in facts and "T2_hit" in facts, facts
    assert "ATR20=+4.0% / ADR20=+4.1%" in facts, facts
    assert "MA200" in facts and "데이터 없음" not in facts, facts
    scenario = {"buy_score": 8, "min_score": 5, "target_price": 110.,
                "stop_loss": 98., "risk_reward_ratio": 5., "market_condition": "moderate_bull"}
    for mode in ("off", "shadow", "live"):
        with patch.object(shadow_lifecycle, "feature_mode", return_value=mode):
            result = buy_gate.evaluate_production_buy_gate(
                scenario, current_price=100., market_regime="moderate_bull", trend_facts=facts,
            )
            unsigned = buy_gate.evaluate_production_buy_gate(
                scenario, current_price=100., market_regime="moderate_bull",
                trend_facts=facts.replace("ATR20=+", "ATR20=").replace("ADR20=+", "ADR20="),
            )
        assert result == unsigned, (mode, result, unsigned)
        assert result["atr20_pct"] == 4.0 and result["adr20_pct"] == 4.1, result
        assert result["volatility_noise_floor_pct"] == 2.05, result
        assert result["allowed"] is (mode != "live"), result
        relevant = [f for f in result["findings"] if f["code"] == "stop_below_volatility_noise_floor"]
        assert len(relevant) == (0 if mode == "off" else 1), result
        if relevant:
            assert relevant[0]["hard"] is (mode == "live"), result
    assert not forbidden, forbidden
    print(market + " actual producer -> gate: off/shadow/live verified; no external calls")
'''


@pytest.mark.parametrize("market", ["KR", "US"])
def test_real_volatility_producer_to_buy_gate(tmp_path, market):
    result = subprocess.run(
        [sys.executable, "-c", RUN, str(ROOT), market], cwd=tmp_path,
        text=True, capture_output=True, timeout=45, check=False,
        env=dict(os.environ, PRISM_DISABLE_SIGNAL_PUBLISH="1", TREND_RESEARCH_CAPTURE_ENABLED="false",
                 PRISM_OBSERVABILITY_SPOOL=str(tmp_path / "events.jsonl")),
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "no external calls" in result.stdout
