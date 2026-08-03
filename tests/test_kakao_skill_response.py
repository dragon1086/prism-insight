from __future__ import annotations

import pytest

from kakao_bot.adapters.kakao.skill_response import (
    list_card_output,
    quick_reply,
    simple_text,
    simple_text_output,
    skill_response,
)


def test_simple_text_builds_skill_response_v2():
    response = simple_text(
        "분석을 시작합니다.",
        quick_replies=[
            quick_reply(label="도움말", message_text="/help"),
        ],
        mention_user_keys=["user-1"],
    )

    assert response == {
        "version": "2.0",
        "template": {
            "outputs": [
                {"simpleText": {"text": "분석을 시작합니다."}},
            ],
            "quickReplies": [
                {
                    "action": "message",
                    "label": "도움말",
                    "messageText": "/help",
                }
            ],
        },
        "extra": {"mentions": [{"userKey": "user-1"}]},
    }


@pytest.mark.parametrize(
    ("factory", "expected_message"),
    [
        (
            lambda: simple_text_output("x" * 1_001),
            "at most 1000",
        ),
        (
            lambda: skill_response(
                [simple_text_output(str(index)) for index in range(4)]
            ),
            "at most 3",
        ),
        (
            lambda: skill_response(
                [simple_text_output("ok")],
                quick_replies=[
                    quick_reply(label=str(index), message_text=str(index))
                    for index in range(11)
                ],
            ),
            "at most 10",
        ),
        (
            lambda: list_card_output(
                header_title="종목",
                items=[{"title": str(index)} for index in range(6)],
            ),
            "at most 5",
        ),
        (
            lambda: skill_response(
                [simple_text_output("ok")],
                mention_user_keys=[f"user-{index}" for index in range(16)],
            ),
            "at most 15",
        ),
    ],
)
def test_skill_response_limits_are_enforced(factory, expected_message):
    with pytest.raises(ValueError, match=expected_message):
        factory()


def test_list_card_copies_caller_data():
    item = {"title": "삼성전자", "description": "005930"}
    output = list_card_output(header_title="오늘의 시그널", items=[item])
    item["title"] = "mutated"

    assert output == {
        "listCard": {
            "header": {"title": "오늘의 시그널"},
            "items": [{"title": "삼성전자", "description": "005930"}],
        }
    }
