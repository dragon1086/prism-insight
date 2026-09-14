"""Remote report charts must not use local master data or invent history."""

from types import SimpleNamespace

import pandas as pd
import pytest

from cores import market_data, stock_chart


@pytest.fixture
def remote(monkeypatch):
    monkeypatch.setenv("PRISM_MARKET_DATA_REMOTE_URL", "http://127.0.0.1:8765")
    monkeypatch.setattr(market_data, "get_market_ticker_list",
                        lambda **kwargs: pytest.fail("Local master must not be called"))


@pytest.mark.parametrize("market,expected", [("KOSPI", "1001"), ("KOSDAQ", "2001")])
def test_remote_market_detection(remote, monkeypatch, market, expected):
    def fetch(capability, ticker):
        assert (capability, ticker) == ("ticker_market", "000660")
        return market

    monkeypatch.setattr(market_data, "default_chain", lambda: SimpleNamespace(fetch=fetch))
    assert stock_chart._detect_index_ticker("000660") == expected


@pytest.mark.parametrize("failure", [True, False])
def test_remote_detection_fails_closed(remote, monkeypatch, caplog, failure):
    def fetch(*args):
        if failure:
            raise RuntimeError("/private/keys.yaml token=secret")
        return "UNKNOWN"

    monkeypatch.setattr(market_data, "default_chain", lambda: SimpleNamespace(fetch=fetch))
    assert stock_chart._detect_index_ticker("000660") is None
    assert "skipping RS panel" in caplog.text
    assert "/private" not in caplog.text
    assert "token=secret" not in caplog.text


def test_no_remote_keeps_local_market_detection(monkeypatch):
    monkeypatch.delenv("PRISM_MARKET_DATA_REMOTE_URL", raising=False)
    monkeypatch.setattr(stock_chart, "_KOSPI_TICKERS_CACHE", None)
    monkeypatch.setattr(market_data, "get_market_ticker_list", lambda **kwargs: ["000660"])
    assert stock_chart._detect_index_ticker("000660") == "1001"
    assert stock_chart._detect_index_ticker("123456") == "2001"


@pytest.mark.parametrize("chart", ["create_oneil_daily_chart", "create_oneil_weekly_chart"])
def test_unknown_market_skips_only_rs_panel(monkeypatch, chart):
    frame = pd.DataFrame({"Open": 100., "High": 110., "Low": 90., "Close": 105., "Volume": 1000.},
                         index=pd.date_range("2025-06-01", periods=300, freq="B"))
    monkeypatch.setattr(stock_chart, "get_market_ohlcv_by_date", lambda *args, **kwargs: frame.copy())
    monkeypatch.setattr(stock_chart, "_detect_index_ticker", lambda ticker: None)
    monkeypatch.setattr(stock_chart, "_fetch_index_close", lambda *args: pytest.fail("Unknown benchmark must not fetch"))
    fig = getattr(stock_chart, chart)("000660", company_name="SK")
    assert fig is not None
    stock_chart.plt.close(fig)


@pytest.mark.parametrize("chart,fetch", [
    ("create_market_cap_chart", "get_market_cap_by_date"),
    ("create_fundamentals_chart", "get_market_fundamental_by_date"),
])
@pytest.mark.parametrize("rows,latest_only", [(2, True), (1, False)])
def test_snapshot_is_not_historical_chart(monkeypatch, chart, fetch, rows, latest_only):
    frame = pd.DataFrame({"MarketCap": [100] * rows, "PER": [10] * rows},
                         index=pd.date_range("2026-09-01", periods=rows))
    frame.attrs["latest_only"] = latest_only
    monkeypatch.setattr(stock_chart, fetch, lambda *args: frame)
    assert getattr(stock_chart, chart)("000660", company_name="SK") is None
