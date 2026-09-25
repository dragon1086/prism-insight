"""Offline prompt contracts; these do not prove source collection or model compliance."""

import ast
from pathlib import Path
from types import SimpleNamespace

import pytest


def _factory(market):
    root = Path(__file__).resolve().parents[1]
    path = root / ("prism-us/" if market == "us" else "") / "cores/agents/news_strategy_agents.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    tree.body = [node for node in tree.body if not isinstance(node, (ast.Import, ast.ImportFrom))]
    namespace = {"Agent": SimpleNamespace}
    exec(compile(tree, str(path), "exec"), namespace)
    return namespace["create_us_news_analysis_agent" if market == "us" else "create_news_analysis_agent"]


# Neither market asks the news agent for Competitive Evidence records: KR and US competitor
# figures come from the deterministic peer tables (kr_peer_comparison / us_peer_comparison).
@pytest.fixture(params=["kr", "us"])
def market_factory(request):
    return request.param, _factory(request.param)


@pytest.mark.parametrize("language", ["ko", "en"])
def test_kr_news_has_no_competitive_evidence_contract(language):
    agent = _factory("kr")("Example", "TEST", "20260910", language=language)
    prompt = agent.instruction
    assert agent.server_names == ["perplexity", "firecrawl"]
    assert "maxAge: 7200000" in prompt and "finance.naver.com/item/news.naver?code=TEST" in prompt
    assert "### 3." in prompt and "#### " in prompt
    for forbidden in ("Competitive Evidence", "SOURCE_CHECKED", "SEARCH_ONLY", "NOT_FOUND", "INCOMPARABLE",
                      "peer_universe", "sector_tailwind", "price_leadership", "business_competitive_position",
                      "Query 1 is REQUIRED"):
        assert forbidden not in prompt
    for required in (("경쟁사 수치 비교는 별도 경쟁사 비교 표가 담당", "최대 2회", "최대 2개", "20260910",
                      "URL을 만들지", "정의되지 않은 기호형 출처 별칭")
                     if language == "ko" else
                     ("separate competitor comparison table", "at most 2 consolidated", "at most 2 cited",
                      "20260910", "Never invent URLs", "undefined symbolic citation aliases")):
        assert required in prompt
    for forbidden in ("firecrawl 1회만", "추가 스크랩 금지", "Perplexity 답변만으로", "필수 - Perplexity 사용",
                      "firecrawl 1 call only", "Mandatory - Use Perplexity"):
        assert forbidden not in prompt
@pytest.mark.parametrize("language", ["ko", "en"])
def test_no_old_verification_prohibitions_remain(market_factory, language):
    _, factory = market_factory
    prompt = factory("Example", "TEST", "20260910", language=language).instruction
    for forbidden in ("firecrawl 1회만", "firecrawl 1 call only", "firecrawl_scrape only 1 call", "뉴스 페이지 1회만", "추가 스크랩 금지", "Perplexity 답변만으로", "Perplexity 답변으로 충분", "Perplexity data only", "Perplexity responses only", "do NOT scrape individual", "do NOT scrape leader", "no firecrawl for leaders", "no additional firecrawl calls needed", "Mandatory - Use Perplexity", "필수 - Perplexity 사용"):
        assert forbidden not in prompt


def test_us_social_prefetch_still_does_not_trigger_duplicate_calls(market_factory):
    market, factory = market_factory
    if market == "us":
        prompt = factory("Example", "TEST", "20260910", language="en", prefetched_social_sentiment="sentiment snapshot").instruction
        assert "sentiment snapshot" in prompt
        assert "do not make extra tool calls for social sentiment" in prompt



@pytest.mark.parametrize("language", ["ko", "en"])
def test_us_news_has_no_competitive_evidence_contract(language):
    factory = _factory("us")
    agent = factory("Example", "TEST", "20260910", language=language)
    prompt = agent.instruction
    assert agent.server_names == ["perplexity", "firecrawl"]
    assert "finance.yahoo.com/quote/TEST/news" in prompt and "maxAge: 7200000" in prompt
    assert "### 3." in prompt and "#### " in prompt
    for forbidden in ("Competitive Evidence", "SOURCE_CHECKED", "SEARCH_ONLY", "peer_universe",
                      "at most 2 consolidated", "COMPETITIVE_EVIDENCE"):
        assert forbidden not in prompt
    assert ("최대 2개" if language == "ko" else "at most 2 original article URLs") in prompt
    assert ("경쟁사 비교표" if language == "ko" else "peer comparison table") in prompt
