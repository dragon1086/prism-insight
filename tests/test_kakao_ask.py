"""The `질문` command, end to end: parse, enqueue, run, render.

Ask is the first job that is not a report. It has no ticker, so it skips the
resolver entirely and carries its input in `payload` instead — these tests pin
that seam, because the report path and the ask path share one table, one
worker, and one outbox.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import pytest

from kakao_bot.adapters.kakao.delivery_renderer import render_delivery
from kakao_bot.adapters.persistence.sqlite import SQLiteKakaoRepository
from kakao_bot.application.analysis_service import AnalysisService
from kakao_bot.application.command_parser import CommandKind
from kakao_bot.application.command_service import (
    IMPLEMENTED_COMMANDS,
    CommandOutcomeKind,
    CommandService,
    help_text,
)
from kakao_bot.domain.models import (
    AnalysisJob,
    ApprovalStatus,
    ClaimedOutboundDelivery,
    InboundMessage,
)
from kakao_bot.ports.analysis import AnalysisOutcome, ResolvedTicker, TickerResolution

NOW = datetime(2026, 8, 3, 5, 0, tzinfo=timezone.utc)
ROOM = "room-1"
USER = "user-1"
QUESTION = "오늘 코스피 왜 빠졌어?"


class FakeResolver:
    """Records calls so a test can assert the ask path never reaches it."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str | None]] = []

    def resolve(self, query: str, *, market: str | None) -> TickerResolution:
        self.calls.append((query, market))
        return TickerResolution(
            ticker=ResolvedTicker(ticker="005930", company_name="삼성전자", market="kr")
        )


class FakeAnalysisPort:
    def __init__(self, *results):
        self._results = list(results)
        self.generate_calls: list[tuple] = []
        self.answer_calls: list[str] = []

    def generate(self, ticker, company_name, *, market):
        self.generate_calls.append((ticker, company_name, market))
        return self._pop()

    def answer(self, question):
        self.answer_calls.append(question)
        return self._pop()

    def _pop(self):
        result = self._results.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


def message(text: str) -> InboundMessage:
    return InboundMessage(
        event_id=f"evt-{text}",
        sequence=1,
        room_id=ROOM,
        user_id=USER,
        nickname=None,
        text=text,
        callback_token="cb",
        occurred_at=NOW,
    )


def delivery(message_type: str, payload: dict) -> ClaimedOutboundDelivery:
    return ClaimedOutboundDelivery(
        delivery_key="d-1",
        room_id=ROOM,
        message_type=message_type,
        payload=payload,
        attempt_count=1,
        lease_owner="worker-1",
        lease_expires_at=NOW,
        created_at=NOW,
    )


@pytest.fixture
def repository(tmp_path):
    with SQLiteKakaoRepository(tmp_path / "kakao.sqlite") as repo:
        repo.discover_room(ROOM, discovered_at=NOW)
        repo.set_room_approval(ROOM, ApprovalStatus.APPROVED)
        yield repo


def ask_job(question: str = QUESTION, *, job_id: str = "job-ask") -> AnalysisJob:
    return AnalysisJob(
        job_id=job_id,
        room_id=ROOM,
        user_id=USER,
        ticker="",
        company_name="",
        market="kr",
        kind="ask",
        payload={"question": question},
    )


# --------------------------------------------------------------------------
# Persistence: kind and payload have to survive the round trip
# --------------------------------------------------------------------------


def test_ask_job_keeps_its_kind_and_question_through_claim(repository):
    repository.enqueue_analysis_job(ask_job(), now=NOW)

    [claimed] = repository.claim_analysis_jobs(now=NOW, lease_seconds=900, limit=1)

    assert claimed.kind == "ask"
    assert claimed.payload == {"question": QUESTION}


