"""KIS whole-market headline store and collector walk (no network)."""

import importlib.util
from datetime import datetime
from pathlib import Path

from prism_core import kr_news_store as store

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("collect_kr_news_titles", ROOT / "tools/collect_kr_news_titles.py")
collector = importlib.util.module_from_spec(spec)
spec.loader.exec_module(collector)


def _row(serial, day, clock, title, tags=(("000150", "두산"),)):
    return {"serial": serial, "day": day, "time": clock, "title": title, "provider": "헤럴드경제",
            "provider_code": "B", "category": "90", "tags": list(tags)}


class FakeFeed:
    """Serves newest-first pages of `rows` at or before the cursor, `size` per page."""

    def __init__(self, rows, size=2):
        self.rows = sorted(rows, key=lambda r: (r["day"], r["time"]), reverse=True)
        self.size = size
        self.calls = []

    def news_title_page(self, ticker, day, clock=""):
        self.calls.append((day, clock))
        cursor = day + (clock or "235959")
        return [r for r in self.rows if r["day"] + r["time"] <= cursor][: self.size]


def test_upsert_search_and_prune(tmp_path):
    conn = store.connect(tmp_path / "news.sqlite")
    rows = [_row("1", "20261006", "094353", "두산로보틱스, 국산 AI칩 기반 '피지컬 AI' 개발"),
            _row("2", "20261002", "154100", "[특징주] 광통신주, 美광통신 강세 등에 줄상승",
                 tags=(("010170", "대한광통신"),)),
            _row("3", "20250101", "090000", "오래된 기사")]
    assert store.upsert(conn, rows) == 3
    assert store.upsert(conn, rows[:1]) == 0  # serial is the identity

    hits = store.search(conn, keywords=["광통신", "로보틱스"], since="2026-10-01 00:00:00")
    assert [h["serial"] for h in hits] == ["1", "2"]
    assert store.search(conn, ticker="010170")[0]["tag_names"] == "대한광통신"

    assert store.prune(conn, keep_days=400, now=datetime(2026, 10, 6)) == 1
    assert store.bounds(conn)[2] == 2
    assert conn.execute("SELECT COUNT(*) FROM news_title_tickers WHERE serial='3'").fetchone()[0] == 0


def test_backfill_walks_back_to_the_until_date(tmp_path):
    conn = store.connect(tmp_path / "news.sqlite")
    feed = FakeFeed([_row(str(i), f"202610{d:02d}", "100000", f"t{i}") for i, d in enumerate(range(6, 0, -1))])
    calls, new, _ = collector.walk(feed, conn, start_day="20261006", start_clock="", max_calls=50, pause=0,
                                   stop=lambda page, n, oldest: oldest["day"] < "20261003")
    assert store.bounds(conn)[0].startswith("2026-10-02")  # the page that crossed `until` is kept
    assert new == 5 and calls == 4  # the cursor row repeats at the top of each page


def test_page_of_known_rows_steps_back_instead_of_looping(tmp_path):
    conn = store.connect(tmp_path / "news.sqlite")
    same_second = [_row(str(i), "20261006", "100000", f"t{i}") for i in range(3)]
    older = [_row("9", "20261005", "090000", "older")]
    feed = FakeFeed(same_second + older, size=2)
    collector.walk(feed, conn, start_day="20261006", start_clock="", max_calls=10, pause=0,
                   stop=lambda page, n, oldest: oldest["day"] < "20261005")
    assert store.known(conn, ["9"]) == {"9"}
    assert feed.calls[2] == ("20261006", "095959")


def test_live_stops_when_it_reaches_stored_headlines(tmp_path):
    conn = store.connect(tmp_path / "news.sqlite")
    store.upsert(conn, [_row("1", "20261006", "090000", "stored")])
    feed = FakeFeed([_row("3", "20261006", "100000", "new b"), _row("2", "20261006", "095000", "new a"),
                     _row("1", "20261006", "090000", "stored")], size=2)
    calls, new, _ = collector.live(feed, conn, max_calls=10)
    assert new == 2 and calls == 2


def test_live_on_an_empty_store_keeps_paging_past_the_repeated_cursor_row(tmp_path):
    conn = store.connect(tmp_path / "news.sqlite")
    feed = FakeFeed([_row(str(i), "20261006", f"{10 + i:02d}0000", f"t{i}") for i in range(6)], size=2)
    calls, new, _ = collector.live(feed, conn, max_calls=10)
    assert new == 6


