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
    "👋 안녕하세요, 주식 애널리스트 PRISM입니다.\n"
    "시장 질문부터 종목 리포트, 내 보유 상황 평가까지 무엇이든 함께 살펴봐요.\n\n"
    "🤖 저는 가상 포트폴리오를 직접 운용하며 무엇을 보고 어떻게 판단했는지 "
    "투명하게 공유합니다. 투자 리딩이나 실제 매수 권유가 아닙니다.\n\n"
    "저를 부르려면 메시지에 저를 멘션해주세요.\n"
    "아래 순서대로 눌러보면 PRISM의 투자 여정을 바로 체험할 수 있어요."
)


def render_welcome_delivery(
    delivery: ClaimedOutboundDelivery,
) -> dict[str, object]:
    if delivery.message_type != ROOM_WELCOME:
        raise ValueError(f"unsupported Kakao delivery type: {delivery.message_type}")
    return _welcome(delivery.payload)


def _welcome(_payload: Mapping[str, object]) -> dict[str, object]:
    return skill_response([simple_text_output(_GREETING), help_card()])
