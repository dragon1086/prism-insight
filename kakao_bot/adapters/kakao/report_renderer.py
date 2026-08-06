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
from kakao_bot.application.command_parser import CommandKind
from kakao_bot.application.command_service import IMPLEMENTED_COMMANDS
from kakao_bot.domain.models import ClaimedOutboundDelivery

ANALYSIS_RESULT = "analysis_result"
ANALYSIS_FAILED = "analysis_failed"
ASK_RESULT = "ask_result"
ASK_FAILED = "ask_failed"
EVALUATE_RESULT = "evaluate_result"
EVALUATE_FAILED = "evaluate_failed"

_SUMMARY_BUDGET = MAX_SIMPLE_TEXT_LENGTH - 120  # leave room for the header
# The echoed question is a header, so it has to fit inside that same slack.
_QUESTION_ECHO = 60
_MARKDOWN_NOISE = re.compile(r"^[#>\s*\-=_]+|[*_`]+", re.MULTILINE)
_BLANK_RUN = re.compile(r"\n{3,}")
# Kakao renders plain text, so list structure has to survive as a character.
_BULLET = re.compile(r"^[ \t]*[-*+][ \t]+", re.MULTILINE)
_TRAILING_SPACES = re.compile(r"[ \t]+$", re.MULTILINE)
_SINGLE_LINE_BREAK = re.compile(r"(?<!\n)\n(?!\n)")

# Reports open with an executive summary. Lifting it beats truncating from the
# top, which cuts off mid-sentence inside the first technical section and reads
# like the message was damaged in transit.
_EXECUTIVE_SUMMARY = re.compile(
    r"^\#{1,3}[ \t]*(?:핵심[ \t]*요약|요약|Executive[ \t]+Summary)[ \t]*$"
    r"(.*?)"
    r"(?=^\#{1,3}[ \t])",
    re.MULTILINE | re.DOTALL | re.IGNORECASE,
)


def render_report_delivery(
    delivery: ClaimedOutboundDelivery,
) -> dict[str, object]:
    if delivery.message_type == ANALYSIS_RESULT:
        return _result(delivery.payload)
    if delivery.message_type == ANALYSIS_FAILED:
        return _failed(delivery.payload)
    if delivery.message_type == ASK_RESULT:
        return _ask_result(delivery.payload)
    if delivery.message_type == ASK_FAILED:
        return _ask_failed(delivery.payload)
    if delivery.message_type == EVALUATE_RESULT:
        return _evaluate_result(delivery.payload)
    if delivery.message_type == EVALUATE_FAILED:
        return _evaluate_failed(delivery.payload)
    raise ValueError(f"unsupported Kakao delivery type: {delivery.message_type}")


