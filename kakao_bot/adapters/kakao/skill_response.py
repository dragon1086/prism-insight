"""Pure builders for Kakao SkillResponse v2 JSON.

The builders intentionally do not know about HTTP request envelopes. Keeping
the message format separate makes the yet-to-be-smoke-tested Bot REST envelope
replaceable without changing application code.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence

MAX_SIMPLE_TEXT_LENGTH = 1_000
MAX_OUTPUTS = 3
MAX_QUICK_REPLIES = 10
MAX_LIST_CARD_ITEMS = 5
MAX_MENTIONS = 15


def simple_text_output(text: str) -> dict[str, object]:
    """Build one SimpleText output and enforce Kakao's character limit."""

    if not text:
        raise ValueError("simple text must not be empty")
    if len(text) > MAX_SIMPLE_TEXT_LENGTH:
        raise ValueError(
            f"simple text must be at most {MAX_SIMPLE_TEXT_LENGTH} characters"
        )
    return {"simpleText": {"text": text}}


def list_card_output(
    *,
    header_title: str,
    items: Sequence[Mapping[str, object]],
    buttons: Sequence[Mapping[str, object]] = (),
) -> dict[str, object]:
    """Build one ListCard output with at most five items."""

    if not header_title:
        raise ValueError("list card header title must not be empty")
    if not items:
        raise ValueError("list card must contain at least one item")
    if len(items) > MAX_LIST_CARD_ITEMS:
        raise ValueError(f"list card must contain at most {MAX_LIST_CARD_ITEMS} items")

    card: dict[str, object] = {
        "header": {"title": header_title},
        "items": [dict(item) for item in items],
    }
    if buttons:
        card["buttons"] = [dict(button) for button in buttons]
    return {"listCard": card}


def quick_reply(
    *,
    label: str,
    message_text: str,
) -> dict[str, object]:
    if not label:
        raise ValueError("quick reply label must not be empty")
    if not message_text:
        raise ValueError("quick reply message_text must not be empty")
    return {
        "action": "message",
        "label": label,
        "messageText": message_text,
    }


def skill_response(
    outputs: Sequence[Mapping[str, object]],
    *,
    quick_replies: Sequence[Mapping[str, object]] = (),
    mention_user_keys: Iterable[str] = (),
) -> dict[str, object]:
    """Build a SkillResponse and enforce collection cardinality limits."""

    if not outputs:
        raise ValueError("skill response must contain at least one output")
    if len(outputs) > MAX_OUTPUTS:
        raise ValueError(f"skill response must contain at most {MAX_OUTPUTS} outputs")
    if len(quick_replies) > MAX_QUICK_REPLIES:
        raise ValueError(
            f"skill response must contain at most {MAX_QUICK_REPLIES} quick replies"
        )

    mentions = tuple(mention_user_keys)
    if len(mentions) > MAX_MENTIONS:
        raise ValueError(f"skill response must contain at most {MAX_MENTIONS} mentions")
    if any(not user_key for user_key in mentions):
        raise ValueError("mention user keys must not be empty")
    if len(set(mentions)) != len(mentions):
        raise ValueError("mention user keys must be unique")

    template: dict[str, object] = {
        "outputs": [dict(output) for output in outputs],
    }
    if quick_replies:
        template["quickReplies"] = [dict(reply) for reply in quick_replies]

    response: dict[str, object] = {
        "version": "2.0",
        "template": template,
    }
    if mentions:
        response["extra"] = {
            "mentions": [{"userKey": user_key} for user_key in mentions]
        }
    return response


def simple_text(
    text: str,
    *,
    quick_replies: Sequence[Mapping[str, object]] = (),
    mention_user_keys: Iterable[str] = (),
) -> dict[str, object]:
    """Build a complete one-bubble SimpleText SkillResponse."""

    return skill_response(
        [simple_text_output(text)],
        quick_replies=quick_replies,
        mention_user_keys=mention_user_keys,
    )
