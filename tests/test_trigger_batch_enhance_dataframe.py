#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import os
import sys
import types

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

krx_stub = types.ModuleType("krx_data_client")
krx_stub.get_market_ohlcv_by_ticker = lambda *args, **kwargs: pd.DataFrame()
krx_stub.get_nearest_business_day_in_a_week = lambda *args, **kwargs: "20260703"
krx_stub.get_market_cap_by_ticker = lambda *args, **kwargs: pd.DataFrame()
krx_stub.get_market_ticker_name = lambda ticker: ticker
krx_stub._get_client = lambda: None
sys.modules.setdefault("krx_data_client", krx_stub)

import trigger_batch as t


def setup_function():
    t._TICKER_NAME_CACHE = None


def test_enhance_dataframe_fetches_ticker_names_in_one_batch(monkeypatch):
    calls = []

    class FakeClient:
        def get_market_ticker_name(self, market="ALL"):
            calls.append(market)
            return {
                "005930": "SAMSUNG",
                "000660": "SKHYNIX",
            }

    monkeypatch.setattr(t, "_get_client", lambda: FakeClient(), raising=False)

    df = pd.DataFrame({"Close": [70000, 120000]}, index=["005930", "000660"])

    result = t.enhance_dataframe(df)

    assert calls == ["ALL"]
    assert result["stock_name"].to_dict() == {
        "005930": "SAMSUNG",
        "000660": "SKHYNIX",
    }


def test_enhance_dataframe_keeps_rows_when_name_lookup_times_out(monkeypatch):
    calls = []

    class TimeoutClient:
        def get_market_ticker_name(self, market="ALL"):
            calls.append(market)
            raise TimeoutError("KRX timeout")

    monkeypatch.setattr(t, "_get_client", lambda: TimeoutClient(), raising=False)

    df = pd.DataFrame({"Close": [70000, 120000]}, index=["005930", "000660"])

    result = t.enhance_dataframe(df)

    assert list(result.index) == ["005930", "000660"]
    assert result["stock_name"].to_dict() == {
        "005930": "005930",
        "000660": "000660",
    }
    assert calls == ["ALL"]


def test_enhance_dataframe_caches_failed_lookup_for_process(monkeypatch):
    calls = []

    class TimeoutClient:
        def get_market_ticker_name(self, market="ALL"):
            calls.append(market)
            raise TimeoutError("KRX timeout")

    monkeypatch.setattr(t, "_get_client", lambda: TimeoutClient(), raising=False)

    df = pd.DataFrame({"Close": [70000]}, index=["005930"])

    first = t.enhance_dataframe(df)
    second = t.enhance_dataframe(df)

    assert calls == ["ALL"]
    assert first["stock_name"].to_dict() == {"005930": "005930"}
    assert second["stock_name"].to_dict() == {"005930": "005930"}


def test_enhance_dataframe_normalizes_numeric_ticker_index(monkeypatch):
    class FakeClient:
        def get_market_ticker_name(self, market="ALL"):
            return {"005930": "SAMSUNG"}

    monkeypatch.setattr(t, "_get_client", lambda: FakeClient(), raising=False)

    df = pd.DataFrame({"Close": [70000, 120000]}, index=[5930, 660])

    result = t.enhance_dataframe(df)

    assert result["stock_name"].to_dict() == {
        5930: "SAMSUNG",
        660: "000660",
    }
