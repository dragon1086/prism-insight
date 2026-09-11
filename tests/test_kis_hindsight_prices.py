"""Weekend hindsight uses requested KIS candles, not a current-only universe."""
import ast
import asyncio
import logging
from pathlib import Path
import threading

import pandas as pd
import pytest

from cores import market_data
from tracking.helpers import get_requested_session_prices


@pytest.fixture
def weekend_prices(monkeypatch):
    calls = []
    monkeypatch.setattr(market_data, "get_nearest_business_day_in_a_week", lambda *a, **k: "20260911")

    def forbidden(*args, **kwargs):
        raise AssertionError("whole-market snapshot must not be requested")

    def candle(start, end, ticker):
        calls.append((start, end, ticker, threading.get_ident()))
        price = {"005930": 72000, "000660": float("nan"), "000001": 0}.get(ticker, 10)
        day = "20260910" if ticker == "999999" else "20260911"
        return pd.DataFrame({"Close": [price]}, index=pd.to_datetime([day]))

    monkeypatch.setattr(market_data, "get_market_ohlcv_by_ticker", forbidden)
    monkeypatch.setattr(market_data, "get_market_ohlcv_by_date", candle)
    return calls


def test_weekend_requested_tickers_exact_day_and_valid_prices_only(weekend_prices):
    assert get_requested_session_prices(["005930", "005930", "000660", "000001", "999999"]) == {"005930": 72000}
    assert len(weekend_prices) == 4
    assert all(call[:2] == ("20260911", "20260911") for call in weekend_prices)


def test_compression_uses_same_requested_only_prices(weekend_prices):
    from tracking.compression import CompressionManager
    manager = CompressionManager.__new__(CompressionManager)
    assert manager._fetch_hindsight_prices([{"ticker": "005930"}, {"ticker": "005930"}]) == {"005930": 72000}
    assert len(weekend_prices) == 1


@pytest.mark.asyncio
async def test_weekly_sell_evaluation_reads_candles_off_thread(weekend_prices):
    # Compile only the pure entrypoint to avoid unrelated credential-loading imports.
    path = Path(__file__).resolve().parents[1] / "weekly_insight_report.py"
    node = next(n for n in ast.parse(path.read_text()).body if isinstance(n, ast.AsyncFunctionDef) and n.name == "_get_sell_evaluation")
    namespace = {
        "asyncio": asyncio, "logger": logging.getLogger(__name__),
        "_get_primary_account_key": lambda market: "kr" if market == "kr" else None,
        "_safe_query_all": lambda *a, **k: [("005930", "삼성전자", 70000)],
        "_sell_verdict": lambda percentage: "검토",
    }
    exec(compile(ast.Module(body=[node], type_ignores=[]), str(path), "exec"), namespace)
    result = await namespace["_get_sell_evaluation"](None, "2026-09-07")
    assert "현재가 72,000원" in result
    assert weekend_prices[0][3] != threading.get_ident()
