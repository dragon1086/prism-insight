"""Render finished analysis deliveries as Kakao SkillResponse.

Two platform facts shape this (2026-07-27 findings):

quickReplies do not render, so every follow-up action has to be a ListCard
item or a card button. And a tapped item sends its *title*, so item titles are
written as the command they trigger — what the user reads is what we parse.

SimpleText holds 1000 characters and collapses past ~500 behind a "전체보기"
link, which suits a report summary: the gist is visible and the rest is one
tap away.
"""

from __future__ import annotations

import re
from collections.abc import Mapping

from kakao_bot.adapters.kakao.skill_response import (
    MAX_SIMPLE_TEXT_LENGTH,
    list_card_output,
    simple_text_output,
    skill_response,
)
from kakao_bot.domain.models import ClaimedOutboundDelivery

ANALYSIS_RESULT = "analysis_result"
ANALYSIS_FAILED = "analysis_failed"

_SUMMARY_BUDGET = MAX_SIMPLE_TEXT_LENGTH - 120  # leave room for the header
_MARKDOWN_NOISE = re.compile(r"^[#>\s*\-=_]+|[*_`]+", re.MULTILINE)
_BLANK_RUN = re.compile(r"\n{3,}")


def render_report_delivery(
    delivery: ClaimedOutboundDelivery,
) -> dict[str, object]:
    if delivery.message_type == ANALYSIS_RESULT:
        return _result(delivery.payload)
    if delivery.message_type == ANALYSIS_FAILED:
        return _failed(delivery.payload)
    raise ValueError(f"unsupported Kakao delivery type: {delivery.message_type}")


def _result(payload: Mapping[str, object]) -> dict[str, object]:
    ticker = _required_text(payload, "ticker")
    company_name = _required_text(payload, "company_name")
    summary = payload.get("summary")
    body = _condense(summary if isinstance(summary, str) else "")

    header = f"📊 {company_name} ({ticker}) 분석 완료"
    text = f"{header}\n\n{body}" if body else header

    return skill_response(
        [
            simple_text_output(text[:MAX_SIMPLE_TEXT_LENGTH]),
            _next_actions(ticker, company_name),
        ]
    )


def _failed(payload: Mapping[str, object]) -> dict[str, object]:
    ticker = _required_text(payload, "ticker")
    company_name = _required_text(payload, "company_name")
    return skill_response(
        [
            simple_text_output(
                f"⚠️ {company_name} ({ticker}) 분석에 실패했습니다.\n"
                "잠시 후 다시 시도해주세요."
            ),
            _next_actions(ticker, company_name),
        ]
    )


def _next_actions(ticker: str, company_name: str) -> dict[str, object]:
    """Item titles double as commands, because a tap sends the title."""

    return list_card_output(
        header_title="이어서 해보기",
        items=[
            {
                "title": f"{company_name} 평가",
                "description": "평단가와 보유 개월을 붙여 보내주세요",
                "action": "message",
                "messageText": f"평가 {company_name}",
            },
            {
                "title": "순위",
                "description": "예측 리더보드 보기",
                "action": "message",
                "messageText": "순위",
            },
        ],
    )


def _condense(summary: str) -> str:
    """Flatten report markdown into something a chat bubble can hold."""

    if not summary.strip():
        return ""
    text = _MARKDOWN_NOISE.sub("", summary)
    text = _BLANK_RUN.sub("\n\n", text).strip()
    if len(text) <= _SUMMARY_BUDGET:
        return text
    return text[: _SUMMARY_BUDGET - 1].rstrip() + "…"


def _required_text(payload: Mapping[str, object], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"analysis payload requires {key}")
    return value.strip()
