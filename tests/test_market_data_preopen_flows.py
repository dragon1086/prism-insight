"""Daily investor-flow requests must not use an unavailable pre-close as-of."""

import pandas as pd
import pytest

from cores import market_data
from cores.market_data.source import SourceChain


@pytest.fixture
def flow_source(monkeypatch):
    class Source:
        name = "fixture"

        def __init__(self):
            self.calls = []

        def investor_flows(self, ticker, start, end):
            self.calls.append(("daily", ticker, start, end))
            return pd.DataFrame({"기관합계": [100]}, index=pd.to_datetime([end]))

        def intraday_investor_estimate(self, ticker, *, as_of):
            self.calls.append(("estimate", ticker))
            return pd.DataFrame(
                {"기관합계": [200]}, index=pd.to_datetime(["2026-09-24"])
            )

    source = Source()
    monkeypatch.setattr(market_data, "default_chain", lambda: SourceChain([source]))
    return source


def set_clock(monkeypatch, clock):
    monkeypatch.setattr(
        market_data, "_now_kst",
        lambda: pd.Timestamp(f"2026-09-24 {clock}", tz="Asia/Seoul").to_pydatetime(),
    )


@pytest.mark.parametrize("clock", ["01:00", "09:29", "09:30", "15:39", "15:40"])
@pytest.mark.parametrize("alias", [False, True])
def test_current_day_flow_cutoff(monkeypatch, flow_source, clock, alias):
    set_clock(monkeypatch, clock)
    getter = (
        market_data.get_market_trading_volume_by_investor if alias
        else market_data.get_market_trading_volume_by_date
    )
    frame = getter("20260901", "20260924", "017670")
    daily_end = "20260924" if clock == "15:40" else "20260923"
    expected = [("daily", "017670", "20260901", daily_end)]
    if "09:30" <= clock < "15:40":
        expected.append(("estimate", "017670"))
    assert flow_source.calls == expected
    assert isinstance(frame.index, pd.DatetimeIndex)
    assert frame.index[-1] == pd.Timestamp(
        "2026-09-23" if clock < "09:30" else "2026-09-24"
    )


@pytest.mark.parametrize("clock", ["01:00", "09:29", "09:30", "15:39", "15:40"])
def test_historical_range_unchanged(monkeypatch, flow_source, clock):
    set_clock(monkeypatch, clock)
    market_data.get_market_trading_volume_by_date("20260901", "20260922", "017670")
    assert flow_source.calls == [("daily", "017670", "20260901", "20260922")]


@pytest.mark.parametrize("clock", ["01:00", "09:29"])
def test_today_only_preopen_is_empty_without_provider_call(monkeypatch, flow_source, clock):
    set_clock(monkeypatch, clock)
    frame = market_data.get_market_trading_volume_by_date("20260924", "20260924", "017670")
    assert frame.empty
    assert flow_source.calls == []


def set_datetime(monkeypatch, stamp):
    monkeypatch.setattr(
        market_data, "_now_kst",
        lambda: pd.Timestamp(stamp, tz="Asia/Seoul").to_pydatetime(),
    )


# KIS rejects an as-of of today before 15:40 on every calendar day
# ("TIME LIMIT 00:00 ~ 15:40"), not only on weekdays. 2026-09-26 is a Saturday.
@pytest.mark.parametrize("stamp", ["2026-09-26 00:26", "2026-09-26 09:25",
                                   "2026-09-27 15:39"])
def test_weekend_before_cutoff_uses_yesterday_as_of_without_estimate(
        monkeypatch, flow_source, stamp):
    set_datetime(monkeypatch, stamp)
    today = pd.Timestamp(stamp).strftime("%Y%m%d")
    yesterday = (pd.Timestamp(stamp) - pd.Timedelta(days=1)).strftime("%Y%m%d")
    market_data.get_market_trading_volume_by_date("20260826", today, "252990")
    assert flow_source.calls == [("daily", "252990", "20260826", yesterday)]


def test_weekend_after_cutoff_keeps_today_as_of(monkeypatch, flow_source):
    set_datetime(monkeypatch, "2026-09-26 15:40")
    market_data.get_market_trading_volume_by_date("20260826", "20260926", "252990")
    assert flow_source.calls == [("daily", "252990", "20260826", "20260926")]
