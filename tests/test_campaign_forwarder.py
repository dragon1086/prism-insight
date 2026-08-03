"""Forwarding campaign events from the orchestrator host to the bot host.

The queue is a SQLite file, so it cannot span the two servers. These tests pin
the contract that lets the split work at all: forwarding is idempotent, a
failed hop is retried rather than lost, and a permanently broken event stops
consuming attempts instead of blocking the queue forever.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from messaging.campaign_forwarder import (
    CampaignForwarder,
    SshCampaignShipper,
)
from messaging.local_campaign_queue import SQLiteBatchCampaignQueue

NOW = datetime(2026, 8, 3, 5, 0, tzinfo=timezone.utc)
OWNER = "db-server:1"


def event(campaign_id: str = "kr-afternoon-20260803") -> dict:
    return {
        "schema_version": 1,
        "event_type": "BATCH_CAMPAIGN_COMPLETED",
        "campaign_id": campaign_id,
        "market": "KR",
        "session": "AFTERNOON",
        "trade_date": "20260803",
        "regime": "NEUTRAL",
        "candidates": [{"ticker": "005930", "company_name": "삼성전자"}],
    }


class FakeShipper:
    """Records what it was handed; replays a queued script of outcomes."""

    def __init__(self, *outcomes) -> None:
        self._outcomes = list(outcomes)
        self.shipped: list[dict] = []

    def ship(self, payload):
        self.shipped.append(dict(payload))
        outcome = self._outcomes.pop(0) if self._outcomes else True
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


@pytest.fixture
def queue(tmp_path):
    with SQLiteBatchCampaignQueue(tmp_path / "campaigns.sqlite") as q:
        yield q


def forwarder(queue, shipper, **kwargs):
    return CampaignForwarder(queue, shipper, lease_owner=OWNER, **kwargs)


def status_of(queue, campaign_id: str) -> str:
    for entry in queue.list_entries():
        if entry["campaign_id"] == campaign_id:
            return entry["status"]
    raise AssertionError(f"{campaign_id} not in queue")


def test_a_published_event_reaches_the_remote_and_is_consumed_locally(queue):
    queue.enqueue(event())
    shipper = FakeShipper(True)

    result = forwarder(queue, shipper).run_once(now=NOW)

    assert result.claimed == 1
    assert result.forwarded == 1
    assert [p["campaign_id"] for p in shipper.shipped] == ["kr-afternoon-20260803"]
    # Consumed locally, so the next cron run does not ship it again.
    assert status_of(queue, "kr-afternoon-20260803") == "CONSUMED"


def test_the_whole_payload_is_forwarded_untouched(queue):
    queue.enqueue(event())

    forwarder(queue, shipper := FakeShipper(True)).run_once(now=NOW)

    [shipped] = shipper.shipped
    assert shipped == event()


def test_an_event_the_remote_already_has_still_counts_as_done(queue):
    # The remote insert is INSERT OR IGNORE, so a re-send is a no-op rather
    # than an error. Treating it as failure would retry forever.
    queue.enqueue(event())
    shipper = FakeShipper(False)

    result = forwarder(queue, shipper).run_once(now=NOW)

    assert result.forwarded == 0
    assert result.duplicate == 1
    assert status_of(queue, "kr-afternoon-20260803") == "CONSUMED"


def test_a_failed_hop_is_retried_not_lost(queue):
    queue.enqueue(event())
    shipper = FakeShipper(RuntimeError("ssh: connect timed out"))

    result = forwarder(queue, shipper).run_once(now=NOW)

    assert result.retry_scheduled == 1
    assert result.forwarded == 0
    assert status_of(queue, "kr-afternoon-20260803") == "PENDING"


def test_the_next_run_picks_up_what_the_last_one_could_not_send(queue):
    queue.enqueue(event())
    shipper = FakeShipper(RuntimeError("network down"), True)

    forwarder(queue, shipper, retry_seconds=1).run_once(now=NOW)
    later = NOW + timedelta(seconds=120)
    result = forwarder(queue, shipper, retry_seconds=1).run_once(now=later)

    assert result.forwarded == 1
    assert len(shipper.shipped) == 2
    assert status_of(queue, "kr-afternoon-20260803") == "CONSUMED"


def test_an_event_that_never_ships_is_eventually_given_up_on(queue):
    queue.enqueue(event())
    shipper = FakeShipper(*[RuntimeError("boom")] * 10)

    moment = NOW
    for _ in range(8):
        forwarder(queue, shipper, retry_seconds=1, max_attempts=3).run_once(now=moment)
        moment += timedelta(seconds=60)

    # Dead, not PENDING: a poison event must not consume every future run.
    assert status_of(queue, "kr-afternoon-20260803") == "DEAD"


def test_several_slots_forward_in_one_run(queue):
    for campaign_id in (
        "kr-morning-20260803",
        "kr-afternoon-20260803",
        "us-morning-20260803",
    ):
        queue.enqueue(event(campaign_id))
    shipper = FakeShipper(True, True, True)

    result = forwarder(queue, shipper).run_once(now=NOW)

    assert result.claimed == 3
    assert result.forwarded == 3


def test_an_empty_queue_is_a_no_op(queue):
    result = forwarder(queue, FakeShipper()).run_once(now=NOW)

    assert (result.claimed, result.forwarded, result.retry_scheduled) == (0, 0, 0)


def test_a_consumed_event_is_not_forwarded_twice(queue):
    queue.enqueue(event())
    shipper = FakeShipper(True, True)

    forwarder(queue, shipper).run_once(now=NOW)
    result = forwarder(queue, shipper).run_once(now=NOW + timedelta(seconds=300))

    assert result.claimed == 0
    assert len(shipper.shipped) == 1


class TestSshShipper:
    """The command shape, without running ssh."""

    def shipper(self, **kwargs):
        defaults = {
            "host": "root@10.0.0.2",
            "repo_path": "/home/prism/prism-insight",
            "queue_path": "/var/lib/prism-kakao/prism_campaign_queue.sqlite",
            "python_path": "/home/prism/venv/bin/python",
        }
        return SshCampaignShipper(**{**defaults, **kwargs})

    def test_the_payload_is_not_in_the_command_line(self):
        # argv is world-readable through `ps`; the payload goes on stdin.
        command = self.shipper()._command()

        assert "root@10.0.0.2" in command
        assert not any("campaign_id" in part for part in command)

    def test_batch_mode_keeps_cron_from_hanging_on_a_prompt(self):
        command = self.shipper()._command()

        assert "BatchMode=yes" in command

    def test_the_remote_runs_the_projects_own_enqueue(self):
        # Not a raw sqlite3 INSERT — validation and INSERT OR IGNORE stay in
        # one implementation.
        script = self.shipper()._command()[-1]

        assert "SQLiteBatchCampaignQueue" in script
        assert "queue.enqueue(payload)" in script
        assert "/home/prism/prism-insight" in script

    def test_an_identity_file_is_passed_through_when_given(self):
        command = self.shipper(identity_file="/root/.ssh/id_ed25519")._command()

        assert "-i" in command
        assert "/root/.ssh/id_ed25519" in command

    def test_no_identity_flag_when_none_is_configured(self):
        assert "-i" not in self.shipper()._command()
