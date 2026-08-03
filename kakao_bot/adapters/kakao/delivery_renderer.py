"""Route an outbox delivery to the renderer that knows its message type.

The sender takes a single renderer, so every message type has to be reachable
from one entry point. Adding a delivery type without adding it here fails at
send time with "unsupported Kakao delivery type", after the work that produced
it is already done — so the mapping is kept explicit and covered by a test that
walks every known type.
"""

from __future__ import annotations

from kakao_bot.adapters.kakao.campaign_renderer import render_campaign_delivery
from kakao_bot.adapters.kakao.report_renderer import (
    ANALYSIS_FAILED,
    ANALYSIS_RESULT,
    ASK_FAILED,
    ASK_RESULT,
    EVALUATE_FAILED,
    EVALUATE_RESULT,
    render_report_delivery,
)
from kakao_bot.adapters.kakao.welcome_renderer import (
    ROOM_WELCOME,
    render_welcome_delivery,
)
from kakao_bot.domain.models import ClaimedOutboundDelivery

CAMPAIGN_TYPES = frozenset({"signal_campaign", "campaign_rest_notice"})
REPORT_TYPES = frozenset(
    {
        ANALYSIS_RESULT,
        ANALYSIS_FAILED,
        ASK_RESULT,
        ASK_FAILED,
        EVALUATE_RESULT,
        EVALUATE_FAILED,
    }
)
LIFECYCLE_TYPES = frozenset({ROOM_WELCOME})

SUPPORTED_TYPES = CAMPAIGN_TYPES | REPORT_TYPES | LIFECYCLE_TYPES


def render_delivery(delivery: ClaimedOutboundDelivery) -> dict[str, object]:
    if delivery.message_type in CAMPAIGN_TYPES:
        return render_campaign_delivery(delivery)
    if delivery.message_type in REPORT_TYPES:
        return render_report_delivery(delivery)
    if delivery.message_type in LIFECYCLE_TYPES:
        return render_welcome_delivery(delivery)
    raise ValueError(f"unsupported Kakao delivery type: {delivery.message_type}")