def _result(payload: Mapping[str, object]) -> dict[str, object]:
    ticker = _required_text(payload, "ticker")
    company_name = _required_text(payload, "company_name")
    summary = payload.get("summary")
    body = _condense(summary if isinstance(summary, str) else "")

    header = f"📊 {company_name} ({ticker}) 분석 완료"
    text = f"{header}\n\n{body}" if body else header

    pdf_url = payload.get("pdf_url")
    return skill_response(
        [
            simple_text_output(text[:MAX_SIMPLE_TEXT_LENGTH]),
            _next_actions(
                ticker,
                company_name,
                pdf_url=pdf_url if isinstance(pdf_url, str) else None,
            ),
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


def _evaluate_result(payload: Mapping[str, object]) -> dict[str, object]:
    """Render a verdict on someone's holding.

    The header repeats the entry price back. In a group room the answer lands
    minutes after the request, and "삼성전자 평가" alone would leave everyone —
    including the person who asked — unsure which position it is about.
    """

    ticker = _required_text(payload, "ticker")
    company_name = _required_text(payload, "company_name")
    verdict = payload.get("verdict")
    body = _clean_text(verdict if isinstance(verdict, str) else "")
    if not body:
        return _evaluate_failed(payload)
    body = _SINGLE_LINE_BREAK.sub("\n\n", body)

    header = f"🧮 {company_name} ({ticker}) 평가{_position_suffix(payload)}"
    body_parts = _split_text(
        body,
        first_limit=MAX_SIMPLE_TEXT_LENGTH - len(header) - 2,
        continuation_limit=MAX_SIMPLE_TEXT_LENGTH,
    )
    text_outputs = [
        simple_text_output(f"{header}\n\n{body_parts[0]}"),
        *[simple_text_output(part) for part in body_parts[1:]],
    ]
    return skill_response(
        [
            *text_outputs,
            _next_actions(ticker, company_name),
        ]
    )


def _evaluate_failed(payload: Mapping[str, object]) -> dict[str, object]:
    ticker = _required_text(payload, "ticker")
    company_name = _required_text(payload, "company_name")
    return skill_response(
        [
            simple_text_output(
                f"⚠️ {company_name} ({ticker}) 평가에 실패했습니다.\n"
                "잠시 후 다시 시도해주세요."
            ),
            _next_actions(ticker, company_name),
        ]
    )


def _position_suffix(payload: Mapping[str, object]) -> str:
    avg_price = payload.get("avg_price")
    if not isinstance(avg_price, (int, float)):
        return ""
    months = payload.get("period_months")
    held = f" · {months}개월 보유" if isinstance(months, int) else ""
    return f"\n평단 {avg_price:,.0f}{held}"


def _ask_result(payload: Mapping[str, object]) -> dict[str, object]:
    """Render an answer to a free-form question.

    Unlike a report there is no ticker to head the bubble, so the question
    itself is the header — in a group chat the answer arrives minutes after it
    was asked, by which time the room has moved on.
    """

    answer = payload.get("answer")
    body = _clean_text(answer if isinstance(answer, str) else "")
    if not body:
        return _ask_failed(payload)

    header = f"💬 {_echo_question(payload)}"
    body_parts = _split_text(
        body,
        first_limit=MAX_SIMPLE_TEXT_LENGTH - len(header) - 2,
        continuation_limit=MAX_SIMPLE_TEXT_LENGTH,
    )
    text_outputs = [
        simple_text_output(f"{header}\n\n{body_parts[0]}"),
        *[simple_text_output(part) for part in body_parts[1:]],
    ]
    return skill_response(
        [
            *text_outputs,
            _ask_actions(),
        ]
    )


def _ask_failed(payload: Mapping[str, object]) -> dict[str, object]:
    return skill_response(
        [
            simple_text_output(
                f"⚠️ 질문에 답하지 못했습니다.\n{_echo_question(payload)}\n"
                "잠시 후 다시 시도해주세요."
            ),
            _ask_actions(),
        ]
    )


def _echo_question(payload: Mapping[str, object]) -> str:
    """The user's own words, short enough to sit in a header."""

    question = payload.get("question")
    text = question.strip() if isinstance(question, str) else ""
    if not text:
        return "질문"
    return text if len(text) <= _QUESTION_ECHO else text[: _QUESTION_ECHO - 1] + "…"


def _ask_actions() -> dict[str, object]:
    """Follow-ups for an answer. Same rule as `_next_actions`: only live ones.

    An answer has no ticker, so there is nothing to offer 평가 or 리포트 on
    without guessing a stock the user never named.
    """

    return list_card_output(
        header_title="이어서 해보기",
        buttons=[{"action": "mention", "label": "💬 다시 질문하기"}],
        items=[
            {
                "title": "도움말",
                "description": "PRISM으로 할 수 있는 것들",
                "action": "message",
                "messageText": "도움말",
            }
        ],
    )


def _next_actions(
    ticker: str,
    company_name: str,
    *,
    pdf_url: str | None = None,
) -> dict[str, object]:
    """Offer only follow-ups that actually work.

    Item titles double as commands, because a tap sends the title. This card
    previously offered 평가 and 순위 — both of which answered "아직 준비 중인
    기능입니다", so a card headed "이어서 해보기" led nowhere twice.

    The full report is a `webLink` button rather than a file because Kakao has
    no attachment field, and "다른 종목" is a `mention` button, which fills the
    bot mention into the input box so the user only types a stock name.
    """

    items: list[dict[str, object]] = []
    if CommandKind.EVALUATE in IMPLEMENTED_COMMANDS:
        # Tapping sends this title as-is, which is deliberately incomplete: the
        # bot replies asking for the price, showing the exact line to copy. A
        # card cannot prefill part of the input box (`messageText` is ignored),
        # so one refusal that teaches the format is the shortest honest path.
        items.append(
            {
                "title": f"평가 {company_name}",
                "description": "내 평단가 기준으로 평가받기",
                "action": "message",
                "messageText": f"평가 {company_name}",
            }
        )
    if CommandKind.ASK in IMPLEMENTED_COMMANDS:
        # A whole sentence routes to a question on its own, so the title can be
        # the question itself rather than a command spelling.
        items.append(
            {
                "title": f"{company_name} 지금 사도 될까?",
                "description": "궁금한 점을 이어서 물어보세요",
                "action": "message",
                "messageText": f"{company_name} 지금 사도 될까?",
            }
        )
    if not items:
        items.append(
            {
                "title": "도움말",
                "description": "PRISM으로 할 수 있는 것들",
                "action": "message",
                "messageText": "도움말",
            }
        )

    buttons: list[dict[str, object]] = []
    if pdf_url:
        buttons.append(
            {
                "action": "webLink",
                "label": "📄 전체 리포트",
                "webLinkUrl": pdf_url,
            }
        )
    buttons.append({"action": "mention", "label": "🔍 다른 종목"})

    return list_card_output(
        header_title="이어서 해보기",
        buttons=buttons,
        items=items,
    )


def _condense(summary: str) -> str:
    """Flatten report markdown into something a chat bubble can hold."""

    text = _clean_text(summary)
    if len(text) <= _SUMMARY_BUDGET:
        return text
    return text[: _SUMMARY_BUDGET - 1].rstrip() + "…"


def _clean_text(text: str) -> str:
    """Remove Markdown noise without discarding any content."""

    if not text.strip():
        return ""
    cleaned = _BULLET.sub("· ", _executive_summary(text))
    cleaned = _MARKDOWN_NOISE.sub("", cleaned)
    cleaned = _TRAILING_SPACES.sub("", cleaned)
    return _BLANK_RUN.sub("\n\n", cleaned).strip()


def _split_text(
    text: str,
    *,
    first_limit: int,
    continuation_limit: int,
) -> list[str]:
    """Fit text into two bubbles, preferring paragraph and sentence ends."""

    if len(text) <= first_limit:
        return [text]

    split_at, resume_at = _natural_split(text, first_limit)
    first = text[:split_at].rstrip()
    remainder = text[resume_at:].lstrip()
    if len(remainder) <= continuation_limit:
        return [first, remainder]

    final_limit = continuation_limit - 1
    final_at, _ = _natural_split(remainder, final_limit)
    second = remainder[:final_at].rstrip() + "…"
    return [first, second]


def _natural_split(text: str, limit: int) -> tuple[int, int]:
    """Return cut/resume offsets at the latest useful boundary."""

    window = text[: limit + 1]
    candidates: list[tuple[int, int]] = []
    for match in re.finditer(r"\n{2,}", window):
        candidates.append((match.start(), match.end()))
    for match in re.finditer(r"(?<=[.!?。！？])(?:[ \t]+|\n+)", window):
        candidates.append((match.start(), match.end()))

    useful = [candidate for candidate in candidates if candidate[0] >= limit // 2]
    if useful:
        return max(useful, key=lambda candidate: candidate[0])
    return limit, limit


def _executive_summary(summary: str) -> str:
    """Return the report's own summary section, or the whole text if absent."""

    match = _EXECUTIVE_SUMMARY.search(summary)
    if not match:
        return summary
    section = match.group(1).strip()
    return section or summary


def _required_text(payload: Mapping[str, object], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"analysis payload requires {key}")
    return value.strip()
