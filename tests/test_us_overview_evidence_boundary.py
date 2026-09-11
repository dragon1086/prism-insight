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
def test_competitive_evidence_is_owned_by_news_not_profile(factories, language, prefetched):
    data = {"company_profile": "PROFILE_SENTINEL"} if prefetched else None
    agent = factories["create_us_company_overview_agent"](
        "Example Corp", "EXAM", "20260911", URLS, language, data
    )
    prompt = agent.instruction
    for term in ("COMPETITIVE_EVIDENCE_OWNER: news", "UNKNOWN", "industry_leadership", "price_RS", "sector_tailwind"):
        assert term in prompt
    assert ("경쟁우위의 증거가 아닙니다" if language == "ko" else "not evidence of competitive advantage") in prompt
    assert ("같은 경쟁사를 다시 조사하지" if language == "ko" else "Do not research the same peers again") in prompt
    assert ("부정적인 투자 판정이 아닙니다" if language == "ko" else "not a negative investment verdict") in prompt
    assert ("경쟁사나 점유율을 만들어" if language == "ko" else "invent competitors or market shares") in prompt
    assert agent.server_names == ([] if prefetched else ["firecrawl", "yahoo_finance"])
    assert agent.name == "us_company_overview_agent"
    assert "### 2-2." in prompt


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
