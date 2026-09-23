"""Public report quote provenance and raw-history isolation, without network."""
import importlib.util
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest
import yfinance as yf

from prism_core.report_technical_facts import build_report_technical_facts

ROOT = Path(__file__).resolve().parents[1]


def load(name):
    spec = importlib.util.spec_from_file_location("public_quote_" + name, ROOT / "prism-us/cores" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def freeze_clock(monkeypatch, module, instant):
    class FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return instant.astimezone(tz) if tz else instant.replace(tzinfo=None)
    monkeypatch.setattr(module, 'datetime', FrozenDateTime)


def test_report_raw_history_override_keeps_shared_default(monkeypatch):
    client_module = load("us_data_client")
    prefetch = load("data_prefetch")
    calls = []

    def history(**kwargs):
        calls.append(kwargs)
        raw = kwargs.get("auto_adjust") is False
        return pd.DataFrame({"Open": [101 if raw else np.nan], "High": [105 if raw else np.nan],
                             "Low": [99 if raw else np.nan], "Close": [np.nan], "Adj Close": [np.nan],
                             "Volume": [100]}, index=pd.DatetimeIndex(["2026-09-22"]))

    monkeypatch.setattr(yf, "Ticker", lambda _: SimpleNamespace(history=history))
    client = client_module.USDataClient()
    client.get_ohlcv("DGX")
    assert "auto_adjust" not in calls[-1]
    monkeypatch.setattr(prefetch, "_get_us_data_client", lambda: client)
    text = prefetch.prefetch_us_stock_ohlcv("DGX")
    assert calls[-1]["auto_adjust"] is False
    assert "101.0" in text and "Latest observed Close (USD): N/A" in text
    assert "No independent provider Close recovery" in text


@pytest.mark.parametrize("current,expected_field,expected_time", [(235.0, "currentPrice", None), (230.0, "currentPrice", None), (None, "regularMarketPrice", 1234567890)])
def test_quote_timestamp_belongs_only_to_chosen_field(monkeypatch, current, expected_field, expected_time):
    module = load("us_data_client")
    monkeypatch.setattr(yf, "Ticker", lambda _: SimpleNamespace(info={
        "longName": "Example", "currentPrice": current, "regularMarketPrice": 230,
        "regularMarketTime": 1234567890, "marketState": "POST", "exchangeTimezoneName": "America/New_York"}))
    info = module.USDataClient().get_company_info("TEST")
    assert info["price_field_source"] == expected_field
    assert info["price_market_time"] == expected_time
    assert info["regular_market_time"] == 1234567890
    assert info["market_state"] == "POST" and info["captured_at_utc"]


@pytest.mark.parametrize("dividend,price,expected", [(4, 200, "2.00%"), (0, 200, "0.00%"),
                                                    (None, 200, "N/A"), (np.nan, 200, "N/A"), (4, 0, "N/A")])
def test_dividend_per_share_yield_has_explicit_definition(monkeypatch, dividend, price, expected):
    prefetch = load("data_prefetch")
    monkeypatch.setattr(prefetch, "_get_us_data_client", lambda: SimpleNamespace(get_company_info=lambda _: {
        "name": "Example", "price": price, "dividend_rate": dividend}))
    text = prefetch.prefetch_stock_info("TEST")
    assert f"| Indicated annualized dividend yield (dividendRate / report reference price) | {expected} |" in text
    assert "USD/share/year" in text and "not realized trailing yield" in text


def test_report_prefers_dated_regular_quote_without_changing_client_price(monkeypatch):
    client_module = load("us_data_client")
    prefetch = load("data_prefetch")
    freeze_clock(monkeypatch, prefetch, datetime.fromtimestamp(1790107200, timezone.utc) + timedelta(days=1))
    monkeypatch.setattr(yf, "Ticker", lambda _: SimpleNamespace(info={
        "longName": "Example", "currentPrice": 234.76, "regularMarketPrice": 233.0,
        "regularMarketTime": 1790107200, "dividendRate": 4.0}))
    client = client_module.USDataClient()
    info = client.get_company_info("TEST")
    assert info["price"] == 234.76 and info["price_market_time"] is None
    monkeypatch.setattr(prefetch, "_get_us_data_client", lambda: client)
    text = prefetch.prefetch_stock_info("TEST")
    assert "| Report reference price (dated regular quote preferred) | $233.00 |" in text
    assert "| Report reference field / market timestamp (Unix seconds) | regularMarketPrice / 1790107200 |" in text
    assert "| Current Price | $234.76 |" in text
    assert "| Selected price market timestamp (Unix seconds) | N/A |" in text


@pytest.mark.parametrize("timestamp", [None, 0, np.nan, True, 1e100, 4102444800])
def test_invalid_regular_timestamp_does_not_promote_dated_report_quote(monkeypatch, timestamp):
    prefetch = load("data_prefetch")
    monkeypatch.setattr(prefetch, "_get_us_data_client", lambda: SimpleNamespace(get_company_info=lambda _: {
        "name": "Example", "price": 234.76, "price_field_source": "currentPrice",
        "regular_market_price": 233, "regular_market_time": timestamp}))
    text = prefetch.prefetch_stock_info("TEST")
    assert "| Report reference price (dated regular quote preferred) | $234.76 |" in text
    assert "| Report reference field / market timestamp (Unix seconds) | currentPrice / N/A |" in text


@pytest.mark.parametrize("zone,expected", [("America/New_York", "2020-01-01T07:00:00-05:00"),
                                         ("not/a-zone", "N/A (valid exchange timezone unavailable)"),
                                         (None, "N/A (valid exchange timezone unavailable)")])
def test_report_reference_times_are_deterministic_and_timezone_safe(monkeypatch, zone, expected):
    prefetch = load("data_prefetch")
    freeze_clock(monkeypatch, prefetch, datetime(2020, 1, 2, 12, tzinfo=timezone.utc))
    timestamp = int(datetime(2020, 1, 1, 12, tzinfo=timezone.utc).timestamp())
    monkeypatch.setattr(prefetch, "_get_us_data_client", lambda: SimpleNamespace(get_company_info=lambda _: {
        "name": "Example", "price": 234.76, "price_field_source": "currentPrice",
        "regular_market_price": 233, "regular_market_time": timestamp, "exchange_timezone": zone}))
    text = prefetch.prefetch_stock_info("TEST")
    assert "| Report reference market time UTC | 2020-01-01T12:00:00+00:00 |" in text
    assert f"| Report reference exchange-local market time | {expected} |" in text


@pytest.mark.parametrize("skew,expected", [(60, "$233.00"), (600, "$234.76")])
def test_report_quote_allows_only_small_clock_skew(monkeypatch, skew, expected):
    prefetch = load("data_prefetch")
    timestamp = datetime.now(timezone.utc).timestamp() + skew
    monkeypatch.setattr(prefetch, "_get_us_data_client", lambda: SimpleNamespace(get_company_info=lambda _: {
        "name": "Example", "price": 234.76, "price_field_source": "currentPrice",
        "regular_market_price": 233, "regular_market_time": timestamp}))
    assert f"| Report reference price (dated regular quote preferred) | {expected} |" in prefetch.prefetch_stock_info("TEST")


@pytest.mark.parametrize("age,expected", [(3, "$233.00"), (6, "$233.00"), (8, "$234.76"), (365, "$234.76")])
def test_recent_weekend_holiday_quotes_allowed_but_stale_quote_not_preferred(monkeypatch, age, expected):
    prefetch = load("data_prefetch")
    now = datetime(2026, 9, 23, 12, tzinfo=timezone.utc)
    freeze_clock(monkeypatch, prefetch, now)
    timestamp = (now - timedelta(days=age)).timestamp()
    monkeypatch.setattr(prefetch, "_get_us_data_client", lambda: SimpleNamespace(get_company_info=lambda _: {
        "name": "Example", "price": 234.76, "price_field_source": "currentPrice", "dividend_rate": 4,
        "regular_market_price": 233, "regular_market_time": timestamp}))
    text = prefetch.prefetch_stock_info("TEST")
    assert f"| Report reference price (dated regular quote preferred) | {expected} |" in text
    if age > 7:
        assert "UNKNOWN_or_unavailable; not certified fresh" in text
        assert "| regularMarketPrice (separate observation) | $233.00 |" in text
        assert "report reference price) | 1.70% |" in text


def test_stale_regular_client_fallback_cannot_become_current_reference(monkeypatch):
    prefetch = load("data_prefetch")
    monkeypatch.setattr(prefetch, "_get_us_data_client", lambda: SimpleNamespace(get_company_info=lambda _: {
        "name": "Example", "price": 233, "price_field_source": "regularMarketPrice", "price_market_time": 1577880000,
        "regular_market_price": 233, "regular_market_time": 1577880000, "dividend_rate": 4}))
    text = prefetch.prefetch_stock_info("TEST")
    assert "| Report reference price (dated regular quote preferred) | N/A |" in text
    assert "report reference price) | N/A |" in text


def test_market_index_canonical_prices_use_points_not_usd(monkeypatch):
    prefetch = load("data_prefetch")
    data = pd.DataFrame({'close': [5000.]}, index=pd.DatetimeIndex(['2026-09-22']))
    monkeypatch.setattr(prefetch, '_get_us_data_client', lambda: SimpleNamespace(get_ohlcv=lambda *a, **kw: data))
    text = prefetch.prefetch_us_stock_ohlcv('^GSPC')
    assert 'Latest observed Close (index points): 5000.0000' in text
    assert 'Close (USD)' not in text


def test_profile_employee_zero_is_valid_and_not_all_staff(monkeypatch):
    prefetch = load("data_prefetch")
    monkeypatch.setattr(yf, "Ticker", lambda _: SimpleNamespace(info={"longName": "Example", "fullTimeEmployees": 0}))
    assert "상근 직원, 전체 인력 아님) | 0 |" in prefetch.prefetch_company_profile("TEST")


def test_dated_historical_flow_fallback_never_claims_current_institutional_flow(monkeypatch):
    prefetch = load("data_prefetch")
    end = pd.Timestamp.now(tz="America/New_York").normalize().tz_localize(None) - pd.Timedelta(days=1)
    index = pd.bdate_range(end=end, periods=60)
    data = pd.DataFrame({"open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0, "volume": 1000}, index=index)
    data.loc[index[-1], "close"] = np.nan
    calls = []

    def history(*args, **kwargs):
        calls.append(kwargs)
        return data

    monkeypatch.setattr(prefetch, "_get_us_data_client", lambda: SimpleNamespace(get_ohlcv=history))
    text = prefetch.prefetch_us_stock_ohlcv("TEST")
    assert len(calls) == 1
    assert "Historical price-volume proxy window only" in text
    assert "Not current flow or institutional net buying" in text
    assert "Latest observed Close (USD): N/A" in text


def test_raw_split_boundary_and_cross_dates_do_not_bridge_gaps():
    index = pd.bdate_range("2025-01-01", periods=250)
    data = pd.DataFrame({"close": np.r_[np.arange(200, 50, -1), np.arange(50, 150)], "stock_splits": 0.0}, index=index)
    data.attrs["price_basis"] = "provider_unadjusted_close"
    facts = build_report_technical_facts(data)
    assert facts["crosses"]["SMA20_50_golden"] is not None
    data.loc[index[-20], "stock_splits"] = 2.0
    facts = build_report_technical_facts(data)
    assert facts["split_boundary_date"] == index[-20].isoformat()
    assert facts["indicators"]["SMA50"] is None and facts["indicators"]["SMA20"] is not None
    assert all(v is None for v in facts["crosses"].values())
    data.loc[index[-10], "close"] = np.nan
    assert build_report_technical_facts(data)["indicators"]["SMA20"] is None
