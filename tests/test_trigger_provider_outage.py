"""Screening must honor the configured data chain without inventing evidence."""
import pandas as pd
import pytest

import cores.market_data as market_data
import trigger_batch as trigger


@pytest.mark.parametrize("field,bad", [(field, value) for field in ("per", "pbr")
                                      for value in ("-1", "0", "NaN", "inf", "")]
                                     + [("per", "10"), ("pbr", "0.8")])
def test_contrarian_requires_actual_kis_finite_positive_ratios(monkeypatch, field, bad):
    from datetime import datetime
    from zoneinfo import ZoneInfo
    from cores.market_data.kis_source import KisSource
    source = KisSource()
    quote = {"per": "10", "pbr": "0.8", "eps": "100", "bps": "1000", field: bad}
    monkeypatch.setattr(source, "_require_current_range", lambda *_: None)
    monkeypatch.setattr(source, "_current_quote", lambda _: (
        quote, datetime(2026, 9, 11, 10, tzinfo=ZoneInfo("Asia/Seoul"))))
    monkeypatch.setattr(market_data, "get_market_fundamental_by_date",
                        lambda start, end, ticker: source.fundamentals(ticker, start, end))
    monkeypatch.setattr(market_data, "get_market_ohlcv_by_date",
                        lambda *_: pd.DataFrame({"High": [100.0]}))
    monkeypatch.setattr(trigger, "enhance_dataframe", lambda frame: frame)
    current = pd.DataFrame({"Open": [75.0], "Close": [80.0], "Volume": [1000000],
                            "Amount": [20000000000]}, index=["005930"])
    previous = current.assign(Close=76.0)
    result = trigger.trigger_contrarian_value("20260911", current, previous)
    assert result.empty == (bad not in ("10", "0.8"))
    if not result.empty:
        assert result.loc["005930", "TrailingPE"] == 10.0
        assert result.loc["005930", "PriceToBook"] == 0.8


@pytest.mark.parametrize("missing", [None, "history", "fundamentals", "unprofitable", "invalid_pbr", "shallow"])
def test_contrarian_uses_chain_and_preserves_required_evidence(monkeypatch, missing):
    direct_calls = []

    def forbidden(*args, **kwargs):
        direct_calls.append(args)
        raise RuntimeError("direct KRX authentication unavailable")

    history = pd.DataFrame({"High": [100.0]})
    fundamentals = pd.DataFrame({"PER": [10.0], "PBR": [0.8]})
    if missing == "unprofitable":
        fundamentals["PER"] = -1.0
    elif missing == "invalid_pbr":
        fundamentals["PBR"] = 0.0
    elif missing == "shallow":
        history["High"] = 85.0
    calls = []

    def fetch_history(start, end, ticker):
        calls.append(("history", start, end, ticker))
        return pd.DataFrame() if missing == "history" else history

    def fetch_fundamentals(start, end, ticker):
        calls.append(("fundamentals", start, end, ticker))
        return pd.DataFrame() if missing == "fundamentals" else fundamentals

    monkeypatch.setattr(market_data, "get_market_ohlcv_by_date", fetch_history)
    monkeypatch.setattr(market_data, "get_market_fundamental_by_date", fetch_fundamentals)
    monkeypatch.setattr(trigger, "enhance_dataframe", lambda frame: frame)
    snapshot = pd.DataFrame({"Open": [75.0], "Close": [80.0], "Volume": [1_000_000],
                             "Amount": [20_000_000_000]}, index=["005930"])
    previous = snapshot.copy()
    previous["Close"] = 76.0
    result = trigger.trigger_contrarian_value("20260911", snapshot, previous)
    assert direct_calls == []
    assert calls[0] == ("history", "20250911", "20260911", "005930")
    if missing:
        assert result.empty
    else:
        assert result.index.tolist() == ["005930"]
        assert result.loc["005930", "Drawdown"] == -20.0
        assert result.loc["005930", "TrailingPE"] == 10.0
        assert result.loc["005930", "PriceToBook"] == 0.8


@pytest.mark.parametrize("column", ["stock_name", "Company Name", "종목명"])
def test_supplied_names_do_not_require_krx_authentication(monkeypatch, column):
    calls = []

    def forbidden():
        calls.append(True)
        raise RuntimeError("KRX authentication unavailable")

    monkeypatch.setattr(trigger, "fetch_kis_master_universe", forbidden)
    monkeypatch.setattr(trigger, "_TICKER_NAME_CACHE", None)
    monkeypatch.setattr(market_data, "get_market_ticker_name", lambda ticker: ticker)
    snapshot = pd.DataFrame({column: ["삼성전자"], "Close": [80.0]}, index=["005930"])
    result = trigger.enhance_dataframe(snapshot)
    assert calls == []
    assert result.loc["005930", "stock_name"] == "삼성전자"
