"""Writers get the price fetch time so they state it once instead of repeating caveats."""

from prism_core.kr_report_context import reference_context, render_observation_time


def test_same_day_intraday_fetch_is_stated_once_and_never_called_a_close():
    text = render_observation_time("2026-10-06T14:49", "20261006")
    assert "10월 6일 14시 49분" in text and "장중 값" in text
    assert "반복하지 마세요" in text and "종가·마감으로 부르지 않는 규칙은 그대로" in text
    en = render_observation_time("2026-10-06T14:49", "20261006", "en")
    assert "Oct 06 14:49 KST" in en and "Never call an intraday value a close" in en


def test_after_the_regular_close_finality_is_still_not_claimed():
    text = render_observation_time("2026-10-06T16:05", "20261006")
    assert "정규장 종료(15:30) 뒤" in text and "최종 확정 여부는 따로 확인되지 않았습니다" in text


def test_past_reference_dates_and_bad_input_add_nothing():
    assert render_observation_time("2026-10-07T09:40", "20261006") == ""
    assert render_observation_time(None, "20261006") == ""
    assert render_observation_time("not-a-time", "20261006") == ""


def test_reference_context_carries_the_line_for_every_section():
    prefetched = {"price_observed_at": "2026-10-06T14:49",
                  "report_calendar_context": {"reference_date": "2026-10-06", "is_session": True, "calendar": "XKRX"}}
    assert "가격 조회 시각: 10월 6일 14시 49분" in reference_context(prefetched, "ko")
    assert "가격 조회 시각" in reference_context(prefetched, "ko", market_only=True)
    assert "가격 조회 시각" not in reference_context({}, "ko")
