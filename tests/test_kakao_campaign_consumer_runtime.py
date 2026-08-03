from __future__ import annotations

from datetime import datetime, timezone

import pytest

from kakao_bot.adapters.persistence.sqlite import SQLiteKakaoRepository
from kakao_bot.domain.models import ApprovalStatus
from kakao_bot.runtime.campaign_consumer_main import (
    ConsumerConfigurationError,
    ConsumerRuntimeConfig,
    load_config,
    run_consumer_once,
)
from messaging.local_campaign_queue import SQLiteBatchCampaignQueue

NOW = datetime(2026, 7, 23, 5, 30, tzinfo=timezone.utc)


def completed_payload() -> dict[str, object]:
    return {
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


def test_consumer_config_uses_local_queue_paths(tmp_path):
    config = load_config(
        {
            "PRISM_CAMPAIGN_QUEUE_PATH": str(tmp_path / "campaigns.sqlite"),
            "KAKAO_BOT_DATABASE_PATH": str(tmp_path / "kakao.sqlite"),
            "KAKAO_CONSUMER_POLL_SECONDS": "1.5",
            "KAKAO_CONSUMER_BATCH_SIZE": "7",
            "KAKAO_CONSUMER_LEASE_SECONDS": "45",
        }
    )

    assert config.queue_path == tmp_path / "campaigns.sqlite"
    assert config.database_path == tmp_path / "kakao.sqlite"
    assert config.poll_seconds == 1.5
    assert config.batch_size == 7
    assert config.lease_seconds == 45

    with pytest.raises(ConsumerConfigurationError, match="positive"):
        load_config({"KAKAO_CONSUMER_POLL_SECONDS": "0"})


def test_consumer_runtime_moves_local_campaign_to_kakao_outbox(tmp_path):
    queue_path = tmp_path / "campaigns.sqlite"
    database_path = tmp_path / "kakao.sqlite"
    with SQLiteKakaoRepository(database_path) as repository:
        repository.discover_room("room-1")
        repository.set_room_approval("room-1", ApprovalStatus.APPROVED)
    with SQLiteBatchCampaignQueue(queue_path) as queue:
        assert queue.enqueue(completed_payload()) is not None

    result = run_consumer_once(
        ConsumerRuntimeConfig(
            queue_path=queue_path,
            database_path=database_path,
            lease_owner="consumer-1",
        ),
        now=NOW,
    )

    assert result.claimed == 1
    assert result.consumed == 1
    with SQLiteBatchCampaignQueue(queue_path) as queue:
        [entry] = queue.list_entries()
        assert entry["status"] == "CONSUMED"
    with SQLiteKakaoRepository(database_path) as repository:
        [delivery] = repository.list_outbox()
        assert delivery["status"] == "PENDING"
        assert delivery["message_type"] == "signal_campaign"


def test_consumer_marks_poison_payload_dead(tmp_path):
    queue_path = tmp_path / "campaigns.sqlite"
    with SQLiteBatchCampaignQueue(queue_path) as queue:
        assert (
            queue.enqueue(
                {
                    "campaign_id": "poison-1",
                    "schema_version": 999,
                }
            )
            is not None
        )

    result = run_consumer_once(
        ConsumerRuntimeConfig(
            queue_path=queue_path,
            database_path=tmp_path / "kakao.sqlite",
            lease_owner="consumer-1",
        ),
        now=NOW,
    )

    assert result.dead == 1
    with SQLiteBatchCampaignQueue(queue_path) as queue:
        [entry] = queue.list_entries()
        assert entry["status"] == "DEAD"
        assert "schema_version" in entry["last_error"]
