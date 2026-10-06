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