def test_a_report_job_still_claims_as_a_report_with_no_payload(repository):
    repository.enqueue_analysis_job(
        AnalysisJob(
            job_id="job-report",
            room_id=ROOM,
            user_id=USER,
            ticker="005930",
            company_name="삼성전자",
            market="kr",
        ),
        now=NOW,
    )

    [claimed] = repository.claim_analysis_jobs(now=NOW, lease_seconds=900, limit=1)

    assert claimed.kind == "report"
    assert claimed.payload is None


def test_a_question_with_quotes_and_unicode_survives_json(repository):
    tricky = '삼성전자 "목표가" 어때? 「급등」 이유는?'
    repository.enqueue_analysis_job(ask_job(tricky), now=NOW)

    [claimed] = repository.claim_analysis_jobs(now=NOW, lease_seconds=900, limit=1)

    assert claimed.payload == {"question": tricky}


# --------------------------------------------------------------------------
# Command service: ask skips ticker resolution
# --------------------------------------------------------------------------


def test_ask_is_actually_enabled():
    # The last switch. Without this the command parses, and then answers
    # "아직 준비 중인 기능입니다".
    assert CommandKind.ASK in IMPLEMENTED_COMMANDS
    # Help advertises asking as a plain question now, not a keyword.
    assert "물어보" in help_text() or "어때" in help_text()


def test_ask_enqueues_without_touching_the_resolver(repository):
    resolver = FakeResolver()
    service = CommandService(repository, resolver)

    outcome = service.handle(message(f"질문 {QUESTION}"), now=NOW)

    assert outcome.kind is CommandOutcomeKind.ACCEPTED
    assert resolver.calls == []
    assert "1분" in outcome.message
    assert "중간 표시가 없어도" in outcome.message

    [claimed] = repository.claim_analysis_jobs(now=NOW, lease_seconds=900, limit=1)
    assert claimed.kind == "ask"
    assert claimed.payload == {"question": QUESTION}


def test_a_bare_ask_asks_for_a_question_not_a_ticker(repository):
    service = CommandService(repository, FakeResolver())

    outcome = service.handle(message("질문"), now=NOW)

    assert outcome.kind is CommandOutcomeKind.REJECTED
    assert "종목" not in outcome.message


def test_an_overlong_question_is_truncated_before_storage(repository):
    service = CommandService(repository, FakeResolver())

    service.handle(message("질문 " + "가" * 900), now=NOW)

    [claimed] = repository.claim_analysis_jobs(now=NOW, lease_seconds=900, limit=1)
    assert len(claimed.payload["question"]) == 500


def test_ask_counts_against_the_same_daily_quota(repository):
    service = CommandService(repository, FakeResolver())

    service.handle(message(f"질문 {QUESTION}"), now=NOW)

    assert repository.count_analysis_jobs_since(
        room_id=ROOM, user_id=None, since=NOW.replace(hour=0)
    ) == 1


# --------------------------------------------------------------------------
# Worker: the ask branch calls answer(), not generate()
# --------------------------------------------------------------------------


def test_worker_answers_an_ask_job_and_enqueues_an_ask_result(repository):
    repository.enqueue_analysis_job(ask_job(), now=NOW)
    analysis = FakeAnalysisPort(
        AnalysisOutcome(succeeded=True, summary="코스피는 외국인 순매도로 하락했습니다.")
    )

    result = AnalysisService(repository, analysis).run_once(now=NOW, limit=1)

    assert result.completed == 1
    assert analysis.answer_calls == [QUESTION]
    assert analysis.generate_calls == []

    [out] = repository.list_outbox()
    assert out["message_type"] == "ask_result"


def test_a_failed_ask_enqueues_an_ask_failure_that_still_names_the_question(
    repository,
):
    repository.enqueue_analysis_job(ask_job(), now=NOW)
    analysis = FakeAnalysisPort(
        AnalysisOutcome(succeeded=False, error_code="ask_empty")
    )

    result = AnalysisService(repository, analysis).run_once(now=NOW, limit=1)

    assert result.failed == 1
    [out] = repository.list_outbox()
    assert out["message_type"] == "ask_failed"
    assert out["payload"]["question"] == QUESTION


