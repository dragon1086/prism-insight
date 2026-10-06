"""KIS headline listing replaces the Naver news-page scrape for the KR news writer."""

from types import SimpleNamespace

import pandas as pd

from cores.agents.news_strategy_agents import create_news_analysis_agent
from cores.market_data.kis_source import KisSource
from cores.market_data.remote_api import MarketDataRequest
from prism_core.kr_news_titles import build_kr_news_listing


def _row(srno, day, hour, title, provider="헤럴드경제", codes=(("000150", "두산"),)):
    row = {"cntt_usiq_srno": srno, "data_dt": day, "data_tm": hour, "dorg": provider,
           "hts_pbnt_titl_cntt": title}
    for i, (code, name) in enumerate(codes, 1):
        row[f"iscd{i}"], row[f"kor_isnm{i}"] = code, name
    return row


def test_news_titles_walks_pages_back_and_keeps_the_window():
    pages = {
        ("20261006", ""): [_row("3", "20261006", "094353", "두산로보틱스, 국산 AI칩 기반 '피지컬 AI' 개발",
                                 codes=(("000150", "두산"),)),
                           _row("2", "20261002", "111400", "광모듈 국적 바뀌어도...두산 CCL은 그대로")],
        ("20261002", "111400"): [_row("2", "20261002", "111400", "광모듈 국적 바뀌어도...두산 CCL은 그대로"),
                                 _row("1", "20260920", "100000", "too old")],
    }
    calls = []
    source = KisSource()

    def fake_fetch(url, tr_id, params):
        key = (params["FID_INPUT_DATE_1"], params["FID_INPUT_HOUR_1"])
        calls.append(key)
        return SimpleNamespace(output=pages.get(key, []))

    source._fetch = fake_fetch
    frame = source.news_titles("000150", "20260929", "20261006")

    assert calls == [("20261006", ""), ("20261002", "111400")]  # stops once a page reaches past start
    assert list(frame["published_at"]) == ["2026-10-06 09:43", "2026-10-02 11:14"]
    assert frame.iloc[0]["tag_names"] == "두산"
    assert frame.attrs["data_status"] == "headlines_only"


def _frame(rows):
    return pd.DataFrame(rows, columns=["published_at", "provider", "title", "tag_codes", "tag_names"])


def test_listing_drops_ranking_tables_but_keeps_disclosures_and_subsidiary_news():
    frame = _frame([
        ["2026-10-06 09:43", "헤럴드경제", "두산로보틱스, 국산 AI칩 기반 '피지컬 AI' 개발…총 989억원 규모", "000150", "두산"],
        ["2026-10-06 09:32", "뉴스핌", "두산로보틱스 | 한국형 피지컬 AI", "454910", "두산로보틱스"],
        ["2026-10-06 09:00", "공시", "(주)두산 주식선물 2단계 가격제한폭 확대요건 도달(상승)", "000150", "두산"],
        ["2026-10-02 14:20", "인포스탁", "1억원 이상 매도체결 상위 20 종목(코스피)", "005930", "삼성전자"],
        ["2026-10-02 15:03", "인포스탁", "외국계 순매수,도 상위종목(코스피) 금액기준", "005380", "현대차"],
    ])
    text = build_kr_news_listing("000150", "20261006", fetch=lambda *a: frame)

    assert "기준일 당일 3건" in text and "3건," in text
    assert "피지컬 AI" in text and "가격제한폭" in text
    assert "매도체결" not in text and "순매수,도" not in text
    assert "두산로보틱스 / 한국형" in text  # table pipes in titles cannot break the table


def test_listing_keeps_minute_form_after_remote_transport():
    from cores.market_data.remote_source import decode_result, encode_result

    frame = _frame([["2026-10-06 09:43", "헤럴드경제", "두산로보틱스 피지컬 AI", "000150", "두산"]])
    remote = decode_result(encode_result(frame))
    text = build_kr_news_listing("000150", "20261006", fetch=lambda *a: remote)
    assert "| 2026-10-06 09:43 |" in text and "09:43:00" not in text


def test_listing_is_empty_when_kis_is_unavailable_or_silent():
    def broken(*_):
        raise RuntimeError("KIS down")

    assert build_kr_news_listing("000150", "20261006", fetch=broken) == ""
    assert build_kr_news_listing("000150", "20261006", fetch=lambda *a: _frame([])) == ""


def test_news_writer_uses_the_listing_first_and_cites_headline_only_sources():
    listing = "| 2026-10-06 09:43 | 헤럴드경제 | 두산로보틱스 피지컬 AI | 두산 |"
    for language, exception in (("ko", "본문 미확인"), ("en", "body not confirmed")):
        prompt = create_news_analysis_agent("두산", "000150", "20261006", language,
                                            news_listing=listing).instruction
        assert "<kis_news_headlines>" in prompt and listing in prompt
        assert exception in prompt
        assert "finance.naver.com" not in prompt


def test_remote_market_data_allows_news_titles_with_a_range():
    request = MarketDataRequest(capability="news_titles", ticker="000150", start="20260929", end="20261006")
    assert request.capability == "news_titles"
