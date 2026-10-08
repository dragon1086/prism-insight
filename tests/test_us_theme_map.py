"""US theme map v1 (roadmap U3): price co-movement clusters, names, coverage. No network."""

import asyncio
import json

import numpy as np

from prism_core import kr_theme_map as kr
from prism_core import us_theme_map as tm


def _synthetic(seed=7, days=250):
    """Two hidden themes (4 + 3 stocks) on top of a market factor, plus 3 loners."""
    rng = np.random.default_rng(seed)
    market = rng.normal(0, 0.01, days)
    f1, f2 = rng.normal(0, 0.015, days), rng.normal(0, 0.015, days)
    cols, names = [], []
    for i in range(4):
        cols.append(1.2 * market + f1 + rng.normal(0, 0.008, days)); names.append(f"A{i}")
    for i in range(3):
        cols.append(0.8 * market + f2 + rng.normal(0, 0.008, days)); names.append(f"B{i}")
    for i in range(3):  # loners share only the market
        cols.append(1.5 * market + rng.normal(0, 0.015, days)); names.append(f"L{i}")
    dates = [f"2026-{1 + d // 28:02d}-{1 + d % 28:02d}" for d in range(days)]
    return names, np.column_stack(cols), market, dates


def test_market_is_removed_before_correlation():
    _, rets, market, _ = _synthetic()
    raw = np.corrcoef(rets[:, 7:].T)[np.triu_indices(3, 1)]
    resid = tm.residual_returns(rets, market)
    res = tm.residual_corr(resid)[7:, 7:][np.triu_indices(3, 1)]
    assert raw.min() > 0.4          # loners look alike only through the market
    assert np.abs(res).max() < 0.2  # ... and not once it is removed


def test_clusters_recover_hidden_themes_and_leave_loners_out():
    names, rets, market, dates = _synthetic()
    themes, _ = tm.build_clusters(names, rets, market, dates, threshold=0.45)
    groups = sorted(sorted(m["code"] for m in t["members"]) for t in themes)
    assert groups == [["A0", "A1", "A2", "A3"], ["B0", "B1", "B2"]]
    for t in themes:
        assert t["id"].startswith("C") and t["source"] == "price"
        assert 0.45 <= t["mean_corr"] <= 1
        assert t["lines"] == len(t["active_days"]) > 0
        assert t["first_seen"] == t["active_days"][0]["date"]
        assert all(m["role"] == "core" and 0 <= m["share"] <= 1 for m in t["members"])


def test_oversized_cluster_is_cut_tighter_and_singletons_dropped():
    corr = np.full((6, 6), 0.5)
    corr[:3, :3] = corr[3:, 3:] = 0.9
    np.fill_diagonal(corr, 1.0)
    assert sorted(map(sorted, tm.cluster_indices(corr, 0.45, max_size=10))) == [[0, 1, 2, 3, 4, 5]]
    assert sorted(map(sorted, tm.cluster_indices(corr, 0.45, max_size=3))) == [[0, 1, 2], [3, 4, 5]]
    lone = np.eye(3)
    assert tm.cluster_indices(lone, 0.45) == []


def test_cluster_stats_and_co_move_days():
    corr = np.array([[1, .6, .4], [.6, 1, .5], [.4, .5, 1]])
    mean, member = tm.cluster_stats(corr, [0, 1, 2])
    assert mean == 0.5 and member == [0.5, 0.55, 0.45]
    resid = np.zeros((10, 2)) + 0.001
    resid[3] = [0.05, 0.04]    # together up
    resid[6] = [0.05, -0.04]   # split: not a co-move day
    resid[8] = [-0.06, -0.05]  # together down
    days, counts = tm.co_move_days(resid, resid, [0, 1], [f"d{i}" for i in range(10)], z=1.5)
    assert [d for d, _ in days] == ["d3", "d8"] and days[0][1] == 4.5
    assert counts == {0: 2, 1: 2}


