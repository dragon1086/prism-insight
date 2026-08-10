"""The closing verdict every Kakao command reply ends on.

Two things have to hold no matter what the model returns: the disclaimer is
always there, and the verdict is never the part that gets cut when the answer
is too long for a bubble.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import pytest

from kakao_bot.adapters.kakao.report_renderer import render_report_delivery
from kakao_bot.adapters.kakao.skill_response import MAX_SIMPLE_TEXT_LENGTH
from kakao_bot.adapters.prism.verdict import (
    DISCLAIMER,
    EVALUATE_TONE_SUFFIX,
    VERDICT_INSTRUCTION,
    VERDICT_MARK,
    append_verdict,
    build_report_verdict,
)
from kakao_bot.domain.models import ClaimedOutboundDelivery

NOW = datetime(2026, 8, 10, 5, 0, tzinfo=timezone.utc)
VERDICT_LINE = "실적 흐름이 받쳐주니 관심 가져볼 만합니다."


def delivery(message_type: str, payload: dict) -> ClaimedOutboundDelivery:
    return ClaimedOutboundDelivery(
        delivery_key=f"analysis:{payload.get('job_id', 'job')}",
        room_id="room-1",
        message_type=message_type,
        payload=payload,
        attempt_count=1,
        lease_owner="worker-1",
        lease_expires_at=NOW,
        created_at=NOW,
    )


def outputs(response: dict) -> list[dict]:
    return response["template"]["outputs"]


def all_text(response: dict) -> str:
    return "\n".join(
        bubble["simpleText"]["text"]
        for bubble in outputs(response)
        if "simpleText" in bubble
    )


def report_payload(**overrides) -> dict:
    payload = {
        "job_id": "job-1",
        "ticker": "005930",
        "company_name": "삼성전자",
        "market": "kr",
        "summary": "## 핵심 요약\n\n실적이 개선되고 있습니다.",
    }
    payload.update(overrides)
    return payload


def ask_payload(**overrides) -> dict:
    payload = {
        "job_id": "job-2",
        "question": "삼성전자 어때?",
        "answer": "최근 실적이 좋습니다.",
    }
    payload.update(overrides)
    return payload


def evaluate_payload(**overrides) -> dict:
    payload = {
        "job_id": "job-3",
        "ticker": "005930",
        "company_name": "삼성전자",
        "market": "kr",
        "avg_price": 50000.0,
        "period_months": 3,
        "verdict": "평단 대비 수익 구간입니다.",
    }
    payload.update(overrides)
    return payload


def with_verdict(body: str) -> str:
    return f"{body}\n\n{VERDICT_MARK}\n{VERDICT_LINE}"


# --- the disclaimer is rendered, not prompted -------------------------------


@pytest.mark.parametrize(
    ("message_type", "payload"),
    [
        ("analysis_result", report_payload()),
        ("ask_result", ask_payload()),
        ("evaluate_result", evaluate_payload()),
    ],
)
def test_every_successful_reply_carries_the_disclaimer(message_type, payload):
    response = render_report_delivery(delivery(message_type, payload))

    assert DISCLAIMER in all_text(response)


@pytest.mark.parametrize(
    ("message_type", "payload"),
    [
        ("analysis_failed", report_payload()),
        ("ask_failed", ask_payload()),
        ("evaluate_failed", evaluate_payload()),
    ],
)
def test_failures_stay_bare(message_type, payload):
    """A failure has nothing to disclaim; the notice would only add noise."""

    response = render_report_delivery(delivery(message_type, payload))

    assert DISCLAIMER not in all_text(response)


def test_disclaimer_appears_once_even_when_the_model_wrote_one():
    answer = with_verdict(f"본문입니다.\n\n※ {DISCLAIMER}")
    response = render_report_delivery(delivery("ask_result", ask_payload(answer=answer)))

    assert all_text(response).count(DISCLAIMER) == 1


# --- the verdict survives every path that shortens text ---------------------


@pytest.mark.parametrize(
    ("message_type", "payload_factory", "field"),
    [
        ("analysis_result", report_payload, "summary"),
        ("ask_result", ask_payload, "answer"),
        ("evaluate_result", evaluate_payload, "verdict"),
    ],
)
def test_verdict_is_kept_and_placed_last(message_type, payload_factory, field):
    payload = payload_factory(**{field: with_verdict("본문입니다.")})
    response = render_report_delivery(delivery(message_type, payload))

    text = all_text(response)
    assert VERDICT_LINE in text
    assert text.index(VERDICT_LINE) < text.index(DISCLAIMER)
    assert text.rstrip().endswith(DISCLAIMER)


@pytest.mark.parametrize(
    ("message_type", "payload_factory", "field"),
    [
        ("analysis_result", report_payload, "summary"),
        ("ask_result", ask_payload, "answer"),
        ("evaluate_result", evaluate_payload, "verdict"),
    ],
)
def test_an_overlong_body_loses_its_middle_not_its_verdict(
    message_type, payload_factory, field
):
    """The verdict sits where truncation bites, so it is held out of the cut."""

    flood = "가" * (MAX_SIMPLE_TEXT_LENGTH * 3)
    payload = payload_factory(**{field: with_verdict(flood)})

    response = render_report_delivery(delivery(message_type, payload))

    text = all_text(response)
    assert VERDICT_LINE in text
    assert DISCLAIMER in text
    for bubble in outputs(response):
        if "simpleText" in bubble:
            assert len(bubble["simpleText"]["text"]) <= MAX_SIMPLE_TEXT_LENGTH


def test_report_verdict_outlives_the_executive_summary_lift():
    """`_clean_text` narrows a report to 핵심 요약; the verdict is appended below."""

    summary = with_verdict(
        "# 삼성전자 분석 보고서\n\n"
        "## 핵심 요약\n\n요약 본문입니다.\n\n"
        "## 1. 기술적 분석\n\n카드에 나오면 안 되는 본문입니다.\n"
    )
    response = render_report_delivery(
        delivery("analysis_result", report_payload(summary=summary))
    )

    text = all_text(response)
    assert "요약 본문입니다." in text
    assert "나오면 안 되는" not in text
    assert VERDICT_LINE in text


def test_a_reply_without_a_verdict_still_closes_cleanly():
    response = render_report_delivery(delivery("ask_result", ask_payload()))

    text = all_text(response)
    assert VERDICT_MARK not in text
    assert DISCLAIMER in text


def test_an_empty_verdict_block_is_not_rendered_as_a_heading():
    answer = f"본문입니다.\n\n{VERDICT_MARK}\n"
    response = render_report_delivery(delivery("ask_result", ask_payload(answer=answer)))

    text = all_text(response)
    assert VERDICT_MARK not in text
    assert DISCLAIMER in text


# --- instructions reach the three commands ----------------------------------


def test_ask_prompt_asks_for_a_one_directional_close():
    assert VERDICT_MARK in VERDICT_INSTRUCTION
    assert "면책" in VERDICT_INSTRUCTION
    assert "사용자에게 확인 과제를" in VERDICT_INSTRUCTION
    assert "네가 먼저 조사" in VERDICT_INSTRUCTION


def test_evaluate_tone_carries_the_same_rule():
    assert VERDICT_MARK in EVALUATE_TONE_SUFFIX
    assert "사용자에게 확인 과제를" in EVALUATE_TONE_SUFFIX
    assert "네가 먼저 조사" in EVALUATE_TONE_SUFFIX


# --- append_verdict ---------------------------------------------------------


def test_append_verdict_adds_the_marked_block():
    assert append_verdict("본문", VERDICT_LINE) == f"본문\n\n{VERDICT_MARK}\n{VERDICT_LINE}"


@pytest.mark.parametrize("verdict", [None, "", "   "])
def test_append_verdict_leaves_the_body_alone_without_one(verdict):
    assert append_verdict("본문", verdict) == "본문"


def test_append_verdict_does_not_stack_a_second_block():
    once = append_verdict("본문", VERDICT_LINE)

    assert append_verdict(once, "다른 결론") == once


# --- the report verdict call fails open -------------------------------------


def test_report_verdict_is_skipped_without_credentials(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    result = asyncio.run(build_report_verdict("삼성전자", "005930", "리포트 본문"))

    assert result is None


def test_report_verdict_is_skipped_for_an_empty_report(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")

    assert asyncio.run(build_report_verdict("삼성전자", "005930", "   ")) is None
