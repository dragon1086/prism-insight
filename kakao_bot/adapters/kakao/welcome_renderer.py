"""Introduce the bot to a room it has just been added to.

Kakao only delivers group messages the bot was mentioned in, so a bot that
waits to be spoken to is invisible: nobody in the room has been told it is
there, and nobody guesses the command grammar unprompted. Arriving with one
message that says what it does — and gives tappable examples — is the
difference between a room that uses the bot and a room that forgets it.

The same card the help command shows is reused on purpose. It already only
advertises commands that are wired up, so the greeting cannot promise a
feature that answers "아직 준비 중인 기능입니다".
"""

from __future__ import annotations

from collections.abc import Mapping

from kakao_bot.adapters.kakao.command_renderer import help_card
from kakao_bot.adapters.kakao.skill_response import (
    simple_text_output,
    skill_response,
)
from kakao_bot.domain.models import ClaimedOutboundDelivery

ROOM_WELCOME = "room_welcome"

_GREETING = (
    "👋 안녕하세요, PRISM입니다.\n"
    "종목 분석 리포트와 시장 질문에 답해드려요.\n\n"
    "저를 부르려면 메시지에 저를 멘션해주세요.\n"
    "아래 항목을 눌러 바로 시작할 수 있어요."
)


def render_welcome_delivery(
    delivery: ClaimedOutboundDelivery,
) -> dict[str, object]:
    if delivery.message_type != ROOM_WELCOME:
        raise ValueError(f"unsupported Kakao delivery type: {delivery.message_type}")
    return _welcome(delivery.payload)


def _welcome(_payload: Mapping[str, object]) -> dict[str, object]:
    return skill_response([simple_text_output(_GREETING), help_card()])
