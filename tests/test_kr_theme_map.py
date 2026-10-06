"""KR theme map v1 (roadmap R2): Infostock co-move lines clustered into themes."""

from prism_core import kr_theme_map as tm

NAMES = {"대한광통신": "010170", "빛과전자": "069540", "RF머트리얼즈": "327260", "성호전자": "043260",
         "대우건설": "047040", "현대건설": "000720", "GS건설": "006360", "DL이앤씨": "375500", "두산": "000150"}


def test_parse_group_keeps_known_members_and_needs_three():
    line = "빛과전자(069540)  +5.91%, RF머트리얼즈 +3.43%, 대한광통신 +3.37%, 모르는회사 +1.0%"
    assert tm.parse_group(line, NAMES) == [("069540", 5.91), ("327260", 3.43), ("010170", 3.37)]
    assert tm.parse_group("빛과전자(069540)  +5.91%, 모르는회사 +1.0%, 또모름 +1.0%", NAMES) == []
    assert tm.parse_group("SK오션플랜트(100090) 소폭 상승세 +3.08%, 4거래일 연속 상승", NAMES) == []


def _obs(day, codes, pct=2.0):
    return (f"2026-{day} 09:05:00", [(c, pct) for c in codes])


OPTIC = ["069540", "327260", "010170", "043260"]
BUILD = ["047040", "000720", "006360", "375500"]


def test_recurring_groups_become_themes_and_one_offs_do_not():
    obs = [_obs(f"09-{d:02d}", OPTIC[:3] + ([OPTIC[3]] if d % 2 else []), 3.0) for d in range(1, 7)]
    obs += [_obs(f"09-{d:02d}", BUILD, -1.5) for d in range(1, 6)]
    obs += [_obs("09-20", ["000150", "047040", "010170"])]  # a one-off mix
    themes = tm.build_themes(sorted(obs))
    cores = [{m["code"] for m in t["members"]} for t in themes]
    assert {"069540", "327260", "010170", "043260"} in cores  # 성호전자 in 3/6 lines >= 30%
    assert set(BUILD) in cores
    assert len(themes) == 2
    build = next(t for t in themes if "047040" in {m["code"] for m in t["members"]})
    assert build["active_days"][0] == {"date": "2026-09-01", "avg_pct": -1.5}
    assert build["first_seen"] == "2026-09-01" and build["last_seen"] == "2026-09-05"


def test_near_duplicate_clusters_merge_and_a_stock_can_sit_in_two_themes():
    a = [_obs(f"08-{d:02d}", ["069540", "327260", "010170"]) for d in range(1, 4)]
    b = [_obs(f"08-{d:02d}", ["069540", "327260", "010170", "000150"]) for d in range(10, 13)]
    c = [_obs(f"08-{d:02d}", ["000150", "047040", "000720"]) for d in range(20, 26)]
    themes = tm.build_themes(sorted(a + b + c))
    assert len(themes) == 2 and themes[0]["lines"] == 6
    assert [t["id"] for t in tm.theme_of(themes, "000150")]  # 두산 belongs to both
    assert len(tm.theme_of(themes, "000150")) == 2


def test_members_split_core_and_related_and_cap_at_twenty():
    counts = {"a": 10, "b": 4, "c": 2, "d": 1}
    members = tm._members(counts, 10)
    assert [(m["code"], m["role"]) for m in members] == [("a", "core"), ("b", "core"), ("c", "related")]
    many = {f"s{i:02d}": 9 for i in range(30)}
    assert len(tm._members(many, 10)) == tm.MAX_MEMBERS


def _named(tid, counts, lines, name, sector="기계·로봇", day="2026-09-01"):
    return {"id": tid, "name": name, "sector": sector, "counts": counts, "lines": lines,
            "members": tm._members(counts, lines), "active_days": [{"date": day, "avg_pct": 2.0}]}


def test_same_named_themes_merge_and_unnamed_ones_stay_apart():
    themes = [_named("T1", {"a": 5, "b": 5, "c": 4}, 6, "협동로봇"),
              _named("T2", {"a": 3, "d": 4, "e": 4}, 5, "협동로봇", day="2026-09-05"),
              _named("T3", {"x": 5, "y": 5}, 5, "휴머노이드 부품"),
              _named("T4", {"p": 5, "q": 5}, 5, "미분류"),
              _named("T5", {"r": 5, "s": 5}, 5, "미분류")]
    merged = tm.merge_by_name(themes)
    robot = next(t for t in merged if t["name"] == "협동로봇")
    assert robot["lines"] == 11 and robot["merged_from"] == ["T1", "T2"]
    assert robot["first_seen"] == "2026-09-01" and robot["last_seen"] == "2026-09-05"
    assert {m["code"] for m in robot["members"]} >= {"a", "b", "c", "d", "e"}
    assert sum(t["name"] == "미분류" for t in merged) == 2 and len(merged) == 4


