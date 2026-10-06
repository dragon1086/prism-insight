"""Co-movement peer context for the KR news writer (Infostock group headlines)."""

from datetime import datetime

from cores.agents.news_strategy_agents import create_news_analysis_agent
from prism_core import kr_news_store as store
from prism_core.kr_peer_context import peer_context


def _row(serial, stamp, title, provider="인포스탁"):
    day, clock = stamp.split()
    return {"serial": serial, "day": day.replace("-", ""), "time": clock.replace(":", ""), "title": title,
            "provider": provider, "provider_code": "7", "category": "02", "tags": []}


GROUPS = [
    ("g1", "2026-10-01 09:06:00", "원일티엔아이(136150)  +8.86%, 팬오션 +3.99%, 일승 +2.52%, SK오션플랜트 +2.14%"),
    ("g2", "2026-09-09 09:07:00", "비에이치아이(083650)  +5.47%, 일승 +5.14%, SK오션플랜트 +2.02%"),
    ("g3", "2026-08-13 09:09:00", "영흥(012160)  +8.51%, 키스트론 +3.51%, SK오션플랜트 +2.04%, 일승 +1.60%"),
    ("g4", "2026-07-27 09:03:00", "씨에스윈드(112610)  -6.02%, SK오션플랜트 -5.18%, 대명에너지 -5.80%"),
    ("g5", "2026-07-30 09:08:00", "SK이터닉스(475150)  +5.18%, 대명에너지 +4.58%, SK오션플랜트 +4.79%"),
]


def _store(tmp_path, extra=()):
    conn = store.connect(tmp_path / "news.sqlite")
    store.upsert(conn, [_row(*g) for g in GROUPS] + list(extra))
    return conn


def test_peers_are_recurring_co_movers_and_a_quiet_day_says_so(tmp_path):
    conn = _store(tmp_path, [
        _row("n1", "2026-10-02 10:02:00", "[특징주] 한미 美 원전 8기 건설 추진…일승 등 원전주↑", provider="뉴스핌"),
        _row("n2", "2026-10-06 11:52:00", "SK오션플랜트(100090) 소폭 상승세 +3.08%, 4거래일 연속 상승"),
    ])
    text = peer_context(conn, "SK오션플랜트", "20261006", now=datetime(2026, 10, 6, 14, 49))
    assert "일승(3회)" in text and "대명에너지(2회)" in text
    assert "팬오션" not in text.split("자주 함께 묶인 종목:")[1].split("\n")[0]  # appeared once only
    assert "기준일 동반 등락 묶음: 없음" in text
    assert "일승 등 원전주↑" in text


def test_a_day_when_peers_moved_together_is_shown(tmp_path):
    conn = _store(tmp_path, [_row("t1", "2026-10-06 09:06:00", "일승(000000)  +6.10%, SK오션플랜트 +4.00%, 키스트론 +2.0%")])
    text = peer_context(conn, "SK오션플랜트", "20261006", now=datetime(2026, 10, 6, 14, 49))
    assert "기준일 동반 등락 묶음: 09:06 일승(000000)" in text


def test_no_recurring_peers_means_no_block(tmp_path):
    conn = _store(tmp_path)
    assert peer_context(conn, "없는종목", "20261006", now=datetime(2026, 10, 6, 14, 49)) == ""


def test_later_headlines_never_leak_into_an_earlier_reference_date(tmp_path):
    conn = _store(tmp_path, [_row("late", "2026-10-07 09:06:00", "일승(000000)  +6.10%, SK오션플랜트 +4.00%")])
    text = peer_context(conn, "SK오션플랜트", "20261006", now=datetime(2026, 10, 8, 9, 0))
    assert "기준일 동반 등락 묶음: 없음" in text


def test_news_writer_gets_the_block_with_its_rule():
    for language, marker in (("ko", "이 종목만의"), ("en", "the stock's own move")):
        prompt = create_news_analysis_agent("SK오션플랜트", "100090", "20261006", language,
                                            news_listing="| x |", peer_context="BLOCK").instruction
        assert "<peer_comovement>\nBLOCK\n</peer_comovement>" in prompt and marker in prompt
    plain = create_news_analysis_agent("SK오션플랜트", "100090", "20261006", "ko", news_listing="| x |").instruction
    assert "<peer_comovement>" not in plain
