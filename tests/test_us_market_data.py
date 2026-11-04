"""KIS overseas price provider (prism_core.us_market_data) without network.

Shapes covered: paged stock rows (raw + adjusted), blank '' fields, duplicate
dates, empty responses, rate-limit retry, rejected requests, splits (forward
and reverse), index volume published as 0, unknown index, yfinance-compatible
history()/download() framing and quotes.
"""
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

import prism_core.us_market_data as md


class Resp:
    def __init__(self, rows=None, ok=True, message=""):
        self.rows, self.ok, self.message = rows or [], ok, message

    def isOK(self):
        return self.ok

    def getErrorMessage(self):
        return self.message

    def getBody(self):
        return SimpleNamespace(output2=self.rows)


def stock_row(day, close, volume=1000, **overrides):
    row = {"xymd": day, "open": str(close), "high": str(close), "low": str(close), "clos": str(close),
           "tvol": str(volume)}
    row.update(overrides)
    return row


class FakeClient:
    """Serves {modp: rows newest-first}; pages of `page` rows ending at BYMD."""

    def __init__(self, raw=(), adj=None, index_rows=(), page=100, responses=None, exchange="NASD"):
        self.series = {"0": list(raw), "1": list(adj if adj is not None else raw)}
        self.index_rows, self.page, self.calls = list(index_rows), page, []
        self.responses = list(responses or [])
        self.exchange = exchange

    def _resolve_exchange(self, symbol):
        return self.exchange

    def _request(self, url, tr_id, params):
        self.calls.append((tr_id, dict(params)))
        if self.responses:
            return self.responses.pop(0)
        if tr_id == md.INDEX_TR_ID:
            end = params["FID_INPUT_DATE_2"]
            rows = [r for r in self.index_rows if r["stck_bsop_date"] <= end]
            return Resp(rows[: self.page])
        rows = [r for r in self.series[params["MODP"]] if r["xymd"] <= params["BYMD"]]
        return Resp(rows[: self.page])

    def get_current_price(self, ticker, strict=False):
        self.calls.append(("quote", {"ticker": ticker, "strict": strict}))
        return None if strict else {"current_price": 12.5}


@pytest.fixture
def client(monkeypatch):
    def install(**kwargs):
        fake = FakeClient(**kwargs)
        monkeypatch.setattr(md, "_client", fake)
        monkeypatch.setattr(md.time, "sleep", lambda _: None)
        return fake
    return install


def days(n, start="2026-10-01"):
    return [d.strftime("%Y%m%d") for d in pd.bdate_range(start, periods=n)]


def test_pages_walk_back_until_start_and_keep_order(client):
    dates = days(7)
    fake = client(raw=[stock_row(d, 10 + i) for i, d in reversed(list(enumerate(dates)))], page=3)
    frame = md.daily_history("AAPL", dates[0], dates[-1])
    assert list(frame.index.strftime("%Y%m%d")) == dates
    assert list(frame["Close"]) == [10, 11, 12, 13, 14, 15, 16]
    assert frame.index.tz is None and frame.index.name == "Date"
    # raw and adjusted series, three pages each, all at the resolved exchange.
    stock_calls = [p for tr, p in fake.calls if tr == md.STOCK_TR_ID]
    assert len(stock_calls) == 6 and {p["EXCD"] for p in stock_calls} == {"NAS"}


def test_blank_fields_stay_missing_not_zero(client):
    dates = days(2)
    client(raw=[stock_row(dates[1], 11, clos="", tvol=""), stock_row(dates[0], 10, open="0")])
    frame = md.daily_history("AAPL", dates[0], dates[1])
    assert np.isnan(frame.loc[dates[1], "Close"]) and np.isnan(frame.loc[dates[1], "Volume"])
    assert np.isnan(frame.loc[dates[0], "Open"])  # a zero price is not a price


def test_duplicate_dates_keep_one_row(client):
    dates = days(2)
    client(raw=[stock_row(dates[1], 11), stock_row(dates[1], 99), stock_row(dates[0], 10)])
    frame = md.daily_history("AAPL", dates[0], dates[1])
    assert len(frame) == 2 and frame["Close"].iloc[-1] == 11


def test_empty_response_raises_unavailable(client):
    client(raw=[])
    with pytest.raises(md.UsMarketDataUnavailable):
        md.daily_history("ZZZZ", "2026-10-01", "2026-10-05")


def test_rejection_raises_and_rate_limit_retries(client):
    dates = days(1)
    fake = client(responses=[Resp(ok=False, message="EGW00201 초당 거래건수를 초과하였습니다"),
                             Resp([stock_row(dates[0], 10)]), Resp([stock_row(dates[0], 10)])])
    assert md.daily_history("AAPL", dates[0], dates[0])["Close"].iloc[0] == 10
    assert len(fake.calls) == 3
    client(responses=[Resp(ok=False, message="EGW00999 invalid")])
    with pytest.raises(md.UsMarketDataUnavailable, match="EGW00999"):
        md.daily_history("AAPL", dates[0], dates[0])


