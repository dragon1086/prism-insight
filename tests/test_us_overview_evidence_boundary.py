"""US overview input contracts; no SDK, network, or model execution."""

import ast
from pathlib import Path
from types import SimpleNamespace

import pytest


@pytest.fixture
def factories():
    source = Path(__file__).resolve().parents[1] / "prism-us/cores/agents/company_info_agents.py"
    tree = ast.parse(source.read_text(encoding="utf-8"))
    tree.body = [node for node in tree.body if not isinstance(node, ast.ImportFrom)]
    namespace = {"Agent": SimpleNamespace, "Dict": dict}
    exec(compile(tree, str(source), "exec"), namespace)
    return namespace


URLS = {key: f"https://example.test/{key}" for key in (
    "profile", "holders", "key_statistics", "financials", "analysis"
)}


@pytest.mark.parametrize("language", ["ko", "en"])
@pytest.mark.parametrize("prefetched", [False, True])
def test_profile_is_not_competitive_evidence_and_no_news_ownership(factories, language, prefetched):
    data = {"company_profile": "PROFILE_SENTINEL"} if prefetched else None
    agent = factories["create_us_company_overview_agent"](
        "Example Corp", "EXAM", "20260911", URLS, language, data
    )
    prompt = agent.instruction
    for term in ("UNKNOWN", "industry_leadership", "price_RS", "sector_tailwind"):
        assert term in prompt
    for forbidden in ("COMPETITIVE_EVIDENCE_OWNER", "Competitive Evidence", "Do not research the same peers again",
                      "같은 경쟁사를 다시 조사하지"):
        assert forbidden not in prompt
    assert ("경쟁우위의 증거가 아닙니다" if language == "ko" else "not evidence of competitive advantage") in prompt
    assert ("부정적인 투자 판정이 아닙니다" if language == "ko" else "not a negative investment verdict") in prompt
    assert ("경쟁사나 점유율을 만들어" if language == "ko" else "invent competitors or market shares") in prompt
    # Without a verified peer table the overview must not fabricate peer figures.
    assert ("경쟁사 비교 자료 부재" if language == "ko" else "Competitor Comparison Unavailable") in prompt
    assert agent.server_names == ([] if prefetched else ["firecrawl", "yahoo_finance"])
    assert agent.name == "us_company_overview_agent"
    assert "### 2-2." in prompt


@pytest.mark.parametrize("language", ["ko", "en"])
def test_peer_table_is_injected_as_pre_collected_data(factories, language):
    agent = factories["create_us_company_overview_agent"](
        "Example Corp", "EXAM", "20260911", URLS, language, {"company_profile": "PROFILE_SENTINEL"},
        peer_table="PEER_TABLE_SENTINEL",
    )
    prompt = agent.instruction
    assert prompt.count("PEER_TABLE_SENTINEL") == 1
    assert ("#### 경쟁사 대비 위치 분석" if language == "ko" else "#### Competitive Position Analysis") in prompt
    assert ("위 표에 있는 값만 인용" if language == "ko" else "Cite only numbers that appear in the table") in prompt
    assert agent.server_names == []


@pytest.mark.parametrize("language", ["ko", "en"])
def test_prefetch_keeps_profile_holders_segments_without_new_tools(factories, language):
    data = {
        "company_profile": "PROFILE_SENTINEL",
        "holder_info": "HOLDERS_SENTINEL",
        "segment_revenue": "SEGMENTS_SENTINEL",
    }
    agent = factories["create_us_company_overview_agent"](
        "Example Corp", "EXAM", "20260911", URLS, language, data
    )
    assert agent.server_names == []
    for value in data.values():
        assert agent.instruction.count(value) == 1
    assert ("MCP 도구 호출 금지" if language == "ko" else "DO NOT call any MCP tools") in agent.instruction
    assert ("기본정보·주주·사업부 자료" if language == "ko" else "profile, holders, and segment data") in agent.instruction


@pytest.mark.parametrize("language", ["ko", "en"])
def test_status_fundamentals_contract_is_not_changed(factories, language):
    agent = factories["create_us_company_status_agent"](
        "Example Corp", "EXAM", "20260911", URLS, language,
        {"stock_info": "STOCK_SENTINEL", "analysis_estimates": "ESTIMATES_SENTINEL"},
    )
    assert agent.server_names == []
    assert "STOCK_SENTINEL" in agent.instruction
    assert "ESTIMATES_SENTINEL" in agent.instruction
    assert "COMPETITIVE_EVIDENCE_OWNER" not in agent.instruction
