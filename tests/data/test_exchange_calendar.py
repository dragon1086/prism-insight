from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

from prism_core.data.exchange_calendar import ExchangeMarket, latest_completed_session


KST = ZoneInfo("Asia/Seoul")
ET = ZoneInfo("America/New_York")


def test_krx_intraday_uses_previous_completed_session() -> None:
    assert latest_completed_session(
        ExchangeMarket.KRX,
        datetime(2026, 7, 27, 10, 0, tzinfo=KST),
    ) == date(2026, 7, 24)


def test_krx_after_close_uses_same_day_completed_session() -> None:
    assert latest_completed_session(
        ExchangeMarket.KRX,
        datetime(2026, 7, 27, 16, 0, tzinfo=KST),
    ) == date(2026, 7, 27)


def test_krx_lunar_new_year_weekday_holiday_uses_prior_exchange_session() -> None:
    assert latest_completed_session(
        ExchangeMarket.KRX,
        datetime(2026, 2, 18, 18, 0, tzinfo=KST),
    ) == date(2026, 2, 13)


def test_nyse_presidents_day_uses_prior_exchange_session() -> None:
    assert latest_completed_session(
        ExchangeMarket.NYSE,
        datetime(2026, 2, 16, 18, 0, tzinfo=ET),
    ) == date(2026, 2, 13)
