from types import SimpleNamespace

import pytest

from cores.archive import insight_agent as insight_module
from cores.archive.insight_agent import (
    InsightAgent,
    _clean_answer_text,
    _extract_actual_tools,
    _has_internal_tool_status,
    _is_archive_only_question,
    _is_broad_recommendation,
    _select_mcp_servers,
)
from cores.archive.query_engine import parse_query_hints


def test_exact_korean_date_is_a_closed_range():
    hints = parse_query_hints(
        "미국 MU SMCI HPE의 2026년 8월 10일 PRISM 리포트만 요약해줘"
    )

    assert hints["market"] == "us"
    assert hints["ticker"] is None
    assert hints["date_from"] == "2026-08-10"
    assert hints["date_to"] == "2026-08-10"


def test_exact_iso_date_is_a_closed_range():
    hints = parse_query_hints("US reports on 2026-08-10")

    assert hints["date_from"] == "2026-08-10"
    assert hints["date_to"] == "2026-08-10"


def test_month_day_shorthand_uses_current_year_as_a_closed_range():
    hints = parse_query_hints("US MU 8/10 리포트만")

    assert hints["date_from"] == hints["date_to"]
    assert hints["date_from"].endswith("-08-10")


def test_archive_only_language_disables_external_tools():
    assert _is_archive_only_question("PRISM 리포트만 근거로 답해줘")
    assert _is_archive_only_question("외부 검색 없이 아카이브만 사용해")
    assert _is_archive_only_question("Use archive reports only; no web search")
    assert not _is_archive_only_question("최신 주가까지 확인해줘")


def test_broad_korean_recommendation_uses_archive_without_mcp_tools():
    question = "우리나라 주식중에 지금부터 장기투자할만한거 찾아줘"
    hints = parse_query_hints(question)

    assert hints["market"] == "kr"
    assert _is_broad_recommendation(question)
    assert _select_mcp_servers(question, hints) == []


def test_explicit_news_request_enables_single_paid_research_server():
    question = "미국 MU 최신 뉴스와 업황을 외부 검색해서 확인해줘"
    hints = parse_query_hints(question)

    assert _select_mcp_servers(question, hints) == ["yahoo_finance", "perplexity"]


def test_internal_tool_limit_language_requires_repair():
    assert _has_internal_tool_status("본 답변은 도구 호출 한도 도달로 완전하지 않습니다")
    assert _has_internal_tool_status("perplexity 도구 호출이 정상 응답을 반환하지 않았습니다")
    assert not _has_internal_tool_status("현재 데이터가 부족해 후보를 추천하기 어렵습니다")


def test_clean_answer_removes_structured_xml_tail():
    raw = (
        "확보된 데이터 기준으로 후보를 정리합니다.</answer>\n"
        '<key_takeaways>["내부 배열"]</key_takeaways>\n</invoke>'
    )

    assert _clean_answer_text(raw) == "확보된 데이터 기준으로 후보를 정리합니다."


def test_actual_tools_come_from_trace_not_model_claims():
    raw = """[Calling tool perplexity_ask with arguments {'messages': []}]
    [Calling tool get_stock_ohlcv with arguments {'ticker': '005930'}]
    {"tools_used": []}
    """

    assert _extract_actual_tools(raw) == ["perplexity_ask", "get_stock_ohlcv"]


def test_ground_metadata_rejects_unretrieved_evidence_and_records_archive():
    agent = InsightAgent(db_path=":memory:")
    parsed = {
        "answer": "ok",
        "key_takeaways": [],
        "tickers_mentioned": ["MU"],
        "tools_used": [],
        "evidence_report_ids": [1430, 1420, 999999],
    }
    context = {
        "reports": [SimpleNamespace(report_id=1430), SimpleNamespace(report_id=1451)]
    }

    grounded = agent._ground_response_metadata(parsed, context, "")

    assert grounded["evidence_report_ids"] == [1430]
    assert grounded["tools_used"] == ["archive_retrieval"]


@pytest.mark.asyncio
async def test_archive_only_multi_ticker_retrieval_keeps_each_exact_date(monkeypatch):
    calls = []

    class FakeQueryEngine:
        def __init__(self, **kwargs):
            pass

        async def retrieve(self, **kwargs):
            calls.append(kwargs)
            ids = {"MU": 1430, "SMCI": 1451, "HPE": 1393}
            return [SimpleNamespace(report_id=ids[kwargs["ticker"]])]

    monkeypatch.setattr(insight_module, "QueryEngine", FakeQueryEngine)
    monkeypatch.setattr(insight_module, "load_api_key", lambda: None)

    context = await InsightAgent(db_path=":memory:")._build_retrieval_context(
        "미국 MU SMCI HPE의 2026년 8월 10일 PRISM 리포트만 요약해줘"
    )

    assert [call["ticker"] for call in calls] == ["MU", "SMCI", "HPE"]
    assert all(
        call["date_from"] == call["date_to"] == "2026-08-10" for call in calls
    )
    assert [report.report_id for report in context["reports"]] == [1430, 1451, 1393]
    assert context["insights"] == []