def test_news_members_need_a_headline_that_also_names_a_member():
    n2c = {"대한광통신": "010170", "머큐리": "100590", "빛과전자": "069540", "삼성전자": "005930"}
    pattern = tm.stock_name_pattern(n2c)
    theme = {"members": [{"code": "010170", "times": 5, "share": 0.8, "role": "core"},
                         {"code": "069540", "times": 4, "share": 0.6, "role": "core"}]}
    titles = ["광통신주 급등…대한광통신·머큐리 上"] * 3 + ["광통신 시장 확대, 삼성전자 관심"] * 5
    added = tm.add_news_members(theme, titles, n2c, pattern)
    assert [(m["code"], m["role"]) for m in added] == [("100590", "news")]  # 삼성전자 never co-mentioned
    assert theme["members"][-1]["code"] == "100590"
    assert tm.theme_of([dict(theme, id="T1")], "100590")  # news members (share None) still resolve


def test_coverage_counts_the_top_caps_in_any_theme():
    themes = [{"members": [{"code": "a"}, {"code": "b"}]}]
    cover = tm.coverage(themes, {"a": 100.0, "b": 50.0, "c": 80.0, "d": 1.0}, top=3)
    assert cover == {"top": 3, "covered": 2, "missing": ["c"]}


def test_same_sector_heavy_overlap_folds_but_other_sectors_do_not():
    a = _named("T1", {"a": 5, "b": 5, "c": 5}, 6, "타이어주", sector="자동차·모빌리티")
    b = _named("T2", {"a": 4, "b": 4, "x": 4}, 5, "타이어·소재", sector="자동차·모빌리티")
    c = _named("T3", {"a": 4, "b": 4, "y": 4}, 5, "고무 화학", sector="정유·화학")
    kept = tm.merge_similar([a, b, c])
    assert len(kept) == 2
    tire = next(t for t in kept if t["name"] == "타이어주")
    assert tire["lines"] == 11 and {"x"} <= {m["code"] for m in tire["members"]}


def test_roundup_headlines_naming_many_stocks_add_nobody():
    n2c = {"현대차": "005380", "기아": "000270", "하이브": "352820", "카카오": "035720", "삼성전자": "005930"}
    theme = {"members": [{"code": "005380", "times": 5, "share": 0.9, "role": "core"}]}
    titles = ["외국인 순매수 상위 현대차·기아·하이브·카카오"] * 5
    assert tm.add_news_members(theme, titles, n2c, tm.stock_name_pattern(n2c)) == []


def test_ai_placement_is_marked_and_new_themes_need_three_stocks():
    themes = [{"id": "T001", "name": "반도체 장비", "members": [{"code": "a", "role": "core"}]}]
    n2c = {"HPSP": "403870", "아모레퍼시픽": "090430", "LG생활건강": "051900", "코스맥스": "192820"}
    added, created = tm.add_ai_members(
        themes, {"HPSP": ["T001", "T999"]},
        [{"name": "화장품 대형주", "sector": "소비재·유통", "stocks": ["아모레퍼시픽", "LG생활건강", "코스맥스"]},
         {"name": "두 종목뿐", "sector": "기타", "stocks": ["아모레퍼시픽", "LG생활건강"]}],
        n2c, ("소비재·유통", "기타"))
    assert added == 1 and themes[0]["members"][-1] == {"code": "403870", "times": 0, "share": None, "role": "ai"}
    assert len(created) == 1 and created[0]["source"] == "ai" and created[0]["id"] == "T002"
    assert {m["role"] for m in created[0]["members"]} == {"ai"}


def test_core_stocks_of_other_sectors_are_not_added_from_news():
    n2c = {"현대차": "005380", "삼성전자": "005930", "에스엘": "005850"}
    car = {"sector": "자동차·모빌리티", "members": [{"code": "005380", "times": 5, "share": 0.9, "role": "core"}]}
    chips = {"sector": "반도체", "members": [{"code": "005930", "times": 9, "share": 1.0, "role": "core"}]}
    titles = ["자동차 관세 대응…현대차·삼성전자"] * 4 + ["자동차 램프…현대차·에스엘"] * 4
    added = tm.add_news_members(car, titles, n2c, tm.stock_name_pattern(n2c), home_sectors=tm.core_sectors([car, chips]))
    assert [m["code"] for m in added] == ["005850"]


def test_an_ai_theme_named_like_an_existing_one_folds_into_it():
    themes = [{"id": "T001", "name": "게임주", "members": [{"code": "a", "role": "core"}]}]
    n2c = {"넷마블": "251270", "크래프톤": "259960", "엔씨소프트": "036570"}
    added, created = tm.add_ai_members(themes, {}, [{"name": "게임주", "sector": "엔터·미디어·게임",
                                                      "stocks": ["넷마블", "크래프톤", "엔씨소프트"]}], n2c, ("엔터·미디어·게임",))
    assert created == [] and added == 3 and len(themes[0]["members"]) == 4
