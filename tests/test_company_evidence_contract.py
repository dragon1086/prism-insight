"""Prompt contracts only: no SDK initialization, reports, or network calls."""

import ast
from pathlib import Path
from types import SimpleNamespace

import pytest


@pytest.fixture
def factories():
    source = Path(__file__).resolve().parents[1] / "cores/agents/company_info_agents.py"
    tree = ast.parse(source.read_text(encoding="utf-8"))
    tree.body = [node for node in tree.body if not isinstance(node, ast.ImportFrom)]
    namespace = {"Agent": SimpleNamespace}
    exec(compile(tree, str(source), "exec"), namespace)
    return namespace


URLS = {
    "기업현황": "https://example.test/status",
    "기업개요": "https://example.test/overview",
    "재무분석": "https://example.test/financial",
    "투자지표": "https://example.test/indicators",
    "경쟁사분석": "https://example.test/peers",
    "업종분석": "https://example.test/industry",
}


@pytest.mark.parametrize("language", ["ko", "en"])
@pytest.mark.parametrize("kind,section", [("status", "2-1"), ("overview", "2-2")])
def test_existing_agent_shape_and_sections_are_preserved(factories, language, kind, section):
    agent = factories[f"create_company_{kind}_agent"]("HDC", "012630", "20260910", URLS, language)
    assert agent.name == f"company_{kind}_agent"
    assert agent.server_names == ["firecrawl"]
    assert f"### {section}." in agent.instruction
    assert "012630" in agent.instruction
    assert 'formats' in agent.instruction


@pytest.mark.parametrize("language", ["ko", "en"])
def test_status_has_bounded_conditional_financial_supplement(factories, language):
    prompt = factories["create_company_status_agent"]("HDC", "012630", "20260910", URLS, language).instruction
    assert URLS["재무분석"] in prompt
    assert "Page Only" not in prompt and "페이지에서만" not in prompt
    for term in ("quarterly EPS", "YoY", "UNKNOWN", "actual/estimate", "consolidated/separate", "annual EPS", "CAN SLIM"):
        assert term in prompt
    assert ("최대 1개" if language == "ko" else "at most 1") in prompt
    assert ("누락된 경우에만" if language == "ko" else "only if missing") in prompt


@pytest.mark.parametrize("language", ["ko", "en"])
def test_status_prefers_one_indicator_page_and_preserves_eps_basis(factories, language):
    prompt = factories["create_company_status_agent"]("삼성전자", "005930", "20260910", URLS, language).instruction
    assert f"EPS_SUPPLEMENT_URL: {URLS['투자지표']}" in prompt
    for term in ("statutory/basic/diluted", "provider-adjusted-share", "SOURCE_REPORTED_YOY", "COMPUTED_YOY", "HTML", "HTTP 200"):
        assert term in prompt
    without_indicators = {key: value for key, value in URLS.items() if key != "투자지표"}
    fallback = factories["create_company_status_agent"]("삼성전자", "005930", "20260910", without_indicators, language).instruction
    assert f"EPS_SUPPLEMENT_URL: {URLS['재무분석']}" in fallback


@pytest.mark.parametrize("language", ["ko", "en"])
def test_eps_formula_and_numeric_denominator_are_separate_evidence(factories, language):
    prompt = factories["create_company_status_agent"]("한국콜마", "161890", "20260914", URLS, language).instruction
    for field in ("EPS_BASIS", "DENOMINATOR_DEFINITION", "DENOMINATOR_VALUE", "BASIS_SOURCE_URL"):
        assert field in prompt
    for term in (("재무계정산식", "수정평균발행주식수", "역산", "수치만 UNKNOWN", "새로운 필수 매수 조건")
                 if language == "ko" else
                 ("formula/help", "adjusted average issued shares", "back-solve", "only the numeric value UNKNOWN", "new mandatory buy condition")):
        assert term in prompt
    assert ("실제로 확인" if language == "ko" else "actually observed") in prompt


@pytest.mark.parametrize("language", ["ko", "en"])
def test_overview_separates_leadership_from_price_and_sector(factories, language):
    prompt = factories["create_company_overview_agent"]("HDC", "012630", "20260910", URLS, language).instruction
    assert URLS["업종분석"] in prompt and URLS["경쟁사분석"] not in prompt
    assert "Page Only" not in prompt and "페이지에서만" not in prompt
    for term in ("industry_leadership", "price_RS", "sector_tailwind", "UNKNOWN", "012630", "CAN SLIM"):
        assert term in prompt
    assert ("최대 1개" if language == "ko" else "at most 1") in prompt
    assert ("누락된 경우에만" if language == "ko" else "only if missing") in prompt
    for term in (("비교군", "기간", "출처") if language == "ko" else ("peer universe", "period", "source")):
        assert term in prompt


@pytest.mark.parametrize("language", ["ko", "en"])
@pytest.mark.parametrize("kind", ["status", "overview"])
def test_missing_optional_urls_are_explicit_not_invented(factories, language, kind):
    minimal = {key: URLS[key] for key in ("기업현황", "기업개요")}
    prompt = factories[f"create_company_{kind}_agent"]("HDC", "012630", "20260910", minimal, language).instruction
    assert "URL_UNAVAILABLE" in prompt
    assert "None" not in prompt
    assert ("URL을 추측" if language == "ko" else "invent URLs") in prompt


@pytest.mark.parametrize("language", ["ko", "en"])
@pytest.mark.parametrize("kind", ["status", "overview"])
def test_entity_scope_is_generic_and_does_not_leak_another_company(factories, language, kind):
    prompt = factories[f"create_company_{kind}_agent"]("삼성전자", "005930", "20260910", URLS, language).instruction
    assert "삼성전자" in prompt and "005930" in prompt
    for unrelated in ("HDC", "012630", "294870"):
        assert unrelated not in prompt
    for term in (("모회사", "자회사", "별도 상장") if language == "ko" else ("parent", "subsidiary", "separately listed")):
        assert term in prompt


@pytest.mark.parametrize("language", ["ko", "en"])
def test_overview_uses_precollected_peer_table_or_bounded_fallback(factories, language):
    table = "#### 경쟁사 비교\n| 구분 | HDC | 비교기업 |\n비교기업 종목코드: 비교기업(111111)"
    prompt = factories["create_company_overview_agent"](
        "HDC", "012630", "20260910", URLS, language, peer_table=table).instruction
    assert table in prompt
    assert ("#### 경쟁사 대비 위치 분석" if language == "ko" else "#### Competitive Position Analysis") in prompt
    assert ("1개 문단" if language == "ko" else "1 paragraph") in prompt
    assert ("표에 있는 값만" if language == "ko" else "only numbers that appear in the table") in prompt
    fallback = factories["create_company_overview_agent"]("HDC", "012630", "20260910", URLS, language).instruction
    assert "#### 경쟁사 비교" not in fallback
    assert ("재무 수치나 순위를 만들어내지" if language == "ko" else "never state peer financial figures") in fallback
