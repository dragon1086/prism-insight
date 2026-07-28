"""Rendering of finished analyses into Kakao bubbles.

The follow-up affordances here are ListCard items rather than quickReplies,
because quickReplies do not render in a group chatroom, and item titles are
written as commands because a tap sends the title.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from kakao_bot.adapters.kakao.report_renderer import render_report_delivery
from kakao_bot.adapters.kakao.skill_response import MAX_SIMPLE_TEXT_LENGTH
from kakao_bot.application.command_parser import CommandKind, parse_command
from kakao_bot.domain.models import ClaimedOutboundDelivery

NOW = datetime(2026, 7, 28, 5, 0, tzinfo=timezone.utc)


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


def result_payload(**overrides) -> dict:
    payload = {
        "job_id": "job-1",
        "ticker": "005930",
        "company_name": "삼성전자",
        "market": "kr",
        "summary": "## 투자 판단\n\n**매수 우위**입니다.\n\n- 실적 개선\n- 수급 양호",
    }
    payload.update(overrides)
    return payload


def outputs(response: dict) -> list[dict]:
    return response["template"]["outputs"]


def test_result_renders_summary_and_follow_up_card():
    response = render_report_delivery(delivery("analysis_result", result_payload()))

    bubbles = outputs(response)
    assert len(bubbles) == 2
    text = bubbles[0]["simpleText"]["text"]
    assert "삼성전자" in text and "005930" in text
    assert "매수 우위" in text
    assert "listCard" in bubbles[1]


def test_markdown_noise_is_stripped_from_the_summary():
    response = render_report_delivery(delivery("analysis_result", result_payload()))

    text = outputs(response)[0]["simpleText"]["text"]
    assert "##" not in text
    assert "**" not in text


def test_summary_is_truncated_within_the_simple_text_limit():
    long_summary = "가" * 5_000
    response = render_report_delivery(
        delivery("analysis_result", result_payload(summary=long_summary))
    )

    text = outputs(response)[0]["simpleText"]["text"]
    assert len(text) <= MAX_SIMPLE_TEXT_LENGTH
    assert text.endswith("…")


def test_missing_summary_still_renders_a_header():
    response = render_report_delivery(
        delivery("analysis_result", result_payload(summary=""))
    )

    text = outputs(response)[0]["simpleText"]["text"]
    assert "삼성전자" in text


def test_follow_up_item_titles_parse_back_into_commands():
    """A tap sends the title, so every title must be a command we accept."""

    response = render_report_delivery(delivery("analysis_result", result_payload()))
    items = outputs(response)[1]["listCard"]["items"]

    kinds = {parse_command(item["title"]).kind for item in items}
    assert CommandKind.UNKNOWN not in kinds
    assert CommandKind.EVALUATE in kinds
    assert CommandKind.LEADERBOARD in kinds


def test_failed_analysis_explains_and_still_offers_follow_ups():
    response = render_report_delivery(
        delivery(
            "analysis_failed",
            {
                "job_id": "job-2",
                "ticker": "005930",
                "company_name": "삼성전자",
                "error_code": "generation_failed",
            },
        )
    )

    bubbles = outputs(response)
    assert "실패" in bubbles[0]["simpleText"]["text"]
    assert "listCard" in bubbles[1]


def test_unknown_message_type_is_rejected():
    with pytest.raises(ValueError, match="unsupported"):
        render_report_delivery(delivery("something_else", result_payload()))


@pytest.mark.parametrize("missing", ["ticker", "company_name"])
def test_required_payload_fields_are_enforced(missing):
    payload = result_payload()
    payload.pop(missing)

    with pytest.raises(ValueError, match=missing):
        render_report_delivery(delivery("analysis_result", payload))
