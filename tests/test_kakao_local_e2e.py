from __future__ import annotations

from datetime import datetime, timezone

import pytest

from kakao_bot.adapters.kakao.gateway_inbound_handler import (
    GatewayDispatchHandler,
)
from kakao_bot.adapters.kakao.gateway_protocol import GatewayDispatch
from kakao_bot.adapters.persistence.sqlite import SQLiteKakaoRepository
from kakao_bot.application.gateway_inbound_service import GatewayInboundService
from kakao_bot.domain.models import ApprovalStatus, MessageSendResult
from kakao_bot.runtime.campaign_consumer_main import (
    ConsumerRuntimeConfig,
    run_consumer_once,
)
from kakao_bot.runtime.sender_main import (
    SenderRuntimeConfig,
    run_sender_once,
)
from messaging.local_campaign_queue import SQLiteBatchCampaignQueue

NOW = datetime(2026, 7, 23, 5, 30, tzinfo=timezone.utc)


class FakeSender:
    def __init__(self) -> None:
        self.calls = []

    async def send_message(self, room_id, skill_response):
        self.calls.append((room_id, skill_response))
        return MessageSendResult(success=True, status_code=200)


@pytest.mark.asyncio
async def test_gateway_to_local_campaign_to_kakao_sender_e2e(tmp_path):
    database_path = tmp_path / "kakao.sqlite"
    queue_path = tmp_path / "campaigns.sqlite"

    with SQLiteKakaoRepository(database_path) as repository:
        gateway_handler = GatewayDispatchHandler(GatewayInboundService(repository))
        await gateway_handler(
            GatewayDispatch(
                sequence=1,
                event_type="ENTRANCE",
                data={
                    "id": "entrance-1",
                    "botGroupKey": "room-1",
                    "timestamp": NOW.isoformat(),
                },
            )
        )
        repository.set_room_approval("room-1", ApprovalStatus.APPROVED)

    payload = {
        "schema_version": 1,
        "event_type": "BATCH_CAMPAIGN_COMPLETED",
        "campaign_id": "kr-afternoon-2026-07-23",
        "market": "KR",
        "session": "AFTERNOON",
        "trade_date": "2026-07-23",
        "regime": "UPTREND",
        "status": "COMPLETED",
        "occurred_at": NOW.isoformat(),
        "candidates": [
            {
                "ticker": "005930",
                "company_name": "삼성전자",
                "score": 91.0,
                "rationale": "종가 돌파",
            }
        ],
    }
    with SQLiteBatchCampaignQueue(queue_path) as queue:
        assert queue.enqueue(payload) is not None
    consume_result = run_consumer_once(
        ConsumerRuntimeConfig(
            queue_path=queue_path,
            database_path=database_path,
            lease_owner="e2e-consumer",
        ),
        now=NOW,
    )
    assert consume_result.consumed == 1
    with SQLiteKakaoRepository(database_path) as repository:
        [queued] = repository.list_outbox()
        assert queued["status"] == "PENDING"
        assert queued["message_type"] == "signal_campaign"

    sender = FakeSender()
    result = await run_sender_once(
        SenderRuntimeConfig(
            token="fake-token",
            database_path=database_path,
            lease_owner="e2e-worker",
        ),
        sender=sender,
        now=NOW,
    )

    assert result.sent == 1
    [(room_id, skill_response)] = sender.calls
    assert room_id == "room-1"
    assert skill_response["version"] == "2.0"
    with SQLiteKakaoRepository(database_path) as repository:
        [sent] = repository.list_outbox()
        assert sent["status"] == "SENT"
