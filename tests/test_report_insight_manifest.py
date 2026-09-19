import json

from prism_core.report_insight_manifest import build_insight_manifest, section_manifest


def build(prefetched=None, macro=None, **kwargs):
    return build_insight_manifest("US", "CAT", "20260919", prefetched, macro, **kwargs)


def test_missing_kr_data_stays_unknown():
    result = build_insight_manifest("KR", "005930", "20260919", {}, {})
    assert len(result["categories"]) == 9
    assert all(c["collection_status"] == "UNKNOWN" for c in result["categories"].values())
    assert all(c["insight_status"] == "UNKNOWN" for c in result["categories"].values())


def test_us_profile_string_and_structured_profile_are_hash_only():
    result = build({"company_profile": "SECRET_BODY" * 10000,
                    "company_research_profile": {"industry": "Machinery"},
                    "segment_revenue": "Mining 45%; energy 55%", "stock_info": "financial data",
                    "financial_statements": "2026Q2", "holder_info": "fund ownership",
                    "recommendations": "consensus", "analysis_estimates": "2027 estimated"})
    assert "SECRET_BODY" not in json.dumps(result)
    assert result["categories"]["business_segments"]["collection_status"] == "INPUT_PRESENT"
    assert result["categories"]["flows_positioning"]["collection_status"] == "UNKNOWN"
    assert "issuer_guidance" in result["categories"]["earnings_estimates_guidance"]["unknown_dimensions"]


def test_sector_candidates_prices_do_not_establish_peer_or_industry_rank():
    result = build({"stock_ohlcv": "prices", "company_research_profile": {"sector": "Technology"},
                    "industry_ranking": {"rank": 1, "verified": True}},
                   {"leading_sectors": ["Technology"], "market_participation": {"breadth": 60}})
    cats = result["categories"]
    assert cats["direct_peers_competitive_position"]["collection_status"] == "UNKNOWN"
    assert cats["industry_price_leadership"]["insight_status"] == "UNKNOWN"
    assert cats["market_rotation_participation"]["collection_status"] == "INPUT_PRESENT"


def test_receipt_only_is_not_injected_source():
    result = build({"report_research": {"receipt": {"sources": [{"source_id": "S1"}]}}})
    assert result["categories"]["direct_peers_competitive_position"]["input_refs"] == []


def test_injected_source_ids_only_no_excerpt_duplication():
    result = build({"report_research": {"section_notes": {"news_analysis": json.dumps(
        {"sources": [{"source_id": "S1", "topic": "direct_peers_competitive_position",
                      "excerpt": "actual original text"}]})}}})
    refs = result["categories"]["direct_peers_competitive_position"]["input_refs"]
    assert refs[0]["source_id"] == "S1"
    assert "actual original text" not in json.dumps(result)


def test_utf8_bounds_drop_whole_categories_and_mark_omission():
    result = build({"company_profile": "한글" * 10000}, max_bytes=1000)
    assert len(json.dumps(result, ensure_ascii=False, separators=(",", ":")).encode()) <= 1000
    assert result["omitted_categories"] > 0
    assert result["omission_reason"] == "BYTE_BUDGET_WHOLE_CATEGORY_OMISSION"
    rendered = section_manifest(build(), "news_analysis", max_bytes=400)
    assert len(rendered.encode()) <= 400
    assert isinstance(json.loads(rendered), dict)


def test_malformed_inputs_fail_to_unknown_without_io(monkeypatch):
    import builtins

    def forbidden(*args, **kwargs):
        raise AssertionError("no I/O allowed")

    monkeypatch.setattr(builtins, "open", forbidden)
    result = build(["malformed"], "malformed")
    assert len(result["categories"]) == 9
    result = build({"company_research_profile": {"bad": object()},
                    "report_research": {"section_notes": {"news_analysis": "not json"}}})
    assert result["categories"]["business_segments"]["collection_status"] == "UNKNOWN"


def test_oversized_note_will_not_be_injected_so_no_source_refs():
    result = build({"report_research": {"section_notes": {"news_analysis": json.dumps(
        {"sources": [{"source_id": "S1", "excerpt": "x" * 10000}]})}}})
    assert result["categories"]["direct_peers_competitive_position"]["input_refs"] == []


def test_malformed_renderer_categories():
    assert json.loads(section_manifest({"categories": []}, "news_analysis"))["categories"] == {}


def test_topic_specific_sources_across_own_sections():
    notes = {section: json.dumps({"sources": [{"source_id": sid, "topic": topic, "excerpt": "original"}]})
             for section, sid, topic in (
                 ("company_overview", "S1", "business_segments"),
                 ("company_status", "S2", "financial_quality_valuation"),
                 ("news_analysis", "S3", "catalysts_risks_counterevidence"))}
    result = build({"insight_prefetch": {"section_notes": notes}})
    cats = result["categories"]
    for topic, sid, section in (("business_segments", "S1", "company_overview"),
                               ("financial_quality_valuation", "S2", "company_status"),
                               ("catalysts_risks_counterevidence", "S3", "news_analysis")):
        assert cats[topic]["input_refs"][0]["source_id"] == sid
        assert cats[topic]["input_refs"][0]["source_section"] == section
    assert cats["direct_peers_competitive_position"]["collection_status"] == "UNKNOWN"


def test_unknown_or_legacy_topics_not_credited_to_other_categories():
    note = json.dumps({"sources": [{"source_id": "S1", "topic": "unknown", "excerpt": "text"},
                                   {"source_id": "S2", "excerpt": "legacy text"},
                                   {"source_id": "S3", "topic": "ownership_governance", "excerpt": "holder text"}]})
    result = build({"report_research": {"section_notes": {"news_analysis": note}}})
    assert result["categories"]["ownership_governance"]["collection_status"] == "INPUT_PRESENT"
    assert result["categories"]["direct_peers_competitive_position"]["input_refs"] == []
    assert result["categories"]["catalysts_risks_counterevidence"]["input_refs"] == []


def test_kr_flow_owner_matches_actual_section():
    result = build_insight_manifest("KR", "005930", "20260919", {"flow_evidence": "flow"}, {})
    section = json.loads(section_manifest(result, "investor_trading_analysis"))
    assert section["categories"]["flows_positioning"]["collection_status"] == "INPUT_PRESENT"


def test_actual_section_utf8_limit_not_character_limit():
    note = json.dumps({"sources": [{"source_id": "S1", "topic": "business_segments", "excerpt": "한" * 2100}]},
                      ensure_ascii=False)
    assert len(note) < 6000 < len(note.encode())
    result = build({"report_research": {"section_notes": {"company_overview": note}}})
    assert result["categories"]["business_segments"]["input_refs"] == []
    for section in ("news_analysis", "company_overview", "company_status"):
        rendered = section_manifest(result, section, max_bytes=500)
        assert len(rendered.encode()) <= 500
        assert isinstance(json.loads(rendered), dict)