def test_a_report_job_is_unaffected_by_the_ask_branch(repository):
    repository.enqueue_analysis_job(
        AnalysisJob(
            job_id="job-report",
            room_id=ROOM,
            user_id=USER,
            ticker="005930",
            company_name="삼성전자",
            market="kr",
        ),
        now=NOW,
    )
    analysis = FakeAnalysisPort(AnalysisOutcome(succeeded=True, summary="요약"))

    AnalysisService(repository, analysis).run_once(now=NOW, limit=1)

    assert analysis.generate_calls == [("005930", "삼성전자", "kr")]
    assert analysis.answer_calls == []
    [out] = repository.list_outbox()
    assert out["message_type"] == "analysis_result"


# --------------------------------------------------------------------------
# The adapter must survive the worker's threading shape
# --------------------------------------------------------------------------


def test_stock_investment_question_expands_into_research_queries():
    from kakao_bot.adapters.prism.report_adapter import _ask_search_plan

    queries, use_primary_sources = _ask_search_plan("카카오 지금 사도 될까?")

    assert use_primary_sources is True
    assert len(queries) == 2
    assert all("카카오" in query for query in queries)
    assert any("실적" in query and "전망" in query for query in queries)
    assert any("공시" in query and "수급" in query for query in queries)
    assert all("사도 될까" not in query for query in queries)


def test_recent_stock_outlook_question_uses_the_stock_research_plan():
    from kakao_bot.adapters.prism.report_adapter import _ask_search_plan

    queries, use_primary_sources = _ask_search_plan("삼성전자 최근 전망 알려줘")

    assert use_primary_sources is True
    assert len(queries) == 2
    assert all("삼성전자" in query for query in queries)
    assert all("알려줘" not in query for query in queries)


def test_intraday_stock_move_question_uses_today_cause_queries():
    from kakao_bot.adapters.prism.report_adapter import _ask_search_plan

    queries, use_primary_sources = _ask_search_plan(
        "SK하이닉스 오늘 거의 하한가까지 갔다가 올라온 이유는 뭐야"
    )

    assert use_primary_sources is True
    assert len(queries) == 2
    assert all("SK하이닉스" in query for query in queries)
    assert any("장중 급락 반등 이유" in query for query in queries)
    assert any("수급" in query and "원인" in query for query in queries)
    assert all("뭐야" not in query for query in queries)


def test_general_question_keeps_the_users_original_search_query():
    from kakao_bot.adapters.prism.report_adapter import _ask_search_plan

    assert _ask_search_plan(QUESTION) == ([QUESTION], False)


@pytest.mark.asyncio
async def test_stock_question_requires_dated_source_evidence(monkeypatch):
    import cores.market_facts_cache as mfc
    import report_generator as rg
    from kakao_bot.adapters.prism.report_adapter import _ask

    captured = {}

    async def facts(market):
        return {}

    async def search(query, prompt, **kwargs):
        captured.update(query=query, prompt=prompt, kwargs=kwargs)
        return "연합뉴스(7/29)에 따르면 AI 수익화가 관건입니다."

    monkeypatch.setattr(mfc, "daily_facts", facts)
    monkeypatch.setattr(rg, "generate_firecrawl_search_response", search)

    answer = await _ask("카카오 지금 사도 될까?")

    assert answer.startswith("연합뉴스")
    assert isinstance(captured["query"], list)
    assert captured["kwargs"].get("include_domains")
    assert captured["kwargs"]["tbs"] == "qdr:d"
    assert captured["kwargs"]["sources"] == ["web"]
    assert captured["kwargs"]["include_source_links"] is True
    assert "매체명과 발행일" in captured["prompt"]
    assert "근거가 없다" in captured["prompt"]
    assert "현재가·등락률·실적" in captured["prompt"]
    assert "현재가·장중 고저가·당일 등락률은 절대" in captured["prompt"]
    assert "[자료 n]" in captured["prompt"]


