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
                _help_card(),
            ]
        )

    return simple_text(outcome.message)


def _help_card() -> dict[str, object]:
    """Item titles are commands, because tapping one sends its title.

    Only advertises what is wired up: a first impression of "아직 준비 중인
    기능입니다" is worse than a shorter list.
    """

    items: list[dict[str, object]] = [
        {
            "title": f"리포트 {_EXAMPLE_TICKER}",
            "description": "종목 분석 리포트를 생성합니다",
            "action": "message",
            "messageText": f"리포트 {_EXAMPLE_TICKER}",
        },
        {
            "title": "리포트 AAPL",
            "description": "미국 종목은 티커로 입력합니다",
            "action": "message",
            "messageText": "리포트 AAPL",
        },
    ]
    if CommandKind.LEADERBOARD in IMPLEMENTED_COMMANDS:
        items.append(
            {
                "title": "순위",
                "description": "예측 리더보드를 봅니다",
                "action": "message",
                "messageText": "순위",
            }
        )

    return list_card_output(
        header_title="바로 해보기",
        items=items,
        # Fills the bot mention into the input box, so asking about a stock we
        # did not list costs a tap plus a name instead of remembering syntax.
        buttons=[{"action": "mention", "label": "🔍 종목 검색"}],
    )
