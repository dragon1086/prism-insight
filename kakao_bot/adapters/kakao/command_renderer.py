"""Render a command outcome as the immediate reply to a user's message.

This is the acknowledgement, not the result — the report itself arrives later
through the outbox, because the callback token expires in five minutes.

Help is rendered as a card rather than only text: a tap sends the item's
title, so an example command becomes a one-tap way to start without having to
learn the grammar or type a mention.
"""

from __future__ import annotations

from kakao_bot.adapters.kakao.skill_response import (
    list_card_output,
    simple_text,
    simple_text_output,
    skill_response,
)
from kakao_bot.application.command_parser import CommandKind
from kakao_bot.application.command_service import (
    IMPLEMENTED_COMMANDS,
    CommandOutcome,
    CommandOutcomeKind,
)

_EXAMPLE_TICKER = "삼성전자"


def render_command_outcome(outcome: CommandOutcome) -> dict[str, object]:
    """Build the reply bubble(s) for one handled command."""

    if outcome.kind is CommandOutcomeKind.IGNORED:
        raise ValueError("ignored outcomes must not be rendered")

    if outcome.kind is CommandOutcomeKind.HELP:
        return skill_response(
            [
                simple_text_output(outcome.message),
                help_card(),
            ]
        )

    return simple_text(outcome.message)


def help_card() -> dict[str, object]:
    """Item titles are commands, because tapping one sends its title.

    Only advertises what is wired up: a first impression of "아직 준비 중인
    기능입니다" is worse than a shorter list.
    """

    # Tapping sends the title verbatim, so each title is written the way we
    # want people to type: a bare stock name, or a plain question. Titles that
    # read `리포트 삼성전자` would teach a command grammar that is no longer
    # necessary — and that users would then keep using.
    items: list[dict[str, object]] = []
    examples = (
        (
            CommandKind.ASK,
            "오늘 시장 어때?",
            "① 시장 흐름을 함께 읽어요",
        ),
        (
            CommandKind.ASK,
            "SK하이닉스 최근 전망",
            "② 최신 뉴스와 근거를 찾아봐요",
        ),
        (
            CommandKind.REPORT,
            _EXAMPLE_TICKER,
            "③ 종목을 깊게 분석해요",
        ),
        (
            CommandKind.EVALUATE,
            "평가 삼성전자 70000 6",
            "④ 내 평단가와 보유기간으로 점검해요",
        ),
        (
            CommandKind.REPORT,
            "AAPL",
            "⑤ 미국 종목은 티커로 물어봐요",
        ),
    )
    for kind, title, description in examples:
        if kind not in IMPLEMENTED_COMMANDS:
            continue
        items.append(
            {
                "title": title,
                "description": description,
                "action": "message",
                "messageText": title,
            }
        )

    return list_card_output(
        header_title="PRISM과 투자 여정 시작하기",
        items=items,
        # Fills the bot mention into the input box, so asking about a stock we
        # did not list costs a tap plus a name instead of remembering syntax.
        buttons=[{"action": "mention", "label": "🔍 종목 검색"}],
    )
