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


def _chain_cannot_answer(monkeypatch):
    """소스 체인이 이름을 못 찾는 상태로 고정한다.

    2026-08-05 부터 ``_get_display_ticker_name`` 은 KRX 대량 조회가 실패하면
    ``cores.market_data`` 체인으로 물러난다(그전에는 곧바로 종목코드였고, 그래서
    KRX 차단일에 시그널 얼럿이 "009150 (009150)" 으로 나갔다).

    체인은 전 소스 소진 시 티커를 그대로 돌려주므로, 여기서 그 상태를 명시적으로
    만든다. 이걸 안 걸면 테스트가 **실제 FDR 상장목록을 네트워크로 받아와** 결과가
    실행 순서에 따라 달라진다 — 실제로 단독 실행 0.35초 / 다른 파일과 함께 18초에
    결과가 갈렸다.
    """
    import cores.market_data as market_data

    monkeypatch.setattr(market_data, "get_market_ticker_name", lambda ticker: ticker)


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
    _chain_cannot_answer(monkeypatch)

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
    _chain_cannot_answer(monkeypatch)

    df = pd.DataFrame({"Close": [70000]}, index=["005930"])

    first = t.enhance_dataframe(df)
    second = t.enhance_dataframe(df)

    assert calls == ["ALL"]
    assert first["stock_name"].to_dict() == {"005930": "005930"}
    assert second["stock_name"].to_dict() == {"005930": "005930"}


def test_enhance_dataframe_uses_the_chain_when_the_bulk_lookup_fails(monkeypatch):
    """KRX 대량 조회가 죽어도 체인이 답하면 실제 종목명이 붙는다.

    이것이 2026-08-05 얼럿 결함의 핵심이다. 위 두 테스트가 '실패하면 코드'를
    검증하는데, 그것만 있으면 **체인이 답할 수 있는데도 코드로 나가는 상태**를
    통과시킨다. 실제로 그렇게 사용자에게 나갔다.
    """
    import cores.market_data as market_data

    class TimeoutClient:
        def get_market_ticker_name(self, market="ALL"):
            raise TimeoutError("KRX timeout")

    monkeypatch.setattr(t, "_get_client", lambda: TimeoutClient(), raising=False)
    monkeypatch.setattr(
        market_data,
        "get_market_ticker_name",
        lambda ticker: {"009150": "삼성전기", "103140": "풍산"}.get(ticker, ticker),
    )

    df = pd.DataFrame({"Close": [1356000, 80700]}, index=["009150", "103140"])

    result = t.enhance_dataframe(df)

    assert result["stock_name"].to_dict() == {
        "009150": "삼성전기",
        "103140": "풍산",
    }


def test_enhance_dataframe_normalizes_numeric_ticker_index(monkeypatch):
    class FakeClient:
        def get_market_ticker_name(self, market="ALL"):
            return {"005930": "SAMSUNG"}

    monkeypatch.setattr(t, "_get_client", lambda: FakeClient(), raising=False)
    _chain_cannot_answer(monkeypatch)

    df = pd.DataFrame({"Close": [70000, 120000]}, index=[5930, 660])

    result = t.enhance_dataframe(df)

    assert result["stock_name"].to_dict() == {
        5930: "SAMSUNG",
        660: "000660",
    }
