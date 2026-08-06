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

_UNAPPROVED = "이 채팅방은 아직 승인되지 않았습니다. 관리자에게 승인을 요청해주세요."
_USER_LIMIT = (
    "사용자별 일일 요청 한도 {limit}회를 모두 사용했습니다.\n"
    "리포트·평가·질문 합산, 최근 24시간 기준입니다."
)
_ROOM_LIMIT = (
    "이 채팅방의 일일 요청 한도 {limit}회를 모두 사용했습니다.\n"
    "모든 사용자의 리포트·평가·질문 합산, 최근 24시간 기준입니다."
)
_NEED_TICKER = "종목을 함께 알려주세요. 예: 리포트 삼성전자"
_NEED_QUESTION = "궁금한 내용을 함께 적어주세요. 예: 질문 오늘 코스피 왜 빠졌어?"
_NEED_AVG_PRICE = (
    "평단가를 함께 알려주세요.\n예: 평가 {stock} 70000 6\n"
    "(종목 · 평단가 · 보유개월 순서, 보유개월은 생략해도 됩니다)"
)
_NOT_READY = "아직 준비 중인 기능입니다."

# Telegram's /ask truncates at the same length; a question longer than this is
# a pasted article, not a question.
_QUESTION_LIMIT = 500

# Single source of truth for what actually works. Help text and every card that
# offers a follow-up read this, because they drifted apart once: the card said
# "이어서 해보기" and both of its items answered "아직 준비 중인 기능입니다".
# Add a kind here only when it is wired end to end.
IMPLEMENTED_COMMANDS = frozenset(
    {
        CommandKind.REPORT,
        CommandKind.ASK,
        CommandKind.EVALUATE,
        CommandKind.HELP,
    }
)

_HELP_LINES = {
    CommandKind.REPORT: (
        " · 삼성전자 — 종목 이름만 보내면 분석 리포트\n"
        " · AAPL — 미국 종목은 티커로"
    ),
    CommandKind.EVALUATE: (
        " · 평가 삼성전자 70000 6 — 내 평단가 기준으로 평가\n"
        "   70000은 평단가, 뒤의 6은 보유기간(월)"
    ),
    CommandKind.ASK: " · 오늘 시장 어때? — 그냥 물어보면 답변",
}

_HELP_FOOTER = (
    "\n\n📌 일일 요청 한도(최근 24시간)"
    f"\n · 리포트·평가·질문 합산 사용자당 {DEFAULT_USER_DAILY_LIMIT}회"
    "\n\n저를 멘션해서 편하게 말 걸어주세요. 명령어를 외울 필요 없어요."
)


def leads_somewhere(utterance: str) -> bool:
    """Would this text get a real answer if someone tapped it?

    Card item titles are sent verbatim when tapped, so a title that parses into
    a command we have not built is a dead end — the card promises something and
    the bot replies "아직 준비 중인 기능입니다". Cards check their own titles
    against this.

    Keyword-free text always leads somewhere as long as either report or ask
    exists, because that is what NATURAL resolves into.
    """

    kind = parse_command(utterance).kind
    if kind is CommandKind.NATURAL:
        return bool(
            IMPLEMENTED_COMMANDS & {CommandKind.REPORT, CommandKind.ASK}
        )
    return kind in IMPLEMENTED_COMMANDS


