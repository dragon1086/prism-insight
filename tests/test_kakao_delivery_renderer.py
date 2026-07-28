"""Every message type that gets enqueued must be renderable.

A renderer that does not know a type fails at send time, after the expensive
work is done and the row is already in the outbox — which is how a real report
died on 2026-07-28: the report renderer existed but was never wired into the
sender, so the delivery went straight to DEAD with "unsupported Kakao delivery
type: analysis_result".

The guard below is deliberately about the *seam*: it reads the message types
the producers actually write and asserts the dispatcher covers them, so adding
a producer without a renderer fails here rather than in production.
"""

from __future__ import annotations

import ast
from datetime import datetime, timezone
from pathlib import Path

import pytest

from kakao_bot.adapters.kakao.delivery_renderer import (
    SUPPORTED_TYPES,
    render_delivery,
)
from kakao_bot.domain.models import ClaimedOutboundDelivery

NOW = datetime(2026, 7, 28, 5, 0, tzinfo=timezone.utc)
PRODUCERS = (
    "kakao_bot/application/analysis_service.py",
    "kakao_bot/application/batch_campaign_service.py",
)


def delivery(message_type: str, payload: dict) -> ClaimedOutboundDelivery:
    return ClaimedOutboundDelivery(
        delivery_key="k",
        room_id="room-1",
        message_type=message_type,
        payload=payload,
        attempt_count=1,
        lease_owner="worker-1",
        lease_expires_at=NOW,
        created_at=NOW,
    )


def test_analysis_result_is_renderable_through_the_dispatcher():
    response = render_delivery(
        delivery(
            "analysis_result",
            {
                "job_id": "job-1",
                "ticker": "005930",
                "company_name": "삼성전자",
                "market": "kr",
                "summary": "# 분석\n\n매수 우위",
            },
        )
    )

    assert response["template"]["outputs"]


def test_analysis_failed_is_renderable_through_the_dispatcher():
    response = render_delivery(
        delivery(
            "analysis_failed",
            {
                "job_id": "job-1",
                "ticker": "005930",
                "company_name": "삼성전자",
                "error_code": "generation_failed",
            },
        )
    )

    assert response["template"]["outputs"]


def test_campaign_types_still_route_to_the_campaign_renderer():
    response = render_delivery(
        delivery(
            "signal_campaign",
            {
                "market": "KR",
                "session": "AFTERNOON",
                "trade_date": "2026-07-28",
                "regime": "UPTREND",
                "candidates": [
                    {"ticker": "005930", "company_name": "삼성전자", "score": 91}
                ],
            },
        )
    )

    assert response["template"]["outputs"]


def test_unknown_type_is_rejected():
    with pytest.raises(ValueError, match="unsupported"):
        render_delivery(delivery("brand_new_type", {}))


def _message_type_literals(relative: str) -> set[str]:
    """Collect the message types a producer writes.

    Handles both shapes in use: passed inline as ``message_type="..."`` and
    assigned to a local first, as the campaign service does.
    """

    root = Path(__file__).resolve().parent.parent
    tree = ast.parse((root / relative).read_text(encoding="utf-8"))
    found: set[str] = set()

    def literal(node: ast.AST) -> str | None:
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return node.value
        return None

    for node in ast.walk(tree):
        if isinstance(node, ast.keyword) and node.arg == "message_type":
            value = literal(node.value)
            if value:
                found.add(value)
        elif isinstance(node, ast.Assign):
            names = {t.id for t in node.targets if isinstance(t, ast.Name)}
            if "message_type" in names:
                value = literal(node.value)
                if value:
                    found.add(value)
    return found


@pytest.mark.parametrize("producer", PRODUCERS)
def test_every_enqueued_message_type_has_a_renderer(producer):
    produced = _message_type_literals(producer)

    assert produced, f"no message_type literals found in {producer}"
    assert produced <= SUPPORTED_TYPES, (
        f"{producer} enqueues {sorted(produced - SUPPORTED_TYPES)}, "
        "which the sender cannot render"
    )
