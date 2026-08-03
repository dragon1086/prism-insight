"""Inbound command handling: approval gate, quotas, resolution, enqueue.

Nothing here may take longer than the five-minute callback token, so the only
work on this path is validation plus enqueue.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from kakao_bot.application.command_service import (
    CommandLimits,
    CommandOutcomeKind,
    CommandService,
)
from kakao_bot.adapters.persistence.sqlite import SQLiteKakaoRepository
from kakao_bot.domain.models import ApprovalStatus, InboundMessage
from kakao_bot.ports.analysis import ResolvedTicker, TickerResolution

NOW = datetime(2026, 7, 28, 5, 0, tzinfo=timezone.utc)
ROOM = "room-1"
USER = "user-1"


class FakeResolver:
    def __init__(self, resolution: TickerResolution | None = None) -> None:
        self.resolution = resolution or TickerResolution(
            ticker=ResolvedTicker(
                ticker="005930", company_name="삼성전자", market="kr"
            )
        )
        self.calls: list[tuple[str, str | None]] = []

    def resolve(self, query: str, *, market: str | None) -> TickerResolution:
        self.calls.append((query, market))
        return self.resolution


def message(text: str, *, room_id: str = ROOM, user_id: str = USER) -> InboundMessage:
    return InboundMessage(
        event_id=f"evt-{text}-{user_id}",
        sequence=1,
        room_id=room_id,
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
def service(repository):
    return CommandService(repository, FakeResolver())


def test_report_command_enqueues_a_job_and_acknowledges(repository, service):
    outcome = service.handle(message("리포트 삼성전자"), now=NOW)

    assert outcome.kind is CommandOutcomeKind.ACCEPTED
    assert outcome.ticker == "005930"
    assert outcome.company_name == "삼성전자"
    assert "삼성전자" in outcome.message

    [job] = repository.list_analysis_jobs()
    assert job["room_id"] == ROOM
    assert job["user_id"] == USER
    assert job["ticker"] == "005930"
    assert job["status"] == "PENDING"


def test_tapped_card_title_is_the_same_command(repository, service):
    outcome = service.handle(message("삼성전자 리포트"), now=NOW)

    assert outcome.kind is CommandOutcomeKind.ACCEPTED
    assert len(repository.list_analysis_jobs()) == 1


def test_unapproved_room_is_refused_without_enqueueing(tmp_path):
    with SQLiteKakaoRepository(tmp_path / "kakao.sqlite") as repo:
        repo.discover_room(ROOM, discovered_at=NOW)  # PENDING
        service = CommandService(repo, FakeResolver())

        outcome = service.handle(message("리포트 삼성전자"), now=NOW)

        assert outcome.kind is CommandOutcomeKind.REJECTED
        assert "승인" in outcome.message
        assert repo.list_analysis_jobs() == ()


def test_undiscovered_room_is_refused_not_crashed(tmp_path):
    with SQLiteKakaoRepository(tmp_path / "kakao.sqlite") as repo:
        service = CommandService(repo, FakeResolver())

        outcome = service.handle(message("리포트 삼성전자", room_id="ghost"), now=NOW)

        assert outcome.kind is CommandOutcomeKind.REJECTED


def test_unresolvable_ticker_reports_the_resolver_message(repository):
    resolver = FakeResolver(TickerResolution(error_message="종목을 찾을 수 없습니다."))
    service = CommandService(repository, resolver)

    outcome = service.handle(message("리포트 없는종목"), now=NOW)

    assert outcome.kind is CommandOutcomeKind.REJECTED
    assert outcome.message == "종목을 찾을 수 없습니다."
    assert repository.list_analysis_jobs() == ()


def test_report_without_a_ticker_asks_for_one(repository, service):
    outcome = service.handle(message("리포트"), now=NOW)

    assert outcome.kind is CommandOutcomeKind.REJECTED
    assert "종목" in outcome.message
    assert repository.list_analysis_jobs() == ()


def test_market_hint_is_passed_through_to_the_resolver(repository):
    resolver = FakeResolver(
        TickerResolution(
            ticker=ResolvedTicker(ticker="AAPL", company_name="AAPL", market="us")
        )
    )
    service = CommandService(repository, resolver)

    service.handle(message("리포트 AAPL"), now=NOW)

    assert resolver.calls == [("AAPL", "us")]


def test_user_daily_limit_blocks_further_requests(repository):
    service = CommandService(
        repository,
        FakeResolver(),
        limits=CommandLimits(user_daily=2, room_daily=99),
    )

    assert service.handle(message("리포트 삼성전자"), now=NOW).kind is (
        CommandOutcomeKind.ACCEPTED
    )
    assert service.handle(message("리포트 삼성전자"), now=NOW).kind is (
        CommandOutcomeKind.ACCEPTED
    )
    blocked = service.handle(message("리포트 삼성전자"), now=NOW)

    assert blocked.kind is CommandOutcomeKind.REJECTED
    assert "한도" in blocked.message
    assert len(repository.list_analysis_jobs()) == 2


def test_user_limit_is_a_rolling_day(repository):
    service = CommandService(
        repository,
        FakeResolver(),
        limits=CommandLimits(user_daily=1, room_daily=99),
    )
    service.handle(message("리포트 삼성전자"), now=NOW)

    later = NOW + timedelta(days=1, minutes=1)
    assert service.handle(message("리포트 삼성전자"), now=later).kind is (
        CommandOutcomeKind.ACCEPTED
    )


def test_room_limit_counts_across_users(repository):
    service = CommandService(
        repository,
        FakeResolver(),
        limits=CommandLimits(user_daily=99, room_daily=2),
    )

    service.handle(message("리포트 삼성전자", user_id="a"), now=NOW)
    service.handle(message("리포트 삼성전자", user_id="b"), now=NOW)
    blocked = service.handle(message("리포트 삼성전자", user_id="c"), now=NOW)

    assert blocked.kind is CommandOutcomeKind.REJECTED
    assert "채팅방" in blocked.message


def test_help_is_answered_without_touching_the_room_gate(tmp_path):
    with SQLiteKakaoRepository(tmp_path / "kakao.sqlite") as repo:
        service = CommandService(repo, FakeResolver())

        outcome = service.handle(message("도움말", room_id="ghost"), now=NOW)

        assert outcome.kind is CommandOutcomeKind.HELP
        assert "리포트" in outcome.message


def test_text_without_a_keyword_is_answered_rather_than_ignored(
    repository, service
):
    # Group rooms only deliver messages the bot was mentioned in, so silence
    # here meant ignoring someone who had deliberately addressed the bot.
    outcome = service.handle(message("아무말"), now=NOW)

    assert outcome.should_reply is True
    assert outcome.kind is not CommandOutcomeKind.IGNORED


def test_not_yet_implemented_commands_say_so(repository, service):
    outcome = service.handle(message("평가 삼성전자 70000 6"), now=NOW)

    assert outcome.kind is CommandOutcomeKind.REJECTED
    assert "준비" in outcome.message