_EXPIRED = '{"rt_cd":"1","msg1":"기간이 만료된 token 입니다.","msg_cd":"EGW00123"}'


def test_expired_token_rebuilds_client_once(client, monkeypatch):
    """2026-11-04: the cached client kept an expired token until the worker restarted."""
    dates = days(1)
    stale = client(responses=[Resp(ok=False, message=_EXPIRED)])
    fresh = FakeClient(raw=[stock_row(dates[0], 10)])
    monkeypatch.setattr(md, "_us_trading_class", lambda: (lambda auto_trading=False: fresh))
    assert md.daily_history("AAPL", dates[0], dates[0])["Close"].iloc[0] == 10
    assert len(stale.calls) == 1 and md._client is fresh


def test_expired_token_twice_is_not_retried_again(client, monkeypatch):
    dates = days(1)
    client(responses=[Resp(ok=False, message=_EXPIRED)])
    again = FakeClient(responses=[Resp(ok=False, message=_EXPIRED)])
    monkeypatch.setattr(md, "_us_trading_class", lambda: (lambda auto_trading=False: again))
    with pytest.raises(md.UsMarketDataUnavailable, match="EGW00123"):
        md.daily_history("AAPL", dates[0], dates[0])
    assert len(again.calls) == 1


@pytest.mark.parametrize("ratio", [4.0, 0.1])
def test_split_matches_yfinance_basis(client, ratio):
    """Split on the third day: KIS raw is unadjusted, KIS volume never split-adjusted."""
    dates = days(4)
    adj_close = [100.0, 101.0, 102.0, 103.0]
    raw = [c * (ratio if i < 2 else 1) for i, c in enumerate(adj_close)]
    raw_volume = [1000 / ratio if i < 2 else 1000 for i in range(4)]
    client(raw=[stock_row(d, c, v) for d, c, v in reversed(list(zip(dates, raw, raw_volume)))],
           adj=[stock_row(d, c * 0.99, v) for d, c, v in reversed(list(zip(dates, adj_close, raw_volume)))])
    unadjusted = md.daily_history("AAPL", dates[0], dates[-1])
    adjusted = md.daily_history("AAPL", dates[0], dates[-1], adjusted=True)
    assert list(unadjusted["Close"]) == pytest.approx(adj_close)
    assert list(adjusted["Close"]) == pytest.approx([c * 0.99 for c in adj_close])
    assert list(unadjusted["Volume"]) == pytest.approx([1000.0] * 4)
    assert list(unadjusted["Stock Splits"]) == pytest.approx([0.0, 0.0, ratio, 0.0])


def test_dividend_drift_is_not_a_split(client):
    dates = days(3)
    client(raw=[stock_row(d, 50) for d in reversed(dates)],
           adj=[stock_row(d, c) for d, c in reversed(list(zip(dates, [48.0, 49.0, 50.0])))])
    frame = md.daily_history("T", dates[0], dates[-1])
    assert list(frame["Close"]) == [50, 50, 50] and list(frame["Stock Splits"]) == [0.0, 0.0, 0.0]


def test_class_shares_use_kis_spelling(client):
    dates = days(1)
    fake = client(raw=[stock_row(dates[0], 500)], exchange="NYSE")
    md.daily_history("BRK-B", dates[0], dates[0])
    assert {(p["SYMB"], p["EXCD"]) for tr, p in fake.calls if tr == md.STOCK_TR_ID} == {("BRK/B", "NYS")}


def test_index_zero_volume_is_unknown_and_unknown_index_raises(client):
    rows = [{"stck_bsop_date": d, "ovrs_nmix_oprc": "1", "ovrs_nmix_hgpr": "2", "ovrs_nmix_lwpr": "0.5",
             "ovrs_nmix_prpr": "1.5", "acml_vol": v} for d, v in zip(reversed(days(2)), ("0", "8180551400"))]
    fake = client(index_rows=rows)
    frame = md.daily_history("^IXIC", days(2)[0], days(2)[1])
    assert np.isnan(frame["Volume"].iloc[-1]) and frame["Volume"].iloc[0] == 8180551400
    assert fake.calls[0][1]["FID_INPUT_ISCD"] == "COMP" and fake.calls[0][1]["FID_COND_MRKT_DIV_CODE"] == "N"
    with pytest.raises(md.UsMarketDataUnavailable):
        md.daily_history("^RUT", "2026-10-01", "2026-10-02")


