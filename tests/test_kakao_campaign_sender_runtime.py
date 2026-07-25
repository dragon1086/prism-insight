from __future__ import annotations

from datetime import datetime, timezone

import pytest

from kakao_bot.adapters.kakao.campaign_renderer import (
    render_campaign_delivery,
)
from kakao_bot.adapters.persistence.sqlite import SQLiteKakaoRepository
from kakao_bot.domain.models import (
    ApprovalStatus,
    ClaimedOutboundDelivery,
    MessageSendResult,
    OutboundDelivery,
)
from kakao_bot.runtime.sender_main import (
    SenderConfigurationError,
    SenderRuntimeConfig,
    load_config,
    run_sender_once,
)

NOW = datetime(2026, 7, 23, 5, 0, tzinfo=timezone.utc)
TOKEN = "do-not-print-this-token"


def claimed(
    *,
    message_type: str,
    payload: dict[str, object],
) -> ClaimedOutboundDelivery:
    return ClaimedOutboundDelivery(
        delivery_key="delivery-1",
        room_id="room-1",
        message_type=message_type,
        payload=payload,
        attempt_count=1,
        lease_owner="worker-1",
        lease_expires_at=NOW,
        created_at=NOW,
    )


def test_signal_campaign_renderer_builds_one_list_card_with_five_item_cap():
    response = render_campaign_delivery(
        claimed(
            message_type="signal_campaign",
            payload={
                "campaign_id": "kr-afternoon-2026-07-23",
                "market": "KR",
                "session": "AFTERNOON",
                "trade_date": "2026-07-23",
                "regime": "UPTREND",
                "candidates": [
                    {
                        "ticker": f"00000{index}",
                        "company_name": f"기업 {index}",
                        "score": 90 - index,
                        "rationale": "종가 돌파",
                    }
                    for index in range(6)
                ],
            },
        )
    )

    assert response["version"] == "2.0"
    [output] = response["template"]["outputs"]
    card = output["listCard"]
    assert card["header"]["title"] == "🇰🇷 한국 오후 시그널"
    assert len(card["items"]) == 5
    assert card["items"][0]["title"] == "기업 0 (000000)"
    assert "점수 90" in card["items"][0]["description"]


def test_rest_notice_renderer_does_not_create_candidate_card():
    response = render_campaign_delivery(
        claimed(
            message_type="campaign_rest_notice",
            payload={
                "campaign_id": "kr-morning-2026-07-23",
                "market": "KR",
                "session": "MORNING",
                "trade_date": "2026-07-23",
                "regime": "CORRECTION",
                "reason": "CORRECTION morning batch rests",
            },
        )
    )

    [output] = response["template"]["outputs"]
    text = output["simpleText"]["text"]
    assert "오전 분석은 쉬어갑니다" in text
    assert "CORRECTION morning batch rests" in text


def test_renderer_rejects_unknown_or_malformed_delivery():
    with pytest.raises(ValueError, match="unsupported"):
        render_campaign_delivery(claimed(message_type="unknown", payload={}))
    with pytest.raises(ValueError, match="candidates"):
        render_campaign_delivery(
            claimed(
                message_type="signal_campaign",
                payload={"market": "KR", "session": "AFTERNOON"},
            )
        )


class FakeSender:
    def __init__(self) -> None:
        self.calls = []

    async def send_message(self, room_id, skill_response):
        self.calls.append((room_id, skill_response))
        return MessageSendResult(success=True, status_code=200)


@pytest.mark.asyncio
async def test_sender_runtime_drains_durable_outbox_once(tmp_path):
    database_path = tmp_path / "kakao.sqlite"
    with SQLiteKakaoRepository(database_path) as repository:
        repository.discover_room("room-1")
        repository.set_room_approval("room-1", ApprovalStatus.APPROVED)
        assert repository.enqueue_outbound(
            OutboundDelivery(
                delivery_key="campaign:1:room:1",
                room_id="room-1",
                message_type="campaign_rest_notice",
                payload={
                    "market": "KR",
                    "session": "MORNING",
                    "trade_date": "2026-07-23",
                    "regime": "CORRECTION",
                    "reason": "조정장",
                },
                created_at=NOW,
            )
        )

    sender = FakeSender()
    result = await run_sender_once(
        SenderRuntimeConfig(
            token=TOKEN,
            database_path=database_path,
            lease_owner="test-worker",
        ),
        sender=sender,
        now=NOW,
    )

    assert result.sent == 1
    assert len(sender.calls) == 1
    with SQLiteKakaoRepository(database_path) as repository:
        [delivery] = repository.list_outbox()
        assert delivery["status"] == "SENT"


def test_sender_config_validates_and_redacts_secret(tmp_path):
    config = load_config(
        {
            "KAKAO_BOT_TOKEN": TOKEN,
            "KAKAO_BOT_DATABASE_PATH": str(tmp_path / "kakao.sqlite"),
            "KAKAO_REST_BASE_URL": "https://example.test",
            "KAKAO_SENDER_POLL_SECONDS": "2.5",
            "KAKAO_SENDER_BATCH_SIZE": "7",
            "KAKAO_SENDER_LEASE_SECONDS": "45",
        }
    )

    assert config.database_path == tmp_path / "kakao.sqlite"
    assert config.base_url == "https://example.test"
    assert config.poll_seconds == 2.5
    assert config.batch_size == 7
    assert config.lease_seconds == 45
    assert TOKEN not in repr(config)

    with pytest.raises(SenderConfigurationError, match="KAKAO_BOT_TOKEN"):
        load_config({})
