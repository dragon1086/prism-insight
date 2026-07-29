"""Turn an inbound utterance into an accepted analysis job, or a refusal.

This layer knows nothing about Kakao's JSON. It returns a
:class:`CommandOutcome` describing *what* to say; rendering it into a card or
a text bubble is the adapter's job (design §3.2, §5.1).

The callback token that lets us reply to a message expires in five minutes,
which is far shorter than a report takes. So the only thing that happens on
the inbound path is validation plus enqueue — the answer is acknowledged
immediately and the finished report is delivered later through the outbox.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum

from kakao_bot.application.command_parser import CommandKind, parse_command
from kakao_bot.domain.errors import RoomNotFoundError
from kakao_bot.domain.models import AnalysisJob, ApprovalStatus, InboundMessage
from kakao_bot.ports.analysis import TickerResolverPort
from kakao_bot.ports.repositories import KakaoRepository

logger = logging.getLogger(__name__)

DEFAULT_USER_DAILY_LIMIT = 5
DEFAULT_ROOM_DAILY_LIMIT = 20

_UNAPPROVED = "이 채팅방은 아직 승인되지 않았습니다. 관리자에게 승인을 요청해주세요."
_USER_LIMIT = "오늘 요청 한도를 모두 사용했습니다. 내일 다시 이용해주세요."
_ROOM_LIMIT = "이 채팅방의 오늘 요청 한도를 모두 사용했습니다."
_NEED_TICKER = "종목을 함께 알려주세요. 예: 리포트 삼성전자"
_NOT_READY = "아직 준비 중인 기능입니다."

# Single source of truth for what actually works. Help text and every card that
# offers a follow-up read this, because they drifted apart once: the card said
# "이어서 해보기" and both of its items answered "아직 준비 중인 기능입니다".
# Add a kind here only when it is wired end to end.
IMPLEMENTED_COMMANDS = frozenset({CommandKind.REPORT, CommandKind.HELP})

_HELP_LINES = {
    CommandKind.REPORT: (
        " · 리포트 삼성전자 — 종목 분석 리포트\n"
        " · 리포트 AAPL — 미국 종목은 티커로"
    ),
    CommandKind.EVALUATE: " · 평가 삼성전자 70000 6 — 평단가·보유개월 기준 평가",
    CommandKind.LEADERBOARD: " · 순위 — 예측 리더보드",
}


def help_text() -> str:
    """Describe only the commands that are actually wired up."""

    lines = [
        text
        for kind, text in _HELP_LINES.items()
        if kind in IMPLEMENTED_COMMANDS
    ]
    return "📊 PRISM 사용법\n" + "\n".join(lines)


class CommandOutcomeKind(Enum):
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    HELP = "help"
    IGNORED = "ignored"


@dataclass(frozen=True)
class CommandOutcome:
    kind: CommandOutcomeKind
    message: str = ""
    job_id: str | None = None
    ticker: str | None = None
    company_name: str | None = None

    @property
    def should_reply(self) -> bool:
        return self.kind is not CommandOutcomeKind.IGNORED


@dataclass(frozen=True)
class CommandLimits:
    user_daily: int = DEFAULT_USER_DAILY_LIMIT
    room_daily: int = DEFAULT_ROOM_DAILY_LIMIT


class CommandService:
    def __init__(
        self,
        repository: KakaoRepository,
        resolver: TickerResolverPort,
        *,
        limits: CommandLimits | None = None,
    ) -> None:
        self._repository = repository
        self._resolver = resolver
        self._limits = limits or CommandLimits()

    def handle(
        self,
        message: InboundMessage,
        *,
        now: datetime | None = None,
    ) -> CommandOutcome:
        moment = now or datetime.now(timezone.utc)
        command = parse_command(message.text)

        if command.kind is CommandKind.UNKNOWN:
            # Group chatter reaches us only when the bot is addressed, so an
            # unrecognized utterance is a real miss, not noise. Still, saying
            # nothing is better than arguing with the room.
            return CommandOutcome(kind=CommandOutcomeKind.IGNORED)

        if command.kind is CommandKind.HELP:
            return CommandOutcome(kind=CommandOutcomeKind.HELP, message=help_text())

        if command.kind not in IMPLEMENTED_COMMANDS:
            return CommandOutcome(
                kind=CommandOutcomeKind.REJECTED,
                message=_NOT_READY,
            )

        try:
            room = self._repository.get_room(message.room_id)
        except RoomNotFoundError:
            # An undiscovered room cannot have been approved either, so the
            # two cases collapse into the same answer.
            room = None
        if room is None or room.approval_status is not ApprovalStatus.APPROVED:
            return CommandOutcome(
                kind=CommandOutcomeKind.REJECTED,
                message=_UNAPPROVED,
            )

        if not command.query:
            return CommandOutcome(
                kind=CommandOutcomeKind.REJECTED,
                message=_NEED_TICKER,
            )

        refusal = self._check_limits(message, moment)
        if refusal is not None:
            return refusal

        resolution = self._resolver.resolve(command.query, market=command.market)
        if not resolution.succeeded:
            return CommandOutcome(
                kind=CommandOutcomeKind.REJECTED,
                message=resolution.error_message or "종목을 찾을 수 없습니다.",
            )

        resolved = resolution.ticker
        job = AnalysisJob(
            job_id=str(uuid.uuid4()),
            room_id=message.room_id,
            user_id=message.user_id,
            ticker=resolved.ticker,
            company_name=resolved.company_name,
            market=resolved.market,
        )
        self._repository.enqueue_analysis_job(job, now=moment)

        return CommandOutcome(
            kind=CommandOutcomeKind.ACCEPTED,
            message=(
                f"📊 {resolved.company_name} ({resolved.ticker}) 분석을 시작했습니다.\n"
                "완료되면 이 방으로 결과를 보내드릴게요."
            ),
            job_id=job.job_id,
            ticker=resolved.ticker,
            company_name=resolved.company_name,
        )

    def _check_limits(
        self,
        message: InboundMessage,
        moment: datetime,
    ) -> CommandOutcome | None:
        since = moment - timedelta(days=1)

        used_by_user = self._repository.count_analysis_jobs_since(
            room_id=None,
            user_id=message.user_id,
            since=since,
        )
        if used_by_user >= self._limits.user_daily:
            return CommandOutcome(
                kind=CommandOutcomeKind.REJECTED,
                message=_USER_LIMIT,
            )

        used_by_room = self._repository.count_analysis_jobs_since(
            room_id=message.room_id,
            user_id=None,
            since=since,
        )
        if used_by_room >= self._limits.room_daily:
            return CommandOutcome(
                kind=CommandOutcomeKind.REJECTED,
                message=_ROOM_LIMIT,
            )
        return None