def test_source_links_follow_the_evidence_numbers_used_by_the_answer():
    from report_generator import _append_source_links

    items = [
        {
            "title": "첫 번째 기사",
            "date": "2026-08-06",
            "url": "https://news.example/one",
        },
        {
            "title": "두 번째 기사",
            "date": "2026-08-06",
            "url": "https://news.example/two",
        },
    ]

    answer = _append_source_links(
        "전망이 개선됐습니다. [서울경제·8월 6일, 자료 2]",
        items,
    )

    assert "https://news.example/two" in answer
    assert "https://news.example/one" not in answer


def test_source_links_fall_back_to_the_newest_three_when_model_omits_numbers():
    from report_generator import _append_source_links

    items = [
        {"title": f"기사 {index}", "date": "2026-08-06", "url": f"https://n/{index}"}
        for index in range(1, 5)
    ]

    answer = _append_source_links("출처 번호 없는 답변", items)

    assert all(f"https://n/{index}" in answer for index in range(1, 4))
    assert "https://n/4" not in answer


def test_intraday_facts_calculate_the_candle_and_low_rebound():
    from kakao_bot.adapters.prism.report_adapter import _format_stock_session_facts

    facts = _format_stock_session_facts(
        "SK하이닉스",
        "000660",
        {"Open": 1_600_000, "High": 1_606_000, "Low": 1_505_000, "Close": 1_522_000, "Volume": 2_259_466},
        {"Close": 1_668_000, "Date": "20260805"},
        observed_at="2026-08-06 11:20 KST",
    )

    assert "전일 종가: 1,668,000원" in facts
    assert "장중 저가: 1,505,000원" in facts
    assert "현재가: 1,522,000원" in facts
    assert "전일 대비: -8.75%" in facts
    assert "저가 대비 반등: +1.13%" in facts
    assert "당일 장중 값" in facts


@pytest.mark.asyncio
async def test_intraday_question_adds_stock_facts_and_forbids_old_cause_inference(
    monkeypatch,
):
    import cores.market_facts_cache as mfc
    import kakao_bot.adapters.prism.report_adapter as adapter
    import report_generator as rg

    captured = {}

    async def facts(market):
        return {}

    async def stock_facts(subject):
        assert subject == "SK하이닉스"
        return "[검증된 종목 당일 일봉 데이터]\n현재가: 1,522,000원"

    async def search(query, prompt, **kwargs):
        captured.update(query=query, prompt=prompt, kwargs=kwargs)
        return "오늘자 수급 기사에 따르면 차익실현 매물이 나왔습니다."

    monkeypatch.setattr(mfc, "daily_facts", facts)
    monkeypatch.setattr(adapter, "_stock_session_facts", stock_facts)
    monkeypatch.setattr(rg, "generate_firecrawl_search_response", search)

    answer = await adapter._ask(
        "SK하이닉스 오늘 거의 하한가까지 갔다가 올라온 이유는 뭐야"
    )

    assert answer.startswith("오늘자")
    assert "현재가: 1,522,000원" in captured["kwargs"]["grounded_facts"]
    assert captured["kwargs"]["period_label"].endswith("당일 장중")
    assert "과거 유사 사례로 오늘 원인을 단정하지 마라" in captured["prompt"]
    assert "오늘자 직접 관련 기사" in captured["prompt"]