def test_universe_skips_reits_funds_and_second_share_classes():
    def info(name, industry="Semiconductors", quote="EQUITY"):
        return {"longName": name, "industry": industry, "quoteType": quote}

    cands = [{"symbol": "GOOGL", "cap": 300, "info": info("Alphabet Inc.")},
             {"symbol": "GOOG", "cap": 290, "info": info("Alphabet Inc.")},
             {"symbol": "O", "cap": 200, "info": info("Realty Income Corporation", "REIT - Retail")},
             {"symbol": "XFND", "cap": 150, "info": info("X Fund", quote="ETF")},
             {"symbol": "NVDA", "cap": 400, "info": info("NVIDIA Corporation")},
             {"symbol": "TSM", "cap": 100, "info": info("Taiwan Semiconductor Manufacturing Company Limited")},
             {"symbol": "SMALL", "cap": 1, "info": info("Small Co")}]
    kept, excluded, aliases = tm.select_universe(
        cands, top=3, eligible=lambda i, _v: None if i["quoteType"] == "EQUITY" else "quote_type")
    assert [c["symbol"] for c in kept] == ["NVDA", "GOOGL", "TSM"]  # ADRs stay, top-N stops the walk
    assert aliases == {"GOOG": "GOOGL"}
    assert excluded == {"share_class": 1, "reit": 1, "quote_type": 1}


def test_korean_aliases_find_names_in_headlines_without_false_hits():
    names = {"MU": "마이크론 테크놀로지", "AAPL": "애플", "AXP": "아메리칸 익스프레스", "AAL": "아메리칸 에어라인스 그룹",
             "TSM": "TSMC(ADR)", "GOOGL": "알파벳 A"}
    aliases = tm.alias_map(names)
    assert aliases["마이크론"] == "MU" and aliases["알파벳"] == "GOOGL" and aliases["TSMC"] == "TSM"
    assert "아메리칸" not in aliases  # generic first word never stands for one company
    pattern = tm.name_pattern(aliases)
    assert set(pattern.findall("마이크론·TSMC 동반 강세, 애플은 약세")) == {"마이크론", "TSMC", "애플"}
    assert pattern.findall("파인애플 수입 증가") == []
    assert tm.keyword_pattern("SMR").search("원전 SMR 수혜") and not tm.keyword_pattern("SMR").search("SMRT 급등")
    assert tm.keyword_pattern("nuclear").search("Nuclear 르네상스")


def _theme(tid, codes, keywords=(), sector="원전·신에너지", role="core"):
    return {"id": tid, "name": tid, "sector": sector, "keywords": list(keywords), "lines": 3, "active_days": [],
            "members": [{"code": c, "times": 1, "share": 0.3, "role": role, "corr": 0.5} for c in codes]}


def test_headline_evidence_counts_co_mentions_and_keywords_and_skips_roundups():
    names = {"CEG": "컨스텔레이션 에너지", "VST": "비스트라", "OKLO": "오클로", "NVDA": "엔비디아"}
    aliases = tm.alias_map(names)
    pattern = tm.name_pattern(aliases)
    theme = _theme("T001", ["CEG", "VST", "OKLO"], keywords=["원전", "SMR"])
    rows = [{"title": "비스트라·오클로 급등, 원전 전력 계약 기대", "tags": ["VST"]},
            {"title": "SMR 규제 완화 기대", "tags": []},
            {"title": "오클로, 원전 부지 확보", "tags": ["OKLO"]},
            {"title": "[뉴욕증시] 비스트라·오클로 강세", "tags": []},
            {"title": "컨스텔레이션 실적 발표", "tags": ["CEG", "VST"]}]
    ev = tm.theme_evidence(theme, rows, aliases, pattern)
    assert ev["co_mention"] == 2 and ev["keyword"] == 1 and ev["keyword_only"] == 1
    assert ev["samples"] == ["비스트라·오클로 급등, 원전 전력 계약 기대", "컨스텔레이션 실적 발표", "오클로, 원전 부지 확보"]


def test_news_members_reuse_kr_rule_with_tags():
    names = {"CEG": "컨스텔레이션 에너지", "VST": "비스트라", "SMR": "뉴스케일 파워", "NVDA": "엔비디아"}
    aliases = tm.alias_map(names)
    pattern = tm.name_pattern(aliases)
    canonical = {s: tm.korean_aliases(n)[0] for s, n in names.items()}
    theme = _theme("T001", ["CEG", "VST"], keywords=["원전"])
    rows = [{"title": "원전株 강세…뉴스케일 파워 급등", "tags": ["VST"]},
            {"title": "원전 수주 기대, 뉴스케일 파워·컨스텔레이션 에너지", "tags": []}]
    added = kr.add_news_members(theme, tm.tagged_titles(rows, canonical), aliases, pattern, min_mentions=2)
    assert [m["code"] for m in added] == ["SMR"]


