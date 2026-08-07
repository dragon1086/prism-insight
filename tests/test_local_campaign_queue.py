from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

from messaging.local_campaign_queue import SQLiteBatchCampaignQueue

NOW = datetime(2026, 7, 23, 5, 30, tzinfo=timezone.utc)


def payload(campaign_id: str = "campaign-1") -> dict[str, object]:
    return {
        "campaign_id": campaign_id,
        "occurred_at": NOW.isoformat(),
        "schema_version": 1,
    }


def test_queue_claim_is_exclusive_and_expired_lease_is_reclaimed(tmp_path):
    database_path = tmp_path / "campaigns.sqlite"
    with (
        SQLiteBatchCampaignQueue(database_path) as first,
        SQLiteBatchCampaignQueue(database_path) as second,
    ):
        assert first.enqueue(payload()) == "campaign-1"
        assert first.enqueue(payload()) is None

        [claimed] = first.claim(
            lease_owner="consumer-1",
            now=NOW,
            lease_seconds=30,
            limit=1,
        )
        assert (
            second.claim(
                lease_owner="consumer-2",
                now=NOW,
                lease_seconds=30,
                limit=1,
            )
            == ()
        )

        [reclaimed] = second.claim(
            lease_owner="consumer-2",
            now=NOW + timedelta(seconds=31),
            lease_seconds=30,
            limit=1,
        )
        assert claimed.attempt_count == 1
        assert reclaimed.attempt_count == 2
        assert not first.acknowledge(
            claimed.queue_id,
            lease_owner="consumer-1",
            consumed_at=NOW,
        )
        assert second.acknowledge(
            reclaimed.queue_id,
            lease_owner="consumer-2",
            consumed_at=NOW + timedelta(seconds=31),
        )


def test_queue_release_delays_retry_and_dead_is_terminal(tmp_path):
    database_path = tmp_path / "campaigns.sqlite"
    with SQLiteBatchCampaignQueue(database_path) as queue:
        queue.enqueue(payload("retry-1"))
        [claimed] = queue.claim(
            lease_owner="consumer-1",
            now=NOW,
            lease_seconds=30,
            limit=1,
        )
        assert queue.release(
            claimed.queue_id,
            lease_owner="consumer-1",
            next_attempt_at=NOW + timedelta(seconds=10),
            error="database busy",
        )
        assert (
            queue.claim(
                lease_owner="consumer-1",
                now=NOW + timedelta(seconds=9),
                lease_seconds=30,
                limit=1,
            )
            == ()
        )
        [retried] = queue.claim(
            lease_owner="consumer-1",
            now=NOW + timedelta(seconds=10),
            lease_seconds=30,
            limit=1,
        )
        assert queue.mark_dead(
            retried.queue_id,
            lease_owner="consumer-1",
            error="poison",
        )
        assert (
            queue.claim(
                lease_owner="consumer-1",
                now=NOW + timedelta(days=1),
                lease_seconds=30,
                limit=1,
            )
            == ()
        )


def test_queue_concurrent_initialization_is_serialized(tmp_path):
    database_path = tmp_path / "campaigns.sqlite"

    def initialize():
        with SQLiteBatchCampaignQueue(database_path) as queue:
            return queue.enqueue(payload("campaign-1"))

    with ThreadPoolExecutor(max_workers=4) as executor:
        results = list(executor.map(lambda _: initialize(), range(8)))

    assert results.count("campaign-1") == 1
    assert results.count(None) == 7


def test_queue_uses_event_id_to_allow_several_stages_for_one_campaign(tmp_path):
    database_path = tmp_path / "campaigns.sqlite"
    first = {**payload("kr-afternoon-2026-08-07"), "event_id": "batch:report:005930"}
    second = {**payload("kr-afternoon-2026-08-07"), "event_id": "batch:portfolio"}

    with SQLiteBatchCampaignQueue(database_path) as queue:
        assert queue.enqueue(first) == "batch:report:005930"
        assert queue.enqueue(second) == "batch:portfolio"
        assert queue.enqueue(first) is None
        assert [row["campaign_id"] for row in queue.list_entries()] == [
            "batch:report:005930",
            "batch:portfolio",
        ]