def test_answer_works_when_called_the_way_the_worker_calls_it(monkeypatch):
    """`run_once` is always reached through `asyncio.to_thread`.

    An earlier version used `asyncio.run()` inside that thread. Retrieval and
    the LLM both succeeded, and then `asyncio.run`'s teardown raised — because
    `daily_facts` imports `krx_data_client`, which applies `nest_asyncio` — so
    a finished answer was recorded as a failed job. This pins the shape.
    """

    import cores.market_facts_cache as mfc
    import report_generator as rg
    from kakao_bot.adapters.prism.report_adapter import PrismReportAdapter

    async def facts(market):
        return {"grounded_facts": "KOSPI 3100", "period_label": "2026-08-03"}

    async def search(query, prompt, **kwargs):
        assert kwargs.get("grounded_facts") == "KOSPI 3100"
        return "외국인 순매도로 하락했습니다."

    monkeypatch.setattr(mfc, "daily_facts", facts)
    monkeypatch.setattr(rg, "generate_firecrawl_search_response", search)

    async def drive():
        return await asyncio.to_thread(PrismReportAdapter().answer, QUESTION)

    outcome = asyncio.run(drive())

    assert outcome.succeeded, outcome.error_code
    assert outcome.summary == "외국인 순매도로 하락했습니다."


def test_a_blank_answer_is_reported_as_a_failure(monkeypatch):
    import cores.market_facts_cache as mfc
    import report_generator as rg
    from kakao_bot.adapters.prism.report_adapter import ASK_EMPTY, PrismReportAdapter

    async def facts(market):
        return {}

    async def search(query, prompt, **kwargs):
        return "   "

    monkeypatch.setattr(mfc, "daily_facts", facts)
    monkeypatch.setattr(rg, "generate_firecrawl_search_response", search)

    outcome = PrismReportAdapter().answer(QUESTION)

    assert not outcome.succeeded
    assert outcome.error_code == ASK_EMPTY


# --------------------------------------------------------------------------
# Rendering
# --------------------------------------------------------------------------


def _first_text(response: dict) -> str:
    return response["template"]["outputs"][0]["simpleText"]["text"]


def test_ask_result_shows_the_question_and_the_answer():
    response = render_delivery(
        delivery("ask_result", {"question": QUESTION, "answer": "외국인 순매도 탓입니다."})
    )

    text = _first_text(response)
    assert QUESTION in text
    assert "외국인 순매도" in text


def test_ask_result_strips_markdown_the_bubble_cannot_show():
    response = render_delivery(
        delivery(
            "ask_result",
            {"question": QUESTION, "answer": "## 요약\n- **외국인** 순매도\n- 금리 우려"},
        )
    )

    text = _first_text(response)
    assert "**" not in text
    assert "##" not in text
    assert "· 외국인 순매도" in text


def test_a_long_answer_is_cut_to_fit_the_bubble():
    response = render_delivery(
        delivery("ask_result", {"question": QUESTION, "answer": "가" * 5_000})
    )

    assert len(_first_text(response)) <= 1_000


def test_an_empty_answer_falls_back_to_the_failure_card():
    # A blank answer used to render as a header with nothing under it, which
    # reads as a broken message rather than a failure.
    response = render_delivery(
        delivery("ask_result", {"question": QUESTION, "answer": "   "})
    )

    assert "답하지 못했습니다" in _first_text(response)


def test_ask_failure_names_the_question():
    response = render_delivery(
        delivery("ask_failed", {"question": QUESTION, "error_code": "ask_error"})
    )

    text = _first_text(response)
    assert QUESTION in text
    assert "ask_error" not in text  # an error code means nothing to the room


def test_a_missing_question_still_renders():
    # The question is echoed for the room's benefit, not required for correctness.
    response = render_delivery(delivery("ask_result", {"answer": "답변입니다."}))

    assert "답변입니다." in _first_text(response)


def test_ask_cards_offer_no_ticker_follow_ups():
    # There is no stock to run 평가 or 리포트 on without inventing one.
    response = render_delivery(
        delivery("ask_result", {"question": QUESTION, "answer": "답변입니다."})
    )

    card = response["template"]["outputs"][1]["listCard"]
    assert [item["title"] for item in card["items"]] == ["도움말"]