def test_memberships_capped_at_three_keeping_price_clusters_first():
    themes = [_theme("T001", ["X"]), _theme("T002", ["X"], role="news"), _theme("T003", ["X"], role="ai"),
              _theme("T004", ["X"], role="ai"), _theme("T005", ["X"])]
    dropped = tm.cap_memberships(themes, 3)
    assert dropped == 2
    assert [t["id"] for t in themes if t["members"]] == ["T001", "T003", "T005"]


def test_same_name_clusters_merge_and_coverage_counts_top_caps():
    corr = {("A", "B"): 0.6, ("A", "C"): 0.3, ("B", "C"): 0.2}

    def pair(a, b):
        return corr.get((a, b), corr.get((b, a)))

    a, b, c = _theme("C001", ["A", "B"]), _theme("C002", ["C"]), _theme("C003", ["D", "E"])
    a["name"] = b["name"] = "지역은행"
    a["sector"] = b["sector"] = "은행"
    a["active_days"] = [{"date": "2026-01-02", "avg_pct": 2.0}]
    b["active_days"] = [{"date": "2026-01-02", "avg_pct": 4.0}, {"date": "2026-01-05", "avg_pct": -1.0}]
    merged = tm.merge_same_name([a, b, c], pair)
    bank = next(t for t in merged if t["name"] == "지역은행")
    assert {m["code"] for m in bank["members"]} == {"A", "B", "C"} and bank["merged_from"] == ["C001", "C002"]
    assert bank["lines"] == 2 and bank["active_days"][0]["avg_pct"] == 3.0
    assert bank["mean_corr"] == round((0.6 + 0.3 + 0.2) / 3, 3)
    assert sorted(t["id"] for t in merged) == ["T001", "T002"]
    cover = kr.coverage(merged, {"A": 5, "B": 4, "D": 3, "Z": 9}, top=3)
    assert cover == {"top": 3, "covered": 2, "missing": ["Z"]}


def test_build_writes_kr_shaped_json_with_fake_model(tmp_path, monkeypatch):
    """End to end on synthetic prices with a fake model: schema matches the KR map, every stock covered."""
    import importlib

    tool = importlib.import_module("tools.build_us_theme_map")
    names, rets, market, dates = _synthetic()
    stocks = [{"symbol": s, "cap": 100 - i, "kr_name": f"회사{s}", "en_name": s, "industry": "X", "summary": ""}
              for i, s in enumerate(names)]
    themes, corr = tm.build_clusters(names, rets, market, dates)
    by_symbol = {s["symbol"]: s for s in stocks}

    async def fake(prompt):
        if '"C001"' in prompt:
            return json.dumps({t["id"]: {"name": f"테마{t['id']}", "sector": "반도체", "keywords": ["반도체", "chip"]}
                               for t in themes})
        if "new_themes" in prompt:
            return json.dumps({"new_themes": [{"name": "신규", "sector": "기타", "keywords": ["신규"],
                                               "stocks": ["L0", "L1", "A0"]}]})
        return json.dumps({"assign": {"L2": ["T001"]}})

    ask = tool.CountingAsk(fake)
    asyncio.run(tool.name_clusters(themes, by_symbol, ask))
    themes = tm.merge_same_name(themes, tm.pair_corr_lookup(names, corr))
    _, created = asyncio.run(tool.place_uncovered(themes, ["L0", "L1", "L2"], by_symbol, ask))
    themes += created
    assert ask.calls == 3 and created[0]["keywords"] == ["신규"]
    cover = kr.coverage(themes, {s["symbol"]: s["cap"] for s in stocks}, top=10)
    assert cover["covered"] == 10
    for t in themes:
        assert {"id", "name", "sector", "keywords", "lines", "active_days", "first_seen", "last_seen",
                "members"} <= set(t)
        assert all({"code", "times", "share", "role"} <= set(m) for m in t["members"])


