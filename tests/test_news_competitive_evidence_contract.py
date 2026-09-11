"""Offline prompt contracts; these do not prove source collection or model compliance."""

import ast
from pathlib import Path
from types import SimpleNamespace

import pytest


@pytest.fixture(params=["kr", "us"])
def market_factory(request):
    root = Path(__file__).resolve().parents[1]
    path = root / ("prism-us/" if request.param == "us" else "") / "cores/agents/news_strategy_agents.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    tree.body = [node for node in tree.body if not isinstance(node, (ast.Import, ast.ImportFrom))]
    namespace = {"Agent": SimpleNamespace}
    exec(compile(tree, str(path), "exec"), namespace)
    name = "create_us_news_analysis_agent" if request.param == "us" else "create_news_analysis_agent"
    return request.param, namespace[name]


@pytest.mark.parametrize("language", ["ko", "en"])
def test_news_preserves_listing_cache_shape_and_bounded_source_reuse(market_factory, language):
    market, factory = market_factory
    agent = factory("Example", "TEST", "20260910", language=language)
    prompt = agent.instruction
    assert agent.server_names == ["perplexity", "firecrawl"]
    assert "maxAge: 7200000" in prompt
    assert ("finance.yahoo.com/quote/TEST/news" if market == "us" else "finance.naver.com/item/news.naver?code=TEST") in prompt
    assert "### 3." in prompt and "#### " in prompt
    for required in ("at most 2 consolidated", "at most 2 additional", "Reuse", "public primary", "never invent URLs", "only if", "publication_date"):
        assert required in prompt


@pytest.mark.parametrize("language", ["ko", "en"])
def test_competitive_records_separate_claims_and_missingness(market_factory, language):
    _, factory = market_factory
    prompt = factory("Example", "TEST", "20260910", language=language).instruction
    for required in ("#### Competitive Evidence", "sector_tailwind", "price_leadership", "business_competitive_position", "field", "type", "entity", "peer_universe", "metric", "value", "period", "geography", "unit", "source", "status", "excerpt", "SOURCE_CHECKED", "SEARCH_ONLY", "NOT_FOUND", "INCOMPARABLE", "not a guarantee", "subsidiary", "decision timestamp"):
        assert required in prompt


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
def test_required_discovery_is_not_waived_by_listing_profile_or_social(market_factory, language):
    market, factory = market_factory
    kwargs = {"prefetched_social_sentiment": "positive social snapshot"} if market == "us" else {}
    prompt = factory("Holding Example", "TEST", "20260910", language=language, **kwargs).instruction
    for required in ("Query 1 is REQUIRED", "at invocation", "complete, comparable", "news listing", "basic company profile", "social sentiment", "does not waive", "target business scope", "appropriate peer_universe", "business_competitive_position", "holding-company peers", "subsidiary business"):
        assert required in prompt
    assert "No blanket mandatory search" not in prompt


@pytest.mark.parametrize("language", ["ko", "en"])
def test_source_status_requires_relevant_original_content_and_audit_reason(market_factory, language):
    _, factory = market_factory
    prompt = factory("Example", "TEST", "20260910", language=language).instruction
    for required in ("exact cited page", "actually opened", "Perplexity answer", "unrelated listing", "NOT_FOUND", "attempted", "unqueried", "not a runtime proof", "unresolved material"):
        assert required in prompt


@pytest.mark.parametrize("language", ["ko", "en"])
def test_price_leadership_is_stock_rs_and_peer_rank_requires_comparability(market_factory, language):
    _, factory = market_factory
    prompt = factory("Example", "TEST", "20260910", language=language).instruction
    for required in ("share-price relative return or RS", "window and peer_universe", "not product pricing or cost leadership", "business_competitive_position", "rank or strongest", "comparable metric", "covered peers", "different fiscal periods", "company strength", "comparison INCOMPARABLE"):
        assert required in prompt
