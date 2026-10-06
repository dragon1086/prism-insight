"""Theme flow block for the KR market section (Infostock co-move lines + theme map + headlines)."""

from datetime import datetime

from prism_core import kr_news_store as store
from prism_core.kr_market_theme_flow import name_line, parse_line, theme_flow


def _row(serial, stamp, title, provider="인포스탁"):
    day, clock = stamp.split()
    return {"serial": serial, "day": day.replace("-", ""), "time": clock.replace(":", ""), "title": title,
            "provider": provider, "provider_code": "7", "category": "02", "tags": []}


def _theme(name, members, keywords=(), lines=10):
    return {"name": name, "keywords": list(keywords), "lines": lines,
            "members": [{"name": m, "code": f"{i:06d}", "role": "core"} for i, m in enumerate(members)]}


THEMES = [
    _theme("광통신", ["빛샘전자", "에이스테크", "RF머트리얼즈", "우리로"], ["광통신"]),
    _theme("강관", ["문배철강", "대동스틸", "금강철강", "TCC스틸"], ["강관"]),
    _theme("수소연료전지", ["두산퓨얼셀", "범한퓨얼셀", "코세스"], ["수소"]),
]


def _store(tmp_path, rows):
    conn = store.connect(tmp_path / "news.sqlite")
    store.upsert(conn, rows)
    return conn


def test_parse_line_reads_leader_and_members():
    assert parse_line("빛샘전자(010170)  +12.40%, 에이스테크 +10.10%, RF머트리얼즈 +7.10%") == [
        ("빛샘전자", 12.4), ("에이스테크", 10.1), ("RF머트리얼즈", 7.1)]
    assert parse_line("[특징주] 광통신주 강세") == []


def test_line_takes_the_theme_its_members_share_or_none():
    assert name_line([("빛샘전자", 1), ("에이스테크", 1), ("모르는종목", 1)], THEMES)["name"] == "광통신"
    assert name_line([("빛샘전자", 1), ("모르는종목", 1), ("다른종목", 1)], THEMES) is None


def test_block_ranks_themes_and_cites_only_theme_titles(tmp_path):
    conn = _store(tmp_path, [
        _row("g1", "2026-10-02 09:01:00", "빛샘전자(010170)  +12.40%, 에이스테크 +10.10%, RF머트리얼즈 +7.10%"),
        _row("g2", "2026-10-02 09:03:00", "문배철강(008420)  +3.00%, 대동스틸 +2.00%, 금강철강 +1.00%"),
        _row("g3", "2026-10-02 09:05:00", "두산퓨얼셀(336260)  -3.70%, 범한퓨얼셀 -3.00%, 코세스 -6.40%"),
        _row("g4", "2026-10-02 09:06:00", "씨싸이트(064820)  +25.50%, LF +7.80%, 형지I&C +5.80%"),
        _row("n1", "2026-10-02 13:18:00", "광통신주, 장초반 줄상승...AI 인프라 확대 기대감", provider="파이낸셜"),
        _row("n2", "2026-10-02 07:45:00", "10월 날씨 쌀쌀...건강관리 유의", provider="뉴스핌"),
        _row("n3", "2026-10-02 10:23:00", "[장중수급포착] 빛샘전자, 외국인/기관 동시 순매수… 주가 +12%", provider="뉴스핌"),
        _row("n4", "2026-10-02 10:40:00", "빛샘전자, 임원 주식 매도", provider="뉴스핌"),
        _row("n5", "2026-10-02 16:00:00", "광통신 장마감 후 기사", provider="뉴스핌"),
    ])
    text = theme_flow(conn, THEMES, "20261002", now=datetime(2026, 10, 2, 14, 50))
    up, down = text.split("**오른 테마**")[1].split("**내린 테마**")
    assert up.index("**씨싸이트 등**") < up.index("**광통신**") < up.index("**강관**")  # by average move
    assert "**수소연료전지** (평균 -4.4%)" in down
    assert "광통신주, 장초반 줄상승" in text
    assert "건강관리" not in text          # "강관" inside another word is not a theme title
    assert "장중수급포착" not in text      # price/flow restatement, no reason
    assert "임원 주식 매도" not in text    # one stock's own news
    assert "장마감 후 기사" not in text    # after the report time
    assert "09:01~09:06" in text


def test_no_lines_means_no_block(tmp_path):
    conn = _store(tmp_path, [_row("n1", "2026-10-02 09:30:00", "광통신주 강세", provider="뉴스핌")])
    assert theme_flow(conn, THEMES, "20261002", now=datetime(2026, 10, 2, 14, 0)) == ""
