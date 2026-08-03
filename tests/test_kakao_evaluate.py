"""The `평가` command: judging a holding against its entry price.

Deliberately not routed through ask. Ask reads the web; this has to state a
return against a price the user gave us, which needs the real series and flow
data. A number invented from search results, about someone's own money, is the
most expensive mistake this bot can make — so the split is pinned here.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from kakao_bot.adapters.kakao.delivery_renderer import render_delivery
from kakao_bot.adapters.persistence.sqlite import SQLiteKakaoRepository
from kakao_bot.application.analysis_service import AnalysisService
from kakao_bot.application.command_parser import CommandKind, parse_command
from kakao_bot.application.command_service import (
    IMPLEMENTED_COMMANDS,
    CommandOutcomeKind,
    CommandService,
)
from kakao_bot.domain.models import (
    ApprovalStatus,
    ClaimedOutboundDelivery,
    InboundMessage,
)
from kakao_bot.ports.analysis import AnalysisOutcome, ResolvedTicker, TickerResolution

NOW = datetime(2026, 8, 4, 5, 0, tzinfo=timezone.utc)
ROOM = "room-1"


class FakeResolver:
    def resolve(self, query: str, *, market: str | None) -> TickerResolution:
        if market == "us":
            return TickerResolution(
                ticker=ResolvedTicker(ticker="AAPL", company_name="AAPL", market="us")
            )
        return TickerResolution(
            ticker=ResolvedTicker(
                ticker="005930", company_name="삼성전자", market="kr"
            )
        )


class SpyAnalysis:
    """Records which port method the worker reached for."""

    def __init__(self, outcome: AnalysisOutcome | None = None) -> None:
        self.outcome = outcome or AnalysisOutcome(
            succeeded=True, summary="지금 12% 수익 중이야. 절반은 익절해도 좋아 보여."
        )
        self.evaluate_calls: list[dict] = []
        self.generate_calls: list[tuple] = []
        self.answer_calls: list[str] = []

    def generate(self, ticker, company_name, *, market):
        self.generate_calls.append((ticker, company_name, market))
        return self.outcome

    def answer(self, question):
        self.answer_calls.append(question)
        return self.outcome

    def evaluate(
        self,
        ticker,
        company_name,
        *,
        market,
        avg_price,
        period_months,
        tone,
        background,
    ):
        self.evaluate_calls.append(
            {
                "ticker": ticker,
                "company_name": company_name,
                "market": market,
                "avg_price": avg_price,
                "period_months": period_months,
                "tone": tone,
                "background": background,
            }
        )
        return self.outcome


def message(text: str, *, user_id: str = "user-1") -> InboundMessage:
    return InboundMessage(
        event_id=f"evt-{text}-{user_id}",
        sequence=1,
        room_id=ROOM,
        user_id=user_id,
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


@pytest.fixture
def service(repository):
    return CommandService(repository, FakeResolver())


class TestParsing:
    def test_stock_price_and_period(self):
        command = parse_command("평가 삼성전자 70000 6")

        assert command.kind is CommandKind.EVALUATE
        assert (command.query, command.avg_price, command.period_months) == (
            "삼성전자",
            70000.0,
            6,
        )

    def test_the_period_may_be_left_out(self):
        command = parse_command("평가 삼성전자 70000")

        assert command.avg_price == 70000.0
        assert command.period_months is None

    def test_a_six_digit_code_is_the_stock_not_the_price(self):
        command = parse_command("평가 005930 70000 6")

        assert command.query == "005930"
        assert command.avg_price == 70000.0

    def test_trailing_words_are_a_requested_tone(self):
        # Reading numbers from the end would see "친구처럼", find no number,
        # and silently drop the price the user did supply.
        command = parse_command("평가 삼성전자 70000 6 취한 친구처럼")

        assert command.avg_price == 70000.0
        assert command.period_months == 6
        assert command.tone == "취한 친구처럼"

    def test_us_keywords_carry_the_market(self):
        command = parse_command("us평가 TSLA 300 12 냉정하게")

        assert command.market == "us"
        assert (command.avg_price, command.period_months) == (300.0, 12)
        assert command.tone == "냉정하게"


class TestEnqueue:
    def test_evaluate_is_actually_enabled(self):
        assert CommandKind.EVALUATE in IMPLEMENTED_COMMANDS

    def test_an_evaluate_becomes_an_evaluate_job(self, repository, service):
        outcome = service.handle(message("평가 삼성전자 70000 6"), now=NOW)

        assert outcome.kind is CommandOutcomeKind.ACCEPTED
        [job] = repository.list_analysis_jobs()
        assert job["kind"] == "evaluate"
        assert job["ticker"] == "005930"
        assert job["payload"]["avg_price"] == 70000.0
        assert job["payload"]["period_months"] == 6

    def test_the_tone_travels_with_the_job(self, repository, service):
        service.handle(message("평가 삼성전자 70000 6 취한 친구처럼"), now=NOW)

        [job] = repository.list_analysis_jobs()
        assert job["payload"]["tone"] == "취한 친구처럼"

    def test_a_missing_price_asks_for_it_by_example(self, repository, service):
        # The stock was already named, so the refusal shows the exact line to
        # send rather than restating the grammar in the abstract.
        outcome = service.handle(message("평가 삼성전자"), now=NOW)

        assert outcome.kind is CommandOutcomeKind.REJECTED
        assert "삼성전자" in outcome.message
        assert "70000" in outcome.message
        assert repository.list_analysis_jobs() == ()

    def test_evaluate_counts_against_the_same_quota(self, repository, service):
        for i in range(6):
            service.handle(message(f"평가 삼성전자 7000{i} 6", user_id="same"), now=NOW)

        assert len(repository.list_analysis_jobs()) == 5

    def test_an_unapproved_room_is_refused(self, tmp_path):
        with SQLiteKakaoRepository(tmp_path / "k.sqlite") as repo:
            repo.discover_room("other", discovered_at=NOW)
            outcome = CommandService(repo, FakeResolver()).handle(
                InboundMessage(
                    event_id="e",
                    sequence=1,
                    room_id="other",
                    user_id="u",
                    nickname=None,
                    text="평가 삼성전자 70000 6",
                    callback_token="cb",
                    occurred_at=NOW,
                ),
                now=NOW,
            )

        assert outcome.kind is CommandOutcomeKind.REJECTED
        assert "승인" in outcome.message


class TestWorker:
    def enqueue(self, repository, service, text="평가 삼성전자 70000 6"):
        service.handle(message(text), now=NOW)

    def test_the_worker_uses_the_evaluation_agent_not_search(
        self, repository, service
    ):
        # The whole point of keeping this off the ask path.
        self.enqueue(repository, service)
        analysis = SpyAnalysis()

        AnalysisService(repository, analysis).run_once(now=NOW, limit=1)

        assert len(analysis.evaluate_calls) == 1
        assert analysis.answer_calls == []
        assert analysis.generate_calls == []

    def test_the_position_reaches_the_agent(self, repository, service):
        self.enqueue(repository, service)
        analysis = SpyAnalysis()

        AnalysisService(repository, analysis).run_once(now=NOW, limit=1)

        [call] = analysis.evaluate_calls
        assert call["avg_price"] == 70000.0
        assert call["period_months"] == 6
        assert call["ticker"] == "005930"
        assert call["market"] == "kr"

    def test_an_empty_tone_is_left_for_the_adapter_to_default(
        self, repository, service
    ):
        # The tone only means something to the prompt, so the default belongs
        # with the prompt rather than in the application layer.
        self.enqueue(repository, service)
        analysis = SpyAnalysis()

        AnalysisService(repository, analysis).run_once(now=NOW, limit=1)

        assert analysis.evaluate_calls[0]["tone"] == ""

    def test_a_completed_evaluation_enqueues_an_evaluate_result(
        self, repository, service
    ):
        self.enqueue(repository, service)

        AnalysisService(repository, SpyAnalysis()).run_once(now=NOW, limit=1)

        [out] = repository.list_outbox()
        assert out["message_type"] == "evaluate_result"
        assert out["payload"]["avg_price"] == 70000.0

    def test_a_failed_evaluation_enqueues_an_evaluate_failure(
        self, repository, service
    ):
        self.enqueue(repository, service)
        analysis = SpyAnalysis(
            AnalysisOutcome(succeeded=False, error_code="evaluate_error")
        )

        AnalysisService(repository, analysis).run_once(now=NOW, limit=1)

        [out] = repository.list_outbox()
        assert out["message_type"] == "evaluate_failed"
        assert out["payload"]["company_name"] == "삼성전자"

    def test_report_and_ask_jobs_are_untouched(self, repository, service):
        service.handle(message("삼성전자"), now=NOW)
        analysis = SpyAnalysis()

        AnalysisService(repository, analysis).run_once(now=NOW, limit=1)

        assert analysis.evaluate_calls == []
        assert len(analysis.generate_calls) == 1


class TestRendering:
    def payload(self, **overrides) -> dict:
        base = {
            "job_id": "j-1",
            "ticker": "005930",
            "company_name": "삼성전자",
            "avg_price": 70000.0,
            "period_months": 6,
            "verdict": "지금 12% 수익 중이야. 절반은 익절해도 좋아 보여.",
            "market": "kr",
        }
        return {**base, **overrides}

    def text_of(self, response) -> str:
        return response["template"]["outputs"][0]["simpleText"]["text"]

    def test_the_card_repeats_the_position_back(self):
        # The answer lands minutes later in a busy room; without the entry
        # price nobody can tell which position it is about.
        response = render_delivery(delivery("evaluate_result", self.payload()))

        text = self.text_of(response)
        assert "삼성전자" in text
        assert "70,000" in text
        assert "6개월" in text

    def test_the_verdict_is_in_the_bubble(self):
        response = render_delivery(delivery("evaluate_result", self.payload()))

        assert "익절" in self.text_of(response)

    def test_markdown_is_stripped(self):
        response = render_delivery(
            delivery("evaluate_result", self.payload(verdict="## 요약\n- **12%** 수익"))
        )

        text = self.text_of(response)
        assert "**" not in text and "##" not in text

    def test_a_blank_verdict_renders_the_failure_card(self):
        response = render_delivery(
            delivery("evaluate_result", self.payload(verdict="   "))
        )

        assert "실패" in self.text_of(response)

    def test_a_missing_period_still_renders(self):
        response = render_delivery(
            delivery("evaluate_result", self.payload(period_months=None))
        )

        text = self.text_of(response)
        assert "70,000" in text
        assert "개월" not in text

    def test_the_failure_card_names_the_stock_not_the_error_code(self):
        response = render_delivery(
            delivery(
                "evaluate_failed",
                {
                    "job_id": "j-1",
                    "ticker": "005930",
                    "company_name": "삼성전자",
                    "error_code": "evaluate_timeout",
                },
            )
        )

        text = self.text_of(response)
        assert "삼성전자" in text
        assert "evaluate_timeout" not in text

    def test_the_bubble_fits_kakao(self):
        response = render_delivery(
            delivery("evaluate_result", self.payload(verdict="가" * 5000))
        )

        assert len(self.text_of(response)) <= 1000
