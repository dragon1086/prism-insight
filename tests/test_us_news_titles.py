"""US report news listing from the local US headline store (roadmap U4). No network."""
from datetime import datetime

from prism_core import kr_news_store as store
from prism_core.us_news_titles import build_us_news_listing


def _row(serial, day, time, title, ticker):
    return {"serial": serial, "day": day, "time": time, "title": title, "provider": "연합미국",
            "provider_code": "US", "category": "종목리포트", "tags": [(ticker, ticker.lower())]}


def test_listing_takes_the_week_through_the_next_kst_morning(tmp_path):
    path = tmp_path / "us.sqlite"
    store.upsert(store.connect(path), [
        _row("1", "20261008", "080718", "마이크론 목표가 3천달러 등장…주가 4%↑", "MU"),   # US session of 10/7 ends here
        _row("2", "20261009", "110000", "마이크론 | 장 마감 후 코멘트", "MU"),
        _row("3", "20261009", "130000", "다음 날 오후 기사", "MU"),                       # after the window
        _row("4", "20260929", "090000", "오래된 기사", "MU"),                             # before the window
        _row("5", "20261008", "090000", "엔비디아 기사", "NVDA"),
    ])
    text = build_us_news_listing("mu", "20261008", path=path, now=datetime(2026, 10, 10, 0, 0))
    assert text.startswith("## KIS 해외 뉴스 제목 목록 (20261001~20261008, 2건)")
    assert "| 2026-10-09 11:00 | 연합미국 | 마이크론 / 장 마감 후 코멘트 |" in text
    assert "마이크론 목표가" in text and "다음 날 오후" not in text and "오래된" not in text and "엔비디아" not in text
    # A batch run before the session ends sees only what was published by then.
    assert "1건" in build_us_news_listing("MU", "20261008", path=path, now=datetime(2026, 10, 9, 9, 0))


def test_listing_is_empty_without_a_store_or_rows(tmp_path):
    assert build_us_news_listing("MU", "20261008", path=tmp_path / "missing.sqlite") == ""
    store.connect(tmp_path / "empty.sqlite").close()
    assert build_us_news_listing("MU", "20261008", path=tmp_path / "empty.sqlite") == ""