def test_search_treats_like_wildcards_in_words_literally(tmp_path):
    conn = store.connect(tmp_path / "news.sqlite")
    store.upsert(conn, [_row("a", "20261006", "100000", "지분 100% 인수"),
                        _row("b", "20261006", "100100", "지분 1000주 매수")])
    assert [r["serial"] for r in store.search(conn, keywords=["100%"])] == ["a"]
    assert [r["serial"] for r in store.search(conn, all_keywords=["지분", "매수"])] == ["b"]
    assert store.search(conn, keywords=["_"]) == []


class MidnightBlindFeed(FakeFeed):
    """KIS treats HOUR '000000' as no hour: it answers with the day's newest page."""

    def news_title_page(self, ticker, day, clock=""):
        return super().news_title_page(ticker, day, "" if clock == "000000" else clock)


def test_backfill_resumes_past_a_headline_stamped_at_midnight(tmp_path):
    conn = store.connect(tmp_path / "news.sqlite")
    stored_day = [_row(f"d{h}", "20260930", f"{h:02d}0000", f"t{h}") for h in range(1, 23)]
    stored_day.append(_row("b", "20260930", "000000", "midnight"))
    rows = stored_day + [_row("c", "20260929", "180000", "previous day")]
    store.upsert(conn, stored_day)  # an earlier run stored the whole day, ending exactly at midnight
    feed = MidnightBlindFeed(rows, size=2)
    calls, new, _ = collector.backfill(feed, conn, "20260929", max_calls=10)
    assert store.known(conn, ["c"]) == {"c"} and new == 1
    assert feed.calls[0] == ("20260929", "235959")


class FakeUsFeed(FakeFeed):
    """The overseas feed is a different method; the KR one must not be called for --market us."""

    def us_news_title_page(self, ticker, day, clock="", exchange=""):
        return FakeFeed.news_title_page(self, ticker, day, clock)

    def news_title_page(self, ticker, day, clock=""):
        raise AssertionError("KR feed called for the US store")


def test_us_store_uses_its_own_file_and_feed(tmp_path, monkeypatch):
    monkeypatch.setenv("PRISM_US_NEWS_DB", str(tmp_path / "us.sqlite"))
    assert store.db_path("US") == tmp_path / "us.sqlite" and store.db_path("KR") != store.db_path("US")
    conn = store.connect(store.db_path("US"))
    feed = FakeUsFeed([{"serial": f"ICH{i}", "day": "20261007", "time": f"1{i}0000", "title": f"엔비디아 {i}",
                        "provider": "연합미국", "provider_code": "US", "category": "특징주",
                        "tags": [("NVDA", "엔비디아")]} for i in range(3)], size=2)
    calls, new, _ = collector.backfill(feed, conn, "20261007", 10, fetch_page=feed.us_news_title_page)
    assert new == 3 and store.search(conn, ticker="NVDA")[0]["tag_names"] == "엔비디아"


def test_kis_us_page_maps_the_overseas_fields():
    from cores.market_data.kis_source import KisSource

    class Body:
        outblock1 = [
            {"news_key": "ICH805227", "data_dt": "20261008", "data_tm": "071538", "class_name": "종목리포트",
             "nation_cd": "US", "exchange_cd": "NAS", "symb": "MRVL", "symb_name": "마벨 테크놀로지 그룹",
             "title": "마벨, 가이던스 이상의 성장세도 기대할 수 있어 씨티", "source": "연합미국"},
            {"news_key": "AKR1", "data_dt": "20261008", "data_tm": "074446", "class_name": "시황", "nation_cd": "US",
             "symb": "", "symb_name": "", "title": "[뉴욕 마켓 브리핑](8일)", "source": "연합미국"},
            {"news_key": "", "data_dt": "20261008", "data_tm": "070000", "title": "no key"},
        ]

    source = KisSource.__new__(KisSource)
    seen = {}
    source._fetch = lambda url, tr, params: seen.update(tr=tr, params=params) or Body()
    page = source.us_news_title_page("NVDA", "20261008", "235959", exchange="NAS")
    assert seen["tr"] == "HHPSTH60100C1" and seen["params"]["SYMB"] == "NVDA" and seen["params"]["EXCHANGE_CD"] == "NAS"
    assert [r["serial"] for r in page] == ["ICH805227", "AKR1"]
    assert page[0]["tags"] == [("MRVL", "마벨 테크놀로지 그룹")] and page[1]["tags"] == []
    assert page[0]["provider"] == "연합미국" and page[0]["provider_code"] == "US" and page[0]["category"] == "종목리포트"
