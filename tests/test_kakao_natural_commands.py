"""Talking to the bot without knowing its grammar.

Kakao only delivers a group message to the bot when the bot was mentioned, so
everything that arrives was aimed at it on purpose. Requiring a keyword on top
of that mention makes the user declare their intent twice, and the old parser
answered *nothing* whenever the second declaration was missing or misspelled —
`삼성전자`, `005930`, `오늘 코스피 어때?` and a bare mention were all silence.

These tests pin the routing that replaced it: a bare stock reference is a
report, anything else is a question, and nothing addressed to the bot goes
unanswered.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from kakao_bot.adapters.persistence.sqlite import SQLiteKakaoRepository
from kakao_bot.application.command_parser import CommandKind, parse_command
from kakao_bot.application.command_service import (
    CommandOutcomeKind,
    CommandService,
)
from kakao_bot.domain.models import ApprovalStatus, InboundMessage
from kakao_bot.ports.analysis import ResolvedTicker, TickerResolution

NOW = datetime(2026, 8, 3, 5, 0, tzinfo=timezone.utc)
ROOM = "room-1"

_KNOWN = {
    "삼성전자": ResolvedTicker(ticker="005930", company_name="삼성전자", market="kr"),
    "005930": ResolvedTicker(ticker="005930", company_name="삼성전자", market="kr"),
    "AAPL": ResolvedTicker(ticker="AAPL", company_name="AAPL", market="us"),
}


class MapResolver:
    """Resolves the handful of names a test needs; records what it was asked."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def resolve(self, query: str, *, market: str | None) -> TickerResolution:
        self.calls.append(query)
        found = _KNOWN.get(query.strip())
        if found is None:
            return TickerResolution(error_message="종목을 찾을 수 없습니다.")
        return TickerResolution(ticker=found)


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


@pytest.fixture
def repository(tmp_path):
    with SQLiteKakaoRepository(tmp_path / "kakao.sqlite") as repo:
        repo.discover_room(ROOM, discovered_at=NOW)
        repo.set_room_approval(ROOM, ApprovalStatus.APPROVED)
        yield repo


@pytest.fixture
def resolver():
    return MapResolver()


@pytest.fixture
def service(repository, resolver):
    return CommandService(repository, resolver)


def kinds_of(repository) -> list[str]:
    return [row["kind"] for row in repository.list_analysis_jobs()]


class TestParsing:
    @pytest.mark.parametrize(
        "utterance",
        ["삼성전자", "005930", "AAPL"],
    )
    def test_a_bare_stock_reference_is_offered_to_the_resolver(self, utterance):
        command = parse_command(utterance)

        assert command.kind is CommandKind.NATURAL
        assert command.resembles_ticker is True
        assert command.query == utterance

    @pytest.mark.parametrize(
        "utterance",
        ["오늘 코스피 어때?", "삼성전자 어때?", "금리 내리면 뭐 사?", "안녕."],
    )
    def test_a_sentence_is_never_taken_for_a_stock_name(self, utterance):
        # The resolver matches substrings, so handing it a sentence risks a
        # word inside it quietly resolving to some unrelated stock.
        command = parse_command(utterance)

        assert command.kind is CommandKind.NATURAL
        assert command.resembles_ticker is False

    def test_a_bare_mention_asks_for_help_instead_of_saying_nothing(self):
        # Kakao strips the mention, so reaching for the bot with nothing else
        # arrives here as an empty string.
        assert parse_command("").kind is CommandKind.HELP
        assert parse_command("@프리즘 ").kind is CommandKind.HELP

    def test_an_explicit_keyword_still_wins(self):
        # Card taps send the item title, and those titles carry keywords.
        assert parse_command("리포트 삼성전자").kind is CommandKind.REPORT
        assert parse_command("질문 오늘 어때?").kind is CommandKind.ASK
        assert parse_command("도움말").kind is CommandKind.HELP


class TestRouting:
    def test_a_stock_name_alone_starts_a_report(self, repository, service):
        outcome = service.handle(message("삼성전자"), now=NOW)

        assert outcome.kind is CommandOutcomeKind.ACCEPTED
        assert outcome.company_name == "삼성전자"
        assert kinds_of(repository) == ["report"]

    def test_a_six_digit_code_alone_starts_a_report(self, repository, service):
        service.handle(message("005930"), now=NOW)

        assert kinds_of(repository) == ["report"]

    def test_a_us_ticker_alone_starts_a_report(self, repository, service):
        service.handle(message("AAPL"), now=NOW)

        assert kinds_of(repository) == ["report"]

    def test_a_question_becomes_a_question(self, repository, service):
        outcome = service.handle(message("오늘 코스피 어때?"), now=NOW)

        assert outcome.kind is CommandOutcomeKind.ACCEPTED
        assert kinds_of(repository) == ["ask"]

    def test_a_stock_inside_a_sentence_becomes_a_question(self, repository, service):
        # A report takes minutes; an answer takes seconds and can still talk
        # about the stock. Guessing this way costs less when it is wrong.
        service.handle(message("삼성전자 어때?"), now=NOW)

        assert kinds_of(repository) == ["ask"]

    def test_a_sentence_is_never_sent_to_the_resolver(self, service, resolver):
        service.handle(message("삼성전자 어때?"), now=NOW)

        assert resolver.calls == []

    def test_an_unknown_word_falls_through_to_a_question(
        self, repository, service
    ):
        # Not a refusal: a question can answer almost anything, so an
        # unrecognised word is still worth trying.
        outcome = service.handle(message("안녕"), now=NOW)

        assert outcome.kind is CommandOutcomeKind.ACCEPTED
        assert kinds_of(repository) == ["ask"]

    def test_the_whole_question_is_kept_not_just_the_leftovers(
        self, repository, service
    ):
        service.handle(message("오늘 코스피 왜 빠졌어?"), now=NOW)

        [job] = repository.list_analysis_jobs()
        assert job["payload"]["question"] == "오늘 코스피 왜 빠졌어?"

    def test_a_bare_mention_answers_with_help(self, service):
        outcome = service.handle(message(""), now=NOW)

        assert outcome.kind is CommandOutcomeKind.HELP
        assert "리포트" in outcome.message

    def test_nothing_addressed_to_the_bot_goes_unanswered(self, service):
        # The old behaviour for all of these was IGNORED — total silence.
        for text in ["삼성전자", "오늘 코스피 어때?", "안녕", ""]:
            outcome = service.handle(message(text, user_id=f"u-{text}"), now=NOW)
            assert outcome.should_reply, text

    def test_an_unapproved_room_is_still_refused(self, tmp_path, resolver):
        # Natural phrasing must not become a way around the approval gate.
        with SQLiteKakaoRepository(tmp_path / "k.sqlite") as repo:
            repo.discover_room("other", discovered_at=NOW)
            outcome = CommandService(repo, resolver).handle(
                InboundMessage(
                    event_id="e",
                    sequence=1,
                    room_id="other",
                    user_id="u",
                    nickname=None,
                    text="삼성전자",
                    callback_token="cb",
                    occurred_at=NOW,
                ),
                now=NOW,
            )

        assert outcome.kind is CommandOutcomeKind.REJECTED
        assert "승인" in outcome.message

    def test_natural_input_counts_against_the_same_quota(self, repository, service):
        for i in range(6):
            service.handle(message(f"오늘 뭐 사면 좋을까 {i}?", user_id="same"), now=NOW)

        # Five per user per day; the sixth is refused rather than enqueued.
        assert len(repository.list_analysis_jobs()) == 5
