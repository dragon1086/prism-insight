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