def _ov_map():
    mem = _theme("T001", ["MU", "WDC", "STX"], sector="반도체")
    mem["name"] = "메모리 반도체 묶음"  # the builder named it differently since the review
    pay = _theme("T002", ["V", "MA", "AON"], sector="보험")
    pay["name"] = "보험중개·결제"
    phones = _theme("T003", ["AAPL", "APH"], sector="IT 하드웨어·네트워크", role="ai")
    phones["name"] = "전자 커넥터"
    return [mem, pay, phones]


OVERRIDES = {
    "version": "test",
    "themes": {"메모리·저장장치": {"sector": "반도체", "anchor": "MU", "keywords": []},
               "보험중개·결제": {"sector": "보험", "keywords": ["보험중개"]},
               "결제 네트워크": {"sector": "결제·핀테크·가상자산", "keywords": ["카드"]},
               "스마트폰·소비자 기기": {"sector": "IT 하드웨어·네트워크", "keywords": ["스마트폰"]}},
    "assign": {"V": ["결제 네트워크"], "MA": ["결제 네트워크"], "AAPL": ["스마트폰·소비자 기기"],
               "SKHY": ["메모리·저장장치"], "NOPE": ["결제 네트워크"]},
    "add": {"AXP": ["결제 네트워크"], "WDC": ["보험중개·결제"]},
}


def test_overrides_resolve_by_anchor_name_or_create():
    themes = _ov_map()
    resolved, how = tm.resolve_targets(themes, OVERRIDES["themes"])
    assert how == {"anchor": ["메모리·저장장치"], "name": ["보험중개·결제"],
                   "created": ["결제 네트워크", "스마트폰·소비자 기기"]}
    memory = resolved["메모리·저장장치"]
    assert memory["id"] == "T001" and memory["name"] == "메모리·저장장치"
    assert memory["renamed_from"] == "메모리 반도체 묶음"
    created = resolved["결제 네트워크"]
    assert created["source"] == "override" and created["id"] == "T004" and created["keywords"] == ["카드"]


def test_overrides_assign_replaces_add_appends_and_unknown_tickers_skip():
    universe = {"MU", "WDC", "STX", "V", "MA", "AON", "AAPL", "APH", "SKHY", "AXP"}
    themes, report = tm.apply_overrides(_ov_map(), OVERRIDES, universe)
    by_name = {t["name"]: t for t in themes}
    homes = {code: sorted(t["name"] for t in themes if code in {m["code"] for m in t["members"]})
             for code in ("V", "MA", "AAPL", "SKHY", "AXP", "WDC")}
    assert homes["V"] == homes["MA"] == ["결제 네트워크"]  # assign replaces
    assert homes["AAPL"] == ["스마트폰·소비자 기기"] and homes["SKHY"] == ["메모리·저장장치"]
    assert homes["AXP"] == ["결제 네트워크"]  # add places a new member
    assert homes["WDC"] == ["메모리·저장장치", "보험중개·결제"]  # add keeps existing memberships
    assert {m["code"]: m["role"] for m in by_name["결제 네트워크"]["members"]} == {
        "V": "override", "MA": "override", "AXP": "override"}
    assert report["skipped_tickers"] == ["NOPE"] and report["assigned"] == 4 and report["added"] == 2
    assert [m["code"] for m in by_name["보험중개·결제"]["members"]] == ["AON", "WDC"]


def test_overrides_drop_emptied_themes_and_keep_override_over_cap():
    themes = [_theme("T001", ["X"]), _theme("T002", ["X"]), _theme("T003", ["X"], role="ai"),
              _theme("T004", ["Y"], role="ai")]
    for i, t in enumerate(themes):
        t["name"] = f"테마{i}"
    ov = {"themes": {"새 테마": {"sector": "기타", "keywords": []}}, "assign": {"Y": ["새 테마"]},
          "add": {"X": ["새 테마"]}}
    themes, report = tm.apply_overrides(themes, ov, {"X", "Y"}, limit=3)
    names = {t["name"]: [m["code"] for m in t["members"]] for t in themes}
    # 테마3: Y moved out; 테마2: its only member X went to the cap. Empty themes are removed.
    assert "테마3" not in names and report["emptied"] == ["테마2", "테마3"]
    assert names["새 테마"] == ["Y", "X"]
    # X sat in two price clusters and one AI theme; the override stays, the AI membership gives way
    assert sorted(n for n, codes in names.items() if "X" in codes) == ["새 테마", "테마0", "테마1"]
    assert report["capped"] == 1
