"""US signal alert theme flow from the US theme map (roadmap U4, 2026-10-08). No network."""
import json
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from prism_core import kr_news_store as store
from prism_core import us_theme_flow as flow

KST = ZoneInfo("Asia/Seoul")
NOW = datetime(2026, 10, 8, 23, 12, tzinfo=KST)


def theme(name, codes, keywords=()):
    return {"name": name, "sector": "s", "keywords": list(keywords),
            "members": [{"code": c, "name": c.lower()} for c in codes]}


MAP = {"meta": {"version": "us_theme_map_v3"}, "themes": [
    theme("양자컴퓨팅", ["RGTI", "QBTS", "IONQ"], ["양자"]),
    theme("비트코인 채굴", ["MARA", "RIOT", "CLSK", "CIFR"]),
    theme("규제 수도", ["AWK", "WTRG"]),                  # two quoted members only: never scored
    theme("방산", ["LMT", "NOC", "GD"]),
]}
CHANGES = {"RGTI": 12.1, "QBTS": 9.4, "IONQ": 3.0, "MARA": -5.0, "RIOT": -4.0, "CLSK": -3.0, "CIFR": 1.0,
           "AWK": 5.0, "WTRG": 5.0, "LMT": 0.2, "NOC": -0.1, "GD": 0.3}


def test_scores_need_three_quoted_members_and_rank_by_mean():
    stats = flow.theme_stats(MAP["themes"], CHANGES)
    assert [s["name"] for s in stats] == ["양자컴퓨팅", "방산", "비트코인 채굴"]
    assert stats[0]["median"] == 9.4 and stats[0]["up"] == 3 and stats[0]["movers"][0][0] == "RGTI"


def test_build_picks_strong_and_weak_themes_with_a_headline(tmp_path):
    conn = store.connect(tmp_path / "us.sqlite")
    store.upsert(conn, [{"serial": "1", "day": "20261008", "time": "220500", "title": "리게티, 양자 칩 수주",
                         "provider": "연합미국", "provider_code": "US", "category": "특징주",
                         "tags": [("RGTI", "리게티 컴퓨팅")]},
                        {"serial": "2", "day": "20261006", "time": "090000", "title": "오래된 기사",
                         "provider": "연합미국", "provider_code": "US", "category": "특징주",
                         "tags": [("RGTI", "리게티 컴퓨팅")]}])
    result = flow.build(MAP, CHANGES, trade_date="20261008", mode="morning", conn=conn, now=NOW)
    assert [t["name"] for t in result["themes"]] == ["양자컴퓨팅", "비트코인 채굴"]   # flat 방산 is left out
    assert result["themes"][0]["headlines"][0]["title"] == "리게티, 양자 칩 수주"     # older than 18h excluded
    text = flow.render(result, "ko")
    assert "1. *양자컴퓨팅* +9.4%  (3/3 상승)\n    RGTI +12.1% · QBTS +9.4% · IONQ +3.0%\n" in text
    assert "    📰 리게티, 양자 칩 수주\n" in text
    assert "📉 *약한 테마*\n· 비트코인 채굴 -3.5%\n" in text and text.endswith("\n\n")


def test_load_fresh_rejects_stale_or_other_day(tmp_path):
    path = tmp_path / "flow.json"
    path.write_text(json.dumps({"as_of": NOW.isoformat(), "trade_date": "20261008", "themes": []}))
    assert flow.load_fresh("20261008", path=path, now=NOW + timedelta(minutes=10)) is not None
    assert flow.load_fresh("20261008", path=path, now=NOW + timedelta(minutes=45)) is None
    assert flow.load_fresh("20261009", path=path, now=NOW) is None
    assert flow.load_fresh("20261008", path=tmp_path / "missing.json", now=NOW) is None
    assert flow.render(None) == "" and flow.render({"themes": []}) == ""


def test_latest_map_picks_the_highest_version(tmp_path, monkeypatch):
    monkeypatch.delenv("PRISM_US_THEME_MAP", raising=False)
    (tmp_path / "runtime").mkdir()
    for v in (1, 3, 2):
        (tmp_path / "runtime" / f"us_theme_map_v{v}.json").write_text("{}")
    (tmp_path / "runtime" / "us_theme_map_v3_review.md").write_text("")
    assert flow.latest_map_path(tmp_path).name == "us_theme_map_v3.json"


def test_one_outlier_cannot_carry_a_small_theme():
    themes = [theme("디지털 플랫폼", ["BSP", "MTCH", "GRAB", "X"])]
    changes = {"BSP": 24.2, "MTCH": 0.6, "GRAB": 0.3, "X": -1.0}   # mean +6.0%, median +0.45%
    assert flow.build({"themes": themes}, changes, trade_date="20261008", mode="morning", now=NOW)["themes"] == []


def test_headlines_lose_markdown_marks_and_are_shortened():
    flow_ = {"quoted": 3, "themes": [{"name": "t", "median": 2.0, "up": 3, "n": 3, "movers": [("A", "a", 2.0)],
                                      "headlines": [{"title": "*급등* [특징주] a_b `x` " + "가" * 60,
                                                     "provider": "p", "at": "2026-10-08 22:05:00"}]}]}
    line = [l for l in flow.render(flow_).splitlines() if "📰" in l][0]
    assert not any(c in line for c in "*_`[]") and line.endswith("…")
