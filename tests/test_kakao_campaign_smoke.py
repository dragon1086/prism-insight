from __future__ import annotations

from datetime import date

import pytest

from kakao_bot.runtime.campaign_smoke_main import (
    CampaignSmokeConfig,
    enqueue_smoke,
)
from messaging.local_campaign_queue import SQLiteBatchCampaignQueue


@pytest.mark.asyncio
async def test_campaign_smoke_enqueues_local_event_without_network(tmp_path):
    queue_path = tmp_path / "campaigns.sqlite"

    campaign_id = await enqueue_smoke(
        CampaignSmokeConfig(
            queue_path=queue_path,
            trade_date=date(2026, 7, 23),
            nonce="test",
        )
    )

    assert campaign_id == "smoke-kr-afternoon-2026-07-23-test"
    with SQLiteBatchCampaignQueue(queue_path) as queue:
        [entry] = queue.list_entries()
        assert entry["campaign_id"] == campaign_id
        assert entry["status"] == "PENDING"