def help_text() -> str:
    """Describe only the commands that are actually wired up.

    Phrased as things to say rather than a command grammar, because there is no
    grammar to learn any more: a stock name is a report, anything else is a
    question. Teaching `리포트 …` here would be teaching a longer way to do the
    same thing.
    """

    lines = [
        text
        for kind, text in _HELP_LINES.items()
        if kind in IMPLEMENTED_COMMANDS
    ]
    return "📊 PRISM 사용법\n" + "\n".join(lines) + _HELP_FOOTER


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
    room_daily: int | None = None


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
            # Reserved for input that is not text at all. Anything a person
            # typed now parses as NATURAL, because a mention is already an
            # unambiguous request for the bot's attention.
            return CommandOutcome(kind=CommandOutcomeKind.IGNORED)

        if command.kind is CommandKind.HELP:
            return CommandOutcome(kind=CommandOutcomeKind.HELP, message=help_text())

        # A keyword-free utterance becomes a report or a question depending on
        # whether it names a stock, and only the resolver knows that. Resolving
        # it here — before the approval gate — would spend a lookup on a room
        # that cannot use the answer, so the decision waits until after.
        if (
            command.kind is not CommandKind.NATURAL
            and command.kind not in IMPLEMENTED_COMMANDS
        ):
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
                message=(
                    _NEED_QUESTION
                    if command.kind is CommandKind.ASK
                    else _NEED_TICKER
                ),
            )

        refusal = self._check_limits(message, moment)
        if refusal is not None:
            return refusal

        kind = command.kind
        resolution = None
        if kind is CommandKind.NATURAL:
            # "삼성전자" is a request for a report; "삼성전자 어때?" is a
            # question. Only a bare stock reference is offered to the resolver,
            # and anything it cannot place falls through to a question rather
            # than to a refusal — a question can answer almost anything, so
            # guessing wrong costs a slower answer, not a dead end.
            if command.resembles_ticker:
                resolution = self._resolver.resolve(
                    command.query, market=command.market
                )
            kind = (
                CommandKind.REPORT
                if resolution is not None and resolution.succeeded
                else CommandKind.ASK
            )
            if kind not in IMPLEMENTED_COMMANDS:
                return CommandOutcome(
                    kind=CommandOutcomeKind.REJECTED,
                    message=_NOT_READY,
                )

        if kind is CommandKind.ASK:
            return self._enqueue_ask(message, command.query, moment)

        if kind is CommandKind.EVALUATE and command.avg_price is None:
            # Not a refusal with nothing after it: the user already named the
            # stock, so tell them the one thing still missing and show it in
            # their own words.
            return CommandOutcome(
                kind=CommandOutcomeKind.REJECTED,
                message=_NEED_AVG_PRICE.format(stock=command.query),
            )

        if resolution is None:
            resolution = self._resolver.resolve(
                command.query, market=command.market
            )
        if not resolution.succeeded:
            return CommandOutcome(
                kind=CommandOutcomeKind.REJECTED,
                message=resolution.error_message or "종목을 찾을 수 없습니다.",
            )

        resolved = resolution.ticker
        is_evaluate = kind is CommandKind.EVALUATE
        job = AnalysisJob(
            job_id=str(uuid.uuid4()),
            room_id=message.room_id,
            user_id=message.user_id,
            ticker=resolved.ticker,
            company_name=resolved.company_name,
            market=resolved.market,
            kind="evaluate" if is_evaluate else "report",
            payload=(
                {
                    "avg_price": command.avg_price,
                    "period_months": command.period_months,
                    # Whatever was left after the numbers were taken off the
                    # end is the requested tone: "평가 삼성전자 70000 6 취한
                    # 친구처럼". Empty means the default.
                    "tone": command.tone or "",
                }
                if is_evaluate
                else None
            ),
        )
        self._repository.enqueue_analysis_job(job, now=moment)

        if is_evaluate:
            ack = (
                f"🧮 {resolved.company_name} ({resolved.ticker}) 평가를 시작했습니다.\n"
                "보통 1~2분 정도 걸립니다.\n"
                "중간 표시가 없어도 정상 처리 중이며, 완료되면 이 방으로 보내드릴게요."
            )
        else:
            ack = (
                f"📊 {resolved.company_name} ({resolved.ticker}) 분석을 시작했습니다.\n"
                "보통 2~5분 정도 걸립니다.\n"
                "중간 표시가 없어도 정상 처리 중이며, 완료되면 이 방으로 보내드릴게요."
            )

        return CommandOutcome(
            kind=CommandOutcomeKind.ACCEPTED,
            message=ack,
            job_id=job.job_id,
            ticker=resolved.ticker,
            company_name=resolved.company_name,
        )

    def _enqueue_ask(
        self,
        message: InboundMessage,
        question: str,
        moment: datetime,
    ) -> CommandOutcome:
        """Queue a free-form question — no ticker to resolve.

        `ticker`/`company_name` are NOT NULL on the jobs table because every
        job used to be a report. An ask has neither, so they are stored empty
        and `kind` is what tells the worker which shape it is holding.
        """

        job = AnalysisJob(
            job_id=str(uuid.uuid4()),
            room_id=message.room_id,
            user_id=message.user_id,
            ticker="",
            company_name="",
            # Retrieval for ask is market-agnostic (Telegram's /ask uses the KR
            # preset with the allowlist off); 'kr' just satisfies the CHECK.
            market="kr",
            kind="ask",
            payload={"question": question[:_QUESTION_LIMIT]},
        )
        self._repository.enqueue_analysis_job(job, now=moment)

        return CommandOutcome(
            kind=CommandOutcomeKind.ACCEPTED,
            message=(
                "🔍 질문을 확인했습니다. 최신 정보를 찾아볼게요.\n"
                "보통 1분 안팎으로 걸립니다.\n"
                "중간 표시가 없어도 정상 처리 중이며, 완료되면 이 방으로 보내드릴게요."
            ),
            job_id=job.job_id,
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
                message=_USER_LIMIT.format(limit=self._limits.user_daily),
            )

        if self._limits.room_daily is None:
            return None

        used_by_room = self._repository.count_analysis_jobs_since(
            room_id=message.room_id,
            user_id=None,
            since=since,
        )
        if used_by_room >= self._limits.room_daily:
            return CommandOutcome(
                kind=CommandOutcomeKind.REJECTED,
                message=_ROOM_LIMIT.format(limit=self._limits.room_daily),
            )
        return None