def test_history_matches_yfinance_framing(client, monkeypatch):
    dates = days(8, start="2026-10-12")
    client(raw=[stock_row(d, 10 + i) for i, d in reversed(list(enumerate(dates)))])
    monkeypatch.setattr(md.pd.Timestamp, "now", classmethod(lambda cls, tz=None: pd.Timestamp(dates[-1], tz=tz)))
    last5 = md.history("AAPL", period="5d")
    assert len(last5) == 5 and str(last5.index.tz) == "America/New_York"
    window = md.history("AAPL", start="2026-10-13", end="2026-10-16")  # end exclusive, like yfinance
    assert list(window.index.strftime("%Y-%m-%d")) == ["2026-10-13", "2026-10-14", "2026-10-15"]
    assert {"Open", "High", "Low", "Close", "Volume", "Stock Splits"} <= set(window.columns)
    assert "Dividends" not in window.columns  # unknown, never zero
    with pytest.raises(ValueError):
        md.history("AAPL", period="bogus")


def test_download_groups_by_ticker_and_omits_unpriced(monkeypatch):
    def history(symbol, **kwargs):
        if symbol == "BAD":
            raise md.UsMarketDataUnavailable("no rows")
        return pd.DataFrame({"Close": [1.0, 2.0]}, index=pd.DatetimeIndex(["2026-10-20", "2026-10-21"]))
    monkeypatch.setattr(md, "history", history)
    frame = md.download(["XLK", "BAD", "XLK"], period="5d")
    assert list(frame["XLK"]["Close"]) == [1.0, 2.0]
    with pytest.raises(KeyError):
        frame["BAD"]
    assert md.download(["BAD"]).empty


def test_quote_strict_by_default(client):
    fake = client()
    assert md.current_quote("aapl") is None
    assert md.current_quote("aapl", allow_previous_close=True) == 12.5
    assert [c[1]["strict"] for c in fake.calls] == [True, False]


def test_missing_client_is_unavailable(monkeypatch):
    monkeypatch.setattr(md, "_client", None)

    def broken():
        raise FileNotFoundError("kis_devlp.yaml")
    monkeypatch.setattr(md, "_us_trading_class", broken)
    with pytest.raises(md.UsMarketDataUnavailable):
        md.daily_history("AAPL", "2026-10-01", "2026-10-02")


@pytest.fixture
def fresh_chain(monkeypatch):
    monkeypatch.setattr(md, "_chain", None)
    yield
    md.set_default_chain(None)


def test_default_chain_is_kis_only(fresh_chain, monkeypatch):
    monkeypatch.delenv("PRISM_US_MARKET_DATA_SOURCES", raising=False)
    assert md.default_chain().names == ["kis"]


def test_unknown_source_name_is_refused(fresh_chain, monkeypatch):
    monkeypatch.setenv("PRISM_US_MARKET_DATA_SOURCES", "kis,bloomberg")
    with pytest.raises(ValueError):
        md.default_chain()


def no_kis(monkeypatch):
    def broken():
        raise FileNotFoundError("no config")
    monkeypatch.setattr(md, "_client", None)
    monkeypatch.setattr(md, "_us_trading_class", broken)


def fake_yfinance(monkeypatch, frame, info):
    import sys
    module = SimpleNamespace(Ticker=lambda symbol: SimpleNamespace(history=lambda **kwargs: frame, info=info))
    monkeypatch.setitem(sys.modules, "yfinance", module)


def test_falls_back_to_next_source_when_kis_cannot_answer(fresh_chain, monkeypatch):
    monkeypatch.setenv("PRISM_US_MARKET_DATA_SOURCES", "kis,yfinance")
    no_kis(monkeypatch)
    index = pd.DatetimeIndex(["2026-10-20", "2026-10-21"]).tz_localize("America/New_York")
    fake_yfinance(monkeypatch, pd.DataFrame({"Open": [1.0, 2.0], "High": [1.0, 2.0], "Low": [1.0, 2.0],
                                             "Close": [1.0, 2.0], "Volume": [10, 20], "Dividends": [0, 0],
                                             "Stock Splits": [0.0, 0.0]}, index=index),
                  {"regularMarketPrice": None, "previousClose": 99.0})
    frame = md.daily_history("aapl", "2026-10-20", "2026-10-21")
    assert list(frame["Close"]) == [1.0, 2.0] and frame.index.tz is None
    assert list(frame.columns) == ["Open", "High", "Low", "Close", "Volume", "Stock Splits"]
    assert md.current_quote("AAPL") is None
    assert md.current_quote("AAPL", allow_previous_close=True) == 99.0


def test_every_source_failing_raises_the_public_error(fresh_chain, monkeypatch):
    monkeypatch.setenv("PRISM_US_MARKET_DATA_SOURCES", "kis,yfinance")
    no_kis(monkeypatch)
    fake_yfinance(monkeypatch, pd.DataFrame(), {})
    with pytest.raises(md.UsMarketDataUnavailable, match="kis.*yfinance"):
        md.daily_history("AAPL", "2026-10-20", "2026-10-21")
